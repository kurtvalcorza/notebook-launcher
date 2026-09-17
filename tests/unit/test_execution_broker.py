from uuid import uuid4

from notebook_launcher.execution import ExecutionBroker, ExecutionCoordinator
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


def test_browser_busy_prevents_dispatch_and_queued_cancel_never_interrupts():
    broker = ExecutionBroker()
    kernel = FakeKernel()
    coordinator = ExecutionCoordinator(broker, kernel)
    operation = broker.enqueue(uuid4())
    broker.observe_kernel_busy("browser-message")

    assert coordinator.dispatch_next({"code": "1"}) is None
    coordinator.cancel(operation.operation_id)
    assert kernel.interrupts == 0


def test_cancel_pending_interrupts_only_when_agent_message_becomes_active():
    broker = ExecutionBroker()
    kernel = FakeKernel(message_id="agent-message")
    coordinator = ExecutionCoordinator(broker, kernel)
    operation = broker.enqueue(uuid4())
    coordinator.dispatch_next({"code": "while True: pass"})

    pending = coordinator.cancel(operation.operation_id)
    assert pending.state is ExecutionState.CANCEL_PENDING
    assert kernel.interrupts == 0

    coordinator.observe_busy_parent(operation.operation_id, "browser-message")
    assert kernel.interrupts == 0
    coordinator.observe_busy_parent(operation.operation_id, "agent-message")
    assert kernel.interrupts == 1
    assert broker.get(operation.operation_id).state is ExecutionState.CANCELLING


def test_queued_timeout_is_distinct_and_never_interrupts():
    broker = ExecutionBroker()
    kernel = FakeKernel()
    coordinator = ExecutionCoordinator(broker, kernel)
    operation = broker.enqueue(uuid4())
    timed_out = coordinator.timeout(operation.operation_id)
    assert timed_out.state is ExecutionState.TIMEOUT
    assert kernel.interrupts == 0


def test_unresponsive_owned_cancel_restarts_kernel_after_bounded_grace():
    broker = ExecutionBroker()
    kernel = FakeKernel(message_id="agent-message")
    waits = []
    coordinator = ExecutionCoordinator(
        broker,
        kernel,
        wait_for_idle=lambda timeout: waits.append(timeout) or False,
        cancel_grace_seconds=2.5,
    )
    operation = broker.enqueue(uuid4())
    coordinator.dispatch_next({"code": "while True: pass"})
    coordinator.observe_busy_parent(operation.operation_id, "agent-message")
    cancelled = coordinator.cancel(operation.operation_id)
    assert waits == [2.5]
    assert kernel.interrupts == 1
    assert kernel.restarts == 1
    assert cancelled.state is ExecutionState.CANCELLED


class FakeKernel:
    def __init__(self, message_id="message"):
        self.message_id = message_id
        self.interrupts = 0
        self.restarts = 0

    def dispatch(self, request):
        return self.message_id

    def interrupt(self):
        self.interrupts += 1

    def restart(self):
        self.restarts += 1
