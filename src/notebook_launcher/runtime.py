from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from .models import GPU


@dataclass(frozen=True, slots=True)
class GPUDevice:
    uuid: str
    name: str


@dataclass(frozen=True, slots=True)
class GPUProbeResult:
    available: bool
    devices: tuple[GPUDevice, ...] = ()
    error: str | None = None


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

    devices: list[GPUDevice] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        uuid, separator, name = line.partition(",")
        if not separator:
            continue
        devices.append(GPUDevice(uuid=uuid.strip(), name=name.strip()))
    return GPUProbeResult(bool(devices), tuple(devices))


def effective_gpu_enabled(mode: GPU, probe: GPUProbeResult) -> bool:
    if mode is GPU.OFF:
        return False
    if mode is GPU.ON:
        if not probe.available:
            raise RuntimeError("GPU-required mode requested but no usable NVIDIA GPU is available")
        return True
    return probe.available
