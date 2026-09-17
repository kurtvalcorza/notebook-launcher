from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import ExecutionTimeout
from .models import GPU
from .network import NetworkPolicyAttestation
from .orchestration import CommandResult, run_argv
from .sandbox import SandboxPolicy, docker_run_argv, validate_sandbox_policy


@dataclass(frozen=True, slots=True)
class GPUDevice:
    uuid: str
    name: str


@dataclass(frozen=True, slots=True)
class GPUProbeResult:
    available: bool
    devices: tuple[GPUDevice, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class GPUAllocation:
    requested_mode: GPU
    enabled: bool
    devices: tuple[GPUDevice, ...]
    host_available: bool
    container_available: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStartResult:
    container_id: str
    argv: tuple[str, ...]
    sandbox_verified: bool
    network_verified: bool
    gpu_enabled: bool
    ready: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    container_id: str
    sandbox_verified: bool
    network_verified: bool
    collaboration_ready: bool
    ready: bool


class RuntimePolicyError(RuntimeError):
    pass


def probe_nvidia(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> GPUProbeResult:
    try:
        result = runner(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return GPUProbeResult(False, error=type(exc).__name__)

    if result.returncode != 0:
        return GPUProbeResult(False, error="nvidia_smi_failed")

    return _parse_gpu_devices(result.stdout)


def _parse_gpu_devices(stdout: str) -> GPUProbeResult:
    devices: list[GPUDevice] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        uuid, separator, name = line.partition(",")
        if not separator:
            continue
        devices.append(GPUDevice(uuid=uuid.strip(), name=name.strip()))
    return GPUProbeResult(bool(devices), tuple(devices))


def probe_nvidia_container(
    image: str,
    *,
    runner: Callable[..., CommandResult] = run_argv,
) -> GPUProbeResult:
    if not image.strip() or "\x00" in image:
        raise ValueError("container image is required")
    argv = (
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--user",
        "1000:1000",
        "--read-only",
        "--pids-limit",
        "64",
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--gpus",
        "all",
        image,
        "nvidia-smi",
        "--query-gpu=uuid,name",
        "--format=csv,noheader",
    )
    try:
        result = runner(argv, timeout_seconds=30, max_output_bytes=8192)
    except (ExecutionTimeout, OSError, subprocess.SubprocessError) as exc:
        return GPUProbeResult(False, error=type(exc).__name__)
    if result.returncode != 0:
        return GPUProbeResult(False, error="container_gpu_probe_failed")
    return _parse_gpu_devices(result.stdout)


def effective_gpu_enabled(mode: GPU, probe: GPUProbeResult) -> bool:
    if mode is GPU.OFF:
        return False
    if mode is GPU.ON:
        if not probe.available:
            raise RuntimeError("GPU-required mode requested but no usable NVIDIA GPU is available")
        return True
    return probe.available


def effective_gpu_probe(
    mode: GPU,
    host_probe: GPUProbeResult,
    container_probe: GPUProbeResult | None,
) -> GPUProbeResult:
    if mode is GPU.OFF:
        return GPUProbeResult(False)
    usable = bool(
        host_probe.available
        and container_probe is not None
        and container_probe.available
    )
    if mode is GPU.ON and not usable:
        detail = (
            container_probe.error
            if host_probe.available and container_probe is not None
            else host_probe.error
        )
        raise RuntimeError(
            "GPU-required mode requested but the NVIDIA device is not usable "
            f"inside the runtime ({detail or 'unavailable'})"
        )
    if not usable:
        return GPUProbeResult(False)

    host_ids = {device.uuid for device in host_probe.devices}
    container_ids = {device.uuid for device in container_probe.devices}
    if host_ids != container_ids:
        if mode is GPU.ON:
            raise RuntimeError(
                "GPU-required mode requested but host/container device identities differ"
            )
        return GPUProbeResult(False, error="device_identity_mismatch")
    return GPUProbeResult(True, container_probe.devices)


def resolve_gpu_allocation(
    mode: GPU,
    host_probe: GPUProbeResult,
    container_probe: GPUProbeResult | None,
) -> GPUAllocation:
    effective = effective_gpu_probe(mode, host_probe, container_probe)
    reason = effective.error
    if mode is GPU.OFF:
        reason = "disabled_by_policy"
    elif not effective.available and reason is None:
        reason = "usable_gpu_unavailable"
    return GPUAllocation(
        requested_mode=mode,
        enabled=effective.available,
        devices=effective.devices,
        host_available=host_probe.available,
        container_available=bool(container_probe and container_probe.available),
        reason=reason,
    )


class DockerRuntime:
    """Start a container only after fail-closed host policy application."""

    def __init__(
        self,
        *,
        runner: Callable[..., CommandResult] = run_argv,
        home: Path | None = None,
    ) -> None:
        self.runner = runner
        self.home = Path.home() if home is None else home

    def start(
        self,
        policy: SandboxPolicy,
        command: Sequence[str],
        *,
        network: NetworkPolicyAttestation,
    ) -> RuntimeStartResult:
        validate_sandbox_policy(policy, home=self.home, require_network=True)
        if not network.verified:
            raise RuntimePolicyError("network policy is not verified")
        if network.commands_verified <= 0 or not network.deny_non_global:
            raise RuntimePolicyError("non-global destination denial is not verified")
        if not network.dns_exceptions_port_53_only:
            raise RuntimePolicyError("DNS exception scope is not verified")
        if network.host_gateway_alias_present:
            raise RuntimePolicyError("host gateway alias is forbidden")
        if policy.network_name != network.network_name:
            raise RuntimePolicyError("sandbox network does not match verified network")
        if not network.verification_commands:
            raise RuntimePolicyError("network attestation has no verification evidence")
        for verification_command in network.verification_commands:
            verification = self.runner(
                verification_command,
                timeout_seconds=30,
                max_output_bytes=4096,
            )
            if verification.returncode != 0:
                raise RuntimePolicyError("network policy verification is stale")

        argv = docker_run_argv(policy, command)
        if "--add-host" in argv or any("host-gateway" in value for value in argv):
            raise RuntimePolicyError("host gateway aliases are forbidden")
        published = [
            argv[index + 1]
            for index, value in enumerate(argv[:-1])
            if value == "--publish"
        ]
        if any(not value.startswith("127.0.0.1:") for value in published):
            raise RuntimePolicyError("published ports must bind to loopback")

        detached_argv = (*argv[:2], "--detach", *argv[2:])
        try:
            result = self.runner(
                detached_argv,
                timeout_seconds=60,
                max_output_bytes=4096,
            )
        except (ExecutionTimeout, OSError, subprocess.SubprocessError) as exc:
            raise RuntimePolicyError(type(exc).__name__) from exc
        if result.returncode != 0:
            raise RuntimePolicyError(f"docker_run_failed_{result.returncode}")
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimePolicyError("docker_run_missing_container_id")
        return RuntimeStartResult(
            container_id=container_id,
            argv=detached_argv,
            sandbox_verified=True,
            network_verified=True,
            gpu_enabled=policy.gpu_enabled,
        )

    def readiness(
        self,
        started: RuntimeStartResult,
        *,
        collaboration_ready: bool,
    ) -> RuntimeReadiness:
        if not started.sandbox_verified:
            raise RuntimePolicyError("sandbox policy is not verified")
        if not started.network_verified:
            raise RuntimePolicyError("network policy is not verified")
        if not collaboration_ready:
            raise RuntimePolicyError("Jupyter collaboration is not ready")
        try:
            result = self.runner(
                (
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}",
                    started.container_id,
                ),
                timeout_seconds=10,
                max_output_bytes=4096,
            )
        except (ExecutionTimeout, OSError, subprocess.SubprocessError) as exc:
            raise RuntimePolicyError(type(exc).__name__) from exc
        if result.returncode != 0 or result.stdout.strip().lower() != "true":
            raise RuntimePolicyError("runtime container is not running")
        return RuntimeReadiness(
            container_id=started.container_id,
            sandbox_verified=True,
            network_verified=True,
            collaboration_ready=True,
            ready=True,
        )
