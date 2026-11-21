import argparse
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from socket import create_connection
from time import monotonic, sleep

from agent_cook.cli import main

EXAMPLES = Path(__file__).resolve().parent


@contextmanager
def reference_server():
    try:
        with create_connection(("127.0.0.1", 8766), timeout=0.2):
            pass
    except OSError:
        pass
    else:
        yield
        return
    script = EXAMPLES.parent / "plugins" / "web-studio" / "mcp" / "server.py"
    with subprocess.Popen([sys.executable, str(script)], stdin=subprocess.DEVNULL) as service:
        try:
            deadline = monotonic() + 10
            while service.poll() is None and monotonic() < deadline:
                try:
                    with create_connection(("127.0.0.1", 8766), timeout=0.2):
                        break
                except OSError:
                    sleep(0.1)
            else:
                raise RuntimeError("The reference MCP did not start on 127.0.0.1:8766.")
            yield
        finally:
            if service.poll() is None:
                service.terminate()
                try:
                    service.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    service.kill()


def demo() -> int:
    parser = argparse.ArgumentParser(description="Run a bundled task on the Docker workspace.")
    parser.add_argument("name", choices=("quickstart", "shop-review", "event-registration", "combined-review"))
    name = parser.parse_args().name
    args = [
        "exec", "--request", str(EXAMPLES / name / "run.json"),
        "--max-turns", "24", "--timeout", "300",
    ]
    if name in ("event-registration", "combined-review"):
        with reference_server():
            return main(args)
    return main(args)


if __name__ == "__main__":
    try:
        raise SystemExit(demo())
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
