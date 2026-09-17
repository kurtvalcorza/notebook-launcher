from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import pytest

from notebook_launcher.orchestration import LifecycleOrchestrator, SessionSnapshot


@dataclass
class FakeStore:
    sessions: dict[UUID, SessionSnapshot] = field(default_factory=dict)
    authorized: set[tuple[UUID, UUID]] = field(default_factory=set)
    notebook_exists: bool = True

    def require_authorized_launch(self, launch_id, workspace_id):
        if (launch_id, workspace_id) not in self.authorized:
            raise PermissionError("fresh launch authorization required")

    def active_session(self, workspace_id):
        return next(
            (
                session
                for session in self.sessions.values()
                if session.workspace_id == workspace_id
                and session.state in {"starting", "ready", "stopping"}
            ),
            None,
        )

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def active_notebook_exists(self, workspace_id):
        return self.notebook_exists

    def mark_session_state(self, session_id, state):
        self.sessions[session_id] = replace(self.sessions[session_id], state=state)


@dataclass
class FakeRuntime:
    alive: set[str] = field(default_factory=set)
    stopped: list[str] = field(default_factory=list)

    def is_alive(self, runtime_id):
        return runtime_id in self.alive

    def stop(self, runtime_id):
        self.stopped.append(runtime_id)
        self.alive.discard(runtime_id)


@dataclass
class FakeInvalidator:
    sessions: list[UUID] = field(default_factory=list)

    def invalidate(self, session_id):
        self.sessions.append(session_id)


class FakeStarter:
    def __init__(self, store):
        self.store = store
        self.calls = 0

    def start(self, workspace_id, launch_id):
        self.calls += 1
        session = snapshot(workspace_id=workspace_id)
        self.store.sessions[session.session_id] = session
        return session


def snapshot(*, workspace_id=None, state="ready"):
    return SessionSnapshot(
        session_id=uuid4(),
        workspace_id=workspace_id or uuid4(),
        runtime_id=f"runtime-{uuid4()}",
        state=state,
        notebook_url="http://127.0.0.1:9000/lab/tree/main.ipynb",
        active_notebook_path="main.ipynb",
    )


def make_orchestrator(existing=None, alive=True):
    store = FakeStore()
    runtime = FakeRuntime()
    invalidator = FakeInvalidator()
    if existing is not None:
        store.sessions[existing.session_id] = existing
        if alive:
            runtime.alive.add(existing.runtime_id)
    starter = FakeStarter(store)
    return (
        LifecycleOrchestrator(
            store=store,
            runtime=runtime,
            invalidators=(invalidator,),
            starter=starter,
        ),
        store,
        runtime,
        invalidator,
        starter,
    )


def test_reopen_requires_fresh_authorization_even_for_live_workspace():
    existing = snapshot()
    orchestrator, _store, _runtime, _invalidator, _starter = make_orchestrator(existing)
    with pytest.raises(PermissionError, match="authorization"):
        orchestrator.reopen(
            workspace_id=existing.workspace_id,
            authorized_launch_id=uuid4(),
        )


def test_reopen_reuses_live_session_without_second_runtime():
    existing = snapshot()
    orchestrator, store, _runtime, _invalidator, starter = make_orchestrator(existing)
    launch_id = uuid4()
    store.authorized.add((launch_id, existing.workspace_id))
    reopened, reused = orchestrator.reopen(
        workspace_id=existing.workspace_id,
        authorized_launch_id=launch_id,
    )
    assert reused and reopened.session_id == existing.session_id
    assert starter.calls == 0


def test_dead_session_is_invalidated_before_reopen():
    existing = snapshot()
    orchestrator, store, _runtime, invalidator, starter = make_orchestrator(
        existing, alive=False
    )
    launch_id = uuid4()
    store.authorized.add((launch_id, existing.workspace_id))
    reopened, reused = orchestrator.reopen(
        workspace_id=existing.workspace_id,
        authorized_launch_id=launch_id,
    )
    assert not reused and reopened.session_id != existing.session_id
    assert store.sessions[existing.session_id].state == "failed"
    assert invalidator.sessions == [existing.session_id]
    assert starter.calls == 1


def test_stop_is_idempotent_and_invalidates_attachment_state():
    existing = snapshot()
    orchestrator, store, runtime, invalidator, _starter = make_orchestrator(existing)
    assert orchestrator.stop(existing.session_id)
    assert runtime.stopped == [existing.runtime_id]
    assert store.sessions[existing.session_id].state == "stopped"
    assert invalidator.sessions == [existing.session_id]
    assert orchestrator.stop(existing.session_id)
    assert runtime.stopped == [existing.runtime_id]
    assert invalidator.sessions == [existing.session_id, existing.session_id]


def test_reopen_refuses_to_guess_when_active_notebook_is_missing():
    workspace_id = uuid4()
    orchestrator, store, _runtime, _invalidator, _starter = make_orchestrator()
    launch_id = uuid4()
    store.authorized.add((launch_id, workspace_id))
    store.notebook_exists = False
    with pytest.raises(FileNotFoundError, match="active notebook"):
        orchestrator.reopen(
            workspace_id=workspace_id,
            authorized_launch_id=launch_id,
        )
