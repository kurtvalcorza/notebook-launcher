from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_CONTAINER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_FORBIDDEN_ENV_FRAGMENTS = (
    "AWS_",
    "AZURE_",
    "GOOGLE_",
    "GITHUB_",
    "GH_TOKEN",
    "SSH_",
    "DOCKER_",
    "KUBECONFIG",
    "PASSWORD",
    "SECRET",
    "PRIVATE_KEY",
    "API_KEY",
    "TOKEN",
    "CREDENTIAL",
    "AUTH",
)

_CONTAINER_CONTROL_NAMES = frozenset(
    {"docker.sock", "podman.sock", "containerd.sock", "crio.sock"}
)


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    image: str
    container_name: str
    workspace_host: Path
    workspace_container: str = "/workspace"
    readonly_mounts: tuple[tuple[Path, str], ...] = ()
    readwrite_mounts: tuple[tuple[Path, str], ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    memory: str = "8g"
    cpus: float = 4.0
    pids_limit: int = 1024
    gpu_enabled: bool = False
    seccomp_profile: Path | None = None
    network_name: str | None = None
    published_ports: tuple[tuple[int, int], ...] = ()
    tmpfs_size: str = "512m"
    nofile_limit: int = 4096


def scrub_host_environment(
    environment: Mapping[str, str],
    *,
    allowed_names: Iterable[str] = (),
) -> dict[str, str]:
    """Return only explicitly allowed, non-secret host environment values."""
    allowed = set(allowed_names)
    clean: dict[str, str] = {}
    for key in allowed:
        value = environment.get(key)
        if value is None:
            continue
        upper = key.upper()
        if any(fragment in upper for fragment in _FORBIDDEN_ENV_FRAGMENTS):
            raise ValueError(f"host secret environment variable is forbidden: {key}")
        clean[key] = value
    return clean


def _validate_container_path(value: str) -> None:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"container mount path must be absolute and normalized: {value}")
    if value == "/" or value == "/var/run/docker.sock":
        raise ValueError(f"container mount path is forbidden: {value}")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_sandbox_policy(
    policy: SandboxPolicy,
    *,
    home: Path | None = None,
    require_network: bool = False,
) -> None:
    home = Path.home() if home is None else home
    if not policy.image.strip() or "\x00" in policy.image:
        raise ValueError("container image is required")
    if not _CONTAINER_NAME.fullmatch(policy.container_name):
        raise ValueError("invalid container name")
    if policy.cpus <= 0 or policy.pids_limit <= 0 or policy.nofile_limit <= 0:
        raise ValueError("sandbox resource limits must be positive")
    if not policy.memory.strip() or not policy.tmpfs_size.strip():
        raise ValueError("sandbox memory and tmpfs limits are required")
    if require_network and not policy.network_name:
        raise ValueError("a launcher-managed network is required")
    if policy.network_name is not None and (
        not policy.network_name.strip() or "\x00" in policy.network_name
    ):
        raise ValueError("invalid launcher-managed network name")
    if policy.seccomp_profile is not None:
        profile = policy.seccomp_profile.resolve(strict=True)
        if not profile.is_file():
            raise ValueError("seccomp profile must be a file")

    validate_mount_policy(policy, home=home)
    destinations: set[str] = set()
    for _host, container in (
        (policy.workspace_host, policy.workspace_container),
        *policy.readonly_mounts,
        *policy.readwrite_mounts,
    ):
        _validate_container_path(container)
        if container in destinations:
            raise ValueError(f"duplicate container mount destination: {container}")
        destinations.add(container)
    for host, container in (
        (policy.workspace_host, policy.workspace_container),
        *policy.readonly_mounts,
        *policy.readwrite_mounts,
    ):
        if "," in str(host) or "," in container:
            raise ValueError("Docker mount paths must not contain commas")

    for key, value in policy.environment:
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise ValueError("invalid container environment entry")
        upper = key.upper()
        if any(fragment in upper for fragment in _FORBIDDEN_ENV_FRAGMENTS):
            raise ValueError(f"host credential variable must not be injected: {key}")

    for host_port, container_port in policy.published_ports:
        if not (1 <= host_port <= 65535 and 1 <= container_port <= 65535):
            raise ValueError("published ports must be between 1 and 65535")


def docker_run_argv(policy: SandboxPolicy, command: Iterable[str]) -> list[str]:
    validate_sandbox_policy(policy)
    normalized_command = tuple(command)
    if any(not isinstance(item, str) or "\x00" in item for item in normalized_command):
        raise ValueError("container command entries must be strings without NUL bytes")
    argv = [
        "docker",
        "run",
        "--rm",
        "--name",
        policy.container_name,
        "--user",
        "1000:1000",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--memory",
        policy.memory,
        "--cpus",
        str(policy.cpus),
        "--pids-limit",
        str(policy.pids_limit),
        "--ulimit",
        f"nofile={policy.nofile_limit}:{policy.nofile_limit}",
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={policy.tmpfs_size}",
        "--tmpfs",
        "/run:rw,noexec,nosuid,nodev,size=64m",
        "--init",
        "--mount",
        f"type=bind,source={policy.workspace_host},target={policy.workspace_container}",
    ]

    if policy.seccomp_profile is not None:
        argv.extend(["--security-opt", f"seccomp={policy.seccomp_profile}"])

    for host, container in policy.readonly_mounts:
        argv.extend(
            ["--mount", f"type=bind,source={host},target={container},readonly"]
        )
    for host, container in policy.readwrite_mounts:
        argv.extend(["--mount", f"type=bind,source={host},target={container}"])
    for key, value in policy.environment:
        argv.extend(["--env", f"{key}={value}"])

    if policy.network_name is not None:
        argv.extend(["--network", policy.network_name])
    for host_port, container_port in policy.published_ports:
        argv.extend(["--publish", f"127.0.0.1:{host_port}:{container_port}"])

    if policy.gpu_enabled:
        argv.extend(["--gpus", "all"])

    argv.append(policy.image)
    argv.extend(normalized_command)
    return argv


def validate_mount_policy(policy: SandboxPolicy, *, home: Path | None = None) -> None:
    forbidden_parts = {".ssh", ".aws", ".docker", ".kube", ".gnupg"}
    docker_socket = Path("/var/run/docker.sock").resolve(strict=False)
    home = (Path.home() if home is None else home).resolve()

    mounts = [(policy.workspace_host, policy.workspace_container)]
    mounts.extend(policy.readonly_mounts)
    mounts.extend(policy.readwrite_mounts)

    for host, _container in mounts:
        resolved = host.expanduser().resolve(strict=False)
        if resolved == docker_socket:
            raise ValueError("Docker socket must not be mounted")
        if resolved.name.casefold() in _CONTAINER_CONTROL_NAMES:
            raise ValueError("container-control sockets must not be mounted")
        if resolved == Path(resolved.anchor):
            raise ValueError("filesystem root must not be mounted")
        if home is not None and (resolved == home or _is_within(home, resolved)):
            raise ValueError("whole home directory must not be mounted")
        if any(part.lower() in forbidden_parts for part in resolved.parts):
            raise ValueError(f"credential directory must not be mounted: {resolved}")
        lowered = resolved.as_posix().lower()
        if "/.config/gcloud" in lowered:
            raise ValueError(f"credential directory must not be mounted: {resolved}")
