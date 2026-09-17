from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from notebook_launcher.app import AppServices, create_app
from notebook_launcher.config import Settings
from notebook_launcher.launch_auth import LaunchTokenCodec
from notebook_launcher.state import StateStore


class UnusedResolver:
    def resolve(self, request):
        raise AssertionError("resolver must not run")


class UnusedStarter:
    def start(self, **kwargs):
        raise AssertionError("starter must not run")


@dataclass
class FakeLifecycle:
    known: set = field(default_factory=set)
    stopped: list = field(default_factory=list)

    def stop(self, session_id):
        if session_id not in self.known:
            return False
        self.stopped.append(session_id)
        return True


@dataclass
class FakeDescriptors:
    values: dict = field(default_factory=dict)

    def describe(self, session_id):
        return self.values.get(session_id)


def make_client(tmp_path: Path):
    settings = Settings(root=tmp_path, host="127.0.0.1", port=8080)
    state = StateStore(settings.state_db)
    state.initialize()
    lifecycle = FakeLifecycle()
    descriptors = FakeDescriptors()
    services = AppServices(
        state=state,
        token_codec=LaunchTokenCodec(b"x" * 32),
        resolver=UnusedResolver(),
        starter=UnusedStarter(),
        lifecycle=lifecycle,
        mcp_descriptors=descriptors,
    )
    return TestClient(create_app(settings, services)), lifecycle, descriptors


def test_ready_mcp_descriptor_is_returned_without_credentials(tmp_path):
    client, _lifecycle, descriptors = make_client(tmp_path)
    session_id = uuid4()
    descriptors.values[session_id] = {
        "session_id": str(session_id),
        "notebook_path": "main.ipynb",
        "available": True,
        "status": "available",
        "transport": "stdio",
        "command": "notebook-launcher",
        "args": ["mcp", str(session_id)],
        "backend": "jupyter-mcp-server/2.x",
    }
    response = client.get(f"/api/sessions/{session_id}/mcp")
    assert response.status_code == 200
    assert "token" not in response.text.lower()


def test_stopped_descriptor_is_gone(tmp_path):
    client, _lifecycle, descriptors = make_client(tmp_path)
    session_id = uuid4()
    descriptors.values[session_id] = {"available": False, "status": "stopped"}
    response = client.get(f"/api/sessions/{session_id}/mcp")
    assert response.status_code == 410


def test_stop_is_local_only_and_idempotent_service_owned(tmp_path):
    client, lifecycle, _descriptors = make_client(tmp_path)
    session_id = uuid4()
    lifecycle.known.add(session_id)
    path = f"/api/sessions/{session_id}"
    denied = client.delete(path, headers={"Origin": "https://evil.example"})
    assert denied.status_code == 400
    assert lifecycle.stopped == []

    stopped = client.delete(
        path, headers={"Origin": "http://127.0.0.1:8080"}
    )
    assert stopped.status_code == 204
    assert lifecycle.stopped == [session_id]


def test_unknown_session_stop_returns_not_found(tmp_path):
    client, _lifecycle, _descriptors = make_client(tmp_path)
    response = client.delete(
        f"/api/sessions/{uuid4()}",
        headers={"Origin": "http://127.0.0.1:8080"},
    )
    assert response.status_code == 404
