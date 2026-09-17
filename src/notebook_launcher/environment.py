from __future__ import annotations

import hashlib
import platform
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .errors import ExecutionTimeout
from .orchestration import CommandResult, run_argv

Runner = Callable[..., CommandResult]
RUNTIME_APPENDIX = (
    "RUN python -m pip install --no-cache-dir "
    "jupyterlab==4.6.3 jupyter-collaboration==5.0.3 notebook==7.6.0"
)
RUNTIME_STRATEGY_VERSION = (
    "repo2docker-v2-jupyterlab-4.6.3-collaboration-5.0.3-notebook-7.6.0"
)


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


@dataclass(frozen=True, slots=True)
class EnvironmentBuildResult:
    identity: EnvironmentIdentity
    cache_hit: bool
    image_id: str
    duration_ms: int


class EnvironmentBuildError(RuntimeError):
    """A bounded, secret-safe repo2docker build failure."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        message = reason if detail is None else f"{reason}: {detail}"
        super().__init__(message)


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

    def image_id(self, identity: EnvironmentIdentity) -> str | None:
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
            return None
        if result.returncode != 0:
            return None
        image_id = result.stdout.strip()
        return image_id or None


class Repo2DockerBuilder:
    """Build deterministic repo2docker images without invoking a shell."""

    def __init__(
        self,
        *,
        runner: Runner = run_argv,
        executable: str | Sequence[str] | None = None,
        timeout_seconds: float = 3600,
        max_output_bytes: int = 64 * 1024,
        runtime_appendix: str = RUNTIME_APPENDIX,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.runner = runner
        if executable is None:
            self.executable = (sys.executable, "-m", "repo2docker")
        elif isinstance(executable, str):
            self.executable = (executable,)
        else:
            self.executable = tuple(executable)
        if not self.executable or any(
            not item or "\x00" in item for item in self.executable
        ):
            raise ValueError("repo2docker executable argv is invalid")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.runtime_appendix = runtime_appendix
        self.cache = DockerEnvironmentCache(runner=runner)

    def ensure(
        self,
        identity: EnvironmentIdentity,
        source_root: Path,
        *,
        redact_values: Sequence[str] = (),
    ) -> EnvironmentBuildResult:
        source_root = source_root.resolve(strict=True)
        if not source_root.is_dir():
            raise ValueError("repo2docker source_root must be a directory")

        cached_id = self.cache.image_id(identity)
        if cached_id is not None:
            return EnvironmentBuildResult(identity, True, cached_id, 0)

        argv = (
            *self.executable,
            "--no-run",
            "--image-name",
            identity.image_tag,
            "--user-id",
            "1000",
            "--user-name",
            "jovyan",
            "--appendix",
            self.runtime_appendix,
            str(source_root),
        )
        try:
            result = self.runner(
                argv,
                timeout_seconds=self.timeout_seconds,
                max_output_bytes=self.max_output_bytes,
                redact_values=tuple(redact_values),
            )
        except ExecutionTimeout as exc:
            raise EnvironmentBuildError("repo2docker_timeout") from exc
        except OSError as exc:
            raise EnvironmentBuildError(
                "repo2docker_unavailable",
                detail=type(exc).__name__,
            ) from exc
        if result.returncode != 0:
            raise EnvironmentBuildError(f"repo2docker_failed_{result.returncode}")

        image_id = self.cache.image_id(identity)
        if image_id is None:
            raise EnvironmentBuildError("repo2docker_image_missing_after_build")
        return EnvironmentBuildResult(
            identity=identity,
            cache_hit=False,
            image_id=image_id,
            duration_ms=result.duration_ms,
        )


def environment_identity(
    *,
    repository_id: int,
    commit_sha: str,
    builder_version: str,
    strategy_version: str = RUNTIME_STRATEGY_VERSION,
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
        probe_command(
            "repo2docker",
            (sys.executable, "-m", "repo2docker", "--version"),
            runner=runner,
        ),
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
