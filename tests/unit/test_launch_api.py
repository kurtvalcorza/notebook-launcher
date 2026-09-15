from datetime import UTC, datetime
from pathlib import Path
import re

from fastapi.testclient import TestClient

from notebook_launcher.app import AppServices, create_app
from notebook_launcher.config import Settings
from notebook_launcher.launch_auth import LaunchTokenCodec
from notebook_launcher.launches import StateLaunchStarter
from notebook_launcher.models import LaunchRequest, ResolvedSource
from notebook_launcher.state import StateStore


class FakeResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, request: LaunchRequest) -> ResolvedSource:
        self.calls += 1
        return ResolvedSource(
            repository_id=123,
            repository_node_id="R_123",
            owner="owner",
            repository="repo",
            requested_ref=request.ref or "main",
            commit_sha="a" * 40,
            notebook_path=request.path or "demo.ipynb",
            clone_url="https://github.com/owner/repo.git",
            resolved_at=datetime.now(UTC),
        )


def make_client(tmp_path: Path):
    settings = Settings(root=tmp_path, host="127.0.0.1", port=8080)
    state = StateStore(settings.state_db)
    state.initialize()
    resolver = FakeResolver()
    services = AppServices(
        state=state,
        token_codec=LaunchTokenCodec(b"x" * 32),
        resolver=resolver,
        starter=StateLaunchStarter(state),
    )
    return TestClient(create_app(settings, services)), resolver, state


def get_token(html: str) -> str:
    match = re.search(r"const launchToken = '([^']+)'", html)
    assert match
    return match.group(1)


def launch_request():
    return {
        "repo": "owner/repo",
        "ref": "main",
        "path": "demo.ipynb",
        "gpu": "auto",
        "agent_mode": "write",
    }


def test_get_open_is_preview_only(tmp_path: Path):
    client, resolver, state = make_client(tmp_path)
    response = client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    assert response.status_code == 200
    assert resolver.calls == 1
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM launches").fetchone()[0] == 0
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_post_launch_consumes_token_once_and_persists_pending_trust(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    preview = client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    token = get_token(preview.text)
    body = {"request": launch_request(), "launch_token": token}

    first = client.post(
        "/api/launches",
        json=body,
        headers={"Origin": "http://127.0.0.1:8080"},
    )
    second = client.post(
        "/api/launches",
        json=body,
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert first.status_code == 202
    assert first.json()["state"] == "pending_trust"
    assert second.status_code == 409
    row = state.get_launch(first.json()["launch_id"])
    assert row is not None
    assert row["state"] == "pending_trust"


def test_cross_origin_launch_is_denied_before_launch_record(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    preview = client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    token = get_token(preview.text)

    response = client.post(
        "/api/launches",
        json={"request": launch_request(), "launch_token": token},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM launches").fetchone()[0] == 0


def test_token_is_bound_to_request(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    preview = client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    token = get_token(preview.text)
    modified = launch_request()
    modified["gpu"] = "on"

    response = client.post(
        "/api/launches",
        json={"request": modified, "launch_token": token},
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert response.status_code == 403
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM launches").fetchone()[0] == 0
