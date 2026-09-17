from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .state import StateStore

MAX_TARGET_LENGTH = 512
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)\b(token|password|secret|api[_-]?key)=([^\s&]+)"),
)


@dataclass(slots=True)
class AuditEvent:
    operation: str
    session_id: UUID | None = None
    operation_id: UUID | None = None
    lease_id: UUID | None = None
    target: str | None = None
    duration_ms: int | None = None
    outcome: str | None = None
    error_type: str | None = None


def sanitize_audit_target(target: str | None) -> str | None:
    if target is None:
        return None
    safe = target.replace("\r", " ").replace("\n", " ")
    safe = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", safe)
    safe = _SECRET_PATTERNS[1].sub(r"\1=[REDACTED]", safe)
    return safe[:MAX_TARGET_LENGTH]


class AuditStore:
    def __init__(self, state: StateStore):
        self.state = state

    def record(self, event: AuditEvent) -> None:
        safe_target = sanitize_audit_target(event.target)
        with self.state.transaction() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    id, session_id, operation_id, lease_id, operation, target,
                    started_at, duration_ms, outcome, error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(event.session_id) if event.session_id else None,
                    str(event.operation_id) if event.operation_id else None,
                    str(event.lease_id) if event.lease_id else None,
                    event.operation,
                    safe_target,
                    datetime.now(UTC).isoformat(),
                    event.duration_ms,
                    event.outcome,
                    event.error_type,
                ),
            )
