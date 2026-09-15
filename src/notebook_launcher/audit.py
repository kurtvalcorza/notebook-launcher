from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .state import StateStore


MAX_TARGET_LENGTH = 512


@dataclass(slots=True)
class AuditEvent:
    operation: str
    session_id: UUID | None = None
    lease_id: UUID | None = None
    target: str | None = None
    duration_ms: int | None = None
    outcome: str | None = None
    error_type: str | None = None


class AuditStore:
    def __init__(self, state: StateStore):
        self.state = state

    def record(self, event: AuditEvent) -> None:
        safe_target = event.target[:MAX_TARGET_LENGTH] if event.target else None
        with self.state.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    id, session_id, lease_id, operation, target,
                    started_at, duration_ms, outcome, error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(event.session_id) if event.session_id else None,
                    str(event.lease_id) if event.lease_id else None,
                    event.operation,
                    safe_target,
                    datetime.now(UTC).isoformat(),
                    event.duration_ms,
                    event.outcome,
                    event.error_type,
                ),
            )
