from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from .state import StateStore
from .workspace import canonical_grant_root


@dataclass(frozen=True, slots=True)
class GrantRecord:
    id: UUID
    workspace_id: UUID
    display_path: Path
    canonical_root: Path
    mode: str
    created_at: datetime
    revoked_at: datetime | None = None


class UserDataGrantStore:
    def __init__(self, state: StateStore) -> None:
        self.state = state
        with self.state.connect() as conn:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_user_data_grant
                ON user_data_grants(workspace_id)
                WHERE revoked_at IS NULL
                """
            )

    def grant(
        self,
        workspace_id: UUID,
        path: Path,
        *,
        mode: str,
        home: Path | None = None,
    ) -> GrantRecord:
        if mode not in {"ro", "rw"}:
            raise ValueError("mode must be ro or rw")
        canonical = canonical_grant_root(path, home=home)
        grant_id = uuid4()
        now = datetime.now(UTC)
        try:
            with self.state.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO user_data_grants (
                        id, workspace_id, display_path, canonical_root,
                        container_path, mode, created_at
                    ) VALUES (?, ?, ?, ?, '/mnt/user-data', ?, ?)
                    """,
                    (
                        str(grant_id),
                        str(workspace_id),
                        str(path),
                        str(canonical),
                        mode,
                        now.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("workspace already has an active data grant or does not exist") from exc
        return GrantRecord(
            id=grant_id,
            workspace_id=workspace_id,
            display_path=path,
            canonical_root=canonical,
            mode=mode,
            created_at=now,
        )

    def active(self, workspace_id: UUID) -> GrantRecord | None:
        with self.state.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_data_grants
                WHERE workspace_id = ? AND revoked_at IS NULL
                """,
                (str(workspace_id),),
            ).fetchone()
        if row is None:
            return None
        return GrantRecord(
            id=UUID(row["id"]),
            workspace_id=UUID(row["workspace_id"]),
            display_path=Path(row["display_path"]),
            canonical_root=Path(row["canonical_root"]),
            mode=row["mode"],
            created_at=datetime.fromisoformat(row["created_at"]),
            revoked_at=None,
        )

    def revalidate(self, workspace_id: UUID) -> GrantRecord | None:
        record = self.active(workspace_id)
        if record is None:
            return None
        current = record.display_path.expanduser().resolve(strict=True)
        if current != record.canonical_root or not current.is_dir():
            raise RuntimeError("granted directory identity changed or became unavailable")
        return record

    def revoke(self, workspace_id: UUID) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.state.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE user_data_grants
                SET revoked_at = ?
                WHERE workspace_id = ? AND revoked_at IS NULL
                """,
                (now, str(workspace_id)),
            )
        return cursor.rowcount == 1
