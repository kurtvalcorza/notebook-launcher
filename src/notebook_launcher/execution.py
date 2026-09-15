from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from .models import ExecutionState


TERMINAL_STATES = {
    ExecutionState.COMPLETED,
    ExecutionState.FAILED,
    ExecutionState.TIMEOUT,
    ExecutionState.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    operation_id: UUID
    session_id: UUID
    state: ExecutionState
    queued_at: datetime
    jupyter_msg_id: str | None = None
    active_parent_msg_id: str | None = None
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def owns_active_kernel_request(self) -> bool:
        return bool(
            self.jupyter_msg_id
            and self.active_parent_msg_id
            and self.jupyter_msg_id == self.active_parent_msg_id
            and self.state in {ExecutionState.RUNNING, ExecutionState.CANCEL_PENDING}
        )


class ExecutionBroker:
    """Pure state machine for serialized agent execution ownership.

    The real Jupyter adapter supplies message IDs and busy-parent observations.
    This class decides whether a cancellation is safe to translate to a
    kernel-wide interrupt.
    """

    def __init__(self) -> None:
        self._records: dict[UUID, ExecutionRecord] = {}
        self._queue: list[UUID] = []
        self._active: UUID | None = None

    def enqueue(self, session_id: UUID) -> ExecutionRecord:
        record = ExecutionRecord(
            operation_id=uuid4(),
            session_id=session_id,
            state=ExecutionState.QUEUED,
            queued_at=datetime.now(UTC),
        )
        self._records[record.operation_id] = record
        self._queue.append(record.operation_id)
        return record

    def get(self, operation_id: UUID) -> ExecutionRecord:
        return self._records[operation_id]

    def next_queued(self) -> ExecutionRecord | None:
        if self._active is not None:
            return None
        while self._queue:
            operation_id = self._queue[0]
            record = self._records[operation_id]
            if record.state is ExecutionState.QUEUED:
                return record
            self._queue.pop(0)
        return None

    def dispatch(self, operation_id: UUID, jupyter_msg_id: str) -> ExecutionRecord:
        if self._active is not None:
            raise RuntimeError("another agent execution is already dispatched")
        record = self._records[operation_id]
        if record.state is not ExecutionState.QUEUED:
            raise RuntimeError("only queued operations may be dispatched")
        if self._queue and self._queue[0] != operation_id:
            raise RuntimeError("execution dispatch must preserve queue order")
        if self._queue:
            self._queue.pop(0)
        updated = replace(
            record,
            state=ExecutionState.DISPATCHED,
            jupyter_msg_id=jupyter_msg_id,
            dispatched_at=datetime.now(UTC),
        )
        self._records[operation_id] = updated
        self._active = operation_id
        return updated

    def observe_busy_parent(self, operation_id: UUID, parent_msg_id: str) -> ExecutionRecord:
        record = self._records[operation_id]
        state = record.state
        started_at = record.started_at
        if record.jupyter_msg_id == parent_msg_id:
            if state is ExecutionState.DISPATCHED:
                state = ExecutionState.RUNNING
                started_at = datetime.now(UTC)
            elif state is ExecutionState.CANCEL_PENDING:
                started_at = started_at or datetime.now(UTC)
        updated = replace(
            record,
            state=state,
            active_parent_msg_id=parent_msg_id,
            started_at=started_at,
        )
        self._records[operation_id] = updated
        return updated

    def request_cancel(self, operation_id: UUID) -> tuple[ExecutionRecord, bool]:
        record = self._records[operation_id]
        now = datetime.now(UTC)
        if record.state in TERMINAL_STATES:
            return record, False
        if record.state is ExecutionState.QUEUED:
            updated = replace(
                record,
                state=ExecutionState.CANCELLED,
                cancel_requested_at=now,
                finished_at=now,
            )
            self._records[operation_id] = updated
            return updated, False

        updated = replace(
            record,
            state=ExecutionState.CANCEL_PENDING,
            cancel_requested_at=now,
        )
        self._records[operation_id] = updated
        return updated, updated.owns_active_kernel_request

    def finish(
        self,
        operation_id: UUID,
        outcome: ExecutionState,
    ) -> ExecutionRecord:
        if outcome not in TERMINAL_STATES:
            raise ValueError("outcome must be terminal")
        record = self._records[operation_id]
        updated = replace(record, state=outcome, finished_at=datetime.now(UTC))
        self._records[operation_id] = updated
        if self._active == operation_id:
            self._active = None
        return updated
