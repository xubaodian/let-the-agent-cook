import asyncio
import json
import os
import shutil
import signal
import sys
from contextlib import suppress
from pathlib import Path
from threading import Event

from agent_cook.sandbox.interface import CommandResult, SandboxError
from agent_cook.sandbox.limits import MAX_COMMAND_OUTPUT

_MAC_SERVICES = (
    "com.apple.system.logger", "com.apple.cfprefsd.agent", "com.apple.cfprefsd.daemon",
    "com.apple.system.opendirectoryd.libinfo", "com.apple.system.notification_center",
    "com.apple.SecurityServer", "com.apple.trustd", "com.apple.trustd.agent",
    "com.apple.PowerManagement.control", "com.apple.logd",
)


def environment(scratch: Path, paths: tuple[Path, ...]) -> dict[str, str]:
    result = {
        "HOME": str(scratch), "USERPROFILE": str(scratch), "TMPDIR": str(scratch),
        "TMP": str(scratch), "TEMP": str(scratch), "APPDATA": str(scratch), "LOCALAPPDATA": str(scratch),
        "LANG": "C.UTF-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
    }
    if sys.platform == "win32":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        result.update(SystemRoot=str(system), WINDIR=str(system), COMSPEC=str(system / "System32/cmd.exe"))
        paths += (system / "System32",)
    else:
        paths += (Path("/usr/bin"), Path("/bin"))
    result["PATH"] = os.pathsep.join(map(str, dict.fromkeys(paths)))
    return result


def _seatbelt(argv: list[str], root: Path, reads: tuple[Path, ...], *, office: bool) -> list[str]:
    executable = Path("/usr/bin/sandbox-exec")
    if not executable.is_file():
        raise SandboxError("This macOS installation does not provide the Seatbelt process launcher.")
    system = ("/usr", "/bin", "/System", "/Library/Apple/System/Library", "/private/var/db/dyld",
              "/private/var/db/timezone")
    readable = " ".join(f"(subpath {json.dumps(str(p), ensure_ascii=False)})" for p in (*system, *reads, root))
    devices = ' '.join(f'(literal "/dev/{name}")' for name in ("null", "zero", "random", "urandom"))
    mime = ' '.join(f'(literal "/private/etc/{name}mime.types")' for name in (
        "", "apache/", "apache2/", "httpd/", "httpd/conf/",
    ))
    mac_services = _MAC_SERVICES
    if office:
        # The stock macOS build initializes AppKit even for headless conversions.
        mac_services += ("com.apple.coreservices.launchservicesd",)
    services = " ".join(f'(global-name "{service}")' for service in mac_services)
    writable = f"(subpath {json.dumps(str(root), ensure_ascii=False)})"
    profile = f"""(version 1)
(deny default (with message "agent-cook isolation"))
(allow process-fork)
(allow process-exec)
(allow signal (target same-sandbox))
(allow process-info* (target same-sandbox))
(allow sysctl-read)
(allow sysctl-write (sysctl-name "kern.grade_cputype"))
(allow file-read-metadata)
(allow file-read-data (literal "/"))
(allow file-read* {readable} {devices} {mime})
(allow file-map-executable {readable})
(allow file-write* {writable} (literal "/dev/null") (literal "/dev/zero"))
(allow mach-lookup {services})
(deny lsopen job-creation appleevent-send)
(allow iokit-open (iokit-registry-entry-class "RootDomainUserClient"))
(allow ipc-posix-shm-read-data (ipc-posix-name "apple.shm.notification_center"))
"""
    return [str(executable), "-p", profile, "--", *argv]


def _bubblewrap(argv: list[str], root: Path, cwd: Path, reads: tuple[Path, ...]) -> list[str]:
    if not (executable := shutil.which("bwrap")):
        raise SandboxError("Install Bubblewrap from your Linux distribution to run local commands.")
    result = [
        executable, "--unshare-all", "--unshare-user", "--disable-userns", "--new-session", "--die-with-parent",
        "--cap-drop", "ALL",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
    ]
    for path in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"), *reads):
        if path.is_dir():
            result.extend(("--ro-bind", str(path), str(path)))
    if Path("/etc/ld.so.cache").is_file():
        result.extend(("--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"))
    result.extend(("--bind", str(root), str(root), "--chdir", str(cwd), "--", *argv))
    return result


async def _collect(stream: asyncio.StreamReader, maximum: int, capture: bytearray) -> bool:
    truncated = False
    while data := await stream.read(8192):
        available = maximum - len(capture)
        capture.extend(data[:available])
        truncated |= len(data) > available
    return truncated


async def _posix(
    argv: list[str], cwd: Path, env: dict[str, str], timeout: float, maximum: int, payload: bytes | None,
) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, start_new_session=True,
        stdin=asyncio.subprocess.PIPE if payload is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = bytearray(), bytearray()
    readers = (
        asyncio.create_task(_collect(process.stdout, maximum, stdout)),
        asyncio.create_task(_collect(process.stderr, maximum, stderr)),
    )
    timed_out = False
    try:
        async with asyncio.timeout(timeout):
            if payload is not None:
                process.stdin.write(payload)
                with suppress(BrokenPipeError, ConnectionResetError):
                    await process.stdin.drain()
                process.stdin.close()
            # Wait for exit rather than for EOF from pipes that descendants can keep open.
            while process.returncode is None:
                await asyncio.sleep(0.02)
    except TimeoutError:
        timed_out = True
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        try:
            async with asyncio.timeout(3):
                await process.wait()
                truncated = any(await asyncio.gather(*readers))
        except TimeoutError:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            truncated = True
    return CommandResult(
        process.returncode if process.returncode is not None else -1,
        stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace"), timed_out, truncated,
    )


async def execute(
    argv: list[str], *, root: Path, cwd: Path, reads: tuple[Path, ...],
    env: dict[str, str], timeout: float, payload: bytes | None = None, maximum: int = MAX_COMMAND_OUTPUT,
    browser_controller: bool = False, office: bool = False,
) -> CommandResult:
    if sys.platform == "win32":
        from agent_cook.sandbox.native.windows import run

        stop = Event()
        pending = asyncio.create_task(asyncio.to_thread(
            run, argv, root, cwd, reads, env, timeout, maximum, payload, stop,
            app_container=not browser_controller,
        ))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            stop.set()
            with suppress(SandboxError, OSError):
                await pending
            raise
    if sys.platform == "darwin":
        command = _seatbelt(argv, root, reads, office=office)
    elif sys.platform.startswith("linux"):
        command = _bubblewrap(argv, root, cwd, reads)
    else:
        raise SandboxError("Local command isolation is unavailable on this operating system.")
    try:
        return await _posix(command, cwd, env, timeout, maximum, payload)
    except OSError as error:
        raise SandboxError(f"Could not start native process isolation ({type(error).__name__}).") from error
