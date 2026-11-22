"""The standard-library launcher copied into the per-user source installation."""

import re
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from subprocess import Popen


def is_revision(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{40}", value) is not None


def installed_revisions(root: Path) -> tuple[str | None, str | None]:
    try:
        refs = (root / "current").read_text(encoding="ascii").splitlines()
    except FileNotFoundError:
        return None, None
    if not 1 <= len(refs) <= 2 or not all(map(is_revision, refs)):
        raise ValueError("The Agent Cook installation has an invalid version record. Run the global installer again.")
    return refs[0], refs[1] if len(refs) == 2 else None


@contextmanager
def version_lease(root: Path, revision: str, *, exclusive: bool = False):
    leases = root / "leases"
    leases.mkdir(exist_ok=True)
    with closing(sqlite3.connect(leases / f"{revision}.sqlite3", timeout=0 if exclusive else 5)) as lease:
        if exclusive:
            lease.execute("BEGIN EXCLUSIVE")
        else:
            lease.execute("BEGIN")
            # Read the database to acquire a shared lock for the lifetime of the CLI.
            lease.execute("SELECT name FROM sqlite_master").fetchall()
        yield


def source_command(project: Path, *args: str) -> list[str]:
    if sys.platform == "win32":
        return [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(project / "cook.ps1"), *args,
        ]
    return ["bash", str(project / "cook"), *args]


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        # A launcher that raced with a version change can reread the active revision.
        for _ in range(2):
            revision, _ = installed_revisions(root)
            if revision is None:
                break
            with version_lease(root, revision):
                project = root / "versions" / revision
                if not (project / ".ready").is_file():
                    continue
                with Popen(source_command(project, *sys.argv[1:])) as child:
                    while True:
                        try:
                            return child.wait()
                        except KeyboardInterrupt:
                            # The CLI in the same terminal handles cancellation itself.
                            continue
        print("Run the Agent Cook global installer to restore the active version.", file=sys.stderr)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
