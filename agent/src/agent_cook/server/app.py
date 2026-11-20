import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest
from time import monotonic
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from agent_cook.core.types import AgentRunError, RunResult
from agent_cook.events.journal import RunEvents
from agent_cook.protocol.events import RunEvent
from agent_cook.sandbox.interface import SandboxError
from agent_cook.sandbox.limits import MAX_FILE_BYTES
from agent_cook.sandbox.paths import workspace_path
from agent_cook.server.store import TERMINAL, ConflictingRun, Job, RunStore

Execute = Callable[[Job, dict[str, bytes], RunEvents, Path], Awaitable[RunResult]]
_RUN_TERMINALS = frozenset({"run.completed", "run.failed", "run.cancelled"})
log = logging.getLogger(__name__)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=100_000)
    file_ids: list[str] = Field(default_factory=list, max_length=256)
    model: str | None = None
    image_model: str | None = None


class RunQueue:
    def __init__(self, store: RunStore, execute: Execute) -> None:
        self.store = store
        self.execute = execute
        self.ready = asyncio.Event()
        self.active: dict[str, asyncio.Task[RunResult]] = {}

    async def consume(self) -> None:
        while True:
            job = self.store.claim()
            if job is None:
                self.ready.clear()
                await self.ready.wait()
                continue
            await self.run(job)

    async def run(self, job: Job) -> None:
        terminal: RunEvent | None = None

        def record(event: RunEvent) -> None:
            nonlocal terminal
            if event.parent_run_id is None and event.type in _RUN_TERMINALS:
                terminal = event
            else:
                self.store.append(event)

        async def execute() -> RunResult:
            files = self.store.input_files(job.file_ids)
            return await self.execute(
                job, files, RunEvents(record, run_id=job.id), self.store.artifacts / f"{job.id}-artifacts",
            )

        task = asyncio.create_task(execute())
        self.active[job.id] = task
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            stopping = asyncio.current_task().cancelling()
            if stopping:
                if not task.done() and not task.cancelling():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.store.finish(job.id, "interrupted", error="The service stopped during this run.")
                raise
            self.store.finish(job.id, "cancelled", event=terminal)
        except AgentRunError as error:
            self.store.finish(job.id, "failed", error=str(error), event=terminal)
        except Exception as error:
            self.store.finish(job.id, "failed", error=f"Run failed ({type(error).__name__}).", event=terminal)
        else:
            self.store.finish(job.id, "succeeded", result=result, event=terminal)
        finally:
            self.active.pop(job.id, None)


def create_app(
    directory: Path, *, token: str, execute: Execute, chat_default: str, concurrency: int = 1,
    image_default: str | None = None, chat_choices: tuple[str, ...] = (),
    image_choices: tuple[str, ...] = (), workspace: bool = False,
    reclaim: Callable[[str], Awaitable[None]] | None = None,
) -> FastAPI:
    if not token.strip() or concurrency < 1:
        raise ValueError("Provide a service token and a positive concurrency limit.")
    chat_models = tuple(dict.fromkeys((chat_default, *chat_choices)))
    image_models = tuple(dict.fromkeys(model for model in (image_default, *image_choices) if model and workspace))
    store = RunStore(directory)
    queue = RunQueue(store, execute)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with store.serve():
            interrupted = store.recover()
            if reclaim is not None:
                for run_id in interrupted:
                    try:
                        await reclaim(run_id)
                    except SandboxError:
                        log.warning("Could not reclaim the Docker sandbox for interrupted run %s.", run_id)
            workers = [asyncio.create_task(queue.consume()) for _ in range(concurrency)]
            try:
                yield
            finally:
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> None:
        if credentials is None or not compare_digest(credentials.credentials.encode(), token.encode()):
            raise HTTPException(401, "Invalid service token.", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(
        title="Agent Cook", lifespan=lifespan, dependencies=[Depends(authenticate)],
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    def get_run(run_id: str) -> dict:
        record = store.get(run_id)
        if record is None:
            raise HTTPException(404, "Run not found.")
        return record

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models():
        return {
            "chat": {"default": chat_default, "available": chat_models},
            "image": {"default": image_default if workspace else None, "available": image_models},
        }

    @app.post("/v1/files", status_code=201)
    async def upload(request: Request, name: str):
        if not workspace:
            raise HTTPException(409, "This service needs a workspace backend to accept attachments.")
        try:
            if workspace_path(name) != name or "/" in name:
                raise ValueError
        except ValueError:
            raise HTTPException(422, "Provide a single filename.") from None
        contents = bytearray()
        async for chunk in request.stream():
            if len(contents) + len(chunk) > MAX_FILE_BYTES:
                raise HTTPException(413, "The attachment exceeds the file size limit.")
            contents.extend(chunk)
        file_id = store.add_file(name, bytes(contents))
        return {"file_id": file_id, "name": name, "bytes": len(contents)}

    @app.post("/v1/runs", status_code=202)
    async def submit(
        body: Submission, key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)] = None,
    ):
        if not body.task.strip():
            raise HTTPException(422, "Provide a nonempty task.")
        if body.file_ids and not workspace:
            raise HTTPException(422, "This service needs a workspace backend to run with attachments.")
        if body.model is not None and body.model not in chat_models:
            raise HTTPException(422, "Select a chat model from /v1/models.")
        if body.image_model is not None and body.image_model not in image_models:
            raise HTTPException(422, "Select an image model from /v1/models.")
        job = Job(
            uuid4().hex, body.task, tuple(body.file_ids), body.model or chat_default,
            body.image_model or (image_default if workspace else None),
        )
        try:
            store.input_files(job.file_ids, load=False)
            run_id = store.enqueue(job, key)
        except ConflictingRun as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        queue.ready.set()
        return get_run(run_id)

    @app.get("/v1/runs/{run_id}")
    async def status(run_id: str):
        return get_run(run_id)

    @app.post("/v1/runs/{run_id}/cancel", status_code=202)
    async def cancel(run_id: str):
        if get_run(run_id)["status"] == "queued":
            store.finish(run_id, "cancelled", expected="queued")
        elif (task := queue.active.get(run_id)) is not None and not task.done() and not task.cancelling():
            task.cancel()
        return get_run(run_id)

    @app.get("/v1/runs/{run_id}/events")
    async def events(
        run_id: str,
        last: Annotated[int, Header(alias="Last-Event-ID", ge=0, le=2**63 - 1)] = 0,
    ):
        get_run(run_id)

        async def stream():
            seq = last
            heartbeat = monotonic()
            while True:
                # Reading status first keeps the terminal event visible even if a run finishes during polling.
                terminal = get_run(run_id)["status"] in TERMINAL
                rows = store.events_after(run_id, seq)
                if rows:
                    for seq, payload in rows:
                        event = json.loads(payload)
                        yield f"id: {seq}\nevent: {event['type']}\ndata: {payload}\n\n"
                    heartbeat = monotonic()
                    continue
                if terminal:
                    return
                if monotonic() - heartbeat >= 15:
                    yield ": keep-alive\n\n"
                    heartbeat = monotonic()
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/v1/runs/{run_id}/artifacts/{artifact_id}")
    async def artifact(run_id: str, artifact_id: str):
        files = get_run(run_id)["artifacts"]
        selected = next((file for file in files if file["id"] == artifact_id), None)
        if selected is None:
            raise HTTPException(404, "Artifact not found.")
        root = (store.artifacts / f"{run_id}-artifacts").resolve()
        target = (store.artifacts / selected["path"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "Artifact file not found.")
        return FileResponse(target, media_type=selected["media_type"], filename=target.name)

    return app
