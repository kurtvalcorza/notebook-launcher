from __future__ import annotations

import getpass
import json
import logging
import os
import secrets
import socket
import stat
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from uuid import UUID, uuid4

from .config import Settings
from .environment import (
    EnvironmentBuildResult,
    EnvironmentIdentity,
    Repo2DockerBuilder,
    environment_identity,
)
from .grants import UserDataGrantStore
from .jupyter import HttpCollaborationBackend, JupyterSessionAdapter, JupyterStateError
from .models import GPU, LaunchRequest, ResolvedSource
from .network import FirewallPlan, NetworkPolicyApplier, NetworkPolicyAttestation
from .orchestration import CommandResult, run_argv
from .repository import SourceCache, SourceSnapshot
from .runtime import (
    DockerRuntime,
    GPUAllocation,
    GPUProbeResult,
    RuntimeReadiness,
    RuntimeStartResult,
    probe_nvidia,
    probe_nvidia_container,
    resolve_gpu_allocation,
)
from .sandbox import SandboxPolicy
from .state import SessionRecord, StateStore, WorkspaceRecord
from .trust_flow import TrustFlowStore
from .workspace import WorkspaceManager

JUPYTER_CONTAINER_PORT = 8888
JUPYTER_CONFIG_CONTAINER_PATH = "/jupyter_server_config.py"
logger = logging.getLogger(__name__)


class SourceAcquirer(Protocol):
    def acquire(self, source: ResolvedSource) -> SourceSnapshot: ...


class EnvironmentBuilder(Protocol):
    def ensure(
        self,
        identity: EnvironmentIdentity,
        source_root: Path,
        *,
        redact_values: Sequence[str] = (),
    ) -> EnvironmentBuildResult: ...


class NetworkApplier(Protocol):
    def apply(self, plan: FirewallPlan) -> NetworkPolicyAttestation: ...
    def verify(self, plan: FirewallPlan) -> NetworkPolicyAttestation: ...
    def remove(self, plan: FirewallPlan) -> None: ...


class RuntimeStarter(Protocol):
    def start(
        self,
        policy: SandboxPolicy,
        command: Sequence[str],
        *,
        network: NetworkPolicyAttestation,
    ) -> RuntimeStartResult: ...

    def readiness(
        self,
        started: RuntimeStartResult,
        *,
        collaboration_ready: bool,
    ) -> RuntimeReadiness: ...


class RuntimeControl(Protocol):
    def is_alive(self, runtime_id: str) -> bool: ...
    def stop(self, runtime_id: str) -> None: ...


class ReadinessProbe(Protocol):
    def wait(
        self,
        *,
        jupyter_url: str,
        token: str,
        notebook_path: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class PipelineLaunchResult:
    launch_id: str
    workspace_id: UUID
    session_id: UUID
    notebook_url: str
    reused_existing: bool
    cache_hit: bool | None
    gpu: GPUAllocation | None


class DockerContainerControl:
    """Small runtime lifecycle adapter which never invokes a shell."""

    def __init__(self, *, runner: Callable[..., CommandResult] = run_argv) -> None:
        self.runner = runner

    def is_alive(self, runtime_id: str) -> bool:
        result = self.runner(
            ("docker", "inspect", "--format", "{{.State.Running}}", runtime_id),
            timeout_seconds=10,
            max_output_bytes=4096,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def stop(self, runtime_id: str) -> None:
        result = self.runner(
            ("docker", "rm", "--force", runtime_id),
            timeout_seconds=30,
            max_output_bytes=4096,
        )
        if result.returncode != 0 and self.is_alive(runtime_id):
            raise RuntimeError(f"docker_stop_failed_{result.returncode}")


class HttpJupyterReadinessProbe:
    """Bounded probe for an authoritative Jupyter collaboration session."""

    def __init__(self, *, timeout_seconds: float = 60, interval_seconds: float = 0.25):
        if timeout_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("readiness timeouts must be positive")
        self.timeout_seconds = timeout_seconds
        self.interval_seconds = interval_seconds

    def wait(
        self,
        *,
        jupyter_url: str,
        token: str,
        notebook_path: str,
    ) -> bool:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            try:
                backend = HttpCollaborationBackend(
                    jupyter_url=jupyter_url,
                    token=token,
                    timeout_seconds=min(2.0, self.interval_seconds * 4),
                )
                JupyterSessionAdapter(
                    notebook_path,
                    backend,
                ).require_collaboration()
                return True
            except (
                HTTPError,
                URLError,
                OSError,
                TimeoutError,
                ValueError,
                JupyterStateError,
            ):
                pass
            time.sleep(self.interval_seconds)
        return False


class LaunchPipeline:
    """Authorize-to-ready runtime pipeline with persistent workspace semantics.

    This class intentionally starts only launch rows already admitted by the
    request-bound authorization and source-trust flow.  All external effects are
    injectable, making the orchestration independently testable.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        state: StateStore,
        source_cache: SourceAcquirer | None = None,
        workspaces: WorkspaceManager | None = None,
        builder: EnvironmentBuilder | None = None,
        network: NetworkApplier | None = None,
        runtime: RuntimeStarter | None = None,
        runtime_control: RuntimeControl | None = None,
        readiness_probe: ReadinessProbe | None = None,
        grants: UserDataGrantStore | None = None,
        builder_version: str | None = None,
        approved_dns_endpoints: Sequence[str] = (),
        host_gpu_probe: Callable[[], GPUProbeResult] = probe_nvidia,
        container_gpu_probe: Callable[[str], GPUProbeResult] = probe_nvidia_container,
        port_allocator: Callable[[], int] | None = None,
        executor: ThreadPoolExecutor | None = None,
        existing_start_timeout_seconds: float = 120,
    ) -> None:
        if existing_start_timeout_seconds <= 0:
            raise ValueError("existing_start_timeout_seconds must be positive")
        settings.ensure_directories()
        self.settings = settings
        self.state = state
        self.source_cache = source_cache or SourceCache(settings.sources_dir)
        self.grants = grants or UserDataGrantStore(state)
        self.workspaces = workspaces or WorkspaceManager(
            state,
            settings.workspaces_dir,
            grants=self.grants,
        )
        self.builder = builder or Repo2DockerBuilder()
        self.network = network or NetworkPolicyApplier()
        self.runtime = runtime or DockerRuntime()
        self.runtime_control = runtime_control or DockerContainerControl()
        self.readiness_probe = readiness_probe or HttpJupyterReadinessProbe()
        self.builder_version = builder_version or _repo2docker_version()
        self.approved_dns_endpoints = tuple(approved_dns_endpoints)
        self.host_gpu_probe = host_gpu_probe
        self.container_gpu_probe = container_gpu_probe
        self.port_allocator = port_allocator or _allocate_loopback_port
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="notebook-launch",
        )
        self._owns_executor = executor is None
        self._scheduled: dict[str, Future[PipelineLaunchResult]] = {}
        self._schedule_lock = threading.RLock()
        self._session_owners: dict[UUID, threading.Event] = {}
        self.existing_start_timeout_seconds = existing_start_timeout_seconds

    def schedule(
        self,
        *,
        launch_id: str,
        request: LaunchRequest,
        source: ResolvedSource | None = None,
    ) -> Future[PipelineLaunchResult]:
        """Schedule at most one in-process worker for a launch identifier."""
        with self._schedule_lock:
            pending = self._scheduled.get(launch_id)
            if pending is not None and not pending.done():
                return pending
            future = self._executor.submit(
                self.run,
                launch_id=launch_id,
                request=request,
                source=source,
            )
            self._scheduled[launch_id] = future
            future.add_done_callback(
                lambda completed, key=launch_id: self._forget(key, completed)
            )
            return future

    def schedule_authorized(
        self,
        launch_id: str,
        *,
        source: ResolvedSource | None = None,
    ) -> Future[PipelineLaunchResult]:
        """Schedule a launch using only its already-authorized persisted policy."""
        request, resolved = self._persisted_request(launch_id, source=source)
        return self.schedule(launch_id=launch_id, request=request, source=resolved)

    def run_authorized(
        self,
        launch_id: str,
        *,
        source: ResolvedSource | None = None,
    ) -> PipelineLaunchResult:
        """Run a persisted authorized launch synchronously."""
        request, resolved = self._persisted_request(launch_id, source=source)
        return self.run(launch_id=launch_id, request=request, source=resolved)

    def run(
        self,
        *,
        launch_id: str,
        request: LaunchRequest,
        source: ResolvedSource | None = None,
    ) -> PipelineLaunchResult:
        launch = self.state.get_launch(launch_id)
        if launch is None:
            raise KeyError(launch_id)
        if launch["state"] != "authorized":
            raise ValueError("launch is not authorized")
        self._verify_launch_binding(launch, request=request, source=source)

        now = _now()
        self.state.update_launch_state(
            launch_id,
            expected_states=("authorized",),
            new_state="starting",
            now=now,
        )

        session_id: UUID | None = None
        owned_session_id: UUID | None = None
        workspace: WorkspaceRecord | None = None
        plan: FirewallPlan | None = None
        runtime_name: str | None = None
        try:
            _resolved, snapshot, workspace = self._prepare_workspace(
                request=request,
                source=source,
                launch_id=launch_id,
            )
            with self._schedule_lock:
                existing = self.state.active_session(workspace.id)
            if existing is not None:
                reused = self._reuse_existing_session(
                    launch_id=launch_id,
                    request=request,
                    session=existing,
                )
                if reused is not None:
                    return reused

            if snapshot is None:
                snapshot = self.source_cache.acquire(_resolved)

            identity = environment_identity(
                repository_id=workspace.source_repository_id,
                commit_sha=workspace.source_commit_sha,
                builder_version=self.builder_version,
            )
            build = self.builder.ensure(identity, snapshot.root)
            allocation = self._gpu_allocation(request.gpu, build.identity.image_tag)

            session_id = uuid4()
            runtime_name = f"notebook-launcher-{session_id.hex}"
            host_port = self.port_allocator()
            base_url = f"http://127.0.0.1:{host_port}"
            notebook_url = _notebook_url(base_url, workspace.active_notebook_path)
            with self._schedule_lock:
                claimed, created = self.state.claim_session(
                    session_id=session_id,
                    workspace_id=workspace.id,
                    runtime_id=runtime_name,
                    host_port=host_port,
                    gpu_enabled=allocation.enabled,
                    agent_mode=request.agent_mode.value,
                    notebook_url=notebook_url,
                    now=_now(),
                    reuse_existing=True,
                )
                if created:
                    owned_session_id = session_id
                    self._session_owners[session_id] = threading.Event()
            if not created:
                reused = self._reuse_existing_session(
                    launch_id=launch_id,
                    request=request,
                    session=claimed,
                )
                if reused is None:
                    raise RuntimeError("stale active session was retired; retry launch")
                return reused

            self.state.update_launch_state(
                launch_id,
                expected_states=("starting",),
                new_state="starting",
                now=_now(),
                workspace_id=workspace.id,
                session_id=session_id,
                message="Preparing sandboxed Jupyter runtime.",
            )

            token = secrets.token_urlsafe(32)
            private_path, config_path = self._write_runtime_secrets(
                session_id,
                jupyter_url=base_url,
                token=token,
            )
            plan = _firewall_plan(
                session_id,
                approved_dns_endpoints=self.approved_dns_endpoints,
            )
            attestation = self.network.apply(plan)
            policy = self._sandbox_policy(
                workspace=workspace,
                session_id=session_id,
                image=build.identity.image_tag,
                config_path=config_path,
                plan=plan,
                host_port=host_port,
                gpu_enabled=allocation.enabled,
            )
            command = _jupyter_command(workspace.active_notebook_path)
            if token in command or any(token in value for value in command):
                raise RuntimeError("Jupyter token must not be present in argv")
            started = self.runtime.start(policy, command, network=attestation)
            collaboration_ready = self.readiness_probe.wait(
                jupyter_url=base_url,
                token=token,
                notebook_path=workspace.active_notebook_path,
            )
            readiness = self.runtime.readiness(
                started,
                collaboration_ready=collaboration_ready,
            )
            if not readiness.ready:
                raise RuntimeError("runtime readiness gates did not pass")
            if not private_path.is_file():
                raise RuntimeError("MCP private state is unavailable")

            self.state.transition_session(
                session_id,
                expected_states=("starting",),
                new_state="ready",
                sandbox_verified=readiness.sandbox_verified,
                network_verified=readiness.network_verified,
                collaboration_ready=readiness.collaboration_ready,
            )
            self.state.update_launch_state(
                launch_id,
                expected_states=("starting",),
                new_state="ready",
                now=_now(),
                workspace_id=workspace.id,
                session_id=session_id,
                message="Notebook runtime is ready.",
            )
            return PipelineLaunchResult(
                launch_id=launch_id,
                workspace_id=workspace.id,
                session_id=session_id,
                notebook_url=notebook_url,
                reused_existing=False,
                cache_hit=build.cache_hit,
                gpu=allocation,
            )
        except Exception as launch_failure:
            cleanup_failures: list[Exception] = []
            runtime_confirmed_stopped = True
            if session_id is not None and runtime_name is not None:
                runtime_confirmed_stopped, stop_failures = (
                    self._stop_and_confirm_runtime(runtime_name)
                )
                cleanup_failures.extend(stop_failures)
                if stop_failures:
                    logger.error("failed to confirm runtime stopped during launch rollback")
            if plan is not None and runtime_confirmed_stopped:
                try:
                    self.network.remove(plan)
                except Exception as exc:
                    cleanup_failures.append(exc)
                    logger.exception("failed to remove network during launch rollback")
            if session_id is not None:
                self._invalidate_session_private_state(session_id)
                self.state.release_session_leases(session_id, now=_now())
                current = self.state.get_session(session_id)
                if current is not None and current.state in {"starting", "stopping"}:
                    try:
                        if not cleanup_failures:
                            self.state.transition_session(
                                session_id,
                                expected_states=(current.state,),
                                new_state="failed",
                                stopped_at=_now(),
                            )
                        elif current.state == "starting":
                            self.state.transition_session(
                                session_id,
                                expected_states=("starting",),
                                new_state="stopping",
                            )
                    except Exception:
                        logger.exception("failed to preserve rolled-back session state")
            try:
                self._fail_launch(
                    launch_id,
                    workspace_id=workspace.id if workspace else None,
                )
            except Exception as exc:
                if not cleanup_failures:
                    raise
                cleanup_failures.append(exc)
            if cleanup_failures:
                _raise_cleanup_failures(
                    "launch rollback cleanup failed",
                    cleanup_failures,
                    cause=launch_failure,
                )
            raise
        finally:
            if owned_session_id is not None:
                with self._schedule_lock:
                    owner = self._session_owners.pop(owned_session_id, None)
                    if owner is not None:
                        owner.set()

    def _reuse_existing_session(
        self,
        *,
        launch_id: str,
        request: LaunchRequest,
        session: SessionRecord,
    ) -> PipelineLaunchResult | None:
        if session.state == "stopping":
            raise RuntimeError("workspace runtime is stopping")
        if session.state == "ready":
            if not (
                session.sandbox_verified
                and session.network_verified
                and session.collaboration_ready
            ):
                self._retire_session(
                    session,
                    expected_state="ready",
                    stop_if_alive=True,
                )
                return None
            if not self.runtime_control.is_alive(session.runtime_id):
                self._retire_session(session, expected_state="ready", stop_if_alive=False)
                return None
            self._assert_session_compatible(session, request)
            try:
                self._reattest_ready_session(session)
            except Exception:
                logger.exception("ready runtime failed live reuse attestation")
                self._retire_session(
                    session,
                    expected_state="ready",
                    stop_if_alive=True,
                )
                return None
            self._mark_launch_ready(launch_id, session)
            return _existing_result(launch_id, session)
        if session.state != "starting":
            raise RuntimeError(f"workspace runtime cannot be reused from {session.state}")

        with self._schedule_lock:
            owner = self._session_owners.get(session.id)
            current = self.state.get_session(session.id) if owner is None else None
        if owner is None:
            if current is None:
                raise RuntimeError("existing workspace runtime disappeared during startup")
            if current.state != "starting":
                return self._reuse_existing_session(
                    launch_id=launch_id,
                    request=request,
                    session=current,
                )
            self._retire_session(session, expected_state="starting", stop_if_alive=True)
            return None
        self._assert_session_compatible(session, request)
        self.state.update_launch_state(
            launch_id,
            expected_states=("starting",),
            new_state="starting",
            now=_now(),
            workspace_id=session.workspace_id,
            session_id=session.id,
            message="Following existing workspace runtime startup.",
        )
        if not owner.wait(timeout=self.existing_start_timeout_seconds):
            raise RuntimeError("timed out following existing workspace runtime startup")

        current = self.state.get_session(session.id)
        if current is None:
            raise RuntimeError("existing workspace runtime disappeared during startup")
        self._assert_session_compatible(current, request)
        if current.state != "ready":
            raise RuntimeError(
                f"existing workspace runtime finished startup as {current.state}"
            )
        return self._reuse_existing_session(
            launch_id=launch_id,
            request=request,
            session=current,
        )

    def _reattest_ready_session(self, session: SessionRecord) -> None:
        """Re-run mutable readiness gates before advertising a persisted session."""
        workspace = self.state.get_workspace(session.workspace_id)
        if workspace is None:
            raise RuntimeError("ready session workspace is unavailable")
        plan = _firewall_plan(
            session.id,
            approved_dns_endpoints=self.approved_dns_endpoints,
        )
        attestation = self.network.verify(plan)
        if not attestation.verified:
            raise RuntimeError("ready session network policy is not verified")
        jupyter_url, token = self._read_runtime_credentials(session)
        collaboration_ready = self.readiness_probe.wait(
            jupyter_url=jupyter_url,
            token=token,
            notebook_path=workspace.active_notebook_path,
        )
        readiness = self.runtime.readiness(
            RuntimeStartResult(
                container_id=session.runtime_id,
                argv=(),
                sandbox_verified=session.sandbox_verified,
                network_verified=attestation.verified,
                gpu_enabled=session.gpu_enabled,
                ready=True,
            ),
            collaboration_ready=collaboration_ready,
        )
        if not (
            readiness.ready
            and readiness.sandbox_verified
            and readiness.network_verified
            and readiness.collaboration_ready
        ):
            raise RuntimeError("ready session failed live readiness attestation")

    def _read_runtime_credentials(self, session: SessionRecord) -> tuple[str, str]:
        runtime_root = self.settings.runtime_dir.resolve()
        session_dir = runtime_root / str(session.id)
        private_path = session_dir / "mcp-private.json"
        if session_dir.is_symlink() or private_path.is_symlink():
            raise RuntimeError("ready session private state is unsafe")
        try:
            resolved = private_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise RuntimeError("ready session private state is unavailable") from exc
        if resolved.parent != session_dir or not resolved.is_file():
            raise RuntimeError("ready session private state is unsafe")
        if resolved.stat().st_size > 64 * 1024:
            raise RuntimeError("ready session private state is invalid")
        private = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(private, dict):
            raise TypeError("ready session private state is invalid")
        jupyter_url = private.get("jupyter_url")
        token = private.get("jupyter_token")
        expected_url = f"http://127.0.0.1:{session.host_port}"
        if jupyter_url != expected_url or not isinstance(token, str) or not token:
            raise RuntimeError("ready session private state is invalid")
        if "\x00" in token:
            raise RuntimeError("ready session private state is invalid")
        return jupyter_url, token

    @staticmethod
    def _assert_session_compatible(
        session: SessionRecord,
        request: LaunchRequest,
    ) -> None:
        requested_mode = request.agent_mode.value
        if session.agent_mode != requested_mode:
            raise RuntimeError(
                "active workspace session agent mode does not match the launch request"
            )
        if request.gpu is GPU.ON and not session.gpu_enabled:
            raise RuntimeError("GPU-required launch cannot reuse a CPU-only session")
        if request.gpu is GPU.OFF and session.gpu_enabled:
            raise RuntimeError("GPU-disabled launch cannot reuse a GPU-enabled session")

    def _mark_launch_ready(self, launch_id: str, session: SessionRecord) -> None:
        self.state.update_launch_state(
            launch_id,
            expected_states=("starting",),
            new_state="ready",
            now=_now(),
            workspace_id=session.workspace_id,
            session_id=session.id,
            message="Reused existing notebook runtime.",
        )

    def _retire_session(
        self,
        session: SessionRecord,
        *,
        expected_state: str,
        stop_if_alive: bool,
    ) -> None:
        runtime_confirmed_stopped, failures = self._stop_and_confirm_runtime(
            session.runtime_id,
            stop_if_alive=stop_if_alive,
        )
        plan = _firewall_plan(
            session.id,
            approved_dns_endpoints=self.approved_dns_endpoints,
        )
        if runtime_confirmed_stopped:
            try:
                self.network.remove(plan)
            except Exception as exc:
                failures.append(exc)
                logger.exception("failed to remove stale runtime network")
        self._invalidate_session_private_state(session.id)
        self.state.release_session_leases(session.id, now=_now())
        if failures:
            if session.state != "stopping":
                try:
                    self.state.transition_session(
                        session.id,
                        expected_states=(expected_state,),
                        new_state="stopping",
                    )
                except Exception as exc:  # noqa: BLE001 - retain cleanup evidence
                    failures.append(exc)
            _raise_cleanup_failures("stale runtime cleanup failed", failures)
        self.state.transition_session(
            session.id,
            expected_states=(expected_state,),
            new_state="failed",
            stopped_at=_now(),
        )
        self.state.fail_launches_for_session(
            session.id,
            now=_now(),
            message="Previous runtime did not survive launcher restart.",
            error_code="stale_runtime_reconciled",
        )

    def reconcile_starting_sessions(self) -> int:
        """Retire persisted starts which have no owner in this launcher process."""
        reconciled = 0
        for session in self.state.sessions_in_states(("starting",)):
            with self._schedule_lock:
                if session.id in self._session_owners:
                    continue
            self._retire_session(
                session,
                expected_state="starting",
                stop_if_alive=True,
            )
            reconciled += 1
        return reconciled

    def stop(self, session_id: UUID) -> bool:
        """Idempotently stop compute and invalidate private/lease state."""
        session = self.state.get_session(session_id)
        if session is None:
            return False
        if session.state in {"stopped", "failed"}:
            self._invalidate_session_private_state(session_id)
            self.state.release_session_leases(session_id, now=_now())
            self._set_workspace_launch_state(session.workspace_id, "stopped")
            return True

        if session.state != "stopping":
            session = self.state.transition_session(
                session_id,
                expected_states=("starting", "ready"),
                new_state="stopping",
            )
        plan = _firewall_plan(
            session_id,
            approved_dns_endpoints=self.approved_dns_endpoints,
        )
        failures: list[Exception] = []
        runtime_confirmed_stopped, stop_failures = self._stop_and_confirm_runtime(
            session.runtime_id
        )
        failures.extend(stop_failures)
        if runtime_confirmed_stopped:
            try:
                self.network.remove(plan)
            except Exception as exc:  # noqa: BLE001 - preserve cleanup evidence
                failures.append(exc)
        self._invalidate_session_private_state(session_id)
        self.state.release_session_leases(session_id, now=_now())

        if failures:
            _raise_cleanup_failures("runtime cleanup failed", failures)
        self.state.transition_session(
            session_id,
            expected_states=("stopping",),
            new_state="stopped",
            stopped_at=_now(),
        )
        self._set_workspace_launch_state(session.workspace_id, "stopped")
        return True

    def _stop_and_confirm_runtime(
        self,
        runtime_id: str,
        *,
        stop_if_alive: bool = True,
    ) -> tuple[bool, list[Exception]]:
        """Return true only when removing the runtime's network is safe."""
        failures: list[Exception] = []
        try:
            alive = self.runtime_control.is_alive(runtime_id)
        except Exception as exc:  # noqa: BLE001 - uncertain liveness retains policy
            return False, [exc]
        if alive and stop_if_alive:
            try:
                self.runtime_control.stop(runtime_id)
            except Exception as exc:  # noqa: BLE001 - confirm liveness after the failure
                failures.append(exc)
        try:
            still_alive = self.runtime_control.is_alive(runtime_id)
        except Exception as exc:  # noqa: BLE001 - uncertain liveness retains policy
            failures.append(exc)
            return False, failures
        if still_alive:
            if not failures:
                failures.append(RuntimeError("runtime remained alive during cleanup"))
            return False, failures
        return True, failures

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=True, cancel_futures=False)

    def _prepare_workspace(
        self,
        *,
        request: LaunchRequest,
        source: ResolvedSource | None,
        launch_id: str,
    ) -> tuple[ResolvedSource, SourceSnapshot | None, WorkspaceRecord]:
        if request.workspace_id is None:
            resolved = source or self._source_from_trust_flow(launch_id)
            if resolved is None:
                raise ValueError("resolved source is required for a fresh launch")
            snapshot = self.source_cache.acquire(resolved)
            workspace = self.workspaces.create(
                snapshot,
                source_owner=resolved.owner,
                source_repository=resolved.repository,
                source_notebook_path=resolved.notebook_path,
            )
        else:
            workspace = self.workspaces.reopen(request.workspace_id)
            resolved = source or _source_from_workspace(workspace)
            _verify_workspace_source(workspace, resolved)
            snapshot = None
        self.state.update_launch_state(
            launch_id,
            expected_states=("starting",),
            new_state="starting",
            now=_now(),
            workspace_id=workspace.id,
        )
        return resolved, snapshot, workspace

    def _source_from_trust_flow(self, launch_id: str) -> ResolvedSource | None:
        identity = TrustFlowStore(self.state).source_for_launch(launch_id)
        if identity is None:
            return None
        return ResolvedSource(
            repository_id=identity.repository_id,
            repository_node_id=identity.repository_node_id,
            owner=identity.owner,
            repository=identity.repository,
            requested_ref=identity.requested_ref,
            commit_sha=identity.commit_sha,
            notebook_path=identity.notebook_path,
            clone_url=identity.clone_url,
            resolved_at=datetime.now(UTC),
        )

    def _persisted_request(
        self,
        launch_id: str,
        *,
        source: ResolvedSource | None,
    ) -> tuple[LaunchRequest, ResolvedSource | None]:
        launch = self.state.get_launch(launch_id)
        if launch is None:
            raise KeyError(launch_id)
        if launch["state"] != "authorized":
            raise ValueError("launch is not authorized")
        if launch["workspace_id"] is not None:
            return (
                LaunchRequest(
                    workspace_id=UUID(launch["workspace_id"]),
                    gpu=GPU(launch["gpu"]),
                    agent_mode=launch["agent_mode"],
                ),
                source,
            )
        resolved = source or self._source_from_trust_flow(launch_id)
        if resolved is None:
            raise ValueError("resolved source is required for a fresh launch")
        return (
            LaunchRequest(
                repo=f"{resolved.owner}/{resolved.repository}",
                ref=resolved.commit_sha,
                path=resolved.notebook_path,
                gpu=GPU(launch["gpu"]),
                agent_mode=launch["agent_mode"],
            ),
            resolved,
        )

    def _verify_launch_binding(
        self,
        launch: object,
        *,
        request: LaunchRequest,
        source: ResolvedSource | None,
    ) -> None:
        row = launch  # sqlite.Row supports mapping access without exposing SQLite here.
        if (
            row["gpu"] != request.gpu.value  # type: ignore[index]
            or row["agent_mode"] != request.agent_mode.value  # type: ignore[index]
        ):
            raise ValueError("launch runtime policy binding changed")
        if request.workspace_id is not None:
            if row["workspace_id"] != str(request.workspace_id):  # type: ignore[index]
                raise ValueError("launch workspace binding changed")
            return
        if source is None:
            return
        expected = (
            int(row["repository_id"]),  # type: ignore[index]
            str(row["commit_sha"]),  # type: ignore[index]
            str(row["notebook_path"]),  # type: ignore[index]
        )
        actual = (source.repository_id, source.commit_sha, source.notebook_path)
        if actual != expected:
            raise ValueError("launch source binding changed")

    def _gpu_allocation(self, mode: GPU, image: str) -> GPUAllocation:
        host = self.host_gpu_probe() if mode is not GPU.OFF else GPUProbeResult(False)
        container = (
            self.container_gpu_probe(image)
            if mode is not GPU.OFF and host.available
            else None
        )
        return resolve_gpu_allocation(mode, host, container)

    def _sandbox_policy(
        self,
        *,
        workspace: WorkspaceRecord,
        session_id: UUID,
        image: str,
        config_path: Path,
        plan: FirewallPlan,
        host_port: int,
        gpu_enabled: bool,
    ) -> SandboxPolicy:
        readonly_mounts: list[tuple[Path, str]] = [
            (config_path, JUPYTER_CONFIG_CONTAINER_PATH),
        ]
        readwrite_mounts: list[tuple[Path, str]] = [
            (workspace.outputs_path, "/workspace/outputs"),
        ]
        grant = self.grants.revalidate(workspace.id)
        if grant is not None:
            destination = "/mnt/user-data"
            if grant.mode == "ro":
                readonly_mounts.append((grant.canonical_root, destination))
            else:
                readwrite_mounts.append((grant.canonical_root, destination))
        return SandboxPolicy(
            image=image,
            container_name=f"notebook-launcher-{session_id.hex}",
            workspace_host=workspace.work_path,
            readonly_mounts=tuple(readonly_mounts),
            readwrite_mounts=tuple(readwrite_mounts),
            environment=(
                ("HOME", "/tmp/notebook-launcher-home"),
                ("JUPYTER_RUNTIME_DIR", "/tmp/jupyter-runtime"),
            ),
            memory=self.settings.runtime_memory,
            cpus=self.settings.runtime_cpus,
            pids_limit=self.settings.runtime_pids_limit,
            gpu_enabled=gpu_enabled,
            network_name=plan.network_name,
            published_ports=((host_port, JUPYTER_CONTAINER_PORT),),
        )

    def _write_runtime_secrets(
        self,
        session_id: UUID,
        *,
        jupyter_url: str,
        token: str,
    ) -> tuple[Path, Path]:
        session_dir = self.settings.runtime_dir / str(session_id)
        session_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        try:
            _restrict_owner_access(session_dir)
        except Exception:
            session_dir.rmdir()
            raise
        private_path = session_dir / "mcp-private.json"
        config_path = session_dir / "jupyter_server_config.py"
        _exclusive_private_write(
            private_path,
            json.dumps(
                {"jupyter_url": jupyter_url, "jupyter_token": token},
                separators=(",", ":"),
            ).encode(),
        )
        config = (
            "c = get_config()\n"
            f"c.IdentityProvider.token = {json.dumps(token)}\n"
            f"c.ServerApp.token = {json.dumps(token)}\n"
            "c.ServerApp.open_browser = False\n"
            "c.ServerApp.allow_remote_access = True\n"
            "c.ServerApp.ip = '0.0.0.0'\n"
        ).encode()
        try:
            _exclusive_private_write(config_path, config)
        except Exception:
            private_path.unlink(missing_ok=True)
            session_dir.rmdir()
            raise
        return private_path, config_path

    def _invalidate_session_private_state(self, session_id: UUID) -> None:
        session_dir = self.settings.runtime_dir / str(session_id)
        if session_dir.is_symlink() or not session_dir.exists():
            return
        for name in ("mcp-private.json", "jupyter_server_config.py"):
            path = session_dir / name
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
        try:
            session_dir.rmdir()
        except OSError:
            # Never recursively delete a runtime directory containing unknown data.
            pass

    def _fail_launch(self, launch_id: str, *, workspace_id: UUID | None) -> None:
        row = self.state.get_launch(launch_id)
        if row is None or row["state"] not in {"authorized", "starting"}:
            return
        try:
            self.state.update_launch_state(
                launch_id,
                expected_states=(str(row["state"]),),
                new_state="failed",
                now=_now(),
                workspace_id=workspace_id,
                message="Notebook runtime launch failed.",
                error_code="launch_pipeline_failed",
            )
        except Exception:
            logger.exception("failed to mark launch failed")

    def _set_workspace_launch_state(self, workspace_id: UUID, new_state: str) -> None:
        with self.state.connect() as connection:
            rows = connection.execute(
                "SELECT id, state FROM launches WHERE workspace_id = ? "
                "AND state IN ('starting','ready')",
                (str(workspace_id),),
            ).fetchall()
        for row in rows:
            try:
                self.state.update_launch_state(
                    row["id"],
                    expected_states=(row["state"],),
                    new_state=new_state,
                    now=_now(),
                    workspace_id=workspace_id,
                )
            except Exception:
                logger.exception("failed to update a workspace launch state")

    def _forget(
        self,
        launch_id: str,
        completed: Future[PipelineLaunchResult],
    ) -> None:
        with self._schedule_lock:
            if self._scheduled.get(launch_id) is completed:
                del self._scheduled[launch_id]


def _repo2docker_version() -> str:
    try:
        return f"repo2docker {version('jupyter-repo2docker')}"
    except PackageNotFoundError:
        return "repo2docker unavailable"


def _allocate_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _raise_cleanup_failures(
    message: str,
    failures: Sequence[Exception],
    *,
    cause: Exception | None = None,
) -> None:
    if not failures:
        return
    if len(failures) == 1:
        raise failures[0] from cause
    raise ExceptionGroup(message, list(failures)) from cause


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _source_from_workspace(workspace: WorkspaceRecord) -> ResolvedSource:
    return ResolvedSource(
        repository_id=workspace.source_repository_id,
        owner=workspace.source_owner,
        repository=workspace.source_repository,
        requested_ref=workspace.source_commit_sha,
        commit_sha=workspace.source_commit_sha,
        notebook_path=workspace.source_notebook_path,
        clone_url=(
            f"https://github.com/{workspace.source_owner}/"
            f"{workspace.source_repository}.git"
        ),
        resolved_at=datetime.now(UTC),
    )


def _verify_workspace_source(
    workspace: WorkspaceRecord,
    source: ResolvedSource,
) -> None:
    expected = (
        workspace.source_repository_id,
        workspace.source_commit_sha,
        workspace.source_notebook_path,
    )
    actual = (source.repository_id, source.commit_sha, source.notebook_path)
    if actual != expected:
        raise ValueError("workspace source identity changed")


def _notebook_url(base_url: str, notebook_path: str) -> str:
    return f"{base_url}/lab/tree/{quote(notebook_path, safe='/')}"


def _jupyter_command(notebook_path: str) -> tuple[str, ...]:
    default_url = f"/lab/tree/{quote(notebook_path, safe='/')}"
    return (
        "jupyter",
        "lab",
        f"--config={JUPYTER_CONFIG_CONTAINER_PATH}",
        "--collaborative",
        f"--ServerApp.port={JUPYTER_CONTAINER_PORT}",
        "--ServerApp.port_retries=0",
        "--ServerApp.root_dir=/workspace",
        f"--ServerApp.default_url={default_url}",
        "--no-browser",
    )


def _firewall_plan(
    session_id: UUID,
    *,
    approved_dns_endpoints: Sequence[str],
) -> FirewallPlan:
    second_octet = 16 + ((session_id.int >> 8) % 224)
    third_octet = 1 + (session_id.int % 253)
    short = session_id.hex[:10]
    return FirewallPlan(
        network_name=f"nl-{short}",
        bridge_name=f"nlbr{session_id.hex[:8]}",
        ipv4_subnet=f"10.{second_octet}.{third_octet}.0/24",
        approved_dns_endpoints=tuple(approved_dns_endpoints),
    )


def _exclusive_private_write(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _restrict_owner_access(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, stat.S_IRWXU)
        return
    principal = getpass.getuser()
    if not principal or "\x00" in principal:
        raise RuntimeError("unable to resolve the current Windows user")
    result = run_argv(
        (
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{principal}:(OI)(CI)F",
        ),
        timeout_seconds=10,
        max_output_bytes=4096,
    )
    if result.returncode != 0:
        raise RuntimeError(f"runtime_private_acl_failed_{result.returncode}")


def _existing_result(launch_id: str, session: SessionRecord) -> PipelineLaunchResult:
    return PipelineLaunchResult(
        launch_id=launch_id,
        workspace_id=session.workspace_id,
        session_id=session.id,
        notebook_url=session.notebook_url,
        reused_existing=True,
        cache_hit=None,
        gpu=None,
    )
