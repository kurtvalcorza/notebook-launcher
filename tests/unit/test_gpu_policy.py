import subprocess

import pytest

from notebook_launcher.models import GPU
from notebook_launcher.network import NetworkPolicyAttestation
from notebook_launcher.orchestration import CommandResult
from notebook_launcher.runtime import (
    DockerRuntime,
    GPUDevice,
    GPUProbeResult,
    RuntimePolicyError,
    RuntimeStartResult,
    effective_gpu_enabled,
    effective_gpu_probe,
    probe_nvidia,
    probe_nvidia_container,
    resolve_gpu_allocation,
)
from notebook_launcher.sandbox import SandboxPolicy


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


def test_container_gpu_probe_is_sandboxed_and_identity_is_matched():
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        return command_result(argv, stdout="GPU-abc, NVIDIA RTX Test\n")

    container = probe_nvidia_container("example:image", runner=runner)
    host = GPUProbeResult(True, (GPUDevice("GPU-abc", "NVIDIA RTX Test"),))
    effective = effective_gpu_probe(GPU.ON, host, container)

    assert effective.available
    assert effective.devices[0].uuid == "GPU-abc"
    assert "--network" in calls[0]
    assert calls[0][calls[0].index("--network") + 1] == "none"
    assert "--cap-drop" in calls[0]
    assert "--privileged" not in calls[0]


def test_gpu_on_fails_when_container_cannot_use_host_device():
    host = GPUProbeResult(True, (GPUDevice("GPU-host", "host"),))
    container = GPUProbeResult(True, (GPUDevice("GPU-other", "container"),))

    with pytest.raises(RuntimeError, match="identities differ"):
        effective_gpu_probe(GPU.ON, host, container)
    assert not effective_gpu_probe(GPU.AUTO, host, container).available


def test_gpu_inventory_requires_exact_host_container_agreement():
    host = GPUProbeResult(
        True,
        (GPUDevice("GPU-a", "a"), GPUDevice("GPU-b", "b")),
    )
    container = GPUProbeResult(
        True,
        (GPUDevice("GPU-a", "a"), GPUDevice("GPU-c", "c")),
    )

    with pytest.raises(RuntimeError, match="identities differ"):
        effective_gpu_probe(GPU.ON, host, container)
    assert effective_gpu_probe(GPU.AUTO, host, container).error == "device_identity_mismatch"


def test_effective_gpu_status_preserves_requested_and_observed_state():
    host = GPUProbeResult(True, (GPUDevice("GPU-host", "host"),))
    container = GPUProbeResult(True, (GPUDevice("GPU-host", "container"),))

    allocation = resolve_gpu_allocation(GPU.AUTO, host, container)

    assert allocation.requested_mode is GPU.AUTO
    assert allocation.enabled
    assert allocation.host_available
    assert allocation.container_available
    assert allocation.devices[0].uuid == "GPU-host"
    assert resolve_gpu_allocation(GPU.OFF, host, None).reason == "disabled_by_policy"


def test_runtime_requires_verified_matching_network_before_start(tmp_path):
    policy = SandboxPolicy(
        image="example",
        container_name="runtime",
        workspace_host=tmp_path,
        network_name="nl-runtime",
        published_ports=((49152, 8888),),
    )
    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        return command_result(argv, stdout="container-id\n")

    runtime = DockerRuntime(runner=runner)
    unverified = NetworkPolicyAttestation("nl-runtime", "nlr0001", False, 0)
    with pytest.raises(RuntimePolicyError, match="not verified"):
        runtime.start(policy, ["jupyter", "lab"], network=unverified)
    assert not calls


def test_runtime_ready_requires_all_policy_and_collaboration_assertions(tmp_path):
    policy = SandboxPolicy(
        image="example",
        container_name="runtime",
        workspace_host=tmp_path,
        network_name="nl-runtime",
        published_ports=((49152, 8888),),
        gpu_enabled=True,
    )

    def runner(argv, **_kwargs):
        if argv[:2] == ("docker", "inspect"):
            return command_result(argv, stdout="true\n")
        return command_result(argv, stdout="container-id\n")

    runtime = DockerRuntime(runner=runner)
    network = NetworkPolicyAttestation(
        "nl-runtime",
        "nlr0001",
        True,
        20,
        verification_commands=(("iptables", "-w", "-C", "DOCKER-USER"),),
    )
    started = runtime.start(policy, ["jupyter", "lab"], network=network)

    assert started.sandbox_verified and started.network_verified
    assert not started.ready
    assert "127.0.0.1:49152:8888" in started.argv
    assert "--gpus" in started.argv
    assert "--privileged" not in started.argv
    with pytest.raises(RuntimePolicyError, match="collaboration"):
        runtime.readiness(started, collaboration_ready=False)
    mutated = RuntimeStartResult(
        container_id=started.container_id,
        argv=started.argv,
        sandbox_verified=False,
        network_verified=True,
        gpu_enabled=True,
    )
    with pytest.raises(RuntimePolicyError, match="sandbox"):
        runtime.readiness(mutated, collaboration_ready=True)
    assert runtime.readiness(started, collaboration_ready=True).ready
