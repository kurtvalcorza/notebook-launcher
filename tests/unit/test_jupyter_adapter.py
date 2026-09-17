from dataclasses import replace
from uuid import uuid4

import pytest

from notebook_launcher.jupyter import (
    BackendNotebookState,
    HttpCollaborationBackend,
    JupyterSessionAdapter,
    JupyterStateError,
    normalize_notebook_path,
)


class FakeBackend:
    def __init__(self, state):
        self.state = state
        self.moves = []

    def observe_notebook(self, path):
        assert path == self.state.path
        return self.state

    def move_notebook(self, source, destination, *, expected_version):
        assert source == self.state.path
        assert expected_version == self.state.version
        self.moves.append((source, destination, expected_version))
        self.state = replace(self.state, path=destination, version="v2")
        return self.state


def test_fixed_target_does_not_follow_browser_focus():
    backend = FakeBackend(BackendNotebookState("notebooks/main.ipynb", "v1", True))
    adapter = JupyterSessionAdapter("notebooks/main.ipynb", backend)

    assert adapter.browser_focused("notebooks/other.ipynb") == "notebooks/main.ipynb"
    assert adapter.observe().active_notebook_path == "notebooks/main.ipynb"


def test_authoritative_observation_fails_closed_without_collaboration():
    backend = FakeBackend(BackendNotebookState("main.ipynb", "v1", False))
    adapter = JupyterSessionAdapter("main.ipynb", backend)

    with pytest.raises(JupyterStateError, match="not ready"):
        adapter.require_collaboration()


class FakeHttpResponse:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode()


def test_contents_style_200_is_not_collaboration_readiness(monkeypatch):
    monkeypatch.setattr(
        "notebook_launcher.jupyter.urlopen",
        lambda *_args, **_kwargs: FakeHttpResponse(
            {"name": "main.ipynb", "type": "notebook", "format": "json"}
        ),
    )
    backend = HttpCollaborationBackend(
        jupyter_url="http://127.0.0.1:8888",
        token="secret",
    )

    with pytest.raises(JupyterStateError, match="identity is unavailable"):
        JupyterSessionAdapter("main.ipynb", backend).require_collaboration()


def test_collaboration_session_identity_is_required_for_readiness(monkeypatch):
    observed = {}

    def open_collaboration(request, *, timeout):
        observed["url"] = request.full_url
        observed["method"] = request.get_method()
        observed["authorization"] = request.get_header("Authorization")
        observed["payload"] = request.data
        observed["timeout"] = timeout
        return FakeHttpResponse(
            {
                "format": "json",
                "type": "notebook",
                "fileId": "file-123",
                "sessionId": "server-456",
            },
            status=201,
        )

    monkeypatch.setattr("notebook_launcher.jupyter.urlopen", open_collaboration)
    backend = HttpCollaborationBackend(
        jupyter_url="http://127.0.0.1:8888",
        token="secret",
    )

    observation = JupyterSessionAdapter(
        "main.ipynb",
        backend,
    ).require_collaboration()

    assert observation.collaboration_ready
    assert observation.authoritative_version == "server-456:file-123"
    assert observed == {
        "url": "http://127.0.0.1:8888/api/collaboration/session/main.ipynb",
        "method": "PUT",
        "authorization": "token secret",
        "payload": b'{"format": "json", "type": "notebook"}',
        "timeout": 2.0,
    }


def test_active_rename_requires_current_version_and_updates_metadata_callback():
    backend = FakeBackend(BackendNotebookState("main.ipynb", "v1", True))
    changes = []
    adapter = JupyterSessionAdapter(
        "main.ipynb",
        backend,
        on_active_path_changed=lambda source, destination: changes.append(
            (source, destination)
        ),
    )

    with pytest.raises(JupyterStateError, match="stale"):
        adapter.rename_active("renamed.ipynb", expected_version="old")
    moved = adapter.rename_active("renamed.ipynb", expected_version="v1")

    assert moved.active_notebook_path == "renamed.ipynb"
    assert moved.authoritative_version == "v2"
    assert changes == [("main.ipynb", "renamed.ipynb")]
    assert backend.moves == [("main.ipynb", "renamed.ipynb", "v1")]


def test_request_ids_and_busy_parent_observations_preserve_ownership():
    backend = FakeBackend(BackendNotebookState("main.ipynb", "v1", True))
    adapter = JupyterSessionAdapter("main.ipynb", backend)
    operation_id = uuid4()
    adapter.register_request(operation_id, "msg-agent")

    browser = adapter.observe_kernel_status("busy", parent_msg_id="msg-browser")
    assert browser.owner_operation_id is None
    agent = adapter.observe_kernel_status("busy", parent_msg_id="msg-agent")
    assert agent.owner_operation_id == operation_id
    idle = adapter.observe_kernel_status("idle", parent_msg_id="msg-agent")
    assert idle.owner_operation_id is None


def test_active_rename_rolls_back_when_metadata_update_fails():
    backend = FakeBackend(BackendNotebookState("main.ipynb", "v1", True))

    def fail_metadata(_source, _destination):
        raise RuntimeError("database unavailable")

    adapter = JupyterSessionAdapter(
        "main.ipynb", backend, on_active_path_changed=fail_metadata
    )

    with pytest.raises(JupyterStateError, match="rolled back"):
        adapter.rename_active("renamed.ipynb", expected_version="v1")

    assert adapter.active_notebook_path == "main.ipynb"
    assert backend.state.path == "main.ipynb"
    assert backend.moves == [
        ("main.ipynb", "renamed.ipynb", "v1"),
        ("renamed.ipynb", "main.ipynb", "v2"),
    ]


@pytest.mark.parametrize(
    "path",
    ["../escape.ipynb", "/absolute.ipynb", "not-a-notebook.txt", ""],
)
def test_notebook_path_validation(path):
    with pytest.raises(ValueError):
        normalize_notebook_path(path)
