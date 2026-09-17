import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
        self.commit_sha = "a" * 40

    def resolve(self, request: LaunchRequest) -> ResolvedSource:
        self.calls += 1
        return ResolvedSource(
            repository_id=123,
            repository_node_id="R_123",
            owner="owner",
            repository="repo",
            requested_ref=request.ref or "main",
            commit_sha=self.commit_sha,
            notebook_path=request.path or "demo.ipynb",
            clone_url="https://github.com/owner/repo.git",
            resolved_at=datetime.now(UTC),
        )


class FakePipelineLifecycle:
    def __init__(self):
        self.reconciliations = 0
        self.closed = False

    def reconcile_starting_sessions(self):
        self.reconciliations += 1

    def close(self):
        self.closed = True


def make_client(tmp_path: Path):
    settings = Settings(root=tmp_path, host="127.0.0.1", port=8080)
    state = StateStore(settings.state_db)
    state.initialize()
    resolver = FakeResolver()
    services = AppServices(
        state=state,
        token_codec=LaunchTokenCodec(b"x" * 32),
        resolver=resolver,
        starter=StateLaunchStarter(state, settings),
    )
    return TestClient(create_app(settings, services)), resolver, state


def get_token(page: str) -> str:
    match = re.search(r"const launchToken=(\"[^\"]+\");", page)
    assert match
    return json.loads(match.group(1))


def launch_request():
    return {
        "repo": "owner/repo",
        "ref": "main",
        "path": "demo.ipynb",
        "gpu": "auto",
        "agent_mode": "write",
    }


def authorize_pending(client: TestClient):
    preview = client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    token = get_token(preview.text)
    return client.post(
        "/api/launches",
        json={"request": launch_request(), "launch_token": token},
        headers={"Origin": "http://127.0.0.1:8080"},
    )


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
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_post_launch_consumes_token_once_and_persists_pending_trust(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    first = authorize_pending(client)
    assert first.status_code == 202
    assert first.json()["state"] == "pending_trust"
    assert "trust_confirmation_url" in first.json()
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
    assert response.status_code == 400
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM launches").fetchone()[0] == 0


def test_invalid_and_expired_tokens_have_distinct_error_codes(tmp_path: Path):
    client, _resolver, _state = make_client(tmp_path)
    invalid = client.post(
        "/api/launches",
        json={"request": launch_request(), "launch_token": "invalid"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    settings = Settings(root=tmp_path / "expired", host="127.0.0.1", port=8080)
    state = StateStore(settings.state_db)
    state.initialize()
    resolver = FakeResolver()
    codec = LaunchTokenCodec(b"x" * 32, ttl_seconds=-1)
    services = AppServices(
        state=state,
        token_codec=codec,
        resolver=resolver,
        starter=StateLaunchStarter(state, settings),
    )
    expired_client = TestClient(create_app(settings, services))
    expired_preview = expired_client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    expired_token = get_token(expired_preview.text)
    expired = expired_client.post(
        "/api/launches",
        json={"request": launch_request(), "launch_token": expired_token},
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert invalid.status_code == 400
    assert invalid.json()["detail"]["error"] == "invalid_launch_token"
    assert expired.status_code == 400
    assert expired.json()["detail"]["error"] == "launch_token_expired"


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
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "launch_request_changed"
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM launches").fetchone()[0] == 0


def test_launch_token_replay_has_contract_error_code(tmp_path: Path):
    client, _resolver, _state = make_client(tmp_path)
    preview = client.get(
        "/open",
        params={"repo": "owner/repo", "ref": "main", "path": "demo.ipynb"},
    )
    token = get_token(preview.text)
    payload = {"request": launch_request(), "launch_token": token}
    headers = {"Origin": "http://127.0.0.1:8080"}

    assert client.post("/api/launches", json=payload, headers=headers).status_code == 202
    replay = client.post("/api/launches", json=payload, headers=headers)

    assert replay.status_code == 400
    assert replay.json()["detail"]["error"] == "launch_token_replayed"


def test_exact_commit_trust_confirmation_is_one_time_and_authorizes_launch(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    pending = authorize_pending(client)
    payload = pending.json()
    launch_id = payload["launch_id"]
    url = payload["trust_confirmation_url"]
    nonce = parse_qs(urlparse(url).query)["nonce"][0]

    page = client.get(url)
    assert page.status_code == 200
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"

    grant = client.post(
        f"/api/launches/{launch_id}/trust",
        json={"nonce": nonce, "scope": "exact_commit"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )
    replay = client.post(
        f"/api/launches/{launch_id}/trust",
        json={"nonce": nonce, "scope": "exact_commit"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert grant.status_code == 200
    assert grant.json()["state"] == "authorized"
    assert replay.status_code == 409
    row = state.get_launch(launch_id)
    assert row["state"] == "authorized"
    with state.connect() as conn:
        trust = conn.execute("SELECT * FROM trust_records").fetchone()
    assert trust["repository_id"] == 123
    assert trust["scope"] == "exact_commit"
    assert trust["commit_sha"] == "a" * 40


def test_trust_confirmation_rejects_source_identity_change(tmp_path: Path):
    client, resolver, state = make_client(tmp_path)
    payload = authorize_pending(client).json()
    launch_id = payload["launch_id"]
    nonce = parse_qs(urlparse(payload["trust_confirmation_url"]).query)["nonce"][0]
    resolver.commit_sha = "b" * 40

    response = client.post(
        f"/api/launches/{launch_id}/trust",
        json={"confirmation_nonce": nonce, "decision": "trust_exact_commit"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert response.status_code == 409
    assert "source identity changed" in response.json()["detail"]
    assert state.get_launch(launch_id)["state"] == "pending_trust"
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM trust_records").fetchone()[0] == 0


def test_app_startup_reconciles_orphaned_runtime_starts(tmp_path: Path):
    settings = Settings(root=tmp_path, host="127.0.0.1", port=8080)
    state = StateStore(settings.state_db)
    state.initialize()
    pipeline = FakePipelineLifecycle()
    services = AppServices(
        state=state,
        token_codec=LaunchTokenCodec(b"x" * 32),
        resolver=FakeResolver(),
        starter=StateLaunchStarter(state, settings),
        pipeline=pipeline,  # type: ignore[arg-type]
    )

    with TestClient(create_app(settings, services)):
        assert pipeline.reconciliations == 1

    assert pipeline.closed


def test_repository_trust_records_no_commit(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    payload = authorize_pending(client).json()
    launch_id = payload["launch_id"]
    nonce = parse_qs(urlparse(payload["trust_confirmation_url"]).query)["nonce"][0]

    response = client.post(
        f"/api/launches/{launch_id}/trust",
        json={"nonce": nonce, "scope": "repository"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )
    assert response.status_code == 200
    with state.connect() as conn:
        trust = conn.execute("SELECT * FROM trust_records").fetchone()
    assert trust["scope"] == "repository"
    assert trust["commit_sha"] is None


def test_contract_trust_decision_deny_creates_no_grant(tmp_path: Path):
    client, _resolver, state = make_client(tmp_path)
    payload = authorize_pending(client).json()
    launch_id = payload["launch_id"]
    nonce = parse_qs(urlparse(payload["trust_confirmation_url"]).query)["nonce"][0]

    response = client.post(
        f"/api/launches/{launch_id}/trust",
        json={"confirmation_nonce": nonce, "decision": "deny"},
        headers={"Origin": "http://127.0.0.1:8080"},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "deny"
    assert state.get_launch(launch_id)["error_code"] == "trust_denied"
    with state.connect() as conn:
        assert conn.execute("SELECT count(*) FROM trust_records").fetchone()[0] == 0


def test_trust_list_and_revoke_api(tmp_path: Path):
    client, _resolver, _state = make_client(tmp_path)
    payload = authorize_pending(client).json()
    launch_id = payload["launch_id"]
    nonce = parse_qs(urlparse(payload["trust_confirmation_url"]).query)["nonce"][0]
    granted = client.post(
        f"/api/launches/{launch_id}/trust",
        json={
            "confirmation_nonce": nonce,
            "decision": "trust_exact_commit",
        },
        headers={"Origin": "http://127.0.0.1:8080"},
    ).json()

    records = client.get("/api/trust").json()
    assert [record["id"] for record in records] == [granted["trust_id"]]
    revoked = client.delete(
        f"/api/trust/{granted['trust_id']}",
        headers={"Origin": "http://127.0.0.1:8080"},
    )
    assert revoked.status_code == 204
    assert client.get("/api/trust").json() == []
