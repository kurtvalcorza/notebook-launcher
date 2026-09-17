from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .errors import ExecutionCancelled, ExecutionTimeout

_CREATE_SUSPENDED = 0x00000004


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    truncated: bool
    duration_ms: int


class _BoundedCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self._chunks: list[bytes] = []
        self._kept = 0

    def read(self, stream: object) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            self.total += len(chunk)
            remaining = self.limit - self._kept
            if remaining > 0:
                kept = chunk[:remaining]
                self._chunks.append(kept)
                self._kept += len(kept)

    def value(self) -> bytes:
        return b"".join(self._chunks)


def _redact(value: str, redactions: tuple[str, ...]) -> str:
    for secret in redactions:
        value = value.replace(secret, "[REDACTED]")
    return value


def _redact_capture_boundary(value: str, redactions: tuple[str, ...]) -> str:
    for secret in redactions:
        for length in range(min(len(secret) - 1, len(value)), 0, -1):
            if value.endswith(secret[:length]):
                value = f"{value[:-length]}[REDACTED]"
                break
    return _redact(value, redactions)


def _bounded_text(
    capture: _BoundedCapture,
    *,
    limit: int,
    redactions: tuple[str, ...],
) -> tuple[str, bool]:
    captured = capture.value()
    text = captured.decode("utf-8", errors="replace")
    if capture.total > len(captured):
        text = _redact_capture_boundary(text, redactions)
    else:
        text = _redact(text, redactions)
    encoded = text.encode("utf-8")
    truncated = capture.total > limit or len(encoded) > limit
    if len(encoded) > limit:
        text = encoded[:limit].decode("utf-8", errors="ignore")
    return text, truncated


class _WindowsJob:
    """Own a Windows job configured to kill every member when closed."""

    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle

    @classmethod
    def create(cls) -> _WindowsJob:
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = (
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            )

        class _IoCounters(ctypes.Structure):
            _fields_ = tuple(
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            )

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = (
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        create_job.restype = wintypes.HANDLE
        set_information = kernel32.SetInformationJobObject
        set_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        set_information.restype = wintypes.BOOL

        handle = create_job(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not set_information(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        return cls(int(handle))

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes
        from ctypes import wintypes

        if self._handle is None:
            raise RuntimeError("Windows job is already closed")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        assign_process = kernel32.AssignProcessToJobObject
        assign_process.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        assign_process.restype = wintypes.BOOL
        if not assign_process(self._handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> None:
        import ctypes
        from ctypes import wintypes

        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        terminate_job = kernel32.TerminateJobObject
        terminate_job.argtypes = (wintypes.HANDLE, wintypes.UINT)
        terminate_job.restype = wintypes.BOOL
        if not terminate_job(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        import ctypes
        from ctypes import wintypes

        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle, self._handle = self._handle, None
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _resume_windows_process(process_id: int) -> None:
    """Resume every thread in a process created with CREATE_SUSPENDED."""
    import ctypes
    from ctypes import wintypes

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    thread_first = kernel32.Thread32First
    thread_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32))
    thread_first.restype = wintypes.BOOL
    thread_next = kernel32.Thread32Next
    thread_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32))
    thread_next.restype = wintypes.BOOL
    open_thread = kernel32.OpenThread
    open_thread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_thread.restype = wintypes.HANDLE
    resume_thread = kernel32.ResumeThread
    resume_thread.argtypes = (wintypes.HANDLE,)
    resume_thread.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000004, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = 0
    entry = _ThreadEntry32(dwSize=ctypes.sizeof(_ThreadEntry32))
    try:
        found = bool(thread_first(snapshot, ctypes.byref(entry)))
        while found:
            if entry.th32OwnerProcessID == process_id:
                thread = open_thread(0x0002, False, entry.th32ThreadID)
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if resume_thread(thread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                    resumed += 1
                finally:
                    close_handle(thread)
            found = bool(thread_next(snapshot, ctypes.byref(entry)))
    finally:
        close_handle(snapshot)
    if resumed == 0:
        raise ProcessLookupError(f"no thread found for suspended process {process_id}")


def _stop_process(
    process: subprocess.Popen[bytes],
    grace_seconds: float,
    windows_job: _WindowsJob | None = None,
) -> None:
    if os.name == "nt":
        if windows_job is not None:
            try:
                windows_job.terminate()
                process.wait()
                return
            except OSError:
                pass
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = os.path.join(system_root, "System32", "taskkill.exe")
        completed = subprocess.run(
            (taskkill, "/PID", str(process.pid), "/T", "/F"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0 and process.poll() is None:
            process.kill()
        process.wait()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_argv(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    terminate_grace_seconds: float = 2.0,
    max_output_bytes: int = 64 * 1024,
    redact_values: Sequence[str] = (),
) -> CommandResult:
    """Run an argv-only subprocess with cancellation and bounded redacted output."""
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("argv must be a non-empty sequence of strings")
    normalized = tuple(argv)
    if any(not isinstance(arg, str) or "\x00" in arg for arg in normalized):
        raise ValueError("argv entries must be strings without NUL bytes")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if terminate_grace_seconds < 0:
        raise ValueError("terminate_grace_seconds cannot be negative")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")

    redactions = tuple(
        sorted(
            {value for value in redact_values if value},
            key=len,
            reverse=True,
        )
    )
    capture_limit = max_output_bytes + max(
        (len(value.encode("utf-8")) for value in redactions),
        default=0,
    )
    started = time.monotonic()
    windows_job: _WindowsJob | None = None
    creationflags = 0
    if os.name == "nt":
        windows_job = _WindowsJob.create()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED
    try:
        process = subprocess.Popen(
            normalized,
            cwd=os.fspath(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except Exception:
        if windows_job is not None:
            windows_job.close()
        raise
    if windows_job is not None:
        try:
            windows_job.assign(process)
            _resume_windows_process(process.pid)
        except Exception:
            process.kill()
            process.wait()
            windows_job.close()
            raise
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_capture = _BoundedCapture(capture_limit)
    stderr_capture = _BoundedCapture(capture_limit)
    readers = (
        threading.Thread(
            target=stdout_capture.read,
            args=(process.stdout,),
            daemon=True,
        ),
        threading.Thread(
            target=stderr_capture.read,
            args=(process.stderr,),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()

    deadline = started + timeout_seconds if timeout_seconds is not None else None
    failure: Exception | None = None
    try:
        while process.poll() is None:
            if cancel_requested is not None and cancel_requested():
                failure = ExecutionCancelled()
                _stop_process(process, terminate_grace_seconds, windows_job)
                break
            if deadline is not None and time.monotonic() >= deadline:
                failure = ExecutionTimeout()
                _stop_process(process, terminate_grace_seconds, windows_job)
                break
            time.sleep(0.02)
    except Exception:
        _stop_process(process, terminate_grace_seconds, windows_job)
        raise
    finally:
        if windows_job is not None:
            windows_job.close()
        for reader in readers:
            reader.join()

    if failure is not None:
        raise failure

    stdout, stdout_truncated = _bounded_text(
        stdout_capture,
        limit=max_output_bytes,
        redactions=redactions,
    )
    stderr, stderr_truncated = _bounded_text(
        stderr_capture,
        limit=max_output_bytes,
        redactions=redactions,
    )
    safe_argv = tuple(_redact(arg, redactions) for arg in normalized)
    return CommandResult(
        argv=safe_argv,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=stdout_capture.total,
        stderr_bytes=stderr_capture.total,
        truncated=stdout_truncated or stderr_truncated,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: UUID
    workspace_id: UUID
    runtime_id: str
    state: str
    notebook_url: str
    active_notebook_path: str


class LifecycleStore(Protocol):
    def require_authorized_launch(self, launch_id: UUID, workspace_id: UUID) -> None: ...
    def active_session(self, workspace_id: UUID) -> SessionSnapshot | None: ...
    def get_session(self, session_id: UUID) -> SessionSnapshot | None: ...
    def active_notebook_exists(self, workspace_id: UUID) -> bool: ...
    def mark_session_state(self, session_id: UUID, state: str) -> None: ...


class RuntimeLifecycle(Protocol):
    def is_alive(self, runtime_id: str) -> bool: ...
    def stop(self, runtime_id: str) -> None: ...


class SessionInvalidator(Protocol):
    def invalidate(self, session_id: UUID) -> None: ...


class AuthorizedSessionStarter(Protocol):
    def start(self, workspace_id: UUID, launch_id: UUID) -> SessionSnapshot: ...


class LifecycleOrchestrator:
    """Coordinate reuse, stale reconciliation, stop, and authorized reopen."""

    def __init__(
        self,
        *,
        store: LifecycleStore,
        runtime: RuntimeLifecycle,
        invalidators: Sequence[SessionInvalidator],
        starter: AuthorizedSessionStarter,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.invalidators = tuple(invalidators)
        self.starter = starter

    def status(self, session_id: UUID) -> SessionSnapshot | None:
        snapshot = self.store.get_session(session_id)
        if snapshot is None:
            return None
        if snapshot.state in {"starting", "ready", "stopping"}:
            self.reconcile(session_id)
            return self.store.get_session(session_id)
        return snapshot

    def reconcile(self, session_id: UUID) -> bool:
        snapshot = self.store.get_session(session_id)
        if snapshot is None or snapshot.state not in {"starting", "ready", "stopping"}:
            return False
        if self.runtime.is_alive(snapshot.runtime_id):
            return True
        for invalidator in self.invalidators:
            invalidator.invalidate(session_id)
        self.store.mark_session_state(session_id, "failed")
        return False

    def stop(self, session_id: UUID) -> bool:
        snapshot = self.store.get_session(session_id)
        if snapshot is None:
            return False
        if snapshot.state in {"stopped", "failed"}:
            for invalidator in self.invalidators:
                invalidator.invalidate(session_id)
            return True
        self.store.mark_session_state(session_id, "stopping")
        if self.runtime.is_alive(snapshot.runtime_id):
            self.runtime.stop(snapshot.runtime_id)
        for invalidator in self.invalidators:
            invalidator.invalidate(session_id)
        self.store.mark_session_state(session_id, "stopped")
        return True

    def reopen(
        self,
        *,
        workspace_id: UUID,
        authorized_launch_id: UUID,
    ) -> tuple[SessionSnapshot, bool]:
        self.store.require_authorized_launch(authorized_launch_id, workspace_id)
        existing = self.store.active_session(workspace_id)
        if existing is not None and self.runtime.is_alive(existing.runtime_id):
            return existing, True
        if existing is not None:
            for invalidator in self.invalidators:
                invalidator.invalidate(existing.session_id)
            self.store.mark_session_state(existing.session_id, "failed")
        if not self.store.active_notebook_exists(workspace_id):
            raise FileNotFoundError("workspace active notebook is missing")
        return self.starter.start(workspace_id, authorized_launch_id), False
