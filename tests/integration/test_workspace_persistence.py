import errno
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

import pytest

import notebook_launcher.workspace as workspace_module
from notebook_launcher.errors import ConflictError, StorageFull
from notebook_launcher.repository import SourceSnapshot, source_tree_digest
from notebook_launcher.state import StateStore
from notebook_launcher.versions import content_hash
from notebook_launcher.workspace import WorkspaceManager


def _workspace(tmp_path: Path) -> tuple[StateStore, WorkspaceManager, object]:
    source_root = tmp_path / "source"
    notebook = source_root / "notebooks" / "demo.ipynb"
    notebook.parent.mkdir(parents=True)
    notebook.write_bytes(b'{"cells": []}')
    source = SourceSnapshot(
        root=source_root,
        notebook=notebook,
        repository_id=42,
        commit_sha="a" * 40,
        tree_digest=source_tree_digest(source_root),
    )
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    manager = WorkspaceManager(state, tmp_path / "workspaces")
    record = manager.create(
        source,
        source_owner="owner",
        source_repository="repo",
        source_notebook_path="notebooks/demo.ipynb",
    )
    return state, manager, record


def test_reopen_preserves_notebook_outputs_and_ordinary_files(tmp_path: Path):
    _state, manager, record = _workspace(tmp_path)
    notebook = record.work_path / record.active_notebook_path
    notebook.write_bytes(b'{"cells": ["edited"]}')
    (record.outputs_path / "result.txt").write_text("result")
    (record.work_path / "artifact.txt").write_text("artifact")

    reopened = manager.reopen(record.id)

    assert reopened.id == record.id
    assert notebook.read_bytes() == b'{"cells": ["edited"]}'
    assert (reopened.outputs_path / "result.txt").read_text() == "result"
    assert (reopened.work_path / "artifact.txt").read_text() == "artifact"


def test_failed_replacement_preserves_bytes_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, manager, record = _workspace(tmp_path)
    version = manager.write_file(record.id, "artifact.bin", b"old")

    def fail_replace(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "disk full")

    replacement_target = (
        "notebook_launcher.safe_paths._rename_open_handle_windows"
        if os.name == "nt"
        else "notebook_launcher.safe_paths.os.replace"
    )
    monkeypatch.setattr(replacement_target, fail_replace)
    with pytest.raises(StorageFull):
        manager.write_file(
            record.id,
            "artifact.bin",
            b"new",
            expected_version=version,
        )

    assert (record.work_path / "artifact.bin").read_bytes() == b"old"
    stored = state.get_document_version(record.id, "artifact.bin")
    assert stored is not None
    assert stored.version == version


def test_concurrent_stale_file_writes_leave_version_and_bytes_consistent(tmp_path: Path):
    state, manager, record = _workspace(tmp_path)
    version = manager.write_file(record.id, "artifact.bin", b"base")

    def write(payload: bytes):
        try:
            return manager.write_file(
                record.id,
                "artifact.bin",
                payload,
                expected_version=version,
            ), payload
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (b"first", b"second")))

    winner = next(result for result in results if result is not None)
    assert sum(result is not None for result in results) == 1
    stored = state.get_document_version(record.id, "artifact.bin")
    assert stored is not None
    assert stored.version == winner[0]
    assert (record.work_path / "artifact.bin").read_bytes() == winner[1]


def test_active_notebook_rename_is_persisted_across_reopen(tmp_path: Path):
    _state, manager, record = _workspace(tmp_path)
    (record.work_path / "renamed").mkdir()

    renamed = manager.rename_active_notebook(record.id, "renamed/demo.ipynb")
    reopened = manager.reopen(record.id)

    assert renamed.active_notebook_path == "renamed/demo.ipynb"
    assert reopened.active_notebook_path == "renamed/demo.ipynb"
    assert not (record.work_path / "notebooks" / "demo.ipynb").exists()
    assert (record.work_path / "renamed" / "demo.ipynb").is_file()


def test_save_copy_preserves_source_and_existing_destination(tmp_path: Path):
    _state, manager, record = _workspace(tmp_path)
    source = record.work_path / record.active_notebook_path
    before = source.read_bytes()
    manager.save_copy(
        record.id,
        source_path=record.active_notebook_path,
        destination_scope="workspace",
        destination_path="copy.ipynb",
    )
    (record.work_path / "copy.ipynb").write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        manager.save_copy(
            record.id,
            source_path=record.active_notebook_path,
            destination_scope="workspace",
            destination_path="copy.ipynb",
        )

    assert source.read_bytes() == before
    assert (record.work_path / "copy.ipynb").read_bytes() == b"existing"


def test_generic_write_rejects_backslash_alias_of_active_notebook(tmp_path: Path):
    _state, manager, record = _workspace(tmp_path)
    source = record.work_path / record.active_notebook_path
    original = source.read_bytes()

    with pytest.raises(ConflictError):
        manager.write_file(
            record.id,
            record.active_notebook_path.replace("/", "\\"),
            b"replacement",
        )

    assert source.read_bytes() == original


def test_save_copy_can_clear_outputs_without_mutating_source(tmp_path: Path):
    _state, manager, record = _workspace(tmp_path)
    source = record.work_path / record.active_notebook_path
    source.write_text(
        '{"cells":[{"cell_type":"code","execution_count":1,'
        '"outputs":[{"output_type":"stream","text":"hello"}]}]}'
    )
    before = source.read_bytes()

    manager.save_copy(
        record.id,
        source_path=record.active_notebook_path,
        destination_scope="workspace",
        destination_path="clean.ipynb",
        include_outputs=False,
    )

    assert source.read_bytes() == before
    assert (record.work_path / "clean.ipynb").read_text() == (
        '{"cells":[{"cell_type":"code","execution_count":null,"outputs":[]}]}'
    )


def test_reopen_reports_missing_active_notebook(tmp_path: Path):
    _state, manager, record = _workspace(tmp_path)
    (record.work_path / record.active_notebook_path).unlink()

    with pytest.raises(FileNotFoundError, match="active notebook"):
        manager.reopen(record.id)


@pytest.mark.skipif(os.name != "nt", reason="Windows path identity is case-insensitive")
def test_windows_case_aliases_share_active_guard_and_document_version(tmp_path: Path):
    state, manager, record = _workspace(tmp_path)
    active_alias = record.active_notebook_path.swapcase()

    with pytest.raises(ConflictError, match="active notebook"):
        manager.write_file(record.id, active_alias, b"replacement")
    manager.save_copy(
        record.id,
        source_path=record.active_notebook_path,
        destination_scope="workspace",
        destination_path="source-copy.ipynb",
    )
    with pytest.raises(ConflictError, match="Save a Copy"):
        manager.save_copy(
            record.id,
            source_path="source-copy.ipynb",
            destination_scope="workspace",
            destination_path=active_alias,
        )

    first = manager.write_file(record.id, "Artifact.bin", b"one")
    second = manager.write_file(
        record.id,
        "artifact.BIN",
        b"two",
        expected_version=first,
    )
    assert state.get_document_version(record.id, "ARTIFACT.bin").version == second
    with state.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM document_versions WHERE workspace_id = ?",
            (str(record.id),),
        ).fetchone()[0]
    assert count == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows path identity is case-insensitive")
def test_windows_persisted_duplicate_case_aliases_fail_closed(tmp_path: Path):
    state, _manager, record = _workspace(tmp_path)
    with state.transaction() as conn:
        conn.executemany(
            """
            INSERT INTO document_versions (
                workspace_id, path, kind, version, content_hash, updated_at
            ) VALUES (?, ?, 'file', ?, NULL, ?)
            """,
            (
                (str(record.id), "Alias.bin", "v1", record.created_at),
                (str(record.id), "alias.BIN", "v2", record.created_at),
            ),
        )

    with pytest.raises(ConflictError, match="Ambiguous persisted"):
        state.get_document_version(record.id, "ALIAS.bin")


def test_active_rename_serializes_destination_against_generic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _state, manager, record = _workspace(tmp_path)
    second_manager = WorkspaceManager(StateStore(manager.state.path), manager.root)
    rename_entered = threading.Event()
    release_rename = threading.Event()
    original = workspace_module.rename_under_root

    def held_rename(*args, **kwargs):
        rename_entered.set()
        assert release_rename.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(workspace_module, "rename_under_root", held_rename)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rename_future = pool.submit(
            manager.rename_active_notebook, record.id, "renamed.ipynb"
        )
        assert rename_entered.wait(timeout=5)
        write_future = pool.submit(
            second_manager.write_file, record.id, "renamed.ipynb", b"competitor"
        )
        assert not write_future.done()
        release_rename.set()
        assert rename_future.result(timeout=5).active_notebook_path == "renamed.ipynb"
        with pytest.raises(ConflictError, match="active notebook"):
            write_future.result(timeout=5)

    assert (record.work_path / "renamed.ipynb").read_bytes() == b'{"cells": []}'


def test_commit_failure_restores_last_good_bytes_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, manager, record = _workspace(tmp_path)
    old_version = manager.write_file(record.id, "artifact.bin", b"old")

    def fail_commit(_conn):
        raise OSError("simulated commit failure")

    monkeypatch.setattr(state, "_commit", fail_commit)
    with pytest.raises(OSError, match="simulated commit failure"):
        manager.write_file(
            record.id,
            "artifact.bin",
            b"new",
            expected_version=old_version,
        )

    assert (record.work_path / "artifact.bin").read_bytes() == b"old"
    assert state.get_document_version(record.id, "artifact.bin").version == old_version


def test_commit_failure_removes_new_file_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, manager, record = _workspace(tmp_path)

    def fail_commit(_conn):
        raise OSError("simulated commit failure")

    monkeypatch.setattr(state, "_commit", fail_commit)
    with pytest.raises(OSError, match="simulated commit failure"):
        manager.write_file(record.id, "new.bin", b"new")

    assert not (record.work_path / "new.bin").exists()
    assert state.get_document_version(record.id, "new.bin") is None


def test_commit_and_filesystem_rollback_failures_are_both_surfaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state, manager, record = _workspace(tmp_path)
    old_version = manager.write_file(record.id, "artifact.bin", b"old")
    original = workspace_module.atomic_write_under_root
    calls = 0

    def fail_commit(_conn):
        raise OSError("simulated commit failure")

    def fail_rollback(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated rollback failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(state, "_commit", fail_commit)
    monkeypatch.setattr(workspace_module, "atomic_write_under_root", fail_rollback)
    with pytest.raises(ExceptionGroup) as raised:
        manager.write_file(
            record.id,
            "artifact.bin",
            b"new",
            expected_version=old_version,
        )

    messages = {str(error) for error in raised.value.exceptions}
    assert "simulated commit failure" in messages
    assert "simulated rollback failure" in messages


def test_commit_compensation_holds_sqlite_lock_until_bytes_are_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_a, manager_a, record = _workspace(tmp_path)
    old_version = manager_a.write_file(record.id, "artifact.bin", b"old")
    state_b = StateStore(state_a.path)
    manager_b = WorkspaceManager(state_b, manager_a.root)
    compensation_entered = threading.Event()
    release_compensation = threading.Event()
    writer_b_started = threading.Event()
    original_write = workspace_module.atomic_write_under_root

    def fail_commit(_conn):
        raise OSError("simulated commit failure")

    def hold_compensation(root, path, data, **kwargs):
        if path == "artifact.bin" and data == b"old":
            compensation_entered.set()
            assert release_compensation.wait(timeout=5)
        return original_write(root, path, data, **kwargs)

    def write_b():
        writer_b_started.set()
        return manager_b.write_file(
            record.id,
            "artifact.bin",
            b"writer-b",
            expected_version=old_version,
        )

    monkeypatch.setattr(state_a, "_commit", fail_commit)
    monkeypatch.setattr(
        workspace_module, "atomic_write_under_root", hold_compensation
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_a = pool.submit(
            manager_a.write_file,
            record.id,
            "artifact.bin",
            b"writer-a",
            expected_version=old_version,
        )
        assert compensation_entered.wait(timeout=5)
        writer_b = pool.submit(write_b)
        assert writer_b_started.wait(timeout=5)
        try:
            with pytest.raises(FutureTimeoutError):
                writer_b.result(timeout=0.25)
        finally:
            release_compensation.set()
        with pytest.raises(OSError, match="simulated commit failure"):
            writer_a.result(timeout=5)
        writer_b_version = writer_b.result(timeout=5)

    stored = state_b.get_document_version(record.id, "artifact.bin")
    assert stored is not None
    assert stored.version == writer_b_version
    assert stored.content_hash == content_hash(b"writer-b")
    assert (record.work_path / "artifact.bin").read_bytes() == b"writer-b"


def test_rename_compensation_holds_sqlite_lock_until_path_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    state_a, manager_a, record = _workspace(tmp_path)
    state_b = StateStore(state_a.path)
    manager_b = WorkspaceManager(state_b, manager_a.root)
    original_bytes = (record.work_path / record.active_notebook_path).read_bytes()
    compensation_entered = threading.Event()
    release_compensation = threading.Event()
    writer_b_started = threading.Event()
    original_rename = workspace_module.rename_under_root

    def fail_commit(_conn):
        raise OSError("simulated commit failure")

    def hold_compensation(root, source, destination, **kwargs):
        if source == "moved.ipynb" and destination == record.active_notebook_path:
            compensation_entered.set()
            assert release_compensation.wait(timeout=5)
        return original_rename(root, source, destination, **kwargs)

    def write_b():
        writer_b_started.set()
        return manager_b.write_file(record.id, "moved.ipynb", b"writer-b")

    monkeypatch.setattr(state_a, "_commit", fail_commit)
    monkeypatch.setattr(workspace_module, "rename_under_root", hold_compensation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_a = pool.submit(
            manager_a.rename_active_notebook, record.id, "moved.ipynb"
        )
        assert compensation_entered.wait(timeout=5)
        writer_b = pool.submit(write_b)
        assert writer_b_started.wait(timeout=5)
        try:
            with pytest.raises(FutureTimeoutError):
                writer_b.result(timeout=0.25)
        finally:
            release_compensation.set()
        with pytest.raises(OSError, match="simulated commit failure"):
            writer_a.result(timeout=5)
        writer_b_version = writer_b.result(timeout=5)

    reopened = manager_b.reopen(record.id)
    assert reopened.active_notebook_path == record.active_notebook_path
    assert (record.work_path / record.active_notebook_path).read_bytes() == original_bytes
    assert (record.work_path / "moved.ipynb").read_bytes() == b"writer-b"
    stored = state_b.get_document_version(record.id, "moved.ipynb")
    assert stored is not None
    assert stored.version == writer_b_version
    assert stored.content_hash == content_hash(b"writer-b")
