from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from notebook_launcher import safe_paths
from notebook_launcher.errors import HostPathEscape
from notebook_launcher.repository import SourceSnapshot, source_tree_digest
from notebook_launcher.safe_paths import (
    atomic_write_under_root,
    rename_under_root,
    root_identity,
)
from notebook_launcher.state import StateStore
from notebook_launcher.workspace import WorkspaceManager


def _manager(tmp_path: Path) -> tuple[StateStore, WorkspaceManager, object]:
    source_root = tmp_path / "source"
    notebook = source_root / "demo.ipynb"
    source_root.mkdir()
    notebook.write_bytes(b'{"cells": []}')
    snapshot = SourceSnapshot(
        root=source_root,
        notebook=notebook,
        repository_id=1,
        commit_sha="a" * 40,
        tree_digest=source_tree_digest(source_root),
    )
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    manager = WorkspaceManager(state, tmp_path / "workspaces")
    record = manager.create(
        snapshot,
        source_owner="owner",
        source_repository="repo",
        source_notebook_path="demo.ipynb",
    )
    return state, manager, record


def test_expected_identity_write_uses_secure_platform_primitive(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()

    if os.name == "nt":
        with pytest.raises(HostPathEscape):
            atomic_write_under_root(
                root,
                "nested/data.bin",
                b"safe",
                expected_identity=root_identity(root),
            )
        assert not (root / "nested").exists()
        return

    atomic_write_under_root(
        root,
        "nested/data.bin",
        b"safe",
        expected_identity=root_identity(root),
        allow_windows_managed_root=True,
    )
    assert (root / "nested" / "data.bin").read_bytes() == b"safe"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Unix dirfd swap behavior is verified on Linux/WSL",
)
def test_component_swap_cannot_redirect_workspace_write_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "root"
    nested = root / "nested"
    outside = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside.mkdir()
    original = safe_paths._open_parent_fd

    @contextmanager
    def swap_after_open(*args, **kwargs):
        with original(*args, **kwargs) as descriptor:
            nested.rename(root / "pinned")
            nested.symlink_to(outside, target_is_directory=True)
            yield descriptor

    monkeypatch.setattr(safe_paths, "_open_parent_fd", swap_after_open)

    atomic_write_under_root(
        root,
        "nested/data.bin",
        b"safe",
        expected_identity=root_identity(root),
        allow_windows_managed_root=True,
    )

    assert (root / "pinned" / "data.bin").read_bytes() == b"safe"
    assert not (outside / "data.bin").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-lock behavior")
def test_component_swap_cannot_redirect_workspace_write_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    original = safe_paths._open_parent_path_windows
    swap_completed = False

    @contextmanager
    def attempt_swap_after_lock(*args, **kwargs):
        nonlocal swap_completed
        with original(*args, **kwargs) as parent:
            nested.rename(root / "swapped")
            nested.mkdir()
            swap_completed = True
            yield parent

    monkeypatch.setattr(safe_paths, "_open_parent_path_windows", attempt_swap_after_lock)

    atomic_write_under_root(
        root,
        "nested/data.bin",
        b"safe",
        expected_identity=root_identity(root),
        allow_windows_managed_root=True,
    )

    assert swap_completed
    assert (root / "swapped" / "data.bin").read_bytes() == b"safe"
    assert not (nested / "data.bin").exists()


def test_save_copy_explicit_overwrite_advances_version_and_bytes(tmp_path: Path):
    state, manager, record = _manager(tmp_path)
    manager.save_copy(
        record.id,
        source_path="demo.ipynb",
        destination_scope="workspace",
        destination_path="copy.ipynb",
    )
    first = state.get_document_version(record.id, "copy.ipynb")
    assert first is not None
    (record.work_path / "demo.ipynb").write_bytes(b'{"cells": ["new"]}')

    manager.save_copy(
        record.id,
        source_path="demo.ipynb",
        destination_scope="workspace",
        destination_path="copy.ipynb",
        overwrite=True,
    )

    second = state.get_document_version(record.id, "copy.ipynb")
    assert second is not None
    assert second.version != first.version
    assert second.content_hash != first.content_hash
    assert (record.work_path / "copy.ipynb").read_bytes() == b'{"cells": ["new"]}'


def test_noreplace_rename_preserves_concurrent_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.ipynb").write_bytes(b"source")

    if sys.platform.startswith("linux"):
        original = safe_paths._rename_noreplace_at

        def inject_destination(*args, **kwargs):
            (root / "destination.ipynb").write_bytes(b"competitor")
            return original(*args, **kwargs)

        monkeypatch.setattr(safe_paths, "_rename_noreplace_at", inject_destination)
    elif os.name == "nt":
        original = safe_paths._rename_open_handle_windows

        def inject_destination(*args, **kwargs):
            (root / "destination.ipynb").write_bytes(b"competitor")
            return original(*args, **kwargs)

        monkeypatch.setattr(
            safe_paths,
            "_rename_open_handle_windows",
            inject_destination,
        )
    else:
        pytest.skip("atomic no-replace primitive unavailable")

    with pytest.raises(FileExistsError):
        rename_under_root(
            root,
            "source.ipynb",
            "destination.ipynb",
            expected_identity=root_identity(root),
            allow_windows_managed_root=True,
        )

    assert (root / "source.ipynb").read_bytes() == b"source"
    assert (root / "destination.ipynb").read_bytes() == b"competitor"
