from __future__ import annotations

from datetime import UTC, datetime

from .launch_auth import request_digest
from .models import LaunchRequest, ResolvedSource
from .state import StateStore


class StateLaunchStarter:
    """Persist an authorized launch without starting runtime execution."""

    def __init__(self, state: StateStore) -> None:
        self.state = state

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
        return {
            "launch_id": launch_id,
            "state": state_name,
            "trust_covered": trust_covered,
        }
