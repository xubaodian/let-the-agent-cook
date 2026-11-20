import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from functools import partial
from importlib.util import find_spec
from math import ceil
from pathlib import Path, PurePosixPath
from shutil import which
from subprocess import DEVNULL, TimeoutExpired, run
from uuid import uuid4

from agent_cook.context.policy import DEFAULT_CONTEXT_POLICY
from agent_cook.core.types import AgentRunError, AgentSpec, RunResult
from agent_cook.events.journal import RunEvents, write_jsonl
from agent_cook.model.catalog import ModelCatalog
from agent_cook.protocol.events import RunEvent
from agent_cook.protocol.request import RunRequest
from agent_cook.runner import run_task
from agent_cook.sandbox.docker import DEFAULT_DOCKER_IMAGE, DockerSandboxFactory
from agent_cook.sandbox.e2b import E2BSandboxFactory
from agent_cook.sandbox.interface import SandboxError, SandboxFactory
from agent_cook.sandbox.limits import MAX_FILE_BYTES, MAX_TRANSFER_BYTES
from agent_cook.sandbox.paths import workspace_path
from agent_cook.state.local import LocalSession, LocalSessions
from agent_cook.terminal import file_mentions, prompt_reader, show_progress


def _select_model(
    override: str | None, requested: str | None, env_name: str, default: str | None,
) -> str | None:
    if override is not None:
        return override.strip() or None
    return (requested or "").strip() or os.environ.get(env_name, "").strip() or default


def _read_bounded(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    if not path.is_file():
        raise ValueError(f"Input '{path}' must be a regular file.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Input '{path}' exceeds the file or batch transfer limit.")
    return data


def _input_files(paths: list[Path]) -> dict[str, bytes]:
    inputs = {}
    remaining = MAX_TRANSFER_BYTES
    for path in paths:
        name = workspace_path(path.name)
        if name in inputs:
            raise ValueError(f"Multiple input files have the name '{name}'.")
        data = _read_bounded(path, min(MAX_FILE_BYTES, remaining))
        inputs[name] = data
        remaining -= len(data)
    return inputs


async def _run(
    spec: AgentSpec,
    task: str,
    api_key: str,
    trace_path: Path,
    run_id: str,
    factory: SandboxFactory | None,
    inputs: dict[str, bytes],
    *,
    observer: Callable[[RunEvent], None] | None = None,
    **options,
) -> RunResult:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("x", encoding="utf-8") as trace:
        def record(event: RunEvent) -> None:
            write_jsonl(trace, event)
            if observer is not None:
                observer(event)

        return await run_task(
            spec, task, api_key, factory, inputs,
            events=RunEvents(record, run_id=run_id),
            artifact_dir=trace_path.parent / f"{run_id}-artifacts",
            **options,
        )


def _prepare(args: argparse.Namespace, request: RunRequest, catalog: ModelCatalog):
    if (args.sandbox_network or request.skill_directories) and args.sandbox is None:
        raise ValueError("Sandbox networking and skill directories require --sandbox.")
    chat_model = _select_model(args.model, request.model, "OPENROUTER_MODEL", catalog.chat.default)
    if not chat_model:
        raise ValueError(
            "Choose a chat model via --model, the task's model, OPENROUTER_MODEL, "
            "or chat.default in --models-config."
        )
    if (args.image_model is not None or request.image_model is not None) and args.sandbox is None:
        raise ValueError("Selecting an image model for the task requires --sandbox.")
    image_model = _select_model(
        args.image_model, request.image_model, "OPENROUTER_IMAGE_MODEL", catalog.image.default,
    )
    spec = AgentSpec(
        model=chat_model, max_model_turns=args.max_turns, timeout_seconds=args.timeout,
        context_policy=replace(DEFAULT_CONTEXT_POLICY, context_window=args.context_window),
    )
    if request.web.browser_origins and args.sandbox is None:
        raise ValueError("web.browser_origins requires --sandbox for the separate browser worker.")
    make_sandbox = None
    if args.sandbox == "docker":
        make_sandbox = partial(DockerSandboxFactory, args.docker_image)
    elif args.sandbox == "e2b":
        if not os.environ.get("E2B_API_KEY", "").strip():
            raise ValueError("Set E2B_API_KEY before using the remote sandbox.")
        make_sandbox = partial(
            E2BSandboxFactory,
            os.environ.get("E2B_TEMPLATE", ""),
            lifetime_seconds=ceil(spec.timeout_seconds) + 60,
        )
    factory = make_sandbox(allow_network=args.sandbox_network) if make_sandbox else None
    browser_factory = (
        make_sandbox(allow_network=True) if make_sandbox and request.web.browser_origins else None
    )
    plugins = ()
    if request.plugins:
        from agent_cook.extensions.plugins import load_plugins

        plugins = load_plugins(request.plugins)
    skill_directories = (*request.skill_directories, *(
        plugin.skills_dir for plugin in plugins if plugin.skills_dir is not None
    ))
    skills = None
    if plugins or skill_directories or request.mounted_skills is not None:
        from agent_cook.extensions.skills import SkillCatalog

        skills = SkillCatalog(skill_directories, request.mounted_skills)
    if factory is None and (
        (skills is not None and skills.names) or any(plugin.hooks or plugin.files for plugin in plugins)
    ):
        raise ValueError("Mounted skills, plugin files, and hooks require --sandbox.")
    return spec, factory, {
        "image_model": image_model, "skills": skills, "plugins": plugins,
        "mcp_tools": request.mcp_tools, "web": request.web, "browser_factory": browser_factory,
    }


def _parser(mode: str) -> argparse.ArgumentParser:
    name = "agent-cook" if mode == "local" else f"agent-cook {mode}"
    parser = argparse.ArgumentParser(
        prog=name, description="Run tasks with your OpenRouter model.",
        epilog="Commands: exec, resume, doctor, serve. Run without a task to start an interactive session.",
    )
    if mode in ("local", "exec"):
        parser.add_argument("task", nargs="?", help="Text to send to the agent, or - with exec to read stdin")
    elif mode == "resume":
        previous = parser.add_mutually_exclusive_group(required=True)
        previous.add_argument("session_id", nargs="?", help="ID printed by the previous interactive session")
        previous.add_argument("--last", action="store_true", help="Resume the latest session in this directory")
    if mode == "serve":
        parser.add_argument("--host", default="127.0.0.1", help="Listening address")
        parser.add_argument("--port", type=int, default=8000, help="Listening port")
        parser.add_argument("--data-dir", type=Path, default=Path(".agent-cook/server"), help="Persistent server data")
        parser.add_argument("--concurrency", type=int, default=1, help="Maximum number of concurrent runs")
    parser.add_argument("-C", "--cd", type=Path, help="Use this directory for task paths and interactive sessions")
    parser.add_argument("--request", type=Path, help="Read the task, model choices and extensions from a JSON request")
    parser.add_argument("--models-config", type=Path, help="Use a custom JSON model catalog")
    parser.add_argument("--list-models", action="store_true", help="Print the configured chat and image models as JSON")
    parser.add_argument("-m", "--model", help="Chat model ID (or set OPENROUTER_MODEL)")
    parser.add_argument("--image-model", help="Image API model ID (or set OPENROUTER_IMAGE_MODEL)")
    if mode in ("local", "exec"):
        parser.add_argument("--json", action="store_true", help="Stream JSONL run events on stdout")
    parser.add_argument("--trace", type=Path, help="Write the run to a new JSONL file at this path")
    parser.add_argument("--sandbox", choices=("docker", "e2b", "none"), help="Enable task workspace tools")
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE, help="Prebuilt local sandbox image")
    parser.add_argument("--sandbox-network", action="store_true", help="Enable outbound sandbox networking")
    parser.add_argument("--input", action="append", type=Path, default=[], help="Copy a local input file into the workspace")
    parser.add_argument(
        "--skills-dir", action="append", type=Path, default=[], help="Load a directory of Skill packages",
    )
    parser.add_argument("--skill", action="append", help="Mount a named Skill. Repeat to select several.")
    parser.add_argument("--plugin", action="append", type=Path, default=[], help="Enable a local plugin directory")
    parser.add_argument("--mcp-tool", action="append", help="Enable an MCP tool by server/tool. Repeat to select several.")
    parser.add_argument(
        "--context-window", type=int, default=DEFAULT_CONTEXT_POLICY.context_window,
        help="Context window in tokens for the selected model (default: %(default)s).",
    )
    parser.add_argument("--max-turns", type=int, default=8, help="Maximum number of model turns")
    parser.add_argument("--timeout", type=float, default=120, help="Run time limit in seconds")
    return parser


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY before running the agent.")
    return key


def _paths(args: argparse.Namespace, directory: Path) -> None:
    for key in ("request", "models_config", "trace", "data_dir"):
        if (path := getattr(args, key, None)) is not None:
            setattr(args, key, directory / path.expanduser())
    for key in ("input", "plugin", "skills_dir"):
        setattr(args, key, [directory / path.expanduser() for path in getattr(args, key)])


def _request(args: argparse.Namespace, task: str) -> RunRequest:
    if args.request is not None:
        return RunRequest.from_file(args.request)
    return RunRequest(
        task=task, plugins=tuple(args.plugin), skill_directories=tuple(args.skills_dir),
        mounted_skills=tuple(args.skill) if args.skill is not None else None,
        mcp_tools=tuple(args.mcp_tool) if args.mcp_tool is not None else None,
    )


def _set_sandbox(args: argparse.Namespace, selected: str | None) -> None:
    if selected not in (None, "none", "docker", "e2b"):
        raise ValueError("The task sandbox must be docker, e2b or none.")
    args.sandbox = selected if selected != "none" else None


def _execute(args, directory, prepared, task, api_key, inputs, *, observer=None, **extra):
    if inputs and prepared[1] is None:
        raise ValueError("Input files require --sandbox docker or --sandbox e2b.")
    spec, factory, options = prepared
    run_id = uuid4().hex
    trace = args.trace or directory / ".agent-cook" / "runs" / f"{run_id}.jsonl"
    result = asyncio.run(_run(
        spec, task, api_key, trace, run_id, factory, inputs,
        observer=observer, **options, **extra,
    ))
    if not getattr(args, "json", False):
        print(result.text)
    print(f"Trace written to {trace}", file=sys.stderr)
    for artifact in result.artifacts:
        print(f"Result file {trace.parent / artifact.path}", file=sys.stderr)
    return result, trace


def _failure(error: Exception) -> None:
    if isinstance(error, ModuleNotFoundError):
        message = f"Install the project dependency '{error.name}' before running."
    elif isinstance(error, (AgentRunError, OSError, SandboxError, ValueError, sqlite3.Error)):
        message = str(error)
    else:
        message = f"The provider or runtime failed ({type(error).__name__})."
    print(message, file=sys.stderr)


def _restore_results(trace: Path, result: RunResult) -> dict[str, bytes]:
    snapshots = {}
    for artifact in result.artifacts:
        relative = PurePosixPath(artifact.path)
        if len(relative.parts) < 3 or relative.parts[0] != f"{result.run_id}-artifacts":
            raise ValueError("The result does not belong to this run's export.")
        path = workspace_path(PurePosixPath(*relative.parts[2:]).as_posix())
        snapshots[path] = _read_bounded(trace.parent / artifact.path)
    return snapshots


def _interactive(args: argparse.Namespace, directory: Path, catalog: ModelCatalog, resume: bool) -> int:
    store = LocalSessions(directory / ".agent-cook" / "sessions.sqlite3")
    saved = store.load(args.session_id) if resume else LocalSession()
    configured = saved.sandbox if resume else os.environ.get("AGENT_COOK_SANDBOX", "").strip() or None
    _set_sandbox(args, args.sandbox or configured)
    if args.model is None:
        args.model = saved.model
    if args.image_model is None and args.sandbox is not None:
        args.image_model = saved.image_model
    read = prompt_reader(directory) if sys.stdin.isatty() and sys.stdout.isatty() else input
    pending = list(args.input)
    print(f"Session {saved.id} in {directory}. Type /help for commands.", file=sys.stderr)
    while True:
        try:
            line = read("cook> ").strip()
        except EOFError:
            return 0
        except KeyboardInterrupt:
            continue
        if not line:
            continue
        if line.startswith("/"):
            command, _, value = line.partition(" ")
            value = value.strip()
            if command == "/exit":
                return 0
            if command == "/new":
                saved = LocalSession()
                pending.clear()
                print(f"Session {saved.id}", file=sys.stderr)
            elif command == "/files":
                names = dict.fromkeys((*saved.inputs, *saved.results, *(path.name for path in pending)))
                print("\n".join(names) or "No files selected.")
            elif command == "/model":
                if value:
                    args.model = value
                else:
                    print("\n".join(catalog.chat.available) or "Set a model with /model <id>.")
            elif command == "/sandbox":
                if value:
                    try:
                        _set_sandbox(args, value)
                        if args.sandbox is None:
                            args.image_model = None
                    except ValueError as error:
                        _failure(error)
                else:
                    print(args.sandbox or "none")
            elif command == "/help":
                print("Use @path for a file. Commands: /files, /model [id], /sandbox [backend], /new, /exit.")
            else:
                print(f"Unknown command {command}. Use /help.", file=sys.stderr)
            continue
        try:
            task, mentions = file_mentions(line, directory)
            added = _input_files(list(dict.fromkeys((*pending, *mentions))))
            inputs = {**saved.inputs, **added}
            staged = {**saved.inputs, **saved.results, **added}
            if sum(map(len, staged.values())) > MAX_TRANSFER_BYTES:
                raise ValueError("This session's inputs and results exceed the workspace transfer limit.")
            request = _request(args, task)
            prepared = _prepare(args, request, catalog)
            spec, _, options = prepared
            history = saved.continue_history(spec.model)
            result, trace = _execute(
                args, directory, prepared, request.task, _api_key(), staged,
                observer=show_progress, session=history,
            )
            snapshots = _restore_results(trace, result) if result.artifacts else saved.results
            if sum(map(len, {**inputs, **snapshots}.values())) > MAX_TRANSFER_BYTES:
                raise ValueError("The result was exported. Its snapshots exceed the session transfer limit.")
            saved = store.save(replace(
                saved, history=history, model=spec.model, sandbox=args.sandbox,
                image_model=options["image_model"], inputs=inputs, results=snapshots,
            ))
            args.trace = None
            pending.clear()
        except KeyboardInterrupt:
            print("Run cancelled.", file=sys.stderr)
        except Exception as error:
            _failure(error)


def _doctor(args: argparse.Namespace, catalog: ModelCatalog) -> int:
    checks = {
        "Python OpenAI SDK": bool(find_spec("openai")),
        "OpenRouter API key configured": bool(os.environ.get("OPENROUTER_API_KEY", "").strip()),
        "Chat model configured": bool(_select_model(args.model, None, "OPENROUTER_MODEL", catalog.chat.default)),
    }
    if args.sandbox == "docker":
        checks["Docker daemon"] = bool(which("docker")) and _docker_check("info", "--format", "{{.ServerVersion}}")
        checks["Sandbox image"] = checks["Docker daemon"] and _docker_check("image", "inspect", args.docker_image)
    if args.sandbox == "e2b":
        checks["E2B SDK"] = bool(find_spec("e2b"))
        checks["E2B credentials and template"] = all(
            os.environ.get(name, "").strip() for name in ("E2B_API_KEY", "E2B_TEMPLATE")
        )
    for label, available in checks.items():
        print(f"{'OK' if available else 'Missing'}  {label}")
    if args.sandbox == "docker" and not checks["Sandbox image"]:
        print("Start Docker and run ./cook setup --docker from the repository.", file=sys.stderr)
    return 0 if all(checks.values()) else 1


def _docker_check(*args: str) -> bool:
    try:
        return run(["docker", *args], stdout=DEVNULL, stderr=DEVNULL, timeout=5).returncode == 0
    except (OSError, TimeoutExpired):
        return False


def _serve(args, catalog: ModelCatalog) -> int:
    token = os.environ.get("AGENT_COOK_SERVER_TOKEN", "").strip()
    if not token:
        raise ValueError("Set a separate AGENT_COOK_SERVER_TOKEN before starting the HTTP service.")
    api_key = _api_key()
    spec, factory, options = _prepare(args, _request(args, "Service defaults"), catalog)
    try:
        import uvicorn

        from agent_cook.server.app import create_app
    except ModuleNotFoundError as error:
        raise ValueError("Install agent-cook[server] or use ./cook serve.") from error

    async def execute(job, inputs, events, artifact_dir):
        limits = replace(spec, model=job.model)
        return await run_task(
            limits, job.task, api_key, factory, inputs,
            events=events, artifact_dir=artifact_dir,
            **{**options, "image_model": job.image_model},
        )

    app = create_app(
        args.data_dir, token=token, execute=execute, concurrency=args.concurrency,
        chat_default=spec.model, image_default=options["image_model"] if factory is not None else None,
        chat_choices=catalog.chat.available, image_choices=catalog.image.available,
        workspace=factory is not None, reclaim=factory.reclaim if isinstance(factory, DockerSandboxFactory) else None,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)
    return 0


def main(argv: list[str] | None = None) -> int:
    words = list(sys.argv[1:] if argv is None else argv)
    mode = words.pop(0) if words and words[0] in ("exec", "resume", "doctor", "serve") else "local"
    parser = _parser(mode)
    args = parser.parse_args(words)
    try:
        directory = (args.cd or Path.cwd()).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("--cd must name a directory.")
        _paths(args, directory)
        catalog = ModelCatalog.load(args.models_config) if args.models_config is not None else ModelCatalog()
        if args.list_models:
            print(json.dumps(asdict(catalog), indent=2))
            return 0
        interactive = mode == "resume" or (mode == "local" and args.task is None and args.request is None)
        if interactive:
            if args.request:
                raise ValueError("Use exec --request to start a one-off JSON task.")
            if getattr(args, "json", False):
                raise ValueError("Use exec --json for machine-readable output.")
            return _interactive(args, directory, catalog, mode == "resume")
        configured = os.environ.get("AGENT_COOK_SANDBOX", "").strip() or None
        _set_sandbox(args, args.sandbox or configured)
        if args.request is not None and (
            getattr(args, "task", None) is not None or args.plugin or args.skills_dir or args.skill or args.mcp_tool
        ):
            raise ValueError("Specify the task and extension selections in the JSON file when using --request.")
        if mode == "doctor":
            return _doctor(args, catalog)
        if mode == "serve":
            if args.input or args.trace:
                raise ValueError("The server accepts files and records traces per submitted run.")
            return _serve(args, catalog)
        api_key = _api_key()
        task = sys.stdin.read() if mode == "exec" and args.task == "-" else (args.task or "")
        request = _request(args, task)
        prepared = _prepare(args, request, catalog)
        inputs = _input_files(args.input)
        if inputs and prepared[1] is None:
            raise ValueError("Input files require --sandbox docker or --sandbox e2b.")
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))

    try:
        observer = partial(write_jsonl, sys.stdout) if args.json else None
        _execute(args, directory, prepared, request.task, api_key, inputs, observer=observer)
    except KeyboardInterrupt:
        print("Run cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        _failure(error)
        return 1
    return 0
