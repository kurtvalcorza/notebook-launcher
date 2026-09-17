import os
import platform
import shutil
from uuid import uuid4

import pytest

from notebook_launcher.network import FirewallPlan, NetworkPolicyApplier
from notebook_launcher.orchestration import run_argv

pytestmark = [pytest.mark.integration, pytest.mark.docker]
IMAGE = "rancher/mirrored-library-busybox:1.37.0"


def _require_network_integration() -> None:
    if os.environ.get("NOTEBOOK_LAUNCHER_RUN_NETWORK_INTEGRATION") != "1":
        pytest.skip("host-firewall integration is opt-in")
    if platform.system() != "Linux" or shutil.which("iptables") is None:
        pytest.skip("host-firewall integration requires Linux iptables")
    check = run_argv(
        ("iptables", "-w", "-C", "DOCKER-USER", "-j", "RETURN"),
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    if check.returncode not in {0, 1}:
        pytest.skip("current user cannot inspect the Docker host firewall")
    image = run_argv(
        ("docker", "image", "inspect", IMAGE),
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    if image.returncode != 0:
        pytest.skip(f"local Docker image unavailable: {IMAGE}")


def test_public_egress_allowed_while_private_and_resolved_private_are_denied():
    _require_network_integration()
    suffix = uuid4().hex[:8]
    plan = FirewallPlan(
        network_name=f"nl-net-{suffix}",
        bridge_name=f"nln{suffix}",
        ipv4_subnet="172.29.253.0/28",
    )
    sentinel = f"nl-private-{suffix}"
    applier = NetworkPolicyApplier()
    attestation = applier.apply(plan)
    try:
        assert attestation.verified
        server = run_argv(
            (
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                sentinel,
                "--network",
                plan.network_name,
                "--ip",
                "172.29.253.3",
                IMAGE,
                "sh",
                "-c",
                "mkdir -p /www && echo ok >/www/index.html && httpd -f -p 8080 -h /www",
            ),
            timeout_seconds=30,
            max_output_bytes=4096,
        )
        assert server.returncode == 0, server.stderr

        public = run_argv(
            (
                "docker",
                "run",
                "--rm",
                "--network",
                plan.network_name,
                IMAGE,
                "wget",
                "-qO-",
                "--timeout=15",
                "http://example.com",
            ),
            timeout_seconds=30,
            max_output_bytes=4096,
        )
        assert public.returncode == 0, public.stderr

        for docker_args, target in (
            ((), "http://172.29.253.3:8080"),
            (("--add-host", "private.test:172.29.253.3"), "http://private.test:8080"),
        ):
            denied = run_argv(
                (
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    plan.network_name,
                    *docker_args,
                    IMAGE,
                    "wget",
                    "-qO-",
                    "--timeout=2",
                    target,
                ),
                timeout_seconds=10,
                max_output_bytes=4096,
            )
            assert denied.returncode != 0
    finally:
        run_argv(
            ("docker", "rm", "--force", sentinel),
            timeout_seconds=10,
            max_output_bytes=4096,
        )
        applier.remove(plan)
