from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from notebook_launcher.grants import UserDataGrantStore
from notebook_launcher.state import StateStore


def insert_workspace(state: StateStore, workspace_id):
    now = datetime.now(UTC).isoformat()
    with state.transaction() as conn:
        conn.execute(
            """
            INSERT INTO workspaces (
                id, source_repository_id, source_owner, source_repository,
                source_commit_sha, source_notebook_path, root_path, work_path,
                outputs_path, active_notebook_path, created_at, updated_at
            ) VALUES (?, 1, 'owner', 'repo', ?, 'a.ipynb', ?, ?, ?, 'a.ipynb', ?, ?)
            """,
            (
                str(workspace_id),
                "a" * 40,
                "/tmp/root",
                "/tmp/work",
                "/tmp/out",
                now,
                now,
            ),
        )


def test_grant_persists_canonical_root_and_revokes(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    workspace_id = uuid4()
    insert_workspace(state, workspace_id)
    data = tmp_path / "data"
    data.mkdir()
    store = UserDataGrantStore(state)

    grant = store.grant(workspace_id, data, mode="rw", home=tmp_path / "not-home")
    active = store.active(workspace_id)

    assert active is not None
    assert active.id == grant.id
    assert active.canonical_root == data.resolve()
    assert active.mode == "rw"
    assert store.revoke(workspace_id)
    assert store.active(workspace_id) is None


def test_second_active_grant_is_rejected(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    workspace_id = uuid4()
    insert_workspace(state, workspace_id)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = UserDataGrantStore(state)
    store.grant(workspace_id, first, mode="ro")

    with pytest.raises(ValueError, match="active data grant"):
        store.grant(workspace_id, second, mode="rw")


def test_grant_revalidation_detects_replaced_path(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    workspace_id = uuid4()
    insert_workspace(state, workspace_id)
    path = tmp_path / "data"
    path.mkdir()
    store = UserDataGrantStore(state)
    store.grant(workspace_id, path, mode="ro")

    original = tmp_path / "original"
    path.rename(original)
    path.mkdir()

    with pytest.raises(RuntimeError, match="identity changed"):
        store.revalidate(workspace_id)
