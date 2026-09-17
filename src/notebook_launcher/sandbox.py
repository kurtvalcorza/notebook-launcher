from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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


def docker_run_argv(policy: SandboxPolicy, command: Iterable[str]) -> list[str]:
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
        "--mount",
        f"type=bind,src={policy.workspace_host},dst={policy.workspace_container},rw",
    ]

    if policy.seccomp_profile is not None:
        argv.extend(["--security-opt", f"seccomp={policy.seccomp_profile}"])

    for host, container in policy.readonly_mounts:
        argv.extend(["--mount", f"type=bind,src={host},dst={container},ro"])
    for host, container in policy.readwrite_mounts:
        argv.extend(["--mount", f"type=bind,src={host},dst={container},rw"])
    for key, value in policy.environment:
        argv.extend(["--env", f"{key}={value}"])

    if policy.gpu_enabled:
        argv.extend(["--gpus", "all"])

    argv.append(policy.image)
    argv.extend(command)
    return argv


def validate_mount_policy(policy: SandboxPolicy, *, home: Path | None = None) -> None:
    forbidden_names = {
        ".ssh",
        ".aws",
        ".config/gcloud",
        ".docker",
    }
    docker_socket = Path("/var/run/docker.sock")
    home = home.resolve() if home is not None else None

    mounts = [(policy.workspace_host, policy.workspace_container)]
    mounts.extend(policy.readonly_mounts)
    mounts.extend(policy.readwrite_mounts)

    for host, _container in mounts:
        resolved = host.resolve()
        if resolved == docker_socket:
            raise ValueError("Docker socket must not be mounted")
        if home is not None and resolved == home:
            raise ValueError("whole home directory must not be mounted")
        text = resolved.as_posix()
        if any(text.endswith(name) for name in forbidden_names):
            raise ValueError(f"credential directory must not be mounted: {resolved}")
