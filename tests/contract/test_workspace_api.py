from pathlib import Path

from fastapi.testclient import TestClient

from notebook_launcher.app import AppServices, create_app
from notebook_launcher.config import Settings
from notebook_launcher.launch_auth import LaunchTokenCodec
from notebook_launcher.launches import StateLaunchStarter
from notebook_launcher.repository import SourceSnapshot, source_tree_digest
from notebook_launcher.state import StateStore
from notebook_launcher.workspace import WorkspaceManager


class UnusedResolver:
    def resolve(self, _request):
        raise AssertionError("resolver is not used by workspace API")


def test_workspace_metadata_and_copy_contract(tmp_path: Path):
    settings = Settings(root=tmp_path / "launcher", host="127.0.0.1", port=8080)
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    source_root = tmp_path / "source"
    source_root.mkdir()
    notebook = source_root / "demo.ipynb"
    notebook.write_text('{"cells":[],"metadata":{},"nbformat":4,"nbformat_minor":5}')
    snapshot = SourceSnapshot(
        source_root,
        notebook,
        7,
        "a" * 40,
        source_tree_digest(source_root),
    )
    workspace = WorkspaceManager(state, settings.workspaces_dir).create(
        snapshot,
        source_owner="owner",
        source_repository="repo",
        source_notebook_path="demo.ipynb",
    )
    services = AppServices(
        state=state,
        token_codec=LaunchTokenCodec(b"x" * 32),
        resolver=UnusedResolver(),
        starter=StateLaunchStarter(state, settings),
    )
    client = TestClient(create_app(settings, services))

    metadata = client.get(f"/api/workspaces/{workspace.id}")
    copied = client.post(
        f"/api/workspaces/{workspace.id}/notebooks/copy",
        json={
            "source_path": "demo.ipynb",
            "destination_scope": "workspace",
            "destination_path": "copies/demo.ipynb",
            "include_outputs": True,
            "overwrite": False,
        },
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert metadata.status_code == 200
    assert metadata.json()["active_notebook_path"] == "demo.ipynb"
    assert copied.status_code == 201
    assert (workspace.work_path / "copies" / "demo.ipynb").is_file()
