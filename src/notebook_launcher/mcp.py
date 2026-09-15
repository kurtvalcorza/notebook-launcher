from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .errors import PermissionDenied
from .models import AgentMode, OutputEnvelope
from .versions import assert_not_active_notebook


@dataclass(frozen=True, slots=True)
class McpCapabilities:
    inspect_notebook: bool
    mutate_notebook: bool
    execute: bool
    workspace_read: bool
    workspace_write: bool
    restart_kernel: bool


def capabilities_for_mode(mode: AgentMode) -> McpCapabilities:
    if mode is AgentMode.READONLY:
        return McpCapabilities(
            inspect_notebook=True,
            mutate_notebook=False,
            execute=False,
            workspace_read=True,
            workspace_write=False,
            restart_kernel=False,
        )
    return McpCapabilities(
        inspect_notebook=True,
        mutate_notebook=True,
        execute=True,
        workspace_read=True,
        workspace_write=True,
        restart_kernel=True,
    )


class NotebookBackend(Protocol):
    def inspect(self) -> dict[str, Any]: ...
    def mutate(self, operation: dict[str, Any], *, expected_version: str) -> str: ...
    def execute(self, request: dict[str, Any]) -> Any: ...
    def restart_kernel(self) -> None: ...


class WorkspaceBackend(Protocol):
    @property
    def active_notebook_path(self) -> str: ...
    def read_file(self, path: str) -> bytes: ...
    def write_file(self, path: str, data: bytes) -> None: ...
    def delete_file(self, path: str) -> None: ...


class SemanticMcpAdapter:
    """Backend-neutral launcher-side MCP capability boundary."""

    def __init__(
        self,
        *,
        mode: AgentMode,
        notebook: NotebookBackend,
        workspace: WorkspaceBackend,
        output_limit: int = 1024 * 1024,
    ) -> None:
        self.mode = mode
        self.capabilities = capabilities_for_mode(mode)
        self.notebook = notebook
        self.workspace = workspace
        self.output_limit = output_limit

    def inspect_notebook(self) -> dict[str, Any]:
        return self.notebook.inspect()

    def mutate_notebook(self, operation: dict[str, Any], *, expected_version: str) -> str:
        self._require(self.capabilities.mutate_notebook)
        return self.notebook.mutate(operation, expected_version=expected_version)

    def execute(self, request: dict[str, Any]) -> OutputEnvelope:
        self._require(self.capabilities.execute)
        result = self.notebook.execute(request)
        return bound_output(result, self.output_limit)

    def restart_kernel(self) -> None:
        self._require(self.capabilities.restart_kernel)
        self.notebook.restart_kernel()

    def read_file(self, path: str) -> bytes:
        self._require(self.capabilities.workspace_read)
        return self.workspace.read_file(path)

    def write_file(self, path: str, data: bytes) -> None:
        self._require(self.capabilities.workspace_write)
        assert_not_active_notebook(
            target_path=path,
            active_notebook_path=self.workspace.active_notebook_path,
        )
        self.workspace.write_file(path, data)

    def delete_file(self, path: str) -> None:
        self._require(self.capabilities.workspace_write)
        assert_not_active_notebook(
            target_path=path,
            active_notebook_path=self.workspace.active_notebook_path,
        )
        self.workspace.delete_file(path)

    @staticmethod
    def _require(allowed: bool) -> None:
        if not allowed:
            raise PermissionDenied("Capability is not available in read-only mode.")


def bound_output(value: Any, limit: int) -> OutputEnvelope:
    if isinstance(value, bytes):
        size = len(value)
        preview = value[:limit]
        return OutputEnvelope(
            mime_type="application/octet-stream",
            serialized_size=size,
            payload=preview,
            truncated=size > limit,
        )

    text = value if isinstance(value, str) else repr(value)
    encoded = text.encode("utf-8", errors="replace")
    size = len(encoded)
    preview_bytes = encoded[:limit]
    preview = preview_bytes.decode("utf-8", errors="replace")
    return OutputEnvelope(
        mime_type="text/plain",
        serialized_size=size,
        payload=preview,
        truncated=size > limit,
    )
