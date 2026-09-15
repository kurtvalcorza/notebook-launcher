from pathlib import Path

import pytest

from notebook_launcher.sandbox import SandboxPolicy, docker_run_argv, validate_mount_policy


def test_standard_sandbox_flags_are_present(tmp_path: Path):
    policy = SandboxPolicy(
        image="example:latest",
        container_name="nl-test",
        workspace_host=tmp_path,
        memory="4g",
        cpus=2,
        pids_limit=256,
    )
    argv = docker_run_argv(policy, ["python", "-V"])
    joined = " ".join(argv)
    assert "--user 1000:1000" in joined
    assert "--security-opt no-new-privileges:true" in joined
    assert "--cap-drop ALL" in joined
    assert "--memory 4g" in joined
    assert "--cpus 2" in joined
    assert "--pids-limit 256" in joined
    assert "/workspace" in joined
    assert "--privileged" not in argv


def test_gpu_flag_is_opt_in(tmp_path: Path):
    off = docker_run_argv(
        SandboxPolicy(
            image="example",
            container_name="off",
            workspace_host=tmp_path,
            gpu_enabled=False,
        ),
        [],
    )
    on = docker_run_argv(
        SandboxPolicy(
            image="example",
            container_name="on",
            workspace_host=tmp_path,
            gpu_enabled=True,
        ),
        [],
    )
    assert "--gpus" not in off
    assert "--gpus" in on


def test_whole_home_mount_is_rejected(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    policy = SandboxPolicy(
        image="example",
        container_name="bad",
        workspace_host=home,
    )
    with pytest.raises(ValueError, match="whole home"):
        validate_mount_policy(policy, home=home)
