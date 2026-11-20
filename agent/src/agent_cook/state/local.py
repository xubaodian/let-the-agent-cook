import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from time import time_ns
from uuid import uuid4

from agent_cook.state.session import Checkpoint, Message, Session
from agent_cook.tools.spec import ToolCall


@dataclass(frozen=True, slots=True)
class LocalSession:
    id: str = field(default_factory=lambda: uuid4().hex)
    revision: int = 0
    history: Session = field(default_factory=Session)
    model: str | None = None
    image_model: str | None = None
    sandbox: str | None = None
    inputs: dict[str, bytes] = field(default_factory=dict)
    results: dict[str, bytes] = field(default_factory=dict)
    result_dir: str | None = None

    def continue_history(self, model: str) -> Session:
        messages = list(self.history.messages)
        if self.model != model:
            messages = [replace(message, provider_data={}) for message in messages]
        return Session(messages, self.history.checkpoint, set(self.history.failed_tool_calls))


class LocalSessions:
    """Atomically save a conversation and its file snapshots for one project."""

    def __init__(self, database: Path) -> None:
        self._database = database
        database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    saved_at INTEGER NOT NULL, record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS session_files (
                    session_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
                    content BLOB NOT NULL, PRIMARY KEY (session_id, kind, path)
                );
            """)

    def load(self, session_id: str | None) -> LocalSession:
        with closing(sqlite3.connect(self._database)) as db, db:
            db.execute("BEGIN")
            if session_id is None:
                row = db.execute(
                    "SELECT id, revision, record FROM sessions ORDER BY saved_at DESC LIMIT 1"
                ).fetchone()
            else:
                row = db.execute("SELECT id, revision, record FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                raise ValueError("No matching session in this working directory.")
            session_id, revision, payload = row
            snapshots: dict[str, dict[str, bytes]] = {"inputs": {}, "results": {}}
            for kind, path, content in db.execute(
                "SELECT kind, path, content FROM session_files WHERE session_id = ?", (session_id,),
            ):
                snapshots[kind][path] = content

        record = json.loads(payload)
        history = Session(
            messages=[
                Message(**{**message, "tool_calls": tuple(ToolCall(**call) for call in message["tool_calls"])})
                for message in record.pop("messages")
            ],
            checkpoint=Checkpoint(**checkpoint) if (checkpoint := record.pop("checkpoint")) else None,
            failed_tool_calls=set(record.pop("failed_tool_calls")),
        )
        return LocalSession(id=session_id, revision=revision, history=history, **record, **snapshots)

    def save(self, session: LocalSession) -> LocalSession:
        revision = session.revision + 1
        record = json.dumps({
            "messages": [asdict(message) for message in session.history.messages],
            "checkpoint": asdict(session.history.checkpoint) if session.history.checkpoint is not None else None,
            "failed_tool_calls": sorted(session.history.failed_tool_calls),
            "model": session.model, "image_model": session.image_model, "sandbox": session.sandbox,
            "result_dir": session.result_dir,
        }, ensure_ascii=False)
        with closing(sqlite3.connect(self._database)) as db, db:
            if session.revision == 0:
                db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (session.id, revision, time_ns(), record))
            else:
                written = db.execute(
                    "UPDATE sessions SET revision = ?, saved_at = ?, record = ? WHERE id = ? AND revision = ?",
                    (revision, time_ns(), record, session.id, session.revision),
                )
                if written.rowcount != 1:
                    raise ValueError("This session changed in another terminal. Resume the latest version.")
            db.execute("DELETE FROM session_files WHERE session_id = ?", (session.id,))
            db.executemany(
                "INSERT INTO session_files VALUES (?, ?, ?, ?)",
                (
                    (session.id, kind, path, content)
                    for kind, files in (("inputs", session.inputs), ("results", session.results))
                    for path, content in files.items()
                ),
            )
        return replace(session, revision=revision)
