import json
import threading
from datetime import UTC, datetime
from typing import ClassVar
from uuid import uuid4

import pytest
from pydantic import ValidationError

from notebook_launcher.cli import (
    McpBridgeContext,
    SqliteLeaseValidator,
    build_parser,
    main,
    resolve_mcp_bridge,
)
from notebook_launcher.config import Settings
from notebook_launcher.errors import PermissionDenied
from notebook_launcher.launches import StateMcpDescriptorProvider
from notebook_launcher.leases import WritableLeaseStore
from notebook_launcher.mcp import MIN_MCP_OUTPUT_BYTES
from notebook_launcher.state import StateStore


class FakeTransport:
    instances: ClassVar[list["FakeTransport"]] = []

    def __init__(self, argv, *, env, backend_identity):
        self.argv = tuple(argv)
        self.env = env
        self.backend_identity = backend_identity
        self.closed = False
        self.calls = []
        self.instances.append(self)

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "read_notebook":
            return {"notebook": params["notebook_name"], "document_version": "v1"}
        return {"ok": True}

    def close(self):
        self.closed = True


def insert_ready_session(state, *, mode="write"):
    workspace_id = uuid4()
    session_id = uuid4()
    now = datetime.now(UTC).isoformat()
    with state.transaction() as conn:
        conn.execute(
            """
            INSERT INTO workspaces (
                id, source_repository_id, source_owner, source_repository,
                source_commit_sha, source_notebook_path, root_path, work_path,
                outputs_path, active_notebook_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(workspace_id), 1, "owner", "repo", "a" * 40, "main.ipynb",
                "/workspace", "/workspace/work", "/workspace/outputs",
                "main.ipynb", now, now,
            ),
        )
        conn.execute(
            """
            INSERT INTO notebook_sessions (
                id, workspace_id, runtime_id, host_port, gpu_enabled,
                agent_mode, sandbox_verified, network_verified,
                collaboration_ready, state, notebook_url, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(session_id), str(workspace_id), "runtime", 9001, 0, mode,
                1, 1, 1, "ready", "http://127.0.0.1:9001/lab", now,
            ),
        )
    return session_id


def make_state(tmp_path):
    settings = Settings(root=tmp_path)
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    return settings, state


def write_private_config(settings, session_id):
    session_dir = settings.runtime_dir / str(session_id)
    session_dir.mkdir()
    (session_dir / "mcp-private.json").write_text(
        json.dumps(
            {"jupyter_url": "http://127.0.0.1:9001", "jupyter_token": "secret"}
        ),
        encoding="utf-8",
    )


def test_mcp_cli_requires_session_and_accepts_profile():
    session_id = uuid4()
    args = build_parser().parse_args(
        ["mcp", str(session_id), "--mode", "readonly", "--client-label", "test"]
    )
    assert args.session_id == session_id
    assert args.mode == "readonly"


def test_resolver_keeps_credentials_out_of_argv_and_releases_write_lease(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    settings, state = make_state(tmp_path)
    session_id = insert_ready_session(state)
    write_private_config(settings, session_id)
    context = resolve_mcp_bridge(
        state=state,
        settings=settings,
        session_id=session_id,
        requested_mode="write",
        client_label="test",
        transport_factory=FakeTransport,
    )
    transport = context.transport
    assert "secret" not in " ".join(transport.argv)
    assert transport.env["JUPYTER_TOKEN"] == "secret"
    assert "OPENAI_API_KEY" not in transport.env
    assert transport.calls[0] == (
        "use_notebook",
        {
            "notebook_name": "main.ipynb",
            "notebook_path": "main.ipynb",
            "mode": "connect",
        },
    )
    assert context.adapter.inspect_notebook()["notebook"] == "main.ipynb"
    with state.connect() as conn:
        row = conn.execute(
            """
            SELECT acquired_at, heartbeat_at FROM writable_agent_leases
            WHERE released_at IS NULL
            """
        ).fetchone()
    assert row is not None
    assert row["heartbeat_at"] == row["acquired_at"]

    context.close()
    with state.connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM writable_agent_leases WHERE released_at IS NULL"
        ).fetchone()[0] == 0


def test_stopped_session_invalidates_existing_bridge(tmp_path):
    settings, state = make_state(tmp_path)
    session_id = insert_ready_session(state)
    write_private_config(settings, session_id)
    context = resolve_mcp_bridge(
        state=state,
        settings=settings,
        session_id=session_id,
        requested_mode="readonly",
        client_label=None,
        transport_factory=FakeTransport,
    )
    with state.transaction() as conn:
        conn.execute(
            "UPDATE notebook_sessions SET state = 'stopped' WHERE id = ?",
            (str(session_id),),
        )
    with pytest.raises(PermissionDenied, match="stopped"):
        context.adapter.inspect_notebook()
    context.close()


def test_bridge_heartbeats_until_close():
    lease_id = uuid4()

    class LeaseProbe:
        def __init__(self):
            self.heartbeat_seen = threading.Event()
            self.released = []

        def heartbeat(self, candidate):
            assert candidate == lease_id
            self.heartbeat_seen.set()
            return True

        def release(self, candidate):
            self.released.append(candidate)

    leases = LeaseProbe()
    transport = FakeTransport((), env={}, backend_identity="test")
    context = McpBridgeContext(
        None,
        transport,
        leases,
        lease_id,
        heartbeat_interval_seconds=0.01,
    )

    assert leases.heartbeat_seen.wait(timeout=1)
    context.close()
    assert leases.released == [lease_id]


def test_bridge_releases_lease_when_transport_close_fails():
    lease_id = uuid4()

    class BrokenCloseTransport(FakeTransport):
        def close(self):
            raise RuntimeError("close failed")

    class LeaseProbe:
        def __init__(self):
            self.released = []

        def heartbeat(self, _candidate):
            return True

        def release(self, candidate):
            self.released.append(candidate)

    leases = LeaseProbe()
    context = McpBridgeContext(
        None,
        BrokenCloseTransport((), env={}, backend_identity="test"),
        leases,
        lease_id,
    )

    with pytest.raises(RuntimeError, match="close failed"):
        context.close()
    assert leases.released == [lease_id]


def test_stale_lease_is_rejected_before_reclamation(tmp_path):
    _settings, state = make_state(tmp_path)
    session_id = insert_ready_session(state)
    lease_id = WritableLeaseStore(state).acquire(session_id)
    with state.transaction() as conn:
        conn.execute(
            "UPDATE writable_agent_leases SET heartbeat_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", str(lease_id)),
        )

    with pytest.raises(PermissionDenied, match="no longer active"):
        SqliteLeaseValidator(state, stale_after_seconds=30).validate(
            session_id,
            lease_id,
        )


def test_descriptor_treats_ttl_expired_write_lease_as_available(tmp_path):
    settings = Settings(
        root=tmp_path,
        writable_lease_stale_seconds=30,
        writable_lease_heartbeat_seconds=10,
    )
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    session_id = insert_ready_session(state)
    lease_id = WritableLeaseStore(state).acquire(session_id)
    provider = StateMcpDescriptorProvider(state, settings)

    assert provider.describe(session_id)["write_lease_available"] is False
    with state.transaction() as conn:
        conn.execute(
            "UPDATE writable_agent_leases SET heartbeat_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", str(lease_id)),
        )

    assert provider.describe(session_id)["write_lease_available"] is True


def test_config_rejects_output_limit_smaller_than_tool_discovery(tmp_path):
    with pytest.raises(ValidationError, match="greater than or equal"):
        Settings(root=tmp_path, max_agent_output_bytes=MIN_MCP_OUTPUT_BYTES - 1)


def test_serve_host_and_port_override_settings(tmp_path, monkeypatch):
    observed = {}
    monkeypatch.setenv("NOTEBOOK_LAUNCHER_ROOT", str(tmp_path))

    def run(app, **kwargs):
        observed.update({"app": app, **kwargs})

    monkeypatch.setattr("notebook_launcher.cli.uvicorn.run", run)

    assert main(["serve", "--host", "0.0.0.0", "--port", "9123"]) == 0
    assert observed == {
        "app": "notebook_launcher.app:app",
        "host": "0.0.0.0",
        "port": 9123,
        "reload": False,
    }
