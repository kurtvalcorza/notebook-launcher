from datetime import UTC, datetime
from uuid import uuid4

import pytest

from notebook_launcher.errors import WritableAgentBusy
from notebook_launcher.leases import WritableLeaseStore


def _insert_session(state_store):
    workspace_id = uuid4()
    session_id = uuid4()
    now = datetime.now(UTC).isoformat()
    with state_store.transaction() as conn:
        conn.execute(
            """
            INSERT INTO workspaces (
                id, source_repository_id, source_owner, source_repository,
                source_commit_sha, source_notebook_path, root_path, work_path,
                outputs_path, active_notebook_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(workspace_id), 1, "owner", "repo", "a" * 40,
                "x.ipynb", "/tmp/root", "/tmp/work", "/tmp/out",
                "x.ipynb", now, now,
            ),
        )
        conn.execute(
            """
            INSERT INTO notebook_sessions (
                id, workspace_id, runtime_id, host_port, gpu_enabled,
                agent_mode, state, notebook_url, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session_id), str(workspace_id), "runtime", 9001, 0,
                "write", "ready", "http://127.0.0.1:9001", now,
            ),
        )
    return session_id


def test_only_one_writable_lease_per_session(state_store):
    session_id = _insert_session(state_store)
    leases = WritableLeaseStore(state_store)
    first = leases.acquire(session_id, "agent-a")
    with pytest.raises(WritableAgentBusy):
        leases.acquire(session_id, "agent-b")
    leases.release(first)
    second = leases.acquire(session_id, "agent-b")
    assert second != first
