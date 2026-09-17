from uuid import uuid4

from notebook_launcher.execution import ExecutionBroker
from notebook_launcher.models import ExecutionState


def test_queued_cancel_never_requests_kernel_interrupt():
    broker = ExecutionBroker()
    operation = broker.enqueue(uuid4())

    cancelled, should_interrupt = broker.request_cancel(operation.operation_id)

    assert cancelled.state is ExecutionState.CANCELLED
    assert not should_interrupt


def test_dispatched_cancel_without_owned_busy_parent_does_not_interrupt():
    broker = ExecutionBroker()
    operation = broker.enqueue(uuid4())
    broker.dispatch(operation.operation_id, "agent-msg")
    broker.observe_busy_parent(operation.operation_id, "browser-msg")

    pending, should_interrupt = broker.request_cancel(operation.operation_id)

    assert pending.state is ExecutionState.CANCEL_PENDING
    assert not should_interrupt


def test_running_agent_owned_request_can_be_interrupted():
    broker = ExecutionBroker()
    operation = broker.enqueue(uuid4())
    broker.dispatch(operation.operation_id, "agent-msg")
    running = broker.observe_busy_parent(operation.operation_id, "agent-msg")

    assert running.state is ExecutionState.RUNNING

    pending, should_interrupt = broker.request_cancel(operation.operation_id)

    assert pending.state is ExecutionState.CANCEL_PENDING
    assert should_interrupt


def test_queue_order_is_preserved():
    broker = ExecutionBroker()
    first = broker.enqueue(uuid4())
    second = broker.enqueue(uuid4())

    assert broker.next_queued().operation_id == first.operation_id
    broker.dispatch(first.operation_id, "m1")
    broker.finish(first.operation_id, ExecutionState.COMPLETED)

    assert broker.next_queued().operation_id == second.operation_id
