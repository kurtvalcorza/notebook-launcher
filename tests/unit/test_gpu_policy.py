import subprocess

import pytest

from notebook_launcher.models import GPU
from notebook_launcher.runtime import GPUProbeResult, effective_gpu_enabled, probe_nvidia


def fake_runner_ok(*args, **kwargs):
    return subprocess.CompletedProcess(
        args=args[0],
        returncode=0,
        stdout="GPU-abc, NVIDIA RTX Test\n",
        stderr="",
    )


def fake_runner_fail(*args, **kwargs):
    return subprocess.CompletedProcess(
        args=args[0],
        returncode=1,
        stdout="",
        stderr="no device",
    )


def test_probe_detects_device():
    result = probe_nvidia(fake_runner_ok)
    assert result.available
    assert result.devices[0].uuid == "GPU-abc"


def test_probe_failure_is_nonfatal():
    result = probe_nvidia(fake_runner_fail)
    assert not result.available
    assert result.error == "nvidia_smi_failed"


def test_gpu_policy_auto_on_off():
    available = GPUProbeResult(True)
    unavailable = GPUProbeResult(False)

    assert effective_gpu_enabled(GPU.AUTO, available)
    assert not effective_gpu_enabled(GPU.AUTO, unavailable)
    assert not effective_gpu_enabled(GPU.OFF, available)
    assert effective_gpu_enabled(GPU.ON, available)

    with pytest.raises(RuntimeError, match="GPU-required"):
        effective_gpu_enabled(GPU.ON, unavailable)
