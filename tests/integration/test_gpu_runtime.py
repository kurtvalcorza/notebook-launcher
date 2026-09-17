import os

import pytest

from notebook_launcher.models import GPU
from notebook_launcher.runtime import (
    effective_gpu_probe,
    probe_nvidia,
    probe_nvidia_container,
)

pytestmark = [pytest.mark.integration, pytest.mark.gpu, pytest.mark.docker]


def test_host_and_container_report_same_gpu_device_identity():
    image = os.environ.get("NOTEBOOK_LAUNCHER_GPU_TEST_IMAGE")
    if not image:
        pytest.skip("NOTEBOOK_LAUNCHER_GPU_TEST_IMAGE is not configured")
    host = probe_nvidia()
    if not host.available:
        pytest.skip(f"host NVIDIA GPU unavailable: {host.error}")
    container = probe_nvidia_container(image)
    if not container.available:
        pytest.skip(f"container NVIDIA GPU unavailable: {container.error}")

    effective = effective_gpu_probe(GPU.ON, host, container)

    assert effective.available
    assert {device.uuid for device in effective.devices} <= {
        device.uuid for device in host.devices
    }
