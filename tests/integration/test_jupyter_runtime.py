from importlib.metadata import PackageNotFoundError, version

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.jupyter]


def test_jupyter_collaboration_runtime_packages_are_installed():
    try:
        lab_version = version("jupyterlab")
        collaboration_version = version("jupyter-collaboration")
    except PackageNotFoundError as exc:
        pytest.skip(f"Jupyter runtime package unavailable: {exc}")

    assert int(lab_version.split(".", 1)[0]) == 4
    assert int(collaboration_version.split(".", 1)[0]) == 5
