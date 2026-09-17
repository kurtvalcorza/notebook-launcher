from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .errors import ConflictError, WorkspaceActive


def document_path_key(path: str) -> str:
    """Return the filesystem identity key used for optimistic concurrency."""
    return path.casefold() if os.name == "nt" else path


USER_DATA_GRANTS_TABLE = """
CREATE TABLE IF NOT EXISTS user_data_grants (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    display_path TEXT NOT NULL,
    canonical_root TEXT NOT NULL,
    root_dev TEXT,
    root_ino TEXT,
    container_path TEXT NOT NULL DEFAULT '/mnt/user-data',
    mode TEXT NOT NULL CHECK (mode IN ('ro','rw')),
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
"""

USER_DATA_GRANTS_ACTIVE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_user_data_grant
ON user_data_grants(workspace_id)
WHERE revoked_at IS NULL;
"""


SCHEMA = f"""
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
    session_id TEXT,
    message TEXT,
    error_code TEXT,
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

{USER_DATA_GRANTS_TABLE}

{USER_DATA_GRANTS_ACTIVE_INDEX}

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    lease_id TEXT,
    operation_id TEXT,
    operation TEXT NOT NULL,
    target TEXT,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    outcome TEXT,
    error_type TEXT
);
"""


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    id: UUID
    source_repository_id: int
    source_owner: str
    source_repository: str
    source_commit_sha: str
    source_notebook_path: str
    root_path: Path
    work_path: Path
    outputs_path: Path
    active_notebook_path: str
    user_data_grant_id: UUID | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: UUID
    workspace_id: UUID
    runtime_id: str
    host_port: int
    gpu_enabled: bool
    agent_mode: str
    sandbox_verified: bool
    network_verified: bool
    collaboration_ready: bool
    state: str
    notebook_url: str
    started_at: str
    stopped_at: str | None


@dataclass(frozen=True, slots=True)
class DocumentVersionRecord:
    workspace_id: UUID
    path: str
    kind: str
    version: str
    content_hash: str | None
    updated_at: str


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "user_data_grants", "root_dev", "TEXT")
            self._ensure_column(conn, "user_data_grants", "root_ino", "TEXT")
            self._ensure_column(conn, "audit_events", "operation_id", "TEXT")
            self._ensure_column(conn, "launches", "session_id", "TEXT")
            self._ensure_column(conn, "launches", "message", "TEXT")
            self._ensure_column(conn, "launches", "error_code", "TEXT")
            self._migrate_user_data_identity_to_text(conn)

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _migrate_user_data_identity_to_text(conn: sqlite3.Connection) -> None:
        columns = {
            row[1]: row[2].upper()
            for row in conn.execute("PRAGMA table_info(user_data_grants)")
        }
        if columns.get("root_dev") == "TEXT" and columns.get("root_ino") == "TEXT":
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP INDEX IF EXISTS ux_one_active_user_data_grant")
            conn.execute(
                "ALTER TABLE user_data_grants RENAME TO user_data_grants_legacy"
            )
            conn.execute(USER_DATA_GRANTS_TABLE)
            conn.execute(
                """
                INSERT INTO user_data_grants (
                    id, workspace_id, display_path, canonical_root,
                    root_dev, root_ino, container_path, mode, created_at, revoked_at
                )
                SELECT
                    id, workspace_id, display_path, canonical_root,
                    CAST(root_dev AS TEXT), CAST(root_ino AS TEXT),
                    container_path, mode, created_at, revoked_at
                FROM user_data_grants_legacy
                """
            )
            conn.execute("DROP TABLE user_data_grants_legacy")
            conn.execute(USER_DATA_GRANTS_ACTIVE_INDEX)
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

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
    def transaction(
        self,
        *,
        rollback_compensation: Callable[[], None] | None = None,
    ) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception as exc:
                self._compensate_before_rollback(
                    conn,
                    original=exc,
                    compensation=rollback_compensation,
                )
                conn.rollback()
                raise
            else:
                try:
                    self._commit(conn)
                except Exception as exc:
                    self._compensate_before_rollback(
                        conn,
                        original=exc,
                        compensation=rollback_compensation,
                    )
                    conn.rollback()
                    raise

    @staticmethod
    def _commit(conn: sqlite3.Connection) -> None:
        conn.commit()

    @staticmethod
    def _compensate_before_rollback(
        conn: sqlite3.Connection,
        *,
        original: Exception,
        compensation: Callable[[], None] | None,
    ) -> None:
        if compensation is None:
            return
        try:
            compensation()
        except Exception as compensation_exc:  # noqa: BLE001 - preserve both failures
            conn.rollback()
            raise ExceptionGroup(
                "transaction and filesystem compensation both failed",
                [original, compensation_exc],
            ) from original

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

    def update_launch_state(
        self,
        launch_id: str,
        *,
        expected_states: tuple[str, ...],
        new_state: str,
        now: str,
        workspace_id: UUID | None = None,
        session_id: UUID | None = None,
        message: str | None = None,
        error_code: str | None = None,
    ) -> None:
        if not expected_states:
            raise ValueError("expected_states must not be empty")
        placeholders = ",".join("?" for _ in expected_states)
        with self.transaction() as conn:
            cursor = conn.execute(
                f"""
                UPDATE launches
                SET state = ?, updated_at = ?,
                    workspace_id = COALESCE(?, workspace_id),
                    session_id = COALESCE(?, session_id),
                    message = ?, error_code = ?
                WHERE id = ? AND state IN ({placeholders})
                """,
                (
                    new_state,
                    now,
                    str(workspace_id) if workspace_id is not None else None,
                    str(session_id) if session_id is not None else None,
                    message,
                    error_code,
                    launch_id,
                    *expected_states,
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError("Launch state changed; refresh and retry.")

    def create_workspace(
        self,
        *,
        workspace_id: UUID,
        source_repository_id: int,
        source_owner: str,
        source_repository: str,
        source_commit_sha: str,
        source_notebook_path: str,
        root_path: Path,
        work_path: Path,
        outputs_path: Path,
        active_notebook_path: str,
        now: str,
    ) -> WorkspaceRecord:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workspaces (
                    id, source_repository_id, source_owner, source_repository,
                    source_commit_sha, source_notebook_path, root_path, work_path,
                    outputs_path, active_notebook_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(workspace_id),
                    source_repository_id,
                    source_owner,
                    source_repository,
                    source_commit_sha,
                    source_notebook_path,
                    str(root_path),
                    str(work_path),
                    str(outputs_path),
                    active_notebook_path,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (str(workspace_id),)
            ).fetchone()
        assert row is not None
        return _workspace_record(row)

    def get_workspace(self, workspace_id: UUID) -> WorkspaceRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (str(workspace_id),)
            ).fetchone()
        return _workspace_record(row) if row is not None else None

    def update_active_notebook(
        self,
        workspace_id: UUID,
        *,
        expected_path: str,
        new_path: str,
        now: str,
    ) -> WorkspaceRecord:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE workspaces
                SET active_notebook_path = ?, updated_at = ?
                WHERE id = ? AND active_notebook_path = ?
                """,
                (new_path, now, str(workspace_id), expected_path),
            )
            if cursor.rowcount != 1:
                raise ConflictError("Active notebook path changed; refresh and retry.")
            conn.execute(
                """
                UPDATE document_versions
                SET path = ?, updated_at = ?
                WHERE workspace_id = ? AND path = ? AND kind = 'notebook'
                """,
                (new_path, now, str(workspace_id), expected_path),
            )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (str(workspace_id),)
            ).fetchone()
        assert row is not None
        return _workspace_record(row)

    @contextmanager
    def active_notebook_rename_guard(
        self,
        workspace_id: UUID,
        *,
        expected_path: str,
        new_path: str,
        now: str,
        rollback_compensation: Callable[[], None] | None = None,
    ) -> Iterator[str]:
        """Serialize an active rename with every versioned workspace write."""
        with self.transaction(
            rollback_compensation=rollback_compensation
        ) as conn:
            workspace = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?", (str(workspace_id),)
            ).fetchone()
            if workspace is None:
                raise KeyError(str(workspace_id))
            current_path = workspace["active_notebook_path"]
            if document_path_key(current_path) != document_path_key(expected_path):
                raise ConflictError("Active notebook path changed; refresh and retry.")
            if document_path_key(current_path) == document_path_key(new_path):
                raise ConflictError("Active notebook destination must be distinct.")

            source_row = self._document_version_row(
                conn, workspace_id=workspace_id, path=current_path
            )
            destination_row = self._document_version_row(
                conn, workspace_id=workspace_id, path=new_path
            )
            if destination_row is not None:
                raise ConflictError("Destination already has an optimistic version.")

            yield current_path

            cursor = conn.execute(
                """
                UPDATE workspaces
                SET active_notebook_path = ?, updated_at = ?
                WHERE id = ? AND active_notebook_path = ?
                """,
                (new_path, now, str(workspace_id), current_path),
            )
            if cursor.rowcount != 1:
                raise ConflictError("Active notebook path changed; refresh and retry.")
            if source_row is not None:
                conn.execute(
                    """
                    UPDATE document_versions
                    SET path = ?, updated_at = ?
                    WHERE workspace_id = ? AND path = ?
                    """,
                    (new_path, now, str(workspace_id), source_row["path"]),
                )

    def active_session(self, workspace_id: UUID) -> SessionRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM notebook_sessions
                WHERE workspace_id = ?
                  AND state IN ('starting','ready','stopping')
                """,
                (str(workspace_id),),
            ).fetchone()
        return _session_record(row) if row is not None else None

    def get_session(self, session_id: UUID) -> SessionRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM notebook_sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
        return _session_record(row) if row is not None else None

    def sessions_in_states(self, states: tuple[str, ...]) -> tuple[SessionRecord, ...]:
        if not states:
            return ()
        placeholders = ",".join("?" for _ in states)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM notebook_sessions WHERE state IN ({placeholders})",
                states,
            ).fetchall()
        return tuple(_session_record(row) for row in rows)

    def fail_launches_for_session(
        self,
        session_id: UUID,
        *,
        now: str,
        message: str,
        error_code: str,
    ) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE launches
                SET state = 'failed', updated_at = ?, message = ?, error_code = ?
                WHERE session_id = ? AND state = 'starting'
                """,
                (now, message, error_code, str(session_id)),
            )
        return cursor.rowcount

    def release_session_leases(self, session_id: UUID, *, now: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE writable_agent_leases
                SET released_at = COALESCE(released_at, ?)
                WHERE session_id = ?
                """,
                (now, str(session_id)),
            )

    def claim_session(
        self,
        *,
        session_id: UUID,
        workspace_id: UUID,
        runtime_id: str,
        host_port: int,
        gpu_enabled: bool,
        agent_mode: str,
        notebook_url: str,
        now: str,
        reuse_existing: bool = True,
    ) -> tuple[SessionRecord, bool]:
        with self.transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM notebook_sessions
                WHERE workspace_id = ?
                  AND state IN ('starting','ready','stopping')
                """,
                (str(workspace_id),),
            ).fetchone()
            if existing is not None:
                if reuse_existing:
                    return _session_record(existing), False
                raise WorkspaceActive()
            try:
                conn.execute(
                    """
                    INSERT INTO notebook_sessions (
                        id, workspace_id, runtime_id, host_port, gpu_enabled,
                        agent_mode, state, notebook_url, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'starting', ?, ?)
                    """,
                    (
                        str(session_id),
                        str(workspace_id),
                        runtime_id,
                        host_port,
                        int(gpu_enabled),
                        agent_mode,
                        notebook_url,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkspaceActive() from exc
            row = conn.execute(
                "SELECT * FROM notebook_sessions WHERE id = ?", (str(session_id),)
            ).fetchone()
        assert row is not None
        return _session_record(row), True

    def transition_session(
        self,
        session_id: UUID,
        *,
        expected_states: tuple[str, ...],
        new_state: str,
        stopped_at: str | None = None,
        sandbox_verified: bool | None = None,
        network_verified: bool | None = None,
        collaboration_ready: bool | None = None,
    ) -> SessionRecord:
        if not expected_states:
            raise ValueError("expected_states must not be empty")
        placeholders = ",".join("?" for _ in expected_states)
        assignments = ["state = ?", "stopped_at = COALESCE(?, stopped_at)"]
        values: list[object] = [new_state, stopped_at]
        for column, value in (
            ("sandbox_verified", sandbox_verified),
            ("network_verified", network_verified),
            ("collaboration_ready", collaboration_ready),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(int(value))
        values.extend((str(session_id), *expected_states))
        with self.transaction() as conn:
            cursor = conn.execute(
                f"""
                UPDATE notebook_sessions
                SET {', '.join(assignments)}
                WHERE id = ? AND state IN ({placeholders})
                """,
                values,
            )
            if cursor.rowcount != 1:
                raise ConflictError("Session state changed; refresh and retry.")
            row = conn.execute(
                "SELECT * FROM notebook_sessions WHERE id = ?", (str(session_id),)
            ).fetchone()
        assert row is not None
        return _session_record(row)

    def get_document_version(
        self, workspace_id: UUID, path: str
    ) -> DocumentVersionRecord | None:
        with self.connect() as conn:
            row = self._document_version_row(
                conn, workspace_id=workspace_id, path=path
            )
        return _document_version_record(row) if row is not None else None

    @staticmethod
    def _document_version_row(
        conn: sqlite3.Connection,
        *,
        workspace_id: UUID,
        path: str,
    ) -> sqlite3.Row | None:
        rows = conn.execute(
            "SELECT * FROM document_versions WHERE workspace_id = ?",
            (str(workspace_id),),
        ).fetchall()
        key = document_path_key(path)
        matches = [row for row in rows if document_path_key(row["path"]) == key]
        if len(matches) > 1:
            raise ConflictError(
                "Ambiguous persisted document path aliases; repair state before retrying."
            )
        return matches[0] if matches else None

    @contextmanager
    def document_write_guard(
        self,
        *,
        workspace_id: UUID,
        path: str,
        kind: str,
        version: str,
        content_digest: str | None,
        expected_version: str | None,
        now: str,
        allow_active_notebook: bool = False,
        rollback_compensation: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        """Serialize a file replacement with its optimistic version update."""
        with self.transaction(
            rollback_compensation=rollback_compensation
        ) as conn:
            workspace = conn.execute(
                "SELECT active_notebook_path FROM workspaces WHERE id = ?",
                (str(workspace_id),),
            ).fetchone()
            if workspace is None:
                raise KeyError(str(workspace_id))
            if (
                not allow_active_notebook
                and document_path_key(path)
                == document_path_key(workspace["active_notebook_path"])
            ):
                raise ConflictError(
                    "The active notebook must be changed through notebook-aware "
                    "operations."
                )
            row = self._document_version_row(
                conn, workspace_id=workspace_id, path=path
            )
            if row is None:
                if expected_version is not None:
                    raise ConflictError()
            elif expected_version is None or row["version"] != expected_version:
                raise ConflictError()
            yield
            if row is None:
                conn.execute(
                    """
                    INSERT INTO document_versions (
                        workspace_id, path, kind, version, content_hash, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(workspace_id),
                        path,
                        kind,
                        version,
                        content_digest,
                        now,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE document_versions
                    SET kind = ?, version = ?, content_hash = ?, updated_at = ?
                    WHERE workspace_id = ? AND path = ? AND version = ?
                    """,
                    (
                        kind,
                        version,
                        content_digest,
                        now,
                        str(workspace_id),
                        row["path"],
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConflictError()

    def set_document_version(
        self,
        *,
        workspace_id: UUID,
        path: str,
        kind: str,
        version: str,
        content_digest: str | None,
        expected_version: str | None,
        now: str,
    ) -> DocumentVersionRecord:
        with self.transaction() as conn:
            row = self._document_version_row(
                conn, workspace_id=workspace_id, path=path
            )
            if row is None:
                if expected_version is not None:
                    raise ConflictError()
                conn.execute(
                    """
                    INSERT INTO document_versions (
                        workspace_id, path, kind, version, content_hash, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(workspace_id),
                        path,
                        kind,
                        version,
                        content_digest,
                        now,
                    ),
                )
            else:
                if expected_version is None or row["version"] != expected_version:
                    raise ConflictError()
                conn.execute(
                    """
                    UPDATE document_versions
                    SET kind = ?, version = ?, content_hash = ?, updated_at = ?
                    WHERE workspace_id = ? AND path = ? AND version = ?
                    """,
                    (
                        kind,
                        version,
                        content_digest,
                        now,
                        str(workspace_id),
                        row["path"],
                        expected_version,
                    ),
                )
            updated = self._document_version_row(
                conn, workspace_id=workspace_id, path=path
            )
        assert updated is not None
        return _document_version_record(updated)


def _workspace_record(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=UUID(row["id"]),
        source_repository_id=row["source_repository_id"],
        source_owner=row["source_owner"],
        source_repository=row["source_repository"],
        source_commit_sha=row["source_commit_sha"],
        source_notebook_path=row["source_notebook_path"],
        root_path=Path(row["root_path"]),
        work_path=Path(row["work_path"]),
        outputs_path=Path(row["outputs_path"]),
        active_notebook_path=row["active_notebook_path"],
        user_data_grant_id=(
            UUID(row["user_data_grant_id"])
            if row["user_data_grant_id"] is not None
            else None
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _session_record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=UUID(row["id"]),
        workspace_id=UUID(row["workspace_id"]),
        runtime_id=row["runtime_id"],
        host_port=row["host_port"],
        gpu_enabled=bool(row["gpu_enabled"]),
        agent_mode=row["agent_mode"],
        sandbox_verified=bool(row["sandbox_verified"]),
        network_verified=bool(row["network_verified"]),
        collaboration_ready=bool(row["collaboration_ready"]),
        state=row["state"],
        notebook_url=row["notebook_url"],
        started_at=row["started_at"],
        stopped_at=row["stopped_at"],
    )


def _document_version_record(row: sqlite3.Row) -> DocumentVersionRecord:
    return DocumentVersionRecord(
        workspace_id=UUID(row["workspace_id"]),
        path=row["path"],
        kind=row["kind"],
        version=row["version"],
        content_hash=row["content_hash"],
        updated_at=row["updated_at"],
    )
