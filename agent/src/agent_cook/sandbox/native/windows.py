"""Launch a Win32 command in a fresh AppContainer and a non-breakaway Job Object."""

import ctypes as ct
import msvcrt
import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from ctypes import wintypes as w
from pathlib import Path
from tempfile import TemporaryFile
from threading import Event
from time import monotonic, sleep
from typing import BinaryIO
from uuid import uuid4

from agent_cook.sandbox.interface import CommandResult, SandboxError

_kernel = ct.WinDLL("kernel32", use_last_error=True)
_advapi = ct.WinDLL("advapi32", use_last_error=True)
_userenv = ct.WinDLL("userenv", use_last_error=True)
_SIZE = ct.c_size_t
_PTR = ct.c_void_p


def _api(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class _SecurityCapabilities(ct.Structure):
    _fields_ = [("sid", _PTR), ("capabilities", _PTR), ("count", w.DWORD), ("reserved", w.DWORD)]


class _Startup(ct.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
        ("x", w.DWORD), ("y", w.DWORD), ("width", w.DWORD), ("height", w.DWORD),
        ("x_chars", w.DWORD), ("y_chars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
        ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_bytes", _PTR),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE),
    ]


class _StartupEx(ct.Structure):
    _fields_ = [("startup", _Startup), ("attributes", _PTR)]


class _Process(ct.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class _JobLimits(ct.Structure):
    _fields_ = [
        ("process_time", ct.c_longlong), ("job_time", ct.c_longlong), ("flags", w.DWORD),
        ("min_memory", _SIZE), ("max_memory", _SIZE), ("process_limit", w.DWORD),
        ("affinity", _SIZE), ("priority", w.DWORD), ("scheduling", w.DWORD),
    ]


class _JobExtended(ct.Structure):
    _fields_ = [
        ("basic", _JobLimits), ("io", ct.c_ulonglong * 6), ("process_memory", _SIZE),
        ("job_memory", _SIZE), ("peak_process_memory", _SIZE), ("peak_job_memory", _SIZE),
    ]


class _JobAccounting(ct.Structure):
    _fields_ = [
        ("time", ct.c_longlong * 4), ("page_faults", w.DWORD), ("total", w.DWORD),
        ("active", w.DWORD), ("terminated", w.DWORD),
    ]


_create_profile = _api(_userenv, "CreateAppContainerProfile", ct.c_long,
                       w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, _PTR, w.DWORD, ct.POINTER(_PTR))
_delete_profile = _api(_userenv, "DeleteAppContainerProfile", ct.c_long, w.LPCWSTR)
_sid_string = _api(_advapi, "ConvertSidToStringSidW", w.BOOL, _PTR, ct.POINTER(w.LPWSTR))
_free_sid = _api(_advapi, "FreeSid", _PTR, _PTR)
_local_free = _api(_kernel, "LocalFree", _PTR, _PTR)
_init_attrs = _api(_kernel, "InitializeProcThreadAttributeList", w.BOOL,
                   _PTR, w.DWORD, w.DWORD, ct.POINTER(_SIZE))
_update_attrs = _api(_kernel, "UpdateProcThreadAttribute", w.BOOL,
                     _PTR, w.DWORD, _SIZE, _PTR, _SIZE, _PTR, _PTR)
_delete_attrs = _api(_kernel, "DeleteProcThreadAttributeList", None, _PTR)
_create_process = _api(_kernel, "CreateProcessW", w.BOOL,
                       w.LPCWSTR, w.LPWSTR, _PTR, _PTR, w.BOOL, w.DWORD, _PTR, w.LPCWSTR,
                       ct.POINTER(_StartupEx), ct.POINTER(_Process))
_create_job = _api(_kernel, "CreateJobObjectW", w.HANDLE, _PTR, w.LPCWSTR)
_set_job = _api(_kernel, "SetInformationJobObject", w.BOOL, w.HANDLE, ct.c_int, _PTR, w.DWORD)
_query_job = _api(_kernel, "QueryInformationJobObject", w.BOOL, w.HANDLE, ct.c_int, _PTR, w.DWORD, _PTR)
_assign_job = _api(_kernel, "AssignProcessToJobObject", w.BOOL, w.HANDLE, w.HANDLE)
_terminate_job = _api(_kernel, "TerminateJobObject", w.BOOL, w.HANDLE, w.UINT)
_terminate_process = _api(_kernel, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
_resume = _api(_kernel, "ResumeThread", w.DWORD, w.HANDLE)
_wait = _api(_kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
_exit_code = _api(_kernel, "GetExitCodeProcess", w.BOOL, w.HANDLE, ct.POINTER(w.DWORD))
_close = _api(_kernel, "CloseHandle", w.BOOL, w.HANDLE)
_peek = _api(_kernel, "PeekNamedPipe", w.BOOL, w.HANDLE, _PTR, w.DWORD, _PTR, ct.POINTER(w.DWORD), _PTR)


def _check(ok, step):
    if not ok:
        raise SandboxError(f"Windows AppContainer {step} failed (Win32 {ct.get_last_error()}).")
    return ok


def _acl(path: Path, *args: str, required: bool = True) -> None:
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
        response = subprocess.run(
            [system / "System32/icacls.exe", path, *args], capture_output=True, timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as error:
        if required:
            raise SandboxError("Could not prepare Windows workspace access.") from error
    else:
        if response.returncode and required:
            raise SandboxError(f"Could not grant AppContainer access to '{path}'.")


@contextmanager
def _profile(root: Path, reads: tuple[Path, ...]):
    name = f"agentcook.{uuid4().hex}"
    sid = _PTR()
    status = _create_profile(name, "Agent Cook task", "Isolated local workspace process", None, 0, ct.byref(sid))
    if status < 0:
        raise SandboxError(f"Could not create a Windows AppContainer (HRESULT {status & 0xffffffff:08x}).")
    try:
        with ExitStack() as cleanup:
            pointer = w.LPWSTR()
            _check(_sid_string(sid, ct.byref(pointer)), "identity lookup")
            try:
                identity = "*" + pointer.value
            finally:
                _local_free(ct.cast(pointer, _PTR))
            for path, rights in ((root, "M"), *((path, "RX") for path in reads)):
                cleanup.callback(_acl, path, "/remove:g", identity, required=False)
                _acl(path, "/grant", f"{identity}:(OI)(CI){rights}")
            yield sid
    finally:
        _free_sid(sid)
        _delete_profile(name)


@contextmanager
def _startup(sid, standard, inherited):
    # Only standard handles and the two private Chromium control pipes can be inherited.
    handles = (*standard, *inherited)
    handle_list = (w.HANDLE * len(handles))(*handles)
    attributes = [(0x00020002, handle_list)]
    if sid is not None:
        attributes.append((0x00020009, _SecurityCapabilities(sid, None, 0, 0)))
    size = _SIZE()
    _init_attrs(None, len(attributes), 0, ct.byref(size))
    storage = ct.create_string_buffer(size.value)
    _check(_init_attrs(storage, len(attributes), 0, ct.byref(size)), "startup attributes")
    try:
        for identifier, value in attributes:
            _check(_update_attrs(storage, 0, identifier, ct.byref(value), ct.sizeof(value), None, None),
                   "startup policy")
        startup = _StartupEx()
        startup.startup.cb = ct.sizeof(startup)
        startup.startup.flags = 0x100
        startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = standard
        startup.attributes = ct.cast(storage, _PTR)
        yield startup
    finally:
        _delete_attrs(storage)


def _job():
    job = _check(_create_job(None, None), "job creation")
    limits = _JobExtended()
    limits.basic.flags = 0x2000 | 0x0008 | 0x0200
    limits.basic.process_limit = 64
    limits.job_memory = 1024 * 1024 * 1024
    try:
        _check(_set_job(job, 9, ct.byref(limits), ct.sizeof(limits)), "job limits")
    except BaseException:
        _close(job)
        raise
    return job


def _pipe(stack: ExitStack) -> tuple[BinaryIO, BinaryIO]:
    reader, writer = os.pipe()
    return stack.enter_context(os.fdopen(reader, "rb", buffering=0)), stack.enter_context(os.fdopen(writer, "wb"))


def _drain(file: BinaryIO, capture: bytearray, maximum: int) -> tuple[bool, bool]:
    available = w.DWORD()
    if not _peek(msvcrt.get_osfhandle(file.fileno()), None, 0, None, ct.byref(available), None) or not available.value:
        return False, False
    data = file.read(min(available.value, 8192))
    left = maximum - len(capture)
    capture.extend(data[:left])
    return True, len(data) > left


def _stop_job(job) -> None:
    _terminate_job(job, 1)
    accounting = _JobAccounting()
    deadline = monotonic() + 4
    while monotonic() < deadline:
        if _query_job(job, 1, ct.byref(accounting), ct.sizeof(accounting), None) and not accounting.active:
            return
        sleep(0.01)
    raise SandboxError("Windows AppContainer did not confirm process cleanup.")


def run(
    argv: list[str], root: Path, cwd: Path, reads: tuple[Path, ...], env: dict[str, str],
    timeout: float, maximum: int, payload: bytes | None, stop: Event,
    *, app_container: bool = True, inherited: tuple[int, ...] = (),
) -> CommandResult:
    with ExitStack() as stack:
        sid = stack.enter_context(_profile(root, reads)) if app_container else None
        source = stack.enter_context(TemporaryFile("w+b"))
        if payload:
            source.write(payload)
            source.seek(0)
        stdout, write_out = _pipe(stack)
        stderr, write_err = _pipe(stack)
        handles = tuple(msvcrt.get_osfhandle(file.fileno()) for file in (source, write_out, write_err))
        for handle in (*handles, *inherited):
            os.set_handle_inheritable(handle, True)
        job = _job()
        stack.callback(_close, job)
        process = _Process()
        command = ct.create_unicode_buffer(subprocess.list2cmdline(argv))
        variables = ct.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in sorted(env.items(), key=lambda x: x[0].upper())) + "\0")
        with _startup(sid, handles, inherited) as startup:
            flags = 0x00000004 | 0x00000400 | 0x00080000 | 0x08000000
            _check(_create_process(argv[0], command, None, None, True, flags, variables, str(cwd),
                                   ct.byref(startup), ct.byref(process)), "process creation")
        stack.callback(_close, process.process)
        stack.callback(_close, process.thread)
        if not _assign_job(job, process.process):
            _terminate_process(process.process, 1)
            _wait(process.process, 4000)
            _check(False, "process supervision")
        write_out.close()
        write_err.close()
        buffers = (bytearray(), bytearray())
        captures = ((stdout, buffers[0]), (stderr, buffers[1]))
        truncated = timed_out = False
        code = w.DWORD(1)
        try:
            _check(_resume(process.thread) != 0xffffffff, "process start")
            deadline = monotonic() + timeout
            while _wait(process.process, 10) == 0x102:
                for stream, buffer in captures:
                    _, clipped = _drain(stream, buffer, maximum)
                    truncated |= clipped
                if stop.is_set() or monotonic() >= deadline:
                    timed_out = True
                    break
            _exit_code(process.process, ct.byref(code))
        finally:
            _stop_job(job)
        for stream, buffer in captures:
            while True:
                available, clipped = _drain(stream, buffer, maximum)
                truncated |= clipped
                if not available:
                    break
        return CommandResult(
            code.value, buffers[0].decode("utf-8", errors="replace"), buffers[1].decode("utf-8", errors="replace"),
            timed_out, truncated,
        )


def chromium_process(arguments: list[str]) -> int:
    from agent_cook.sandbox.native.executor import environment

    try:
        directory, binary = arguments
        scratch = Path(directory).resolve(strict=True)
        executable = Path(binary).resolve(strict=True)
        if executable.name.lower() != "chrome-headless-shell.exe":
            raise ValueError("Expected the managed Chromium headless executable.")
        control = (msvcrt.get_osfhandle(3), msvcrt.get_osfhandle(4))
        profile = scratch / "chromium-profile"
        command = [
            str(executable), "--headless", "--no-sandbox", "--single-process", "--disable-gpu",
            "--disable-background-networking", "--disable-component-update", "--no-first-run",
            "--no-startup-window", "--remote-debugging-pipe",
            f"--remote-debugging-io-pipes={control[0]},{control[1]}", f"--user-data-dir={profile}",
        ]
        result = run(
            command, scratch, scratch, (executable.parent,), environment(scratch, (executable.parent,)),
            65, 8192, None, Event(), inherited=control,
        )
    except (OSError, ValueError, SandboxError) as error:
        print(error, file=sys.stderr)
        return 1
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return 1 if result.timed_out else result.exit_code
