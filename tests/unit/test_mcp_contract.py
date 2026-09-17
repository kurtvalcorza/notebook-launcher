from dataclasses import dataclass, field

import pytest

from notebook_launcher.errors import ConflictError, PermissionDenied
from notebook_launcher.mcp import SemanticMcpAdapter, bound_output
from notebook_launcher.models import AgentMode


@dataclass
class FakeNotebook:
    version: str = "v1"
    restarted: bool = False

    def inspect(self):
        return {"version": self.version}

    def mutate(self, operation, *, expected_version: str):
        if expected_version != self.version:
            raise ConflictError()
        self.version = "v2"
        return self.version

    def execute(self, request):
        return request.get("result", "ok")

    def restart_kernel(self):
        self.restarted = True


@dataclass
class FakeWorkspace:
    active_notebook_path: str = "notebooks/main.ipynb"
    files: dict[str, bytes] = field(default_factory=dict)

    def read_file(self, path: str) -> bytes:
        return self.files[path]

    def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def delete_file(self, path: str) -> None:
        self.files.pop(path, None)


def test_readonly_denies_mutation_execution_and_restart():
    adapter = SemanticMcpAdapter(
        mode=AgentMode.READONLY,
        notebook=FakeNotebook(),
        workspace=FakeWorkspace(),
    )
    with pytest.raises(PermissionDenied):
        adapter.mutate_notebook({}, expected_version="v1")
    with pytest.raises(PermissionDenied):
        adapter.execute({})
    with pytest.raises(PermissionDenied):
        adapter.restart_kernel()


def test_active_notebook_cannot_be_overwritten_by_generic_file_api():
    adapter = SemanticMcpAdapter(
        mode=AgentMode.WRITE,
        notebook=FakeNotebook(),
        workspace=FakeWorkspace(),
    )
    with pytest.raises(ConflictError):
        adapter.write_file("notebooks/main.ipynb", b"replacement")


def test_workspace_write_is_allowed_for_other_files():
    workspace = FakeWorkspace()
    adapter = SemanticMcpAdapter(
        mode=AgentMode.WRITE,
        notebook=FakeNotebook(),
        workspace=workspace,
    )
    adapter.write_file("outputs/result.txt", b"ok")
    assert workspace.files["outputs/result.txt"] == b"ok"


def test_output_is_bounded():
    envelope = bound_output("x" * 100, 10)
    assert envelope.serialized_size == 100
    assert envelope.truncated is True
    assert len(envelope.payload.encode()) <= 10
