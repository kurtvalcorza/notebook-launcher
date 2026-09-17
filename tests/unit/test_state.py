import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from notebook_launcher.state import StateStore


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


def test_initialize_migrates_grant_identity_columns_to_text(tmp_path):
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    with state.connect() as conn:
        conn.execute(
            """
            INSERT INTO workspaces (
                id, source_repository_id, source_owner, source_repository,
                source_commit_sha, source_notebook_path, root_path, work_path,
                outputs_path, active_notebook_path, created_at, updated_at
            ) VALUES (
                'workspace', 1, 'owner', 'repo', 'commit', 'notebook.ipynb',
                'root', 'work', 'outputs', 'notebook.ipynb', 'now', 'now'
            )
            """
        )
        conn.execute("DROP TABLE user_data_grants")
        conn.executescript(
            """
            CREATE TABLE user_data_grants (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                display_path TEXT NOT NULL,
                canonical_root TEXT NOT NULL,
                root_dev INTEGER,
                root_ino INTEGER,
                container_path TEXT NOT NULL DEFAULT '/mnt/user-data',
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE UNIQUE INDEX ux_one_active_user_data_grant
            ON user_data_grants(workspace_id)
            WHERE revoked_at IS NULL;
            INSERT INTO user_data_grants (
                id, workspace_id, display_path, canonical_root,
                root_dev, root_ino, mode, created_at
            ) VALUES ('grant', 'workspace', 'display', 'canonical', 12, 34, 'ro', 'now');
            """
        )

    state.initialize()

    with state.connect() as conn:
        columns = {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(user_data_grants)")
        }
        row = conn.execute(
            "SELECT root_dev, root_ino, typeof(root_dev), typeof(root_ino) "
            "FROM user_data_grants"
        ).fetchone()
        indexes = {
            index[1] for index in conn.execute("PRAGMA index_list(user_data_grants)")
        }
    assert columns["root_dev"] == "TEXT"
    assert columns["root_ino"] == "TEXT"
    assert tuple(row) == ("12", "34", "text", "text")
    assert "ux_one_active_user_data_grant" in indexes


def test_claim_session_is_transactional_under_concurrency(state_store):
    workspace_id = uuid4()
    now = datetime.now(UTC).isoformat()
    root = state_store.path.parent / "root"
    state_store.create_workspace(
        workspace_id=workspace_id,
        source_repository_id=1,
        source_owner="owner",
        source_repository="repo",
        source_commit_sha="a" * 40,
        source_notebook_path="x.ipynb",
        root_path=root,
        work_path=root / "work",
        outputs_path=root / "outputs",
        active_notebook_path="x.ipynb",
        now=now,
    )

    def claim(index):
        return state_store.claim_session(
            session_id=uuid4(),
            workspace_id=workspace_id,
            runtime_id=f"runtime-{index}",
            host_port=9000 + index,
            gpu_enabled=False,
            agent_mode="write",
            notebook_url=f"http://127.0.0.1:{9000 + index}",
            now=now,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))

    assert sum(created for _session, created in results) == 1
    assert len({session.id for session, _created in results}) == 1


def test_initialize_adds_backward_compatible_audit_operation_id(tmp_path):
    state = StateStore(tmp_path / "legacy.db")
    with state.connect() as conn:
        conn.execute(
            """
            CREATE TABLE audit_events (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                lease_id TEXT,
                operation TEXT NOT NULL,
                target TEXT,
                started_at TEXT NOT NULL,
                duration_ms INTEGER,
                outcome TEXT,
                error_type TEXT
            )
            """
        )

    state.initialize()

    with state.connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_events)")}
    assert "operation_id" in columns
