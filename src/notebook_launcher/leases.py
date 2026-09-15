from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .errors import WritableAgentBusy
from .state import StateStore


class WritableLeaseStore:
    def __init__(self, state: StateStore):
        self.state = state

    def acquire(self, session_id: UUID, client_label: str | None = None) -> UUID:
        lease_id = uuid4()
        now = datetime.now(UTC).isoformat()
        try:
            with self.state.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO writable_agent_leases
                        (id, session_id, client_label, acquired_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(lease_id), str(session_id), client_label, now),
                )
        except sqlite3.IntegrityError as exc:
            raise WritableAgentBusy() from exc
        return lease_id

    def release(self, lease_id: UUID) -> None:
        with self.state.transaction() as conn:
            conn.execute(
                """
                UPDATE writable_agent_leases
                SET released_at = ?
                WHERE id = ? AND released_at IS NULL
                """,
                (datetime.now(UTC).isoformat(), str(lease_id)),
            )

    def invalidate_session(self, session_id: UUID) -> None:
        with self.state.transaction() as conn:
            conn.execute(
                """
                UPDATE writable_agent_leases
                SET released_at = COALESCE(released_at, ?)
                WHERE session_id = ?
                """,
                (datetime.now(UTC).isoformat(), str(session_id)),
            )
