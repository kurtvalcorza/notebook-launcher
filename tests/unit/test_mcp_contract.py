import json
from dataclasses import dataclass, field
from io import StringIO
from uuid import UUID, uuid4

import pytest

from notebook_launcher.errors import ConflictError, PermissionDenied
from notebook_launcher.mcp import (
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    MIN_MCP_OUTPUT_BYTES,
    DatalayerBackend,
    SemanticMcpAdapter,
    bound_output,
    build_attachment_descriptor,
    run_stdio_bridge,
)
from notebook_launcher.models import AgentMode


@dataclass
class FakeNotebook:
    version: str = "v1"
    restarted: bool = False
    paths: list[str] = field(default_factory=list)

    def inspect(self, notebook_path):
        self.paths.append(notebook_path)
        return {"version": self.version, "path": notebook_path}

    def read_cell(self, notebook_path, cell_id):
        self.paths.append(notebook_path)
        return {"cell_id": cell_id, "version": self.version}

    def mutate(self, notebook_path, operation, *, expected_version: str):
        self.paths.append(notebook_path)
        if expected_version != self.version:
            raise ConflictError()
        self.version = "v2"
        return self.version

    def move(self, notebook_path, destination, *, expected_version: str):
        self.mutate(notebook_path, {}, expected_version=expected_version)
        return destination

    def execute(self, notebook_path, request):
        self.paths.append(notebook_path)
        return request.get("result", "ok")

    def cancel(self, operation_id):
        return {"operation_id": operation_id, "state": "cancelled"}

    def read_output(self, notebook_path, request):
        self.paths.append(notebook_path)
        return request.get("result", "ok")

    def restart_kernel(self, notebook_path):
        self.paths.append(notebook_path)


class FakeDatalayerTransport:
    backend_identity = "jupyter-mcp-server/2.1.16"

    def __init__(self):
        self.version = "etag-v1"
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        if method in {"read_notebook", "read_cell"}:
            return {
                "cells": [] if method == "read_notebook" else None,
                "cell_id": params.get("cell_id"),
                "_mcp_meta": {
                    "io.modelcontextprotocol/cache": {"etag": self.version}
                },
            }
        self.version = "etag-v2"
        return {"ok": True}

    def close(self):
        return
        self.restarted = True


@dataclass
class FakeWorkspace:
    files: dict[str, bytes] = field(default_factory=dict)

    def read_file(self, path: str) -> bytes:
        return self.files[path]

    def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def move_file(self, source: str, destination: str) -> None:
        self.files[destination] = self.files.pop(source)

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)

    def list_files(self, path: str, *, limit: int):
        return [{"path": name} for name in sorted(self.files)[:limit]]


class FakeLeaseValidator:
    def __init__(self, valid: bool = True):
        self.valid = valid
        self.calls = 0

    def validate(self, session_id: UUID, lease_id: UUID) -> None:
        self.calls += 1
        if not self.valid:
            raise PermissionDenied("lease expired")


def make_adapter(
    *,
    mode=AgentMode.WRITE,
    notebook=None,
    workspace=None,
    lease=True,
    output_limit=1024 * 1024,
):
    return SemanticMcpAdapter(
        mode=mode,
        notebook=notebook or FakeNotebook(),
        workspace=workspace or FakeWorkspace(),
        active_notebook_path="notebooks/main.ipynb",
        session_id=uuid4(),
        lease_id=uuid4() if lease else None,
        lease_validator=FakeLeaseValidator() if lease else None,
        output_limit=output_limit,
    )


def test_readonly_denies_all_mutation_execution_and_workspace_access():
    adapter = make_adapter(mode=AgentMode.READONLY, lease=False)
    denied = (
        lambda: adapter.mutate_notebook({}, expected_version="v1"),
        lambda: adapter.execute({}),
        adapter.restart_kernel,
        lambda: adapter.read_file("notes.txt"),
        lambda: adapter.cancel_execution("operation"),
    )
    for operation in denied:
        with pytest.raises(PermissionDenied):
            operation()


def test_write_requires_current_lease():
    adapter = make_adapter(lease=False)
    with pytest.raises(PermissionDenied, match="lease"):
        adapter.write_file("outputs/result.txt", b"ok")


def test_active_notebook_cannot_be_changed_by_generic_file_api():
    adapter = make_adapter()
    with pytest.raises(ConflictError):
        adapter.write_file("notebooks/main.ipynb", b"replacement")
    with pytest.raises(ConflictError):
        adapter.move_file("notebooks/main.ipynb", "other.ipynb")
    with pytest.raises(ConflictError):
        adapter.delete_file("notebooks/main.ipynb")


def test_fixed_target_does_not_follow_browser_focus_and_move_updates_only_explicitly():
    notebook = FakeNotebook()
    adapter = make_adapter(notebook=notebook)
    adapter.inspect_notebook()
    adapter.inspect_notebook()
    assert notebook.paths == ["notebooks/main.ipynb", "notebooks/main.ipynb"]

    assert adapter.move_notebook("renamed.ipynb", expected_version="v1") == "renamed.ipynb"
    adapter.inspect_notebook()
    assert notebook.paths[-1] == "renamed.ipynb"


def test_workspace_write_is_allowed_for_other_files_with_lease():
    workspace = FakeWorkspace()
    adapter = make_adapter(workspace=workspace)
    adapter.write_file("outputs/result.txt", b"ok")
    assert workspace.files["outputs/result.txt"] == b"ok"


def test_output_is_bounded_for_text_binary_and_structured_values():
    text = bound_output("x" * 100, 10)
    binary = bound_output(b"y" * 100, 10)
    structured = bound_output({"value": "z" * 100}, 20)
    assert text.serialized_size == 100 and text.truncated
    assert binary.mime_type == "application/octet-stream" and binary.truncated
    assert structured.mime_type == "application/json" and structured.truncated
    assert len(str(text.payload).encode()) <= 10
    assert isinstance(structured.payload, dict)
    json.dumps(structured.payload)


def test_datalayer_mutation_enforces_and_refreshes_authoritative_etag():
    transport = FakeDatalayerTransport()
    backend = DatalayerBackend(transport)

    assert backend.inspect("main.ipynb")["document_version"] == "etag-v1"
    with pytest.raises(ConflictError):
        backend.mutate(
            "main.ipynb",
            {"method": "cell.update", "params": {"cell_id": "c1", "cell_source": "x"}},
            expected_version="stale",
        )
    updated = backend.mutate(
        "main.ipynb",
        {"method": "cell.update", "params": {"cell_id": "c1", "cell_source": "x"}},
        expected_version="etag-v1",
    )
    assert updated == "etag-v2"


def test_datalayer_cell_read_promotes_backend_etag_to_document_version():
    backend = DatalayerBackend(FakeDatalayerTransport())

    result = backend.read_cell("main.ipynb", "c1")

    assert result["document_version"] == "etag-v1"


def test_datalayer_backend_activates_the_fixed_notebook_before_use():
    transport = FakeDatalayerTransport()
    backend = DatalayerBackend(transport)

    backend.activate("notebooks/main.ipynb")

    assert transport.calls == [
        (
            "use_notebook",
            {
                "notebook_name": "notebooks/main.ipynb",
                "notebook_path": "notebooks/main.ipynb",
                "mode": "connect",
            },
        )
    ]


def test_descriptor_is_non_secret_and_lists_readonly_contract():
    descriptor = build_attachment_descriptor(
        session_id=uuid4(),
        notebook_path="main.ipynb",
        mode=AgentMode.READONLY,
        backend="backend/1",
        available=True,
        write_lease_available=False,
    ).public_dict()
    encoded = json.dumps(descriptor)
    assert "token" not in encoded.lower()
    assert descriptor["capabilities"] == ["cell.read", "notebook.read", "output.read"]


def test_write_descriptor_fails_closed_to_conformant_read_methods():
    descriptor = build_attachment_descriptor(
        session_id=uuid4(),
        notebook_path="main.ipynb",
        mode=AgentMode.WRITE,
        backend="backend/1",
        available=True,
        write_lease_available=True,
    ).public_dict()

    assert descriptor["capabilities"] == ["cell.read", "notebook.read", "output.read"]
    adapter = make_adapter(mode=AgentMode.WRITE)
    with pytest.raises(PermissionDenied, match="not available"):
        adapter.call(
            "cell.update",
            {"cell_id": "c1", "cell_source": "x", "expected_document_version": "v1"},
        )


def test_stdio_bridge_exposes_only_semantic_tools_and_structured_permission_error():
    adapter = make_adapter(mode=AgentMode.READONLY, lease=False)
    requests = "\n".join(
        (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "kernel.restart", "arguments": {}},
                }
            ),
        )
    )
    output = StringIO()
    assert run_stdio_bridge(adapter, input_stream=StringIO(requests), output_stream=output) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == {"notebook.read", "cell.read", "output.read"}
    assert responses[2]["result"]["isError"] is True
    detail = json.loads(responses[2]["result"]["content"][0]["text"])
    assert detail["code"] == "permission_denied"


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("notebook.read", {}),
        ("cell.read", {"cell_id": "c1"}),
        ("output.read", {"cell_id": "c1"}),
    ],
)
def test_stdio_bridge_bounds_complete_tool_response_without_duplicate_content(
    method, arguments
):
    class LargeNotebook(FakeNotebook):
        def _large_result(self, notebook_path):
            return {
                "path": notebook_path,
                "document_version": "v1",
                "cells": [{"source": "x" * 20_000}],
                "authoritative_location": "notebooks/main.ipynb",
            }

        def inspect(self, notebook_path):
            return self._large_result(notebook_path)

        def read_cell(self, notebook_path, cell_id):
            return self._large_result(notebook_path)

        def read_output(self, notebook_path, request):
            return self._large_result(notebook_path)

    adapter = make_adapter(
        mode=AgentMode.READONLY,
        notebook=LargeNotebook(),
        lease=False,
        output_limit=MIN_MCP_OUTPUT_BYTES,
    )
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": method, "arguments": arguments},
        }
    )
    output = StringIO()

    run_stdio_bridge(adapter, input_stream=StringIO(request), output_stream=output)

    wire = output.getvalue()
    assert len(wire.encode("utf-8")) <= MIN_MCP_OUTPUT_BYTES
    response = json.loads(wire)
    assert "structuredContent" not in response["result"]
    assert len(response["result"]["content"]) == 1
    envelope = json.loads(response["result"]["content"][0]["text"])
    assert envelope["truncated"] is True
    assert envelope["serialized_size"] > 20_000
    assert envelope["authoritative_location"] == "notebooks/main.ipynb"
    assert isinstance(envelope["payload"], dict)
    assert envelope["payload"]["document_version"] == "v1"
    json.dumps(envelope["payload"])


def test_stdio_bridge_bounds_errors_and_continues_after_bad_requests():
    class BrokenNotebook(FakeNotebook):
        def inspect(self, notebook_path):
            raise RuntimeError("backend exploded " + "z" * 20_000)

    adapter = make_adapter(
        mode=AgentMode.READONLY,
        notebook=BrokenNotebook(),
        lease=False,
        output_limit=MIN_MCP_OUTPUT_BYTES,
    )
    requests = "\n".join(
        (
            "[]",
            "{not-json",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "notebook.read", "arguments": {}},
                }
            ),
            json.dumps(
                {"jsonrpc": "2.0", "id": 4, "method": "unknown/method"}
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": [],
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "unknown/notification"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "notebook.read", "arguments": {}},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "ping"}),
            json.dumps({"jsonrpc": "2.0", "id": 6, "method": "ping"}),
        )
    )
    output = StringIO()

    run_stdio_bridge(adapter, input_stream=StringIO(requests), output_stream=output)

    lines = output.getvalue().splitlines(keepends=True)
    assert len(lines) == 6
    assert all(len(line.encode("utf-8")) <= MIN_MCP_OUTPUT_BYTES for line in lines)
    responses = [json.loads(line) for line in lines]
    assert responses[0]["error"]["code"] == JSONRPC_INVALID_REQUEST
    assert responses[1]["error"]["code"] == JSONRPC_PARSE_ERROR
    assert responses[2]["result"]["isError"] is True
    tool_error = json.loads(responses[2]["result"]["content"][0]["text"])
    assert tool_error["code"] == "backend_error"
    assert responses[3]["error"]["code"] == JSONRPC_METHOD_NOT_FOUND
    assert responses[4]["error"]["code"] == JSONRPC_INVALID_PARAMS
    assert responses[5]["result"] == {}


def test_adapter_rejects_output_limit_that_cannot_fit_tool_discovery():
    with pytest.raises(ValueError, match="tools/list"):
        make_adapter(output_limit=MIN_MCP_OUTPUT_BYTES - 1)


def test_minimum_output_limit_fits_tool_discovery_with_largest_safe_id():
    adapter = make_adapter(
        mode=AgentMode.READONLY,
        lease=False,
        output_limit=MIN_MCP_OUTPUT_BYTES,
    )
    request = json.dumps(
        {"jsonrpc": "2.0", "id": "\x00" * 128, "method": "tools/list"}
    )
    output = StringIO()

    run_stdio_bridge(adapter, input_stream=StringIO(request), output_stream=output)

    assert len(output.getvalue().encode("utf-8")) == MIN_MCP_OUTPUT_BYTES
    assert len(json.loads(output.getvalue())["result"]["tools"]) == 3


def test_request_id_accepts_exactly_128_utf8_bytes_and_rejects_129():
    adapter = make_adapter(
        mode=AgentMode.READONLY,
        lease=False,
        output_limit=MIN_MCP_OUTPUT_BYTES,
    )
    requests = "\n".join(
        (
            json.dumps(
                {"jsonrpc": "2.0", "id": "x" * 128, "method": "tools/list"}
            ),
            json.dumps(
                {"jsonrpc": "2.0", "id": "x" * 129, "method": "tools/list"}
            ),
        )
    )
    output = StringIO()

    run_stdio_bridge(adapter, input_stream=StringIO(requests), output_stream=output)

    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["id"] == "x" * 128
    assert len(responses[0]["result"]["tools"]) == 3
    assert responses[1]["id"] is None
    assert responses[1]["error"]["code"] == JSONRPC_INVALID_REQUEST
