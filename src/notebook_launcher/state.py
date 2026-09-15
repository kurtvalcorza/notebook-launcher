from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS launch_authorization_replay (
    jti_hash TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    consumed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS launches (
    id TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    repository_id INTEGER,
    commit_sha TEXT,
    notebook_path TEXT,
    workspace_id TEXT,
    gpu TEXT NOT NULL CHECK (gpu IN ('auto','on','off')),
    agent_mode TEXT NOT NULL CHECK (agent_mode IN ('write','readonly')),
    trust_covered INTEGER NOT NULL CHECK (trust_covered IN (0,1)),
    state TEXT NOT NULL CHECK (
        state IN ('pending_trust','authorized','starting','ready','failed','stopped')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_records (
    id TEXT PRIMARY KEY,
    repository_id INTEGER NOT NULL,
    repository_node_id TEXT,
    grant_owner TEXT NOT NULL,
    grant_repository TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('exact_commit','repository')),
    commit_sha TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    CHECK (
        (scope = 'exact_commit' AND commit_sha IS NOT NULL)
        OR
        (scope = 'repository' AND commit_sha IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_active_repository_trust
ON trust_records(repository_id)
WHERE scope = 'repository' AND revoked_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_active_exact_commit_trust
ON trust_records(repository_id, commit_sha)
WHERE scope = 'exact_commit' AND revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    source_repository_id INTEGER NOT NULL,
    source_owner TEXT NOT NULL,
    source_repository TEXT NOT NULL,
    source_commit_sha TEXT NOT NULL,
    source_notebook_path TEXT NOT NULL,
    root_path TEXT NOT NULL,
    work_path TEXT NOT NULL,
    outputs_path TEXT NOT NULL,
    active_notebook_path TEXT NOT NULL,
    user_data_grant_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notebook_sessions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    runtime_id TEXT NOT NULL,
    host_port INTEGER NOT NULL,
    gpu_enabled INTEGER NOT NULL CHECK (gpu_enabled IN (0,1)),
    agent_mode TEXT NOT NULL CHECK (agent_mode IN ('write','readonly')),
    sandbox_verified INTEGER NOT NULL DEFAULT 0,
    network_verified INTEGER NOT NULL DEFAULT 0,
    collaboration_ready INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL CHECK (
        state IN ('starting','ready','stopping','stopped','failed')
    ),
    notebook_url TEXT NOT NULL,
    started_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_session_per_workspace
ON notebook_sessions(workspace_id)
WHERE state IN ('starting','ready','stopping');

CREATE TABLE IF NOT EXISTS writable_agent_leases (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES notebook_sessions(id),
    client_label TEXT,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT,
    released_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_writable_lease
ON writable_agent_leases(session_id)
WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS document_versions (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('notebook','file')),
    version TEXT NOT NULL,
    content_hash TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, path)
);

CREATE TABLE IF NOT EXISTS user_data_grants (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    display_path TEXT NOT NULL,
    canonical_root TEXT NOT NULL,
    container_path TEXT NOT NULL DEFAULT '/mnt/user-data',
    mode TEXT NOT NULL CHECK (mode IN ('ro','rw')),
    created_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    lease_id TEXT,
    operation TEXT NOT NULL,
    target TEXT,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    outcome TEXT,
    error_type TEXT
);
"""


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def consume_launch_jti(
        self,
        *,
        jti_hash: str,
        request_digest: str,
        consumed_at: str,
    ) -> bool:
        try:
            with self.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO launch_authorization_replay
                        (jti_hash, request_digest, consumed_at)
                    VALUES (?, ?, ?)
                    """,
                    (jti_hash, request_digest, consumed_at),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def create_launch(
        self,
        *,
        request_digest: str,
        repository_id: int | None,
        commit_sha: str | None,
        notebook_path: str | None,
        workspace_id: str | None,
        gpu: str,
        agent_mode: str,
        trust_covered: bool,
        state: str,
        now: str,
    ) -> str:
        launch_id = str(uuid4())
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO launches (
                    id, request_digest, repository_id, commit_sha,
                    notebook_path, workspace_id, gpu, agent_mode,
                    trust_covered, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    launch_id,
                    request_digest,
                    repository_id,
                    commit_sha,
                    notebook_path,
                    workspace_id,
                    gpu,
                    agent_mode,
                    int(trust_covered),
                    state,
                    now,
                    now,
                ),
            )
        return launch_id

    def get_launch(self, launch_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM launches WHERE id = ?",
                (launch_id,),
            ).fetchone()
