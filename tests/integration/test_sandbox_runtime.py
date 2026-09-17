from pathlib import Path

import pytest

from notebook_launcher.orchestration import run_argv
from notebook_launcher.sandbox import SandboxPolicy, docker_run_argv

pytestmark = [pytest.mark.integration, pytest.mark.docker]
IMAGE = "rancher/mirrored-library-busybox:1.37.0"


def _image_available() -> bool:
    result = run_argv(
        ("docker", "image", "inspect", IMAGE),
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    return result.returncode == 0


def test_standard_sandbox_runs_non_root_without_sensitive_mounts(tmp_path: Path):
    if not _image_available():
        pytest.skip(f"local Docker image unavailable: {IMAGE}")
    policy = SandboxPolicy(
        image=IMAGE,
        container_name="nl-sandbox-integration",
        workspace_host=tmp_path,
        network_name="none",
        memory="256m",
        cpus=1,
        pids_limit=64,
    )
    argv = docker_run_argv(
        policy,
        (
            "sh",
            "-c",
            (
                "test \"$(id -u)\" = 1000 && "
                "test ! -S /var/run/docker.sock && test ! -e /home/host"
            ),
        ),
    )

    result = run_argv(argv, timeout_seconds=30, max_output_bytes=4096)

    assert result.returncode == 0, result.stderr
