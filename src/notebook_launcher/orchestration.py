from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .errors import ExecutionCancelled, ExecutionTimeout


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


def _stop_process(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if os.name == "nt":
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
    process = subprocess.Popen(
        normalized,
        cwd=os.fspath(cwd) if cwd is not None else None,
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=os.name != "nt",
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        ),
    )
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
                _stop_process(process, terminate_grace_seconds)
                break
            if deadline is not None and time.monotonic() >= deadline:
                failure = ExecutionTimeout()
                _stop_process(process, terminate_grace_seconds)
                break
            time.sleep(0.02)
    except Exception:
        _stop_process(process, terminate_grace_seconds)
        raise
    finally:
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
