from __future__ import annotations

import hashlib
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import Settings
from .errors import ExecutionTimeout
from .orchestration import CommandResult, run_argv


Runner = Callable[..., CommandResult]


@dataclass(frozen=True, slots=True)
class CapabilityDiagnostic:
    name: str
    available: bool
    version: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HostDiagnostics:
    capabilities: tuple[CapabilityDiagnostic, ...]

    def get(self, name: str) -> CapabilityDiagnostic:
        return next(item for item in self.capabilities if item.name == name)


@dataclass(frozen=True, slots=True)
class EnvironmentIdentity:
    repository_id: int
    commit_sha: str
    builder_version: str
    strategy_version: str
    environment_digest: str
    image_tag: str


class DockerEnvironmentCache:
    def __init__(self, *, runner: Runner = run_argv) -> None:
        self.runner = runner

    def contains(self, identity: EnvironmentIdentity) -> bool:
        try:
            result = self.runner(
                (
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    identity.image_tag,
                ),
                timeout_seconds=10,
                max_output_bytes=4096,
            )
        except (ExecutionTimeout, OSError):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())


def environment_identity(
    *,
    repository_id: int,
    commit_sha: str,
    builder_version: str,
    strategy_version: str = "repo2docker-v1",
) -> EnvironmentIdentity:
    normalized_sha = commit_sha.lower()
    if len(normalized_sha) != 40 or any(
        character not in "0123456789abcdef" for character in normalized_sha
    ):
        raise ValueError("commit_sha must be a 40-character hexadecimal SHA")
    if repository_id <= 0:
        raise ValueError("repository_id must be positive")
    if not builder_version.strip() or not strategy_version.strip():
        raise ValueError("builder and strategy versions are required")
    digest_input = "\0".join(
        (
            str(repository_id),
            normalized_sha,
            builder_version.strip(),
            strategy_version.strip(),
        )
    ).encode()
    digest = hashlib.sha256(digest_input).hexdigest()
    return EnvironmentIdentity(
        repository_id=repository_id,
        commit_sha=normalized_sha,
        builder_version=builder_version.strip(),
        strategy_version=strategy_version.strip(),
        environment_digest=digest,
        image_tag=f"notebook-launcher/{repository_id}:{digest[:20]}",
    )


def probe_command(
    name: str,
    argv: Sequence[str],
    *,
    runner: Runner = run_argv,
) -> CapabilityDiagnostic:
    try:
        result = runner(argv, timeout_seconds=5, max_output_bytes=4096)
    except ExecutionTimeout:
        return CapabilityDiagnostic(name, False, error="timeout")
    except OSError as exc:
        return CapabilityDiagnostic(name, False, error=type(exc).__name__)
    if result.returncode != 0:
        return CapabilityDiagnostic(
            name,
            False,
            error=f"command_failed_{result.returncode}",
        )
    version = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()),
        None,
    )
    return CapabilityDiagnostic(name, True, version=version)


def probe_python_distribution(
    name: str,
    distribution: str,
    *,
    runner: Runner = run_argv,
) -> CapabilityDiagnostic:
    script = (
        "from importlib.metadata import version; "
        "import sys; print(version(sys.argv[1]))"
    )
    return probe_command(
        name,
        (sys.executable, "-c", script, distribution),
        runner=runner,
    )


def probe_state_directory(path: Path) -> CapabilityDiagnostic:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".probe-", delete=True) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError as exc:
        return CapabilityDiagnostic(
            "state_directory",
            False,
            error=type(exc).__name__,
        )
    return CapabilityDiagnostic("state_directory", True, version=str(path))


def probe_network_policy(
    *,
    runner: Runner = run_argv,
    system: str | None = None,
) -> CapabilityDiagnostic:
    if (system or platform.system()) != "Linux":
        return CapabilityDiagnostic(
            "network_policy",
            False,
            error="unsupported_platform",
        )
    for command in (("nft", "--version"), ("iptables", "--version")):
        result = probe_command("network_policy", command, runner=runner)
        if result.available:
            return result
    return CapabilityDiagnostic(
        "network_policy",
        False,
        error="nft_and_iptables_unavailable",
    )


def collect_host_diagnostics(
    settings: Settings,
    *,
    runner: Runner = run_argv,
    system: str | None = None,
) -> HostDiagnostics:
    capabilities = (
        probe_command("git", ("git", "--version"), runner=runner),
        probe_command(
            "docker",
            ("docker", "version", "--format", "{{.Server.Version}}"),
            runner=runner,
        ),
        probe_command("repo2docker", ("repo2docker", "--version"), runner=runner),
        probe_python_distribution(
            "jupyter_collaboration",
            "jupyter-collaboration",
            runner=runner,
        ),
        probe_python_distribution(
            "mcp_backend",
            "jupyter-mcp-server",
            runner=runner,
        ),
        probe_command(
            "gpu",
            (
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ),
            runner=runner,
        ),
        probe_network_policy(runner=runner, system=system),
        probe_state_directory(settings.root),
    )
    return HostDiagnostics(capabilities)
