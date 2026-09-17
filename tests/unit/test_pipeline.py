import json
import os
import stat
import threading
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from notebook_launcher.config import Settings
from notebook_launcher.environment import EnvironmentBuildResult
from notebook_launcher.jupyter import BackendNotebookState
from notebook_launcher.leases import WritableLeaseStore
from notebook_launcher.models import GPU, AgentMode, LaunchRequest, ResolvedSource
from notebook_launcher.network import NetworkPolicyAttestation
from notebook_launcher.pipeline import HttpJupyterReadinessProbe, LaunchPipeline
from notebook_launcher.repository import SourceSnapshot, source_tree_digest
from notebook_launcher.runtime import (
    GPUProbeResult,
    RuntimePolicyError,
    RuntimeReadiness,
    RuntimeStartResult,
)
from notebook_launcher.state import StateStore


class FakeSourceCache:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.sources = []
        self.failure = None

    def acquire(self, source):
        self.sources.append(source)
        if self.failure is not None:
            raise self.failure
        return self.snapshot


class FakeBuilder:
    def __init__(self, *, cache_hit=False):
        self.cache_hit = cache_hit
        self.calls = []

    def ensure(self, identity, source_root, *, redact_values=()):
        self.calls.append((identity, source_root, tuple(redact_values)))
        return EnvironmentBuildResult(
            identity=identity,
            cache_hit=self.cache_hit,
            image_id="sha256:test",
            duration_ms=3,
        )


class FakeNetwork:
    def __init__(self):
        self.applied = []
        self.verified = []
        self.removed = []
        self.remove_failure = None
        self.verify_failure = None

    def apply(self, plan):
        self.applied.append(plan)
        return NetworkPolicyAttestation(
            network_name=plan.network_name,
            bridge_name=plan.bridge_name,
            verified=True,
            commands_verified=1,
            verification_commands=(("iptables", "-C", "DOCKER-USER"),),
        )

    def verify(self, plan):
        self.verified.append(plan)
        if self.verify_failure is not None:
            raise self.verify_failure
        return NetworkPolicyAttestation(
            network_name=plan.network_name,
            bridge_name=plan.bridge_name,
            verified=True,
            commands_verified=1,
            verification_commands=(("iptables", "-C", "DOCKER-USER"),),
        )

    def remove(self, plan):
        self.removed.append(plan)
        if self.remove_failure is not None:
            raise self.remove_failure


class FakeRuntime:
    def __init__(self):
        self.starts = []
        self.readiness_calls = []

    def start(self, policy, command, *, network):
        self.starts.append((policy, tuple(command), network))
        return RuntimeStartResult(
            container_id=f"container-{len(self.starts)}",
            argv=("docker", "run"),
            sandbox_verified=True,
            network_verified=True,
            gpu_enabled=policy.gpu_enabled,
        )

    def readiness(self, started, *, collaboration_ready):
        self.readiness_calls.append((started, collaboration_ready))
        if not collaboration_ready:
            raise RuntimePolicyError("Jupyter collaboration is not ready")
        return RuntimeReadiness(
            container_id=started.container_id,
            sandbox_verified=True,
            network_verified=True,
            collaboration_ready=True,
            ready=True,
        )


class FakeRuntimeControl:
    def __init__(self):
        self.alive = set()
        self.stopped = []
        self.stop_failure = None
        self.liveness_failure = None

    def is_alive(self, runtime_id):
        if self.liveness_failure is not None:
            raise self.liveness_failure
        return runtime_id not in self.stopped

    def stop(self, runtime_id):
        if self.stop_failure is not None:
            raise self.stop_failure
        self.stopped.append(runtime_id)
        self.alive.discard(runtime_id)


class FakeReadiness:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    def wait(self, *, jupyter_url, token, notebook_path):
        self.calls.append((jupyter_url, token, notebook_path))
        return self.ready


class BlockingReadiness(FakeReadiness):
    def __init__(self):
        super().__init__(True)
        self.entered = threading.Event()
        self.release = threading.Event()

    def wait(self, *, jupyter_url, token, notebook_path):
        self.entered.set()
        assert self.release.wait(timeout=5)
        return super().wait(
            jupyter_url=jupyter_url,
            token=token,
            notebook_path=notebook_path,
        )


class CollaborationBackend:
    def __init__(self, *, ready):
        self.ready = ready

    def observe_notebook(self, path):
        return BackendNotebookState(path, "shared-document-id", self.ready)

    def move_notebook(self, *_args, **_kwargs):
        raise AssertionError("readiness probe must not move notebooks")


def _source():
    return ResolvedSource(
        repository_id=101,
        repository_node_id="R_repo",
        owner="example",
        repository="demo",
        requested_ref="main",
        commit_sha="a" * 40,
        notebook_path="notebooks/demo.ipynb",
        clone_url="https://github.com/example/demo.git",
        resolved_at=datetime.now(UTC),
    )


def _launch(
    state,
    *,
    source=None,
    workspace_id=None,
    gpu="auto",
    agent_mode="write",
):
    source = source or _source()
    return state.create_launch(
        request_digest="digest",
        repository_id=source.repository_id if workspace_id is None else None,
        commit_sha=source.commit_sha if workspace_id is None else None,
        notebook_path=source.notebook_path if workspace_id is None else None,
        workspace_id=str(workspace_id) if workspace_id is not None else None,
        gpu=gpu,
        agent_mode=agent_mode,
        trust_covered=True,
        state="authorized",
        now=datetime.now(UTC).isoformat(),
    )


def _wait_for_session_binding(state, launch_id, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session_id = state.get_launch(launch_id)["session_id"]
        if session_id is not None:
            return session_id
        time.sleep(0.01)
    raise AssertionError("launch was not bound to the existing starting session")


@pytest.fixture
def rig(tmp_path):
    settings = Settings(root=tmp_path / "launcher")
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    source_root = tmp_path / "source"
    notebook = source_root / "notebooks" / "demo.ipynb"
    notebook.parent.mkdir(parents=True)
    notebook.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    snapshot = SourceSnapshot(
        root=source_root,
        notebook=notebook,
        repository_id=101,
        commit_sha="a" * 40,
        tree_digest=source_tree_digest(source_root),
    )
    cache = FakeSourceCache(snapshot)
    builder = FakeBuilder()
    network = FakeNetwork()
    runtime = FakeRuntime()
    control = FakeRuntimeControl()
    readiness = FakeReadiness()
    pipeline = LaunchPipeline(
        settings=settings,
        state=state,
        source_cache=cache,
        builder=builder,
        network=network,
        runtime=runtime,
        runtime_control=control,
        readiness_probe=readiness,
        builder_version="repo2docker test",
        host_gpu_probe=lambda: GPUProbeResult(False),
        container_gpu_probe=lambda _image: GPUProbeResult(False),
        port_allocator=lambda: 19001,
        existing_start_timeout_seconds=5,
    )
    yield {
        "settings": settings,
        "state": state,
        "cache": cache,
        "builder": builder,
        "network": network,
        "runtime": runtime,
        "control": control,
        "readiness": readiness,
        "pipeline": pipeline,
    }
    pipeline.close()


def test_fresh_launch_reaches_ready_without_putting_token_in_argv(rig):
    source = _source()
    launch_id = _launch(rig["state"], source=source, gpu="off")

    result = rig["pipeline"].run(
        launch_id=launch_id,
        request=LaunchRequest(
            repo="example/demo",
            ref="main",
            path="notebooks/demo.ipynb",
            gpu=GPU.OFF,
            agent_mode=AgentMode.WRITE,
        ),
        source=source,
    )

    assert not result.reused_existing
    assert rig["state"].get_launch(launch_id)["state"] == "ready"
    session = rig["state"].get_session(result.session_id)
    assert session is not None and session.state == "ready"
    assert session.workspace_id == result.workspace_id
    private_path = (
        rig["settings"].runtime_dir / str(result.session_id) / "mcp-private.json"
    )
    private = json.loads(private_path.read_text())
    token = private["jupyter_token"]
    policy, command, _attestation = rig["runtime"].starts[0]
    assert all(token not in argument for argument in command)
    assert all(token not in value for _key, value in policy.environment)
    assert policy.published_ports == ((19001, 8888),)
    assert policy.gpu_enabled is False
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(private_path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(private_path.parent).st_mode) == 0o700


def test_pipeline_readiness_requires_authoritative_collaboration(monkeypatch):
    monkeypatch.setattr(
        "notebook_launcher.pipeline.HttpCollaborationBackend",
        lambda **_kwargs: CollaborationBackend(ready=False),
    )
    probe = HttpJupyterReadinessProbe(
        timeout_seconds=0.01,
        interval_seconds=0.001,
    )

    assert not probe.wait(
        jupyter_url="http://127.0.0.1:8888",
        token="secret",
        notebook_path="main.ipynb",
    )


def test_reopen_reuses_active_session_without_second_build_or_runtime(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    second_launch = _launch(rig["state"], workspace_id=first.workspace_id)

    second = rig["pipeline"].run(
        launch_id=second_launch,
        request=LaunchRequest(workspace_id=first.workspace_id),
    )

    assert second.reused_existing
    assert second.session_id == first.session_id
    assert len(rig["builder"].calls) == 1
    assert len(rig["runtime"].starts) == 1
    assert len(rig["network"].verified) == 1
    assert len(rig["readiness"].calls) == 2
    assert len(rig["runtime"].readiness_calls) == 2
    assert rig["state"].get_launch(second_launch)["state"] == "ready"


def test_reopen_retires_ready_session_when_live_network_attestation_fails(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    original = rig["state"].get_session(first.session_id)
    rig["network"].verify_failure = RuntimeError("network policy missing")
    second_launch = _launch(rig["state"], workspace_id=first.workspace_id)

    second = rig["pipeline"].run(
        launch_id=second_launch,
        request=LaunchRequest(workspace_id=first.workspace_id),
    )

    assert not second.reused_existing
    assert second.session_id != first.session_id
    assert rig["state"].get_session(first.session_id).state == "failed"
    assert original.runtime_id in rig["control"].stopped
    assert len(rig["network"].verified) == 1


def test_reopen_retires_ready_session_when_collaboration_is_no_longer_ready(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    original = rig["state"].get_session(first.session_id)
    rig["readiness"].ready = False
    second_launch = _launch(rig["state"], workspace_id=first.workspace_id)

    with pytest.raises(RuntimePolicyError, match="collaboration"):
        rig["pipeline"].run(
            launch_id=second_launch,
            request=LaunchRequest(workspace_id=first.workspace_id),
        )

    assert rig["state"].get_session(first.session_id).state == "failed"
    assert original.runtime_id in rig["control"].stopped
    assert len(rig["network"].verified) == 1


def test_reopen_reuses_healthy_session_without_source_cache_access(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    cache_calls = len(rig["cache"].sources)
    rig["cache"].failure = RuntimeError("source cache unavailable")
    second_launch = _launch(rig["state"], workspace_id=first.workspace_id)

    second = rig["pipeline"].run(
        launch_id=second_launch,
        request=LaunchRequest(workspace_id=first.workspace_id),
    )

    assert second.reused_existing
    assert second.session_id == first.session_id
    assert len(rig["cache"].sources) == cache_calls


def test_stale_runtime_is_retired_before_cache_failure_blocks_reopen(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    session = rig["state"].get_session(first.session_id)
    rig["control"].stopped.append(session.runtime_id)
    rig["cache"].failure = RuntimeError("source cache unavailable")
    second_launch = _launch(rig["state"], workspace_id=first.workspace_id)

    with pytest.raises(RuntimeError, match="source cache unavailable"):
        rig["pipeline"].run(
            launch_id=second_launch,
            request=LaunchRequest(workspace_id=first.workspace_id),
        )

    assert rig["state"].get_session(first.session_id).state == "failed"
    assert rig["state"].active_session(first.workspace_id) is None
    assert rig["state"].get_launch(second_launch)["state"] == "failed"


def test_reopen_rejects_agent_mode_mismatch_instead_of_reusing_write_session(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    second_launch = _launch(
        rig["state"],
        workspace_id=first.workspace_id,
        agent_mode="readonly",
    )

    with pytest.raises(RuntimeError, match="agent mode"):
        rig["pipeline"].run(
            launch_id=second_launch,
            request=LaunchRequest(
                workspace_id=first.workspace_id,
                agent_mode=AgentMode.READONLY,
            ),
        )

    assert rig["state"].get_launch(second_launch)["state"] == "failed"
    assert len(rig["runtime"].starts) == 1


def test_gpu_on_reopen_rejects_cpu_only_active_session(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    second_launch = _launch(
        rig["state"],
        workspace_id=first.workspace_id,
        gpu="on",
    )

    with pytest.raises(RuntimeError, match="CPU-only"):
        rig["pipeline"].run(
            launch_id=second_launch,
            request=LaunchRequest(workspace_id=first.workspace_id, gpu=GPU.ON),
        )

    assert rig["state"].get_launch(second_launch)["state"] == "failed"
    assert len(rig["runtime"].starts) == 1


def test_reopen_does_not_reuse_ready_row_without_collaboration_attestation(rig):
    source = _source()
    first_launch = _launch(rig["state"], source=source)
    first = rig["pipeline"].run(
        launch_id=first_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    original = rig["state"].get_session(first.session_id)
    with rig["state"].transaction() as connection:
        connection.execute(
            "UPDATE notebook_sessions SET collaboration_ready = 0 WHERE id = ?",
            (str(first.session_id),),
        )
    second_launch = _launch(rig["state"], workspace_id=first.workspace_id)

    second = rig["pipeline"].run(
        launch_id=second_launch,
        request=LaunchRequest(workspace_id=first.workspace_id),
    )

    assert not second.reused_existing
    assert second.session_id != first.session_id
    assert rig["state"].get_session(first.session_id).state == "failed"
    assert original.runtime_id in rig["control"].stopped


def test_second_reopen_follows_existing_start_to_ready(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["pipeline"].stop(initial.session_id)

    blocking = BlockingReadiness()
    rig["pipeline"].readiness_probe = blocking
    first_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    second_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    request = LaunchRequest(workspace_id=initial.workspace_id)

    first = rig["pipeline"].schedule(launch_id=first_launch, request=request)
    assert blocking.entered.wait(timeout=5)
    second = rig["pipeline"].schedule(launch_id=second_launch, request=request)
    assert _wait_for_session_binding(rig["state"], second_launch)
    blocking.release.set()

    first_result = first.result(timeout=5)
    second_result = second.result(timeout=5)
    assert second_result.reused_existing
    assert second_result.session_id == first_result.session_id
    assert rig["state"].get_launch(first_launch)["state"] == "ready"
    assert rig["state"].get_launch(second_launch)["state"] == "ready"


def test_second_reopen_follows_existing_start_to_failed(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["pipeline"].stop(initial.session_id)

    blocking = BlockingReadiness()
    blocking.ready = False
    rig["pipeline"].readiness_probe = blocking
    first_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    second_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    request = LaunchRequest(workspace_id=initial.workspace_id)

    first = rig["pipeline"].schedule(launch_id=first_launch, request=request)
    assert blocking.entered.wait(timeout=5)
    second = rig["pipeline"].schedule(launch_id=second_launch, request=request)
    assert _wait_for_session_binding(rig["state"], second_launch)
    blocking.release.set()

    with pytest.raises(RuntimePolicyError):
        first.result(timeout=5)
    with pytest.raises(RuntimeError, match="finished startup as failed"):
        second.result(timeout=5)
    assert rig["state"].get_launch(first_launch)["state"] == "failed"
    assert rig["state"].get_launch(second_launch)["state"] == "failed"


def test_stale_starting_snapshot_reloads_ready_session_after_owner_event_removed(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["pipeline"].stop(initial.session_id)

    blocking = BlockingReadiness()
    rig["pipeline"].readiness_probe = blocking
    owner_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    request = LaunchRequest(workspace_id=initial.workspace_id)
    owner = rig["pipeline"].schedule(launch_id=owner_launch, request=request)
    assert blocking.entered.wait(timeout=5)
    stale_snapshot = rig["state"].active_session(initial.workspace_id)
    assert stale_snapshot is not None and stale_snapshot.state == "starting"
    follower_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    rig["state"].update_launch_state(
        follower_launch,
        expected_states=("authorized",),
        new_state="starting",
        now=datetime.now(UTC).isoformat(),
        workspace_id=initial.workspace_id,
    )

    blocking.release.set()
    owner_result = owner.result(timeout=5)
    stopped_before_reuse = list(rig["control"].stopped)
    follower_result = rig["pipeline"]._reuse_existing_session(
        launch_id=follower_launch,
        request=request,
        session=stale_snapshot,
    )

    assert follower_result is not None and follower_result.reused_existing
    assert follower_result.session_id == owner_result.session_id
    assert rig["control"].stopped == stopped_before_reuse
    assert rig["state"].get_launch(follower_launch)["state"] == "ready"


def test_dead_persisted_starting_session_is_retired_before_reopen(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["pipeline"].stop(initial.session_id)

    stale_id = uuid4()
    stale, created = rig["state"].claim_session(
        session_id=stale_id,
        workspace_id=initial.workspace_id,
        runtime_id="dead-after-restart",
        host_port=19002,
        gpu_enabled=False,
        agent_mode="readonly",
        notebook_url="http://127.0.0.1:19002/lab/tree/notebooks/demo.ipynb",
        now=datetime.now(UTC).isoformat(),
    )
    assert created
    rig["control"].stopped.append(stale.runtime_id)
    reopen_launch = _launch(rig["state"], workspace_id=initial.workspace_id)

    reopened = rig["pipeline"].run(
        launch_id=reopen_launch,
        request=LaunchRequest(workspace_id=initial.workspace_id),
    )

    assert not reopened.reused_existing
    assert reopened.session_id != stale_id
    assert rig["state"].get_session(stale_id).state == "failed"
    assert rig["state"].get_launch(reopen_launch)["state"] == "ready"


def test_startup_reconciliation_fails_orphaned_start_and_linked_launch(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["pipeline"].stop(initial.session_id)
    stale_id = uuid4()
    stale, created = rig["state"].claim_session(
        session_id=stale_id,
        workspace_id=initial.workspace_id,
        runtime_id="orphaned-start",
        host_port=19003,
        gpu_enabled=False,
        agent_mode="write",
        notebook_url="http://127.0.0.1:19003/lab/tree/notebooks/demo.ipynb",
        now=datetime.now(UTC).isoformat(),
    )
    assert created
    rig["control"].stopped.append(stale.runtime_id)
    stale_launch = _launch(rig["state"], workspace_id=initial.workspace_id)
    rig["state"].update_launch_state(
        stale_launch,
        expected_states=("authorized",),
        new_state="starting",
        now=datetime.now(UTC).isoformat(),
        workspace_id=initial.workspace_id,
        session_id=stale_id,
    )

    assert rig["pipeline"].reconcile_starting_sessions() == 1

    assert rig["state"].get_session(stale_id).state == "failed"
    failed_launch = rig["state"].get_launch(stale_launch)
    assert failed_launch["state"] == "failed"
    assert failed_launch["error_code"] == "stale_runtime_reconciled"


def test_stale_reconciliation_network_failure_keeps_session_blocking(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["pipeline"].stop(initial.session_id)
    stale_id = uuid4()
    stale, created = rig["state"].claim_session(
        session_id=stale_id,
        workspace_id=initial.workspace_id,
        runtime_id="orphaned-network",
        host_port=19004,
        gpu_enabled=False,
        agent_mode="write",
        notebook_url="http://127.0.0.1:19004/lab/tree/notebooks/demo.ipynb",
        now=datetime.now(UTC).isoformat(),
    )
    assert created
    rig["control"].stopped.append(stale.runtime_id)
    rig["network"].remove_failure = RuntimeError("network cleanup failed")

    with pytest.raises(RuntimeError, match="network cleanup failed"):
        rig["pipeline"].reconcile_starting_sessions()

    current = rig["state"].get_session(stale_id)
    assert current is not None and current.state == "stopping"
    assert rig["state"].active_session(initial.workspace_id).id == stale_id


def test_readiness_failure_stops_runtime_and_invalidates_private_state(rig):
    rig["readiness"].ready = False
    source = _source()
    launch_id = _launch(rig["state"], source=source)

    with pytest.raises(RuntimePolicyError):
        rig["pipeline"].run(
            launch_id=launch_id,
            request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
            source=source,
        )

    launch = rig["state"].get_launch(launch_id)
    assert launch["state"] == "failed"
    workspace_id = launch["workspace_id"]
    session = rig["state"].active_session(UUID(workspace_id))
    assert session is None
    with rig["state"].connect() as connection:
        failed = connection.execute(
            "SELECT * FROM notebook_sessions WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
    assert failed["state"] == "failed"
    assert not (rig["settings"].runtime_dir / failed["id"]).exists()
    assert rig["control"].stopped == [failed["runtime_id"]]
    assert len(rig["network"].removed) == 1


def test_launch_rollback_network_failure_keeps_session_blocking(rig):
    rig["readiness"].ready = False
    rig["network"].remove_failure = RuntimeError("network cleanup failed")
    source = _source()
    launch_id = _launch(rig["state"], source=source)

    with pytest.raises(RuntimeError, match="network cleanup failed"):
        rig["pipeline"].run(
            launch_id=launch_id,
            request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
            source=source,
        )

    launch = rig["state"].get_launch(launch_id)
    session = rig["state"].get_session(UUID(launch["session_id"]))
    assert launch["state"] == "failed"
    assert session is not None and session.state == "stopping"
    assert rig["state"].active_session(session.workspace_id).id == session.id


def test_stop_invalidates_lease_but_preserves_workspace_and_build(rig):
    source = _source()
    launch_id = _launch(rig["state"], source=source)
    result = rig["pipeline"].run(
        launch_id=launch_id,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    session = rig["state"].get_session(result.session_id)
    rig["control"].alive.add(session.runtime_id)
    lease_id = WritableLeaseStore(rig["state"]).acquire(result.session_id, "test")
    workspace = rig["state"].get_workspace(result.workspace_id)

    assert rig["pipeline"].stop(result.session_id)

    assert rig["state"].get_session(result.session_id).state == "stopped"
    assert workspace.work_path.is_dir()
    assert workspace.outputs_path.is_dir()
    assert len(rig["builder"].calls) == 1
    assert not (
        rig["settings"].runtime_dir / str(result.session_id) / "mcp-private.json"
    ).exists()
    with rig["state"].connect() as connection:
        lease = connection.execute(
            "SELECT released_at FROM writable_agent_leases WHERE id = ?",
            (str(lease_id),),
        ).fetchone()
    assert lease["released_at"] is not None
    assert rig["state"].get_launch(launch_id)["state"] == "stopped"


def test_stop_network_failure_keeps_session_and_launch_nonterminal(rig):
    source = _source()
    launch_id = _launch(rig["state"], source=source)
    result = rig["pipeline"].run(
        launch_id=launch_id,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["network"].remove_failure = RuntimeError("network cleanup failed")

    with pytest.raises(RuntimeError, match="network cleanup failed"):
        rig["pipeline"].stop(result.session_id)

    session = rig["state"].get_session(result.session_id)
    assert session is not None and session.state == "stopping"
    assert rig["state"].active_session(result.workspace_id).id == result.session_id
    assert rig["state"].get_launch(launch_id)["state"] == "ready"

    rig["network"].remove_failure = None
    assert rig["pipeline"].stop(result.session_id)
    assert rig["state"].get_session(result.session_id).state == "stopped"
    assert rig["state"].active_session(result.workspace_id) is None
    assert rig["state"].get_launch(launch_id)["state"] == "stopped"


def test_stop_failure_keeps_network_policy_until_runtime_is_confirmed_dead(rig):
    source = _source()
    launch_id = _launch(rig["state"], source=source)
    result = rig["pipeline"].run(
        launch_id=launch_id,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["control"].stop_failure = RuntimeError("simulated stop failure")

    with pytest.raises(RuntimeError, match="simulated stop failure"):
        rig["pipeline"].stop(result.session_id)

    assert rig["network"].removed == []
    assert rig["state"].get_session(result.session_id).state == "stopping"
    rig["control"].stop_failure = None
    assert rig["pipeline"].stop(result.session_id)
    assert len(rig["network"].removed) == 1


def test_uncertain_runtime_liveness_keeps_network_policy(rig):
    source = _source()
    launch_id = _launch(rig["state"], source=source)
    result = rig["pipeline"].run(
        launch_id=launch_id,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["control"].liveness_failure = RuntimeError("liveness unavailable")

    with pytest.raises(RuntimeError, match="liveness unavailable"):
        rig["pipeline"].stop(result.session_id)

    assert rig["network"].removed == []
    assert rig["state"].get_session(result.session_id).state == "stopping"


def test_stale_retirement_stop_failure_preserves_network_policy(rig):
    source = _source()
    initial_launch = _launch(rig["state"], source=source)
    initial = rig["pipeline"].run(
        launch_id=initial_launch,
        request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
        source=source,
    )
    rig["control"].stop_failure = RuntimeError("simulated stop failure")

    with pytest.raises(RuntimeError, match="simulated stop failure"):
        rig["pipeline"]._retire_session(
            rig["state"].get_session(initial.session_id),
            expected_state="ready",
            stop_if_alive=True,
        )

    assert rig["network"].removed == []
    assert rig["state"].get_session(initial.session_id).state == "stopping"


def test_launch_rollback_stop_failure_preserves_network_policy(rig):
    rig["readiness"].ready = False
    rig["control"].stop_failure = RuntimeError("simulated stop failure")
    source = _source()
    launch_id = _launch(rig["state"], source=source)

    with pytest.raises(RuntimeError, match="simulated stop failure"):
        rig["pipeline"].run(
            launch_id=launch_id,
            request=LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path),
            source=source,
        )

    launch = rig["state"].get_launch(launch_id)
    session = rig["state"].get_session(UUID(launch["session_id"]))
    assert rig["network"].removed == []
    assert session is not None and session.state == "stopping"


def test_schedule_deduplicates_an_inflight_launch(rig):
    blocking = BlockingReadiness()
    rig["pipeline"].readiness_probe = blocking
    source = _source()
    launch_id = _launch(rig["state"], source=source)
    request = LaunchRequest(repo="example/demo", ref="main", path=source.notebook_path)

    first = rig["pipeline"].schedule(
        launch_id=launch_id,
        request=request,
        source=source,
    )
    assert blocking.entered.wait(timeout=5)
    second = rig["pipeline"].schedule(
        launch_id=launch_id,
        request=request,
        source=source,
    )
    assert second is first
    blocking.release.set()

    assert first.result(timeout=5).launch_id == launch_id
