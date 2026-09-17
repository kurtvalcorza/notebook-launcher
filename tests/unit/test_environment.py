from notebook_launcher.config import Settings
from notebook_launcher.environment import (
    DockerEnvironmentCache,
    collect_host_diagnostics,
    environment_identity,
    probe_command,
    probe_network_policy,
)
from notebook_launcher.orchestration import CommandResult


def command_result(argv, *, returncode=0, stdout="", stderr=""):
    return CommandResult(
        argv=tuple(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=len(stdout.encode()),
        stderr_bytes=len(stderr.encode()),
        truncated=False,
        duration_ms=1,
    )


def test_probe_command_reports_secret_safe_failure():
    def runner(argv, **_kwargs):
        return command_result(argv, returncode=2, stderr="token=secret")

    diagnostic = probe_command("tool", ("tool", "--version"), runner=runner)

    assert not diagnostic.available
    assert diagnostic.error == "command_failed_2"
    assert "secret" not in repr(diagnostic)


def test_network_policy_is_explicitly_unavailable_off_linux():
    diagnostic = probe_network_policy(system="Windows")

    assert not diagnostic.available
    assert diagnostic.error == "unsupported_platform"


def test_collect_host_diagnostics_covers_required_capabilities(tmp_path):
    def runner(argv, **_kwargs):
        command = argv[0]
        if command == "repo2docker" or "jupyter-mcp-server" in argv:
            return command_result(argv, returncode=1, stderr="missing")
        return command_result(argv, stdout=f"{command} 1.0\n")

    diagnostics = collect_host_diagnostics(
        Settings(root=tmp_path / "launcher"),
        runner=runner,
        system="Windows",
    )

    assert {item.name for item in diagnostics.capabilities} == {
        "git",
        "docker",
        "repo2docker",
        "jupyter_collaboration",
        "mcp_backend",
        "gpu",
        "network_policy",
        "state_directory",
    }
    assert diagnostics.get("docker").available
    assert not diagnostics.get("repo2docker").available
    assert not diagnostics.get("mcp_backend").available
    assert diagnostics.get("state_directory").available


def test_environment_identity_is_deterministic_and_builder_sensitive():
    first = environment_identity(
        repository_id=42,
        commit_sha="A" * 40,
        builder_version="repo2docker 2025.1",
    )
    same = environment_identity(
        repository_id=42,
        commit_sha="a" * 40,
        builder_version="repo2docker 2025.1",
    )
    changed = environment_identity(
        repository_id=42,
        commit_sha="a" * 40,
        builder_version="repo2docker 2026.1",
    )

    assert first == same
    assert first.environment_digest != changed.environment_digest
    assert first.image_tag.startswith("notebook-launcher/42:")


def test_docker_environment_cache_uses_deterministic_image_tag():
    identity = environment_identity(
        repository_id=42,
        commit_sha="a" * 40,
        builder_version="repo2docker 2025.1",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        return command_result(argv, stdout="sha256:image\n")

    assert DockerEnvironmentCache(runner=runner).contains(identity)
    assert calls == [
        (
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            identity.image_tag,
        )
    ]
