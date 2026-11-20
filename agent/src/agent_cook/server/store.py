import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_cook.core.types import RunResult
from agent_cook.protocol.events import RunEvent
from agent_cook.sandbox.limits import MAX_TRANSFER_BYTES

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})


class ConflictingRun(ValueError):
    """The same idempotency key was already used for another request."""


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    task: str
    file_ids: tuple[str, ...]
    model: str
    image_model: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class RunStore:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = directory / "runs.sqlite3"
        self.artifacts = directory / "artifacts"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, content BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    position INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
                    request TEXT NOT NULL, idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    started_at TEXT, finished_at TEXT,
                    text TEXT, artifacts TEXT NOT NULL DEFAULT '[]', error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL,
                    PRIMARY KEY (run_id, seq)
                );
            """)

    @contextmanager
    def connection(self, *, write: bool = False):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.row_factory = sqlite3.Row
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    @contextmanager
    def serve(self):
        # A separate SQLite file holds the server lease without locking run and event writes.
        with closing(sqlite3.connect(self.database.with_name("server.lock.sqlite3"), timeout=0)) as lease:
            try:
                lease.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                raise RuntimeError("The data directory already has an active server or cannot be locked.") from error
            yield

    def add_file(self, name: str, content: bytes) -> str:
        file_id = uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO files VALUES (?, ?, ?)", (file_id, name, content))
        return file_id

    def input_files(self, ids: tuple[str, ...], *, load: bool = True) -> dict[str, bytes]:
        files: dict[str, bytes] = {}
        total = 0
        with self.connection() as db:
            for file_id in ids:
                row = db.execute(
                    "SELECT name, length(content) AS size FROM files WHERE id = ?", (file_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("An attached file ID was not found.")
                name = row["name"]
                if name in files:
                    raise ValueError(f"Multiple input files have the name '{name}'.")
                total += row["size"]
                if total > MAX_TRANSFER_BYTES:
                    raise ValueError("The attachments exceed the workspace transfer limit.")
                files[name] = (
                    db.execute("SELECT content FROM files WHERE id = ?", (file_id,)).fetchone()[0]
                    if load else b""
                )
        return files

    def enqueue(self, job: Job, key: str | None) -> str:
        request = asdict(job)
        request.pop("id")
        payload = _json(request)
        with self.connection(write=True) as db:
            if key is not None:
                saved = db.execute("SELECT id, request FROM runs WHERE idempotency_key = ?", (key,)).fetchone()
                if saved is not None:
                    if saved["request"] != payload:
                        raise ConflictingRun("The idempotency key belongs to a different submission.")
                    return saved["id"]
            db.execute(
                "INSERT INTO runs (id, request, idempotency_key, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                (job.id, payload, key, _now()),
            )
            return job.id

    def claim(self) -> Job | None:
        with self.connection(write=True) as db:
            row = db.execute("SELECT id, request FROM runs WHERE status = 'queued' ORDER BY position LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE runs SET status = 'running', started_at = ? WHERE id = ?", (_now(), row["id"]))
        values = json.loads(row["request"])
        values["file_ids"] = tuple(values["file_ids"])
        return Job(id=row["id"], **values)

    def get(self, run_id: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT id AS run_id, status, created_at, started_at, finished_at, text, artifacts, error "
                "FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "artifacts": json.loads(row["artifacts"])}

    def events_after(self, run_id: str, seq: int) -> list[tuple[int, str]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT seq, event FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT 128", (run_id, seq),
            ).fetchall()
        return [(row["seq"], row["event"]) for row in rows]

    def append(self, event: RunEvent) -> None:
        with self.connection() as db:
            self._append(db, event)

    @staticmethod
    def _append(db: sqlite3.Connection, event: RunEvent) -> None:
        db.execute("INSERT INTO events VALUES (?, ?, ?)", (event.root_run_id, event.seq, _json(asdict(event))))

    def finish(
        self, run_id: str, status: str, *, result: RunResult | None = None,
        error: str | None = None, event: RunEvent | None = None, expected: str = "running",
    ) -> None:
        with self.connection(write=True) as db:
            self._finish(db, run_id, status, result=result, error=error, event=event, expected=expected)

    def recover(self) -> tuple[str, ...]:
        with self.connection(write=True) as db:
            interrupted = db.execute("SELECT id FROM runs WHERE status = 'running'").fetchall()
            for row in interrupted:
                self._finish(db, row["id"], "interrupted", error="The service was restarted.")
        return tuple(row["id"] for row in interrupted)

    @classmethod
    def _finish(
        cls, db, run_id, status, *, result=None, error=None, event=None, expected="running",
    ) -> None:
        stamp = _now()
        changed = db.execute(
            "UPDATE runs SET status = ?, finished_at = ?, text = ?, artifacts = ?, error = ? "
            "WHERE id = ? AND status = ?",
            (status, stamp, result.text if result else None,
             _json([asdict(item) for item in result.artifacts]) if result else "[]",
             error, run_id, expected),
        )
        if changed.rowcount != 1:
            return
        if event is None:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)).fetchone()[0]
            details = {"message": error} if error is not None else {}
            kind = "completed" if status == "succeeded" else status
            event = RunEvent(run_id, run_id, None, seq, stamp, f"run.{kind}", details)
        cls._append(db, event)
