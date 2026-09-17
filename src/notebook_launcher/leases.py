from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from .errors import WritableAgentBusy
from .state import StateStore


class WritableLeaseStore:
    def __init__(
        self,
        state: StateStore,
        *,
        stale_after_seconds: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.state = state
        self.stale_after_seconds = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def acquire(self, session_id: UUID, client_label: str | None = None) -> UUID:
        lease_id = uuid4()
        now_value = self._now()
        now = now_value.isoformat()
        stale_before = (now_value - timedelta(seconds=self.stale_after_seconds)).isoformat()
        try:
            with self.state.transaction() as conn:
                session = conn.execute(
                    "SELECT state, agent_mode FROM notebook_sessions WHERE id = ?",
                    (str(session_id),),
                ).fetchone()
                if (
                    session is None
                    or session["state"] != "ready"
                    or session["agent_mode"] != "write"
                ):
                    raise WritableAgentBusy()
                conn.execute(
                    """
                    UPDATE writable_agent_leases
                    SET released_at = ?
                    WHERE session_id = ?
                      AND released_at IS NULL
                      AND COALESCE(heartbeat_at, acquired_at) < ?
                    """,
                    (now, str(session_id), stale_before),
                )
                conn.execute(
                    """
                    INSERT INTO writable_agent_leases
                        (id, session_id, client_label, acquired_at, heartbeat_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(lease_id), str(session_id), client_label, now, now),
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
                (self._now().isoformat(), str(lease_id)),
            )

    def heartbeat(self, lease_id: UUID) -> bool:
        with self.state.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE writable_agent_leases
                SET heartbeat_at = ?
                WHERE id = ? AND released_at IS NULL
                """,
                (self._now().isoformat(), str(lease_id)),
            )
        return cursor.rowcount == 1

    def invalidate_session(self, session_id: UUID) -> None:
        with self.state.transaction() as conn:
            conn.execute(
                """
                UPDATE writable_agent_leases
                SET released_at = COALESCE(released_at, ?)
                WHERE session_id = ?
                """,
                (self._now().isoformat(), str(session_id)),
            )

    def reconcile(self) -> int:
        """Release leases whose session stopped or whose heartbeat expired."""
        now_value = self._now()
        now = now_value.isoformat()
        stale_before = (now_value - timedelta(seconds=self.stale_after_seconds)).isoformat()
        with self.state.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE writable_agent_leases
                SET released_at = COALESCE(released_at, ?)
                WHERE released_at IS NULL
                  AND (
                      COALESCE(heartbeat_at, acquired_at) < ?
                      OR session_id IN (
                          SELECT id FROM notebook_sessions
                          WHERE state NOT IN ('starting','ready','stopping')
                      )
                  )
                """,
                (now, stale_before),
            )
        return cursor.rowcount

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("lease clock must return a timezone-aware datetime")
        return value.astimezone(UTC)
