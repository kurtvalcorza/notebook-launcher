import platform
import shutil

import pytest

from notebook_launcher.config import Settings
from notebook_launcher.environment import collect_host_diagnostics

pytestmark = pytest.mark.integration


def test_installed_runtime_dependencies_are_detected(tmp_path):
    diagnostics = collect_host_diagnostics(
        Settings(root=tmp_path / "launcher"),
        system=platform.system(),
    )
    missing = [
        name
        for name in (
            "git",
            "docker",
            "repo2docker",
            "jupyter_collaboration",
            "mcp_backend",
            "state_directory",
        )
        if not diagnostics.get(name).available
    ]
    if missing:
        pytest.skip(f"runtime dependencies unavailable: {', '.join(missing)}")

    assert diagnostics.get("repo2docker").version
    assert diagnostics.get("jupyter_collaboration").version
    assert diagnostics.get("mcp_backend").version


def test_network_policy_capability_is_linux_host_qualified(tmp_path):
    diagnostics = collect_host_diagnostics(
        Settings(root=tmp_path / "launcher"),
        system=platform.system(),
    )
    network = diagnostics.get("network_policy")
    if platform.system() != "Linux":
        assert not network.available
        assert network.error == "unsupported_platform"
        pytest.skip("host firewall integration requires the launcher to run on Linux/WSL2")
    if shutil.which("iptables") is None and shutil.which("nft") is None:
        pytest.skip("no supported host firewall command is installed")
    assert network.available
