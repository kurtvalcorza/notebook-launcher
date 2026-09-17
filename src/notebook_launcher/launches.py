from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from .config import Settings
from .launch_auth import request_digest
from .mcp import build_attachment_descriptor
from .models import AgentMode, LaunchRequest, ResolvedSource
from .state import StateStore
from .trust_flow import TrustFlowStore

if TYPE_CHECKING:
    from .pipeline import LaunchPipeline


class StateLaunchStarter:
    """Persist an authorized launch without starting runtime execution."""

    def __init__(self, state: StateStore, settings: Settings | None = None) -> None:
        self.state = state
        self.settings = settings or Settings()
        self.trust_flow = TrustFlowStore(state)

    def start(
        self,
        *,
        request: LaunchRequest,
        source: ResolvedSource | None,
        trust_covered: bool,
    ) -> dict[str, object]:
        payload = request.model_dump(mode="json", exclude_none=True)
        if source is not None:
            payload["resolved_repository_id"] = source.repository_id
            payload["resolved_commit_sha"] = source.commit_sha
            payload["resolved_notebook_path"] = source.notebook_path
        digest = request_digest(payload)
        state_name = "authorized" if trust_covered or source is None else "pending_trust"
        now = datetime.now(UTC).isoformat()
        launch_id = self.state.create_launch(
            request_digest=digest,
            repository_id=source.repository_id if source else None,
            commit_sha=source.commit_sha if source else None,
            notebook_path=source.notebook_path if source else None,
            workspace_id=str(request.workspace_id) if request.workspace_id else None,
            gpu=request.gpu.value,
            agent_mode=request.agent_mode.value,
            trust_covered=trust_covered,
            state=state_name,
            now=now,
        )
        result: dict[str, object] = {
            "launch_id": launch_id,
            "state": state_name,
            "trust_covered": trust_covered,
        }
        if source is not None and not trust_covered:
            nonce, expires = self.trust_flow.create_challenge(
                launch_id,
                source,
                ttl_seconds=self.settings.trust_challenge_ttl_seconds,
            )
            result["trust_confirmation_url"] = f"/trust/{launch_id}?nonce={nonce}"
            result["trust_expires_at"] = expires.isoformat()
        return result


class PipelineLaunchStarter:
    """Persist authorization first, then schedule the fail-closed runtime pipeline."""

    def __init__(self, state_starter: StateLaunchStarter, pipeline: LaunchPipeline) -> None:
        self.state_starter = state_starter
        self.pipeline = pipeline

    def start(
        self,
        *,
        request: LaunchRequest,
        source: ResolvedSource | None,
        trust_covered: bool,
    ) -> dict[str, object]:
        result = self.state_starter.start(
            request=request,
            source=source,
            trust_covered=trust_covered,
        )
        if result["state"] == "authorized":
            self.pipeline.schedule(
                launch_id=str(result["launch_id"]),
                request=request,
                source=source,
            )
        return result

    def continue_authorized(self, launch_id: str) -> None:
        self.pipeline.schedule_authorized(launch_id)


class StateMcpDescriptorProvider:
    """Project public attachment metadata from state without exposing secrets."""

    def __init__(
        self,
        state: StateStore,
        settings: Settings | None = None,
        *,
        backend: str = "jupyter-mcp-server/2.x",
    ) -> None:
        self.state = state
        self.settings = settings or Settings()
        self.backend = backend

    def describe(self, session_id: UUID) -> dict[str, object] | None:
        stale_before = (
            datetime.now(UTC)
            - timedelta(seconds=self.settings.writable_lease_stale_seconds)
        ).isoformat()
        with self.state.connect() as conn:
            row = conn.execute(
                """
                SELECT s.state, s.agent_mode, w.active_notebook_path,
                       EXISTS(
                           SELECT 1 FROM writable_agent_leases AS l
                           WHERE l.session_id = s.id AND l.released_at IS NULL
                             AND COALESCE(l.heartbeat_at, l.acquired_at) >= ?
                       ) AS write_lease_held
                FROM notebook_sessions AS s
                JOIN workspaces AS w ON w.id = s.workspace_id
                WHERE s.id = ?
                """,
                (stale_before, str(session_id)),
            ).fetchone()
        if row is None:
            return None
        state = str(row["state"])
        descriptor = build_attachment_descriptor(
            session_id=session_id,
            notebook_path=row["active_notebook_path"],
            mode=AgentMode(row["agent_mode"]),
            backend=self.backend,
            available=state == "ready",
            write_lease_available=state == "ready" and not bool(row["write_lease_held"]),
        ).public_dict()
        descriptor["status"] = state if state in {"stopped", "failed"} else descriptor["status"]
        return descriptor
