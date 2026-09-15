from datetime import UTC, datetime
from uuid import uuid4

import sqlite3


def test_launch_authorization_jti_is_one_time(state_store):
    assert state_store.consume_launch_jti(
        jti_hash="abc",
        request_digest="digest",
        consumed_at="2026-09-16T00:00:00Z",
    )
    assert not state_store.consume_launch_jti(
        jti_hash="abc",
        request_digest="digest",
        consumed_at="2026-09-16T00:00:01Z",
    )


def test_only_one_active_session_per_workspace(state_store):
    workspace_id = str(uuid4())
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
                workspace_id, 1, "owner", "repo", "a" * 40, "x.ipynb",
                "/tmp/root", "/tmp/work", "/tmp/out", "x.ipynb", now, now,
            ),
        )
        conn.execute(
            """
            INSERT INTO notebook_sessions (
                id, workspace_id, runtime_id, host_port, gpu_enabled,
                agent_mode, state, notebook_url, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid4()), workspace_id, "r1", 9001, 0, "write", "ready", "http://127.0.0.1:9001", now),
        )
    with state_store.transaction() as conn:
        try:
            conn.execute(
                """
                INSERT INTO notebook_sessions (
                    id, workspace_id, runtime_id, host_port, gpu_enabled,
                    agent_mode, state, notebook_url, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), workspace_id, "r2", 9002, 0, "write", "starting", "http://127.0.0.1:9002", now),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("second active session was accepted")
