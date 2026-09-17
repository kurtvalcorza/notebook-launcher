from __future__ import annotations

import base64
import json
import secrets
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import IO, Any, Protocol
from uuid import UUID

from .errors import ConflictError, LauncherError, PermissionDenied
from .models import AgentMode, OutputEnvelope
from .versions import assert_not_active_notebook

READONLY_METHODS = frozenset({"notebook.read", "cell.read", "output.read"})
REQUIRED_WRITE_METHODS = frozenset(
    {
        *READONLY_METHODS,
        "cell.insert",
        "cell.update",
        "cell.delete",
        "notebook.move",
        "cell.execute",
        "notebook.execute_all",
        "kernel.execute_code",
        "execution.cancel",
        "kernel.restart",
        "workspace.file.read",
        "workspace.file.write",
        "workspace.file.move",
        "workspace.file.delete",
        "workspace.file.list",
    }
)

# The pinned backend has not yet passed the launcher contract for atomic shared
# document writes, owned cancellation, notebook move, or workspace file access.
# Keep the public bridge fail-closed instead of advertising partially mapped
# operations that can violate the specification under concurrency.
EXPOSED_WRITE_METHODS = READONLY_METHODS

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_SERVER_ERROR = -32000
MAX_REQUEST_ID_BYTES = 128


def _tool_descriptors(methods: Sequence[str]) -> list[dict[str, object]]:
    return [
        {
            "name": method,
            "description": f"Notebook Launcher semantic capability: {method}",
            "inputSchema": {"type": "object", "additionalProperties": True},
        }
        for method in methods
    ]


def minimum_mcp_output_bytes(methods: Sequence[str]) -> int:
    """Return the wire bytes needed to discover tools with the largest safe ID."""
    response = {
        "jsonrpc": "2.0",
        # An ASCII control byte expands to a six-byte JSON escape.  This is the
        # largest encoded form of any accepted MAX_REQUEST_ID_BYTES string.
        "id": "\x00" * MAX_REQUEST_ID_BYTES,
        "result": {"tools": _tool_descriptors(methods)},
    }
    return len(
        (
            json.dumps(response, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    )


MIN_MCP_OUTPUT_BYTES = minimum_mcp_output_bytes(tuple(sorted(READONLY_METHODS)))


@dataclass(frozen=True, slots=True)
class McpCapabilities:
    inspect_notebook: bool
    mutate_notebook: bool
    execute: bool
    workspace_read: bool
    workspace_write: bool
    restart_kernel: bool

    @property
    def methods(self) -> tuple[str, ...]:
        selected = EXPOSED_WRITE_METHODS if self.mutate_notebook else READONLY_METHODS
        return tuple(sorted(selected))


@dataclass(frozen=True, slots=True)
class McpAttachmentDescriptor:
    session_id: UUID
    notebook_path: str
    available: bool
    status: str
    transport: str
    command: str
    args: tuple[str, ...]
    agent_mode: AgentMode
    backend: str
    write_lease_available: bool
    capabilities: tuple[str, ...]

    def public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["session_id"] = str(self.session_id)
        value["agent_mode"] = self.agent_mode.value
        value["args"] = list(self.args)
        value["capabilities"] = list(self.capabilities)
        return value


def capabilities_for_mode(mode: AgentMode) -> McpCapabilities:
    if mode is AgentMode.READONLY:
        return McpCapabilities(True, False, False, False, False, False)
    return McpCapabilities(True, False, False, False, False, False)


def build_attachment_descriptor(
    *,
    session_id: UUID,
    notebook_path: str,
    mode: AgentMode,
    backend: str,
    available: bool,
    write_lease_available: bool,
    command: str = "notebook-launcher",
) -> McpAttachmentDescriptor:
    """Return the public attachment contract without runtime credentials."""
    capabilities = capabilities_for_mode(mode)
    return McpAttachmentDescriptor(
        session_id=session_id,
        notebook_path=notebook_path,
        available=available,
        status="available" if available else "unavailable",
        transport="stdio",
        command=command,
        args=("mcp", str(session_id), "--mode", mode.value),
        agent_mode=mode,
        backend=backend,
        write_lease_available=write_lease_available,
        capabilities=capabilities.methods,
    )


class LeaseValidator(Protocol):
    def validate(self, session_id: UUID, lease_id: UUID) -> None: ...


class AttachmentValidator(Protocol):
    def validate(self, session_id: UUID) -> None: ...


class NotebookBackend(Protocol):
    def inspect(self, notebook_path: str) -> dict[str, Any]: ...
    def read_cell(self, notebook_path: str, cell_id: str) -> dict[str, Any]: ...
    def mutate(
        self,
        notebook_path: str,
        operation: dict[str, Any],
        *,
        expected_version: str,
    ) -> str: ...
    def move(
        self,
        notebook_path: str,
        destination: str,
        *,
        expected_version: str,
    ) -> str: ...
    def execute(self, notebook_path: str, request: dict[str, Any]) -> Any: ...
    def cancel(self, operation_id: str) -> dict[str, Any]: ...
    def read_output(self, notebook_path: str, request: dict[str, Any]) -> Any: ...
    def restart_kernel(self, notebook_path: str) -> None: ...


class WorkspaceBackend(Protocol):
    def read_file(self, path: str) -> bytes: ...
    def write_file(self, path: str, data: bytes) -> None: ...
    def move_file(self, source: str, destination: str) -> None: ...
    def delete_file(self, path: str) -> None: ...
    def list_files(self, path: str, *, limit: int) -> list[dict[str, Any]]: ...


class SemanticBackendTransport(Protocol):
    @property
    def backend_identity(self) -> str: ...
    def call(self, method: str, params: Mapping[str, Any]) -> Any: ...
    def close(self) -> None: ...


class DatalayerBackend:
    """Semantic mapping for a launcher-owned Datalayer/Jupyter transport."""

    def __init__(self, transport: SemanticBackendTransport) -> None:
        self.transport = transport

    def activate(self, notebook_path: str) -> None:
        """Connect the configured backend to the launcher-selected notebook."""
        self._mapping(
            "use_notebook",
            {
                "notebook_name": notebook_path,
                "notebook_path": notebook_path,
                "mode": "connect",
            },
        )

    def inspect(self, notebook_path: str) -> dict[str, Any]:
        result = self._mapping(
            "read_notebook",
            {
                "notebook_name": notebook_path,
                "response_format": "detailed",
                "limit": 0,
            },
        )
        version = _mcp_result_etag(result)
        if version:
            result["document_version"] = version
        return result

    def read_cell(self, notebook_path: str, cell_id: str) -> dict[str, Any]:
        result = self._mapping(
            "read_cell", {"notebook_name": notebook_path, "cell_id": cell_id}
        )
        version = _mcp_result_etag(result)
        if version:
            result["document_version"] = version
        return result

    def mutate(
        self,
        notebook_path: str,
        operation: dict[str, Any],
        *,
        expected_version: str,
    ) -> str:
        current = self.inspect(notebook_path)
        current_version = current.get("document_version") or current.get("etag")
        if not current_version or not secrets.compare_digest(
            str(current_version), expected_version
        ):
            raise ConflictError("The notebook document version changed; refresh and retry.")
        method = str(operation["method"])
        params = dict(operation.get("params", {}))
        params.pop("expected_document_version", None)
        params["notebook_name"] = notebook_path
        backend_method = {
            "cell.insert": "insert_cell",
            "cell.update": "overwrite_cell_source",
            "cell.delete": "delete_cell",
        }[method]
        if method == "cell.delete" and "cell_id" in params:
            params["cell_ids_to_delete"] = [params.pop("cell_id")]
        result = self._mapping(backend_method, params)
        updated_version = result.get("document_version") or result.get("etag")
        if not updated_version:
            refreshed = self.inspect(notebook_path)
            updated_version = refreshed.get("document_version") or refreshed.get("etag")
        if not updated_version:
            raise ConflictError("The backend did not provide an authoritative version.")
        return str(updated_version)

    def move(
        self,
        notebook_path: str,
        destination: str,
        *,
        expected_version: str,
    ) -> str:
        result = self._mapping(
            "notebook.move",
            {
                "notebook_path": notebook_path,
                "destination": destination,
                "expected_document_version": expected_version,
            },
        )
        return str(result.get("notebook_path", destination))

    def execute(self, notebook_path: str, request: dict[str, Any]) -> Any:
        method = str(request.get("method", "kernel.execute_code"))
        backend_method = {
            "cell.execute": "execute_cell",
            "notebook.execute_all": "notebook_run_all_cells",
            "kernel.execute_code": "execute_code",
        }[method]
        params = dict(request.get("params", {}))
        if method != "kernel.execute_code":
            params["notebook_name"] = notebook_path
        return self.transport.call(
            backend_method,
            params,
        )

    def cancel(self, operation_id: str) -> dict[str, Any]:
        return self._mapping("tasks/cancel", {"task_id": operation_id})

    def read_output(self, notebook_path: str, request: dict[str, Any]) -> Any:
        return self.transport.call(
            "read_cell", {**request, "notebook_name": notebook_path}
        )

    def restart_kernel(self, notebook_path: str) -> None:
        self.transport.call("restart_notebook", {"notebook_name": notebook_path})

    def read_file(self, path: str) -> bytes:
        result = self.transport.call("workspace.file.read", {"path": path})
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode("utf-8")
        if isinstance(result, Mapping) and "content" in result:
            return str(result["content"]).encode("utf-8")
        raise TypeError("workspace file backend returned unsupported content")

    def write_file(self, path: str, data: bytes) -> None:
        self.transport.call(
            "workspace.file.write",
            {"path": path, "content": data.decode("utf-8")},
        )

    def move_file(self, source: str, destination: str) -> None:
        self.transport.call(
            "workspace.file.move", {"source": source, "destination": destination}
        )

    def delete_file(self, path: str) -> None:
        self.transport.call("workspace.file.delete", {"path": path})

    def list_files(self, path: str, *, limit: int) -> list[dict[str, Any]]:
        result = self.transport.call(
            "workspace.file.list", {"path": path, "limit": limit}
        )
        if not isinstance(result, list):
            raise TypeError("workspace listing backend returned a non-list")
        return [dict(item) for item in result]

    def _mapping(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        result = self.transport.call(method, params)
        if not isinstance(result, Mapping):
            raise TypeError(f"{method} backend returned a non-object")
        return dict(result)


class SemanticMcpAdapter:
    """Backend-neutral launcher-side MCP capability and lease boundary."""

    def __init__(
        self,
        *,
        mode: AgentMode,
        notebook: NotebookBackend,
        workspace: WorkspaceBackend,
        active_notebook_path: str,
        session_id: UUID | None = None,
        lease_id: UUID | None = None,
        lease_validator: LeaseValidator | None = None,
        attachment_validator: AttachmentValidator | None = None,
        output_limit: int = 1024 * 1024,
    ) -> None:
        self.mode = mode
        self.capabilities = capabilities_for_mode(mode)
        minimum_output = minimum_mcp_output_bytes(self.capabilities.methods)
        if output_limit < minimum_output:
            raise ValueError(
                f"output_limit must be at least {minimum_output} bytes "
                "so tools/list always fits"
            )
        self.notebook = notebook
        self.workspace = workspace
        self._active_notebook_path = _normalize_workspace_path(active_notebook_path)
        self.session_id = session_id
        self.lease_id = lease_id
        self.lease_validator = lease_validator
        self.attachment_validator = attachment_validator
        self.output_limit = output_limit

    @property
    def active_notebook_path(self) -> str:
        return self._active_notebook_path

    def inspect_notebook(self) -> dict[str, Any]:
        self._validate_attachment()
        return self.notebook.inspect(self._active_notebook_path)

    def read_cell(self, cell_id: str) -> dict[str, Any]:
        self._validate_attachment()
        return self.notebook.read_cell(self._active_notebook_path, cell_id)

    def mutate_notebook(
        self, operation: dict[str, Any], *, expected_version: str
    ) -> str:
        self._require_write()
        return self.notebook.mutate(
            self._active_notebook_path,
            operation,
            expected_version=expected_version,
        )

    def move_notebook(self, destination: str, *, expected_version: str) -> str:
        self._require_write()
        destination = _normalize_workspace_path(destination)
        updated = self.notebook.move(
            self._active_notebook_path,
            destination,
            expected_version=expected_version,
        )
        self._active_notebook_path = _normalize_workspace_path(updated)
        return self._active_notebook_path

    def execute(self, request: dict[str, Any]) -> OutputEnvelope:
        self._require_write()
        return bound_output(
            self.notebook.execute(self._active_notebook_path, request), self.output_limit
        )

    def cancel_execution(self, operation_id: str) -> dict[str, Any]:
        self._require_write()
        return self.notebook.cancel(operation_id)

    def read_output(self, request: dict[str, Any]) -> OutputEnvelope:
        self._validate_attachment()
        return bound_output(
            self.notebook.read_output(self._active_notebook_path, request),
            self.output_limit,
        )

    def restart_kernel(self) -> None:
        self._require_write()
        self.notebook.restart_kernel(self._active_notebook_path)

    def read_file(self, path: str) -> bytes:
        self._require_write()
        return self.workspace.read_file(_normalize_workspace_path(path))

    def write_file(self, path: str, data: bytes) -> None:
        self._require_write()
        path = _normalize_workspace_path(path)
        self._assert_not_active(path)
        self.workspace.write_file(path, data)

    def move_file(self, source: str, destination: str) -> None:
        self._require_write()
        source = _normalize_workspace_path(source)
        destination = _normalize_workspace_path(destination)
        self._assert_not_active(source)
        self._assert_not_active(destination)
        self.workspace.move_file(source, destination)

    def delete_file(self, path: str) -> None:
        self._require_write()
        path = _normalize_workspace_path(path)
        self._assert_not_active(path)
        self.workspace.delete_file(path)

    def list_files(self, path: str = ".", *, limit: int = 1000) -> list[dict[str, Any]]:
        self._require_write()
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        return self.workspace.list_files(_normalize_workspace_path(path), limit=limit)

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        values = dict(params or {})
        if method not in self.capabilities.methods:
            raise PermissionDenied(f"MCP capability {method!r} is not available.")
        if method == "notebook.read":
            return self.inspect_notebook()
        if method == "cell.read":
            return self.read_cell(str(values["cell_id"]))
        if method in {"cell.insert", "cell.update", "cell.delete"}:
            version = self.mutate_notebook(
                {"method": method, "params": values},
                expected_version=str(values["expected_document_version"]),
            )
            return {"document_version": version}
        if method == "notebook.move":
            path = self.move_notebook(
                str(values["destination"]),
                expected_version=str(values["expected_document_version"]),
            )
            return {"notebook_path": path}
        if method in {"cell.execute", "notebook.execute_all", "kernel.execute_code"}:
            return self.execute({"method": method, "params": values}).model_dump()
        if method == "execution.cancel":
            return self.cancel_execution(str(values["operation_id"]))
        if method == "output.read":
            return self.read_output(values).model_dump()
        if method == "kernel.restart":
            self.restart_kernel()
            return {"restarted": True}
        if method == "workspace.file.read":
            return bound_output(
                self.read_file(str(values["path"])), self.output_limit
            ).model_dump()
        if method == "workspace.file.write":
            content = values.get("content", "")
            data = content if isinstance(content, bytes) else str(content).encode("utf-8")
            self.write_file(str(values["path"]), data)
            return {"written": True}
        if method == "workspace.file.move":
            self.move_file(str(values["source"]), str(values["destination"]))
            return {"moved": True}
        if method == "workspace.file.delete":
            self.delete_file(str(values["path"]))
            return {"deleted": True}
        if method == "workspace.file.list":
            return self.list_files(
                str(values.get("path", ".")), limit=int(values.get("limit", 1000))
            )
        raise PermissionDenied(f"Unknown MCP capability {method!r}.")

    def _validate_attachment(self) -> None:
        if self.attachment_validator is not None:
            if self.session_id is None:
                raise PermissionDenied("MCP attachment has no session identity.")
            self.attachment_validator.validate(self.session_id)

    def _require_write(self) -> None:
        self._validate_attachment()
        if self.mode is AgentMode.READONLY:
            raise PermissionDenied("Capability is not available in read-only mode.")
        if self.lease_validator is None or self.session_id is None or self.lease_id is None:
            raise PermissionDenied("A current writable-agent lease is required.")
        self.lease_validator.validate(self.session_id, self.lease_id)

    def _assert_not_active(self, path: str) -> None:
        assert_not_active_notebook(
            target_path=path, active_notebook_path=self._active_notebook_path
        )


def _normalize_workspace_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise PermissionDenied("Workspace path must remain relative to the workspace.")
    value = normalized.as_posix()
    return "." if value in {"", "."} else value


def bound_output(value: Any, limit: int) -> OutputEnvelope:
    if limit <= 0:
        raise ValueError("limit must be positive")
    authoritative_location: str | None = None
    if isinstance(value, Mapping) and "authoritative_location" in value:
        authoritative_location = str(value["authoritative_location"])
    if isinstance(value, bytes):
        encoded = value
        mime_type = "application/octet-stream"
        payload: Any = value[:limit]
    elif isinstance(value, str):
        encoded = value.encode("utf-8", errors="replace")
        mime_type = "text/plain"
        payload = encoded[:limit].decode("utf-8", errors="replace")
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        encoded = text.encode("utf-8", errors="replace")
        mime_type = "application/json"
        normalized = json.loads(text)
        payload = _structured_preview(normalized, limit)
    return OutputEnvelope(
        mime_type=mime_type,
        serialized_size=len(encoded),
        payload=payload,
        truncated=len(encoded) > limit,
        authoritative_location=authoritative_location,
    )


def _structured_preview(value: Any, limit: int) -> Any:
    """Keep JSON payloads structurally valid while reducing string content."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) <= limit:
        return value

    preview = _largest_string_capped_value(value, limit)
    if preview is not None:
        return preview

    if isinstance(value, Mapping):
        essential = {
            key: value[key]
            for key in ("document_version", "etag", "version", "path", "cell_id")
            if key in value
        }
        preview = _largest_string_capped_value(essential, limit)
        if preview is not None:
            return preview
        return {}
    if isinstance(value, list):
        return []
    return None


def _largest_string_capped_value(value: Any, limit: int) -> Any | None:
    low = 0
    high = limit
    best: Any | None = None
    while low <= high:
        string_limit = (low + high) // 2
        candidate = _cap_json_strings(value, string_limit)
        size = len(
            json.dumps(
                candidate,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if size <= limit:
            best = candidate
            low = string_limit + 1
        else:
            high = string_limit - 1
    return best


def _cap_json_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [_cap_json_strings(item, limit) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _cap_json_strings(item, limit)
            for key, item in value.items()
        }
    return value


class JsonLineTransport:
    """Private JSON-line subprocess transport for a launcher-owned backend."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        backend_identity: str = "datalayer/jupyter-mcp-server 2.x",
    ) -> None:
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError("backend argv must be a non-empty sequence")
        self._process = subprocess.Popen(
            tuple(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=dict(env) if env is not None else None,
            shell=False,
        )
        self._lock = threading.Lock()
        self._request_id = 0
        self._backend_identity = backend_identity
        self._initialize()

    @property
    def backend_identity(self) -> str:
        return self._backend_identity

    def call(self, method: str, params: Mapping[str, Any]) -> Any:
        rpc_method = method if "/" in method else "tools/call"
        rpc_params: Mapping[str, Any]
        if rpc_method == "tools/call":
            rpc_params = {"name": method, "arguments": dict(params)}
        else:
            rpc_params = dict(params)
        response = self._request(rpc_method, rpc_params)
        result = response.get("result")
        if rpc_method == "tools/call" and isinstance(result, Mapping):
            if result.get("isError"):
                raise RuntimeError(_tool_error_message(result))
            if "structuredContent" in result:
                structured = result["structuredContent"]
                if isinstance(structured, Mapping):
                    structured = dict(structured)
                    meta = result.get("_meta") or result.get("meta")
                    if isinstance(meta, Mapping):
                        structured["_mcp_meta"] = dict(meta)
                return structured
            content = result.get("content")
            if isinstance(content, list) and len(content) == 1:
                item = content[0]
                if isinstance(item, Mapping) and item.get("type") == "text":
                    text = str(item.get("text", ""))
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
        return result

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "notebook-launcher", "version": "0.1.0"},
            },
        )
        assert self._process.stdin is not None
        self._process.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                separators=(",", ":"),
            )
            + "\n"
        )
        self._process.stdin.flush()

    def _request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._process.poll() is not None:
            raise RuntimeError("MCP backend process is not running")
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            request: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params),
            }
            self._process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
            while True:
                line = self._process.stdout.readline()
                if not line:
                    raise RuntimeError("MCP backend closed its output")
                response = json.loads(line)
                if response.get("id") == request_id:
                    break
        if "error" in response:
            error = response["error"]
            message = (
                error.get("message", "backend request failed")
                if isinstance(error, Mapping)
                else str(error)
            )
            raise RuntimeError(message)
        return response

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            self._process.wait(timeout=2)


def run_stdio_bridge(
    adapter: SemanticMcpAdapter,
    *,
    input_stream: IO[str],
    output_stream: IO[str],
) -> int:
    """Serve newline-delimited JSON-RPC without exposing backend credentials."""
    for line in input_stream:
        if not line.strip():
            continue
        request_id: object = None
        is_notification = False
        try:
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _jsonrpc_error(
                    None,
                    JSONRPC_PARSE_ERROR,
                    f"Parse error: {exc.msg}",
                )
                output_stream.write(_encode_response_line(response, adapter.output_limit))
                output_stream.flush()
                continue
            if not isinstance(request, Mapping):
                response = _jsonrpc_error(
                    None,
                    JSONRPC_INVALID_REQUEST,
                    "Invalid Request: JSON-RPC request must be an object",
                )
                output_stream.write(_encode_response_line(response, adapter.output_limit))
                output_stream.flush()
                continue
            result: Any
            is_notification = "id" not in request
            raw_request_id = request.get("id")
            request_id = _safe_request_id(raw_request_id)
            if not is_notification and raw_request_id is not None and request_id is None:
                response = _jsonrpc_error(
                    None,
                    JSONRPC_INVALID_REQUEST,
                    "Invalid Request: id is invalid or too large",
                )
                output_stream.write(_encode_response_line(response, adapter.output_limit))
                output_stream.flush()
                continue
            if request.get("jsonrpc") != "2.0" or not isinstance(
                request.get("method"), str
            ):
                response = _jsonrpc_error(
                    request_id,
                    JSONRPC_INVALID_REQUEST,
                    "Invalid Request",
                )
                if is_notification:
                    continue
                output_stream.write(_encode_response_line(response, adapter.output_limit))
                output_stream.flush()
                continue
            method = request["method"]
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "notebook-launcher", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tool_descriptors(adapter.capabilities.methods)}
            elif method == "tools/call":
                params = request.get("params") or {}
                if not isinstance(params, Mapping):
                    raise TypeError("tools/call params must be an object")
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, Mapping):
                    raise TypeError("tools/call requires a name and object arguments")
                try:
                    semantic_result = adapter.call(name, arguments)
                except LauncherError as exc:
                    response = _bounded_tool_error(
                        request_id,
                        exc.code,
                        exc.message,
                        adapter.output_limit,
                    )
                except RuntimeError as exc:
                    response = _bounded_tool_error(
                        request_id,
                        "backend_error",
                        str(exc),
                        adapter.output_limit,
                    )
                else:
                    response = _bounded_tool_response(
                        request_id,
                        semantic_result,
                        adapter.output_limit,
                    )
                if is_notification:
                    continue
                output_stream.write(_encode_response_line(response, adapter.output_limit))
                output_stream.flush()
                continue
            else:
                response = _jsonrpc_error(
                    request_id,
                    JSONRPC_METHOD_NOT_FOUND,
                    f"Method not found: {method}",
                )
                if is_notification:
                    continue
                output_stream.write(_encode_response_line(response, adapter.output_limit))
                output_stream.flush()
                continue
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except LauncherError as exc:
            response = _jsonrpc_error(
                request_id,
                JSONRPC_SERVER_ERROR,
                f"{exc.code}: {exc.message}",
            )
        except (KeyError, TypeError, ValueError) as exc:
            response = _jsonrpc_error(
                request_id,
                JSONRPC_INVALID_PARAMS,
                f"Invalid params: {exc}",
            )
        except RuntimeError as exc:
            response = _jsonrpc_error(
                request_id,
                JSONRPC_INTERNAL_ERROR,
                f"Internal error: {exc}",
            )
        if is_notification:
            continue
        output_stream.write(_encode_response_line(response, adapter.output_limit))
        output_stream.flush()
    return 0


def _jsonrpc_error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_error_message(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        return " ".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, Mapping) and item.get("type") == "text"
        ) or "backend tool call failed"
    return "backend tool call failed"


def _mcp_result_etag(result: Mapping[str, Any]) -> str | None:
    meta = result.get("_mcp_meta")
    if not isinstance(meta, Mapping):
        return None
    cache = meta.get("io.modelcontextprotocol/cache")
    if not isinstance(cache, Mapping):
        return None
    value = cache.get("etag")
    return str(value) if value else None


def _safe_request_id(value: object) -> object:
    if value is None:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str):
        if len(value.encode("utf-8")) <= MAX_REQUEST_ID_BYTES:
            return value
        return None
    try:
        encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    except ValueError:
        return None
    if len(encoded) <= MAX_REQUEST_ID_BYTES:
        return value
    return None


def _bounded_tool_response(
    request_id: object,
    value: Any,
    limit: int,
) -> dict[str, Any]:
    low = 1
    high = limit
    best: dict[str, Any] | None = None
    while low <= high:
        preview_limit = (low + high) // 2
        envelope = _wire_output_envelope(value, preview_limit)
        candidate = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            envelope,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                ],
                "isError": False,
            },
        }
        if _response_size(candidate) <= limit:
            best = candidate
            low = preview_limit + 1
        else:
            high = preview_limit - 1
    if best is not None:
        return best
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": JSONRPC_INTERNAL_ERROR,
            "message": "Response exceeds configured output limit",
        },
    }


def _bounded_tool_error(
    request_id: object,
    error_code: str,
    message: str,
    limit: int,
) -> dict[str, Any]:
    low = 0
    high = len(message)
    best: dict[str, Any] | None = None
    while low <= high:
        length = (low + high) // 2
        detail = json.dumps(
            {"code": error_code, "message": message[:length]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        candidate = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": detail}],
                "isError": True,
            },
        }
        if _response_size(candidate) <= limit:
            best = candidate
            low = length + 1
        else:
            high = length - 1
    if best is not None:
        return best
    return _jsonrpc_error(
        request_id,
        JSONRPC_INTERNAL_ERROR,
        "Tool error exceeds configured output limit",
    )


def _wire_output_envelope(value: Any, preview_limit: int) -> dict[str, Any]:
    if isinstance(value, Mapping) and {
        "serialized_size",
        "payload",
        "truncated",
    }.issubset(value):
        original = OutputEnvelope.model_validate(value)
        preview = bound_output(original.payload, preview_limit)
        envelope = original.model_copy(
            update={
                "payload": preview.payload,
                "truncated": original.truncated or preview.truncated,
            }
        ).model_dump()
    else:
        envelope = bound_output(value, preview_limit).model_dump()
    payload = envelope.get("payload")
    if isinstance(payload, bytes):
        envelope["payload"] = base64.b64encode(payload).decode("ascii")
        envelope["payload_encoding"] = "base64"
    return envelope


def _response_size(response: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ) + 1


def _encode_response_line(response: Mapping[str, Any], limit: int) -> str:
    encoded = json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if len(encoded.encode("utf-8")) + 1 <= limit:
        return encoded + "\n"
    error = response.get("error")
    if isinstance(error, Mapping) and "code" in error:
        message = str(error.get("message", ""))
        low = 0
        high = len(message)
        best: str | None = None
        while low <= high:
            length = (low + high) // 2
            candidate = {
                "jsonrpc": "2.0",
                "id": _safe_request_id(response.get("id")),
                "error": {
                    "code": error["code"],
                    "message": message[:length],
                },
            }
            serialized = json.dumps(candidate, separators=(",", ":"))
            if len(serialized.encode("utf-8")) + 1 <= limit:
                best = serialized
                low = length + 1
            else:
                high = length - 1
        if best is not None:
            return best + "\n"
    fallback = {
        "jsonrpc": "2.0",
        "id": _safe_request_id(response.get("id")),
        "error": {
            "code": JSONRPC_INTERNAL_ERROR,
            "message": "Response exceeds configured output limit",
        },
    }
    encoded = json.dumps(fallback, separators=(",", ":"))
    if len(encoded.encode("utf-8")) + 1 > limit:
        raise ValueError("output_limit is too small for a JSON-RPC error")
    return encoded + "\n"
