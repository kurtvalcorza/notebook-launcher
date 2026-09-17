from pathlib import Path

import pytest

from notebook_launcher.sandbox import (
    SandboxPolicy,
    docker_run_argv,
    scrub_host_environment,
    validate_mount_policy,
    validate_sandbox_policy,
)


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
    assert "--read-only" in argv
    assert "--init" in argv
    assert "noexec,nosuid,nodev" in joined
    assert "/workspace" in joined
    assert "--privileged" not in argv
    assert "seccomp=unconfined" not in joined


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


def test_loopback_only_publication_and_managed_network(tmp_path: Path):
    policy = SandboxPolicy(
        image="example",
        container_name="ports",
        workspace_host=tmp_path,
        network_name="nl-test",
        published_ports=((49152, 8888),),
    )

    validate_sandbox_policy(policy, require_network=True)
    argv = docker_run_argv(policy, ["jupyter", "lab"])

    assert "127.0.0.1:49152:8888" in argv
    assert argv[argv.index("--network") + 1] == "nl-test"
    assert "--add-host" not in argv
    assert not any("host-gateway" in value for value in argv)


def test_docker_socket_and_credential_mounts_are_rejected(tmp_path: Path):
    base = SandboxPolicy(
        image="example",
        container_name="mounts",
        workspace_host=tmp_path,
        readonly_mounts=((Path("/var/run/docker.sock"), "/mnt/socket"),),
    )
    with pytest.raises(ValueError, match="Docker socket"):
        validate_mount_policy(base)

    credential = tmp_path / ".ssh"
    credential.mkdir()
    secrets = SandboxPolicy(
        image="example",
        container_name="secrets",
        workspace_host=tmp_path,
        readonly_mounts=((credential, "/mnt/ssh"),),
    )
    with pytest.raises(ValueError, match="credential"):
        validate_mount_policy(secrets)


def test_secret_environment_is_rejected_and_host_environment_is_allowlisted(tmp_path: Path):
    policy = SandboxPolicy(
        image="example",
        container_name="env",
        workspace_host=tmp_path,
        environment=(("GITHUB_TOKEN", "secret"),),
    )
    with pytest.raises(ValueError, match="credential"):
        docker_run_argv(policy, [])

    host = {"LANG": "C.UTF-8", "GITHUB_TOKEN": "secret", "OTHER": "value"}
    assert scrub_host_environment(host, allowed_names=("LANG",)) == {"LANG": "C.UTF-8"}
    with pytest.raises(ValueError, match="secret"):
        scrub_host_environment(host, allowed_names=("GITHUB_TOKEN",))


def test_generic_api_key_and_container_control_socket_are_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="secret"):
        scrub_host_environment(
            {"OPENAI_API_KEY": "secret"}, allowed_names=("OPENAI_API_KEY",)
        )

    socket = tmp_path / "podman.sock"
    socket.touch()
    policy = SandboxPolicy(
        image="example",
        container_name="runtime",
        workspace_host=tmp_path / "workspace",
        readonly_mounts=((socket, "/run/podman.sock"),),
    )
    with pytest.raises(ValueError, match="container-control"):
        validate_sandbox_policy(policy)


def test_gpu_does_not_weaken_standard_sandbox(tmp_path: Path):
    argv = docker_run_argv(
        SandboxPolicy(
            image="example",
            container_name="gpu",
            workspace_host=tmp_path,
            gpu_enabled=True,
            network_name="nl-gpu",
        ),
        [],
    )
    assert "--gpus" in argv
    assert "--cap-drop" in argv
    assert "no-new-privileges:true" in argv
    assert "--privileged" not in argv
