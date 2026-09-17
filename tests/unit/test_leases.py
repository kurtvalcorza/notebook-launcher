from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
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


def test_only_one_concurrent_writable_lease_wins(state_store):
    session_id = _insert_session(state_store)
    leases = WritableLeaseStore(state_store)

    def acquire(index):
        try:
            return leases.acquire(session_id, f"agent-{index}")
        except WritableAgentBusy:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire, range(8)))

    assert sum(result is not None for result in results) == 1


def test_reconcile_releases_stopped_session_lease(state_store):
    session_id = _insert_session(state_store)
    leases = WritableLeaseStore(state_store)
    lease_id = leases.acquire(session_id)
    with state_store.transaction() as conn:
        conn.execute(
            "UPDATE notebook_sessions SET state = 'stopped' WHERE id = ?",
            (str(session_id),),
        )

    assert leases.reconcile() == 1
    assert not leases.heartbeat(lease_id)


def test_stale_crashed_lease_is_reclaimed_atomically(state_store):
    session_id = _insert_session(state_store)
    current = datetime(2026, 1, 1, tzinfo=UTC)
    leases = WritableLeaseStore(
        state_store,
        stale_after_seconds=30,
        clock=lambda: current,
    )
    crashed = leases.acquire(session_id, "crashed-agent")

    current += timedelta(seconds=31)
    replacement = leases.acquire(session_id, "replacement-agent")

    assert replacement != crashed
    assert not leases.heartbeat(crashed)
    assert leases.heartbeat(replacement)


def test_recent_heartbeat_prevents_lease_reclamation(state_store):
    session_id = _insert_session(state_store)
    current = datetime(2026, 1, 1, tzinfo=UTC)
    leases = WritableLeaseStore(
        state_store,
        stale_after_seconds=30,
        clock=lambda: current,
    )
    lease_id = leases.acquire(session_id, "live-agent")

    current += timedelta(seconds=20)
    assert leases.heartbeat(lease_id)
    current += timedelta(seconds=20)

    with pytest.raises(WritableAgentBusy):
        leases.acquire(session_id, "other-agent")
