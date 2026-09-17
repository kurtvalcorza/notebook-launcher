from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID


class JupyterBackend(Protocol):
    """Authoritative Jupyter-side operations used by the launcher adapter."""

    def observe_notebook(self, path: str) -> BackendNotebookState: ...

    def move_notebook(
        self,
        source: str,
        destination: str,
        *,
        expected_version: str,
    ) -> BackendNotebookState: ...


@dataclass(frozen=True, slots=True)
class BackendNotebookState:
    path: str
    version: str
    collaboration_ready: bool


@dataclass(frozen=True, slots=True)
class NotebookObservation:
    active_notebook_path: str
    authoritative_version: str
    collaboration_ready: bool


@dataclass(frozen=True, slots=True)
class KernelObservation:
    status: str
    parent_msg_id: str | None
    owner_operation_id: UUID | None


class JupyterStateError(RuntimeError):
    pass


class HttpCollaborationBackend:
    """Probe Jupyter's collaboration session registry for one fixed document."""

    def __init__(
        self,
        *,
        jupyter_url: str,
        token: str,
        timeout_seconds: float = 2.0,
    ) -> None:
        parsed = urlsplit(jupyter_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}:
            raise ValueError("Jupyter collaboration URL must use loopback HTTP")
        if not token or "\x00" in token:
            raise ValueError("Jupyter token is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.jupyter_url = jupyter_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def observe_notebook(self, path: str) -> BackendNotebookState:
        path = normalize_notebook_path(path)
        encoded = quote(path, safe="/")
        request = Request(
            f"{self.jupyter_url}/api/collaboration/session/{encoded}",
            data=json.dumps({"format": "json", "type": "notebook"}).encode(),
            headers={
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status not in {200, 201}:
                raise JupyterStateError(
                    f"Jupyter collaboration session returned {response.status}"
                )
            try:
                payload = json.loads(response.read())
            except (TypeError, ValueError) as exc:
                raise JupyterStateError(
                    "Jupyter collaboration session returned invalid JSON"
                ) from exc
        if not isinstance(payload, dict):
            raise JupyterStateError("Jupyter collaboration session response is invalid")
        file_id = payload.get("fileId")
        session_id = payload.get("sessionId")
        if (
            payload.get("format") != "json"
            or payload.get("type") != "notebook"
            or not isinstance(file_id, str)
            or not file_id
            or not isinstance(session_id, str)
            or not session_id
        ):
            raise JupyterStateError(
                "Jupyter collaboration session identity is unavailable"
            )
        return BackendNotebookState(
            path=path,
            version=f"{session_id}:{file_id}",
            collaboration_ready=True,
        )

    def move_notebook(
        self,
        source: str,
        destination: str,
        *,
        expected_version: str,
    ) -> BackendNotebookState:
        raise JupyterStateError(
            "HTTP collaboration readiness backend does not support notebook moves"
        )


def normalize_notebook_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise ValueError("notebook path must be workspace-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("notebook path must not traverse the workspace")
    if path.suffix.lower() != ".ipynb":
        raise ValueError("active notebook must have an .ipynb suffix")
    return path.as_posix()


class JupyterSessionAdapter:
    """Keep MCP fixed to one launcher-selected collaborative notebook."""

    def __init__(
        self,
        active_notebook_path: str,
        backend: JupyterBackend,
        *,
        on_active_path_changed: Callable[[str, str], None] | None = None,
    ) -> None:
        self._active_notebook_path = normalize_notebook_path(active_notebook_path)
        self._backend = backend
        self._on_active_path_changed = on_active_path_changed
        self._request_owners: dict[str, UUID] = {}
        self._kernel = KernelObservation("unknown", None, None)

    @property
    def active_notebook_path(self) -> str:
        return self._active_notebook_path

    def observe(self) -> NotebookObservation:
        state = self._backend.observe_notebook(self._active_notebook_path)
        observed_path = normalize_notebook_path(state.path)
        if observed_path != self._active_notebook_path:
            raise JupyterStateError("Jupyter observation changed the fixed notebook target")
        if not state.version:
            raise JupyterStateError("Jupyter did not provide an authoritative version")
        return NotebookObservation(
            active_notebook_path=self._active_notebook_path,
            authoritative_version=state.version,
            collaboration_ready=state.collaboration_ready,
        )

    def require_collaboration(self) -> NotebookObservation:
        observation = self.observe()
        if not observation.collaboration_ready:
            raise JupyterStateError("Jupyter collaboration is not ready")
        return observation

    def browser_focused(self, _path: str) -> str:
        """Browser focus is observational and never retargets MCP."""
        return self._active_notebook_path

    def rename_active(
        self,
        destination: str,
        *,
        expected_version: str,
    ) -> NotebookObservation:
        current = self.require_collaboration()
        if current.authoritative_version != expected_version:
            raise JupyterStateError("stale active-notebook version")
        destination = normalize_notebook_path(destination)
        if destination == self._active_notebook_path:
            return current

        source = self._active_notebook_path
        moved = self._backend.move_notebook(
            source,
            destination,
            expected_version=expected_version,
        )
        if normalize_notebook_path(moved.path) != destination:
            raise JupyterStateError("Jupyter returned an unexpected moved notebook path")
        if not moved.version or not moved.collaboration_ready:
            raise JupyterStateError("moved notebook is not collaboration-ready")
        if self._on_active_path_changed is not None:
            try:
                self._on_active_path_changed(source, destination)
            except Exception as exc:
                try:
                    self._backend.move_notebook(
                        destination,
                        source,
                        expected_version=moved.version,
                    )
                except Exception as rollback_exc:
                    raise JupyterStateError(
                        "workspace metadata update and Jupyter move rollback failed"
                    ) from rollback_exc
                raise JupyterStateError(
                    "workspace metadata update failed; Jupyter move was rolled back"
                ) from exc
        self._active_notebook_path = destination
        return NotebookObservation(destination, moved.version, True)

    def register_request(self, operation_id: UUID, jupyter_msg_id: str) -> None:
        if not jupyter_msg_id or "\x00" in jupyter_msg_id:
            raise ValueError("Jupyter message ID is required")
        existing = self._request_owners.get(jupyter_msg_id)
        if existing is not None and existing != operation_id:
            raise JupyterStateError("Jupyter message ID already has a different owner")
        self._request_owners[jupyter_msg_id] = operation_id

    def observe_kernel_status(
        self,
        status: str,
        *,
        parent_msg_id: str | None,
    ) -> KernelObservation:
        if status not in {"starting", "idle", "busy", "restarting", "dead"}:
            raise ValueError("unsupported Jupyter kernel status")
        owner = (
            self._request_owners.get(parent_msg_id)
            if status == "busy" and parent_msg_id is not None
            else None
        )
        active_parent = parent_msg_id if status == "busy" else None
        self._kernel = KernelObservation(status, active_parent, owner)
        return self._kernel

    def complete_request(self, jupyter_msg_id: str) -> None:
        self._request_owners.pop(jupyter_msg_id, None)

    @property
    def kernel_observation(self) -> KernelObservation:
        return self._kernel
