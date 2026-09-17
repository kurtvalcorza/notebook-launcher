from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol
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
    requested_outcome: ExecutionState | None = None
    deadline_at: datetime | None = None
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
        self._kernel_busy_parent: str | None = None
        self._lock = RLock()

    def enqueue(
        self,
        session_id: UUID,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionRecord:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        now = datetime.now(UTC)
        record = ExecutionRecord(
            operation_id=uuid4(),
            session_id=session_id,
            state=ExecutionState.QUEUED,
            queued_at=now,
            deadline_at=(
                now + timedelta(seconds=timeout_seconds)
                if timeout_seconds is not None
                else None
            ),
        )
        with self._lock:
            self._records[record.operation_id] = record
            self._queue.append(record.operation_id)
        return record

    def get(self, operation_id: UUID) -> ExecutionRecord:
        with self._lock:
            return self._records[operation_id]

    def next_queued(self) -> ExecutionRecord | None:
        with self._lock:
            if self._active is not None or self._kernel_busy_parent is not None:
                return None
            while self._queue:
                operation_id = self._queue[0]
                record = self._records[operation_id]
                if record.state is ExecutionState.QUEUED:
                    return record
                self._queue.pop(0)
            return None

    def dispatch(self, operation_id: UUID, jupyter_msg_id: str) -> ExecutionRecord:
        if not jupyter_msg_id:
            raise ValueError("jupyter_msg_id is required")
        with self._lock:
            if self._active is not None:
                raise RuntimeError("another agent execution is already dispatched")
            if self._kernel_busy_parent is not None:
                raise RuntimeError("kernel is busy with another request")
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
        with self._lock:
            self._kernel_busy_parent = parent_msg_id
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

    def observe_kernel_busy(self, parent_msg_id: str) -> None:
        with self._lock:
            self._kernel_busy_parent = parent_msg_id

    def observe_kernel_idle(self) -> None:
        with self._lock:
            self._kernel_busy_parent = None

    def interrupt_required(self, operation_id: UUID) -> bool:
        with self._lock:
            return self._records[operation_id].owns_active_kernel_request

    def request_cancel(self, operation_id: UUID) -> tuple[ExecutionRecord, bool]:
        with self._lock:
            record = self._records[operation_id]
            now = datetime.now(UTC)
            if record.state in TERMINAL_STATES:
                return record, False
            if record.state is ExecutionState.QUEUED:
                updated = replace(
                    record,
                    state=ExecutionState.CANCELLED,
                    cancel_requested_at=now,
                    requested_outcome=ExecutionState.CANCELLED,
                    finished_at=now,
                )
                self._records[operation_id] = updated
                return updated, False

            updated = replace(
                record,
                state=ExecutionState.CANCEL_PENDING,
                cancel_requested_at=now,
                requested_outcome=ExecutionState.CANCELLED,
            )
            self._records[operation_id] = updated
            return updated, updated.owns_active_kernel_request

    def request_timeout(self, operation_id: UUID) -> tuple[ExecutionRecord, bool]:
        with self._lock:
            record = self._records[operation_id]
            now = datetime.now(UTC)
            if record.state in TERMINAL_STATES:
                return record, False
            if record.state is ExecutionState.QUEUED:
                updated = replace(
                    record,
                    state=ExecutionState.TIMEOUT,
                    cancel_requested_at=now,
                    requested_outcome=ExecutionState.TIMEOUT,
                    finished_at=now,
                )
                self._records[operation_id] = updated
                return updated, False
            updated = replace(
                record,
                state=ExecutionState.CANCEL_PENDING,
                cancel_requested_at=now,
                requested_outcome=ExecutionState.TIMEOUT,
            )
            self._records[operation_id] = updated
            return updated, updated.owns_active_kernel_request

    def mark_cancelling(self, operation_id: UUID) -> ExecutionRecord:
        with self._lock:
            record = self._records[operation_id]
            if not record.owns_active_kernel_request:
                raise RuntimeError("kernel interrupt requires confirmed agent ownership")
            updated = replace(record, state=ExecutionState.CANCELLING)
            self._records[operation_id] = updated
            return updated

    def expire(
        self,
        *,
        now: datetime | None = None,
    ) -> list[tuple[ExecutionRecord, bool]]:
        current = now or datetime.now(UTC)
        expired: list[tuple[ExecutionRecord, bool]] = []
        with self._lock:
            operation_ids = tuple(self._records)
        for operation_id in operation_ids:
            record = self.get(operation_id)
            if (
                record.deadline_at is not None
                and record.deadline_at <= current
                and record.state not in TERMINAL_STATES
            ):
                expired.append(self.request_timeout(operation_id))
        return expired

    def finish(
        self,
        operation_id: UUID,
        outcome: ExecutionState,
    ) -> ExecutionRecord:
        if outcome not in TERMINAL_STATES:
            raise ValueError("outcome must be terminal")
        with self._lock:
            record = self._records[operation_id]
            updated = replace(record, state=outcome, finished_at=datetime.now(UTC))
            self._records[operation_id] = updated
            if self._active == operation_id:
                self._active = None
            return updated

    def invalidate_session(self, session_id: UUID) -> tuple[ExecutionRecord, ...]:
        invalidated: list[ExecutionRecord] = []
        with self._lock:
            operation_ids = tuple(
                operation_id
                for operation_id, record in self._records.items()
                if record.session_id == session_id and record.state not in TERMINAL_STATES
            )
        for operation_id in operation_ids:
            invalidated.append(self.finish(operation_id, ExecutionState.CANCELLED))
        return tuple(invalidated)


class KernelController(Protocol):
    def dispatch(self, request: dict[str, object]) -> str: ...
    def interrupt(self) -> None: ...
    def restart(self) -> None: ...


class ExecutionCoordinator:
    """Bind broker ownership decisions to kernel-wide side effects."""

    def __init__(
        self,
        broker: ExecutionBroker,
        kernel: KernelController,
        *,
        wait_for_idle: Callable[[float], bool] | None = None,
        cancel_grace_seconds: float = 10,
    ) -> None:
        if cancel_grace_seconds < 0:
            raise ValueError("cancel_grace_seconds cannot be negative")
        self.broker = broker
        self.kernel = kernel
        self.wait_for_idle = wait_for_idle
        self.cancel_grace_seconds = cancel_grace_seconds

    def dispatch_next(self, request: dict[str, object]) -> ExecutionRecord | None:
        queued = self.broker.next_queued()
        if queued is None:
            return None
        message_id = self.kernel.dispatch(request)
        return self.broker.dispatch(queued.operation_id, message_id)

    def observe_busy_parent(self, operation_id: UUID, parent_msg_id: str) -> ExecutionRecord:
        record = self.broker.observe_busy_parent(operation_id, parent_msg_id)
        if record.state is ExecutionState.CANCEL_PENDING and record.owns_active_kernel_request:
            self._interrupt_owned(operation_id)
            return self.broker.get(operation_id)
        return record

    def cancel(self, operation_id: UUID) -> ExecutionRecord:
        record, should_interrupt = self.broker.request_cancel(operation_id)
        if should_interrupt:
            self._interrupt_owned(operation_id)
            return self.broker.get(operation_id)
        return record

    def timeout(self, operation_id: UUID) -> ExecutionRecord:
        record, should_interrupt = self.broker.request_timeout(operation_id)
        if should_interrupt:
            self._interrupt_owned(operation_id)
            return self.broker.get(operation_id)
        return record

    def _interrupt_owned(self, operation_id: UUID) -> None:
        record = self.broker.mark_cancelling(operation_id)
        self.kernel.interrupt()
        if self.wait_for_idle is None:
            return
        if not self.wait_for_idle(self.cancel_grace_seconds):
            self.kernel.restart()
        self.broker.finish(
            operation_id,
            record.requested_outcome or ExecutionState.CANCELLED,
        )
