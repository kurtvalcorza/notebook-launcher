import os
from pathlib import Path
from uuid import uuid4

import pytest

from notebook_launcher.errors import HostPathEscape, PermissionDenied
from notebook_launcher.grants import UserDataGrantStore
from notebook_launcher.repository import SourceSnapshot, source_tree_digest
from notebook_launcher.state import StateStore
from notebook_launcher.workspace import WorkspaceManager


def _manager(tmp_path: Path, *, mode: str):
    source_root = tmp_path / "source"
    notebook = source_root / "demo.ipynb"
    source_root.mkdir()
    notebook.write_bytes(b"notebook")
    snapshot = SourceSnapshot(
        root=source_root,
        notebook=notebook,
        repository_id=1,
        commit_sha="a" * 40,
        tree_digest=source_tree_digest(source_root),
    )
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    grants = UserDataGrantStore(state)
    manager = WorkspaceManager(state, tmp_path / "workspaces", grants=grants)
    record = manager.create(
        snapshot,
        source_owner="owner",
        source_repository="repo",
        source_notebook_path="demo.ipynb",
        workspace_id=uuid4(),
    )
    data = tmp_path / "user-data"
    data.mkdir()
    grants.grant(record.id, data, mode=mode)
    return manager, record, data


def test_save_copy_to_writable_user_data(tmp_path: Path):
    manager, record, data = _manager(tmp_path, mode="rw")

    if os.name == "nt":
        with pytest.raises(HostPathEscape):
            manager.save_copy(
                record.id,
                source_path="demo.ipynb",
                destination_scope="user_data",
                destination_path="copies/demo.ipynb",
            )
        assert not (data / "copies" / "demo.ipynb").exists()
        return

    manager.save_copy(
        record.id,
        source_path="demo.ipynb",
        destination_scope="user_data",
        destination_path="copies/demo.ipynb",
    )

    assert (data / "copies" / "demo.ipynb").read_bytes() == b"notebook"


def test_save_copy_to_readonly_user_data_is_rejected(tmp_path: Path):
    manager, record, data = _manager(tmp_path, mode="ro")

    with pytest.raises(PermissionDenied, match="read-only"):
        manager.save_copy(
            record.id,
            source_path="demo.ipynb",
            destination_scope="user_data",
            destination_path="copy.ipynb",
        )

    assert not (data / "copy.ipynb").exists()


def test_save_copy_to_user_data_rejects_escape(tmp_path: Path):
    manager, record, _data = _manager(tmp_path, mode="rw")

    with pytest.raises(HostPathEscape):
        manager.save_copy(
            record.id,
            source_path="demo.ipynb",
            destination_scope="user_data",
            destination_path="../escape.ipynb",
        )

    assert not (tmp_path / "escape.ipynb").exists()
