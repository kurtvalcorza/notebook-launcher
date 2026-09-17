import os
import sys
import time

import pytest

from notebook_launcher.errors import ExecutionCancelled, ExecutionTimeout
from notebook_launcher.orchestration import run_argv


def test_run_argv_does_not_interpret_shell_metacharacters(tmp_path):
    marker = tmp_path / "injected"
    argument = f"; echo injected > {marker}"

    result = run_argv(
        (sys.executable, "-c", "import sys; print(sys.argv[1])", argument)
    )

    assert result.returncode == 0
    assert result.stdout.strip() == argument
    assert not marker.exists()


def test_run_argv_redacts_and_bounds_output():
    secret = "private-token-value"
    result = run_argv(
        (
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); print('x' * 200)",
            secret,
        ),
        max_output_bytes=32,
        redact_values=(secret,),
    )

    assert secret not in result.stdout
    assert secret not in " ".join(result.argv)
    assert len(result.stdout.encode("utf-8")) <= 32
    assert result.stdout_bytes > 32
    assert result.truncated


def test_run_argv_redacts_secret_prefix_at_capture_boundary():
    secret = "private-token-value"
    result = run_argv(
        (
            sys.executable,
            "-c",
            "import sys; sys.stdout.write((sys.argv[1] * 2) + 'xxxxxxx' + sys.argv[1])",
            secret,
        ),
        max_output_bytes=32,
        redact_values=(secret,),
    )

    assert "priva" not in result.stdout
    assert len(result.stdout.encode("utf-8")) <= 32


def test_run_argv_redacts_longest_overlapping_secret_first():
    secret = "private-token-value"
    result = run_argv(
        (
            sys.executable,
            "-c",
            "import sys; sys.stdout.write(sys.argv[1])",
            secret,
        ),
        redact_values=("private-token", secret),
    )

    assert result.stdout == "[REDACTED]"
    assert result.argv[-1] == "[REDACTED]"


def test_run_argv_times_out():
    with pytest.raises(ExecutionTimeout):
        run_argv(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            timeout_seconds=0.05,
            terminate_grace_seconds=0.1,
        )


def test_run_argv_timeout_stops_descendant_processes(tmp_path):
    marker = tmp_path / "descendant-survived"
    child_code = (
        "import pathlib, time; "
        "time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('alive')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )

    with pytest.raises(ExecutionTimeout):
        run_argv(
            (sys.executable, "-c", parent_code),
            timeout_seconds=0.05,
            terminate_grace_seconds=0.1,
        )

    time.sleep(1.7)
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows job-object regression")
def test_run_argv_stops_orphaned_grandchild_after_intermediary_exit(tmp_path):
    grandchild_marker = tmp_path / "grandchild-survived"
    intermediary_exited = tmp_path / "intermediary-exited"
    grandchild_code = (
        "import pathlib, time; "
        "time.sleep(1.5); "
        f"pathlib.Path({str(grandchild_marker)!r}).write_text('alive')"
    )
    intermediary_code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])"
    )
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        f"intermediary = subprocess.Popen([sys.executable, '-c', {intermediary_code!r}]); "
        "intermediary.wait(); "
        f"pathlib.Path({str(intermediary_exited)!r}).write_text('exited'); "
        "time.sleep(10)"
    )

    with pytest.raises(ExecutionCancelled):
        run_argv(
            (sys.executable, "-c", parent_code),
            cancel_requested=intermediary_exited.exists,
            timeout_seconds=10,
            terminate_grace_seconds=0.1,
        )

    assert intermediary_exited.exists()
    time.sleep(0.9)
    assert not grandchild_marker.exists()


def test_run_argv_can_be_cancelled():
    probes = 0

    def cancel_requested() -> bool:
        nonlocal probes
        probes += 1
        return probes >= 3

    with pytest.raises(ExecutionCancelled):
        run_argv(
            (sys.executable, "-c", "import time; time.sleep(10)"),
            cancel_requested=cancel_requested,
            terminate_grace_seconds=0.1,
        )
