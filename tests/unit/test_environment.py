import sys

import pytest

from notebook_launcher.config import Settings
from notebook_launcher.environment import (
    RUNTIME_APPENDIX,
    RUNTIME_STRATEGY_VERSION,
    DockerEnvironmentCache,
    EnvironmentBuildError,
    Repo2DockerBuilder,
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
        if "repo2docker" in argv or "jupyter-mcp-server" in argv:
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
    assert first.strategy_version == RUNTIME_STRATEGY_VERSION


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


def test_repo2docker_cache_hit_does_not_build(tmp_path):
    identity = environment_identity(
        repository_id=42,
        commit_sha="a" * 40,
        builder_version="repo2docker 2025.1",
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        return command_result(argv, stdout="sha256:cached\n")

    result = Repo2DockerBuilder(runner=runner).ensure(identity, tmp_path)

    assert result.cache_hit
    assert result.image_id == "sha256:cached"
    assert len(calls) == 1
    assert calls[0][:3] == ("docker", "image", "inspect")


def test_repo2docker_cache_miss_builds_with_structured_argv(tmp_path):
    identity = environment_identity(
        repository_id=42,
        commit_sha="a" * 40,
        builder_version="repo2docker 2025.1",
    )
    inspected = 0
    calls = []

    def runner(argv, **_kwargs):
        nonlocal inspected
        calls.append(tuple(argv))
        if argv[:3] == ("docker", "image", "inspect"):
            inspected += 1
            if inspected == 1:
                return command_result(argv, returncode=1)
            return command_result(argv, stdout="sha256:built\n")
        return command_result(argv)

    result = Repo2DockerBuilder(runner=runner).ensure(identity, tmp_path)

    assert not result.cache_hit
    assert result.image_id == "sha256:built"
    assert calls[1] == (
        sys.executable,
        "-m",
        "repo2docker",
        "--no-run",
        "--image-name",
        identity.image_tag,
        "--user-id",
        "1000",
        "--user-name",
        "jovyan",
        "--appendix",
        RUNTIME_APPENDIX,
        str(tmp_path.resolve()),
    )


def test_repo2docker_failure_is_bounded_and_uses_runner_redaction(tmp_path):
    identity = environment_identity(
        repository_id=42,
        commit_sha="a" * 40,
        builder_version="repo2docker 2025.1",
    )
    inspected = False

    def runner(argv, **kwargs):
        nonlocal inspected
        if argv[:3] == ("docker", "image", "inspect"):
            inspected = True
            return command_result(argv, returncode=1)
        assert kwargs["max_output_bytes"] == 64 * 1024
        assert kwargs["redact_values"] == ("top-secret",)
        return command_result(argv, returncode=9, stderr="token=[REDACTED]")

    with pytest.raises(EnvironmentBuildError) as caught:
        Repo2DockerBuilder(runner=runner).ensure(
            identity,
            tmp_path,
            redact_values=("top-secret",),
        )

    assert inspected
    assert caught.value.reason == "repo2docker_failed_9"
    assert "top-secret" not in str(caught.value)
