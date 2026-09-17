from __future__ import annotations

import argparse
import ipaddress
import json
import os
import stat
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import uvicorn

from .config import Settings
from .errors import PermissionDenied
from .grants import UserDataGrantStore
from .leases import WritableLeaseStore
from .mcp import (
    DatalayerBackend,
    JsonLineTransport,
    SemanticMcpAdapter,
    run_stdio_bridge,
)
from .models import AgentMode
from .state import StateStore
from .trust import TrustStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="notebook-launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    mcp = sub.add_parser("mcp")
    mcp.add_argument("session_id", type=UUID)
    mcp.add_argument("--mode", choices=("write", "readonly"))
    mcp.add_argument("--client-label")
    trust = sub.add_parser("trust")
    trust_sub = trust.add_subparsers(dest="trust_command", required=True)
    trust_sub.add_parser("list")
    revoke = trust_sub.add_parser("revoke")
    revoke.add_argument("trust_id", type=UUID)

    workspace = sub.add_parser("workspace")
    workspace_sub = workspace.add_subparsers(dest="workspace_command", required=True)

    mount = workspace_sub.add_parser("mount")
    mount.add_argument("workspace_id", type=UUID)
    mount.add_argument("path", type=Path)
    mount.add_argument("--mode", choices=("ro", "rw"), default="ro")

    unmount = workspace_sub.add_parser("unmount")
    unmount.add_argument("workspace_id", type=UUID)

    show = workspace_sub.add_parser("mount-status")
    show.add_argument("workspace_id", type=UUID)
    return parser


@dataclass(slots=True)
class McpBridgeContext:
    adapter: SemanticMcpAdapter
    transport: JsonLineTransport
    leases: WritableLeaseStore
    lease_id: UUID | None
    heartbeat_interval_seconds: int = 10
    _heartbeat_stop: threading.Event = field(init=False, repr=False)
    _heartbeat_thread: threading.Thread | None = field(init=False, repr=False)
    _closed: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._closed = False
        if self.lease_id is not None:
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name=f"mcp-lease-{self.lease_id}",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(
                timeout=max(1, self.heartbeat_interval_seconds * 2)
            )
        try:
            self.transport.close()
        finally:
            if self.lease_id is not None:
                self.leases.release(self.lease_id)

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval_seconds):
            assert self.lease_id is not None
            if not self.leases.heartbeat(self.lease_id):
                return


class SqliteAttachmentValidator:
    def __init__(self, state: StateStore) -> None:
        self.state = state

    def validate(self, session_id: UUID) -> None:
        with self.state.connect() as conn:
            row = conn.execute(
                "SELECT state FROM notebook_sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
        if row is None or row["state"] != "ready":
            raise PermissionDenied("The notebook session is not ready or was stopped.")


class SqliteLeaseValidator:
    def __init__(self, state: StateStore, *, stale_after_seconds: int = 30) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.state = state
        self.stale_after_seconds = stale_after_seconds

    def validate(self, session_id: UUID, lease_id: UUID) -> None:
        stale_before = (
            datetime.now(UTC) - timedelta(seconds=self.stale_after_seconds)
        ).isoformat()
        with self.state.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM writable_agent_leases
                WHERE id = ? AND session_id = ? AND released_at IS NULL
                  AND COALESCE(heartbeat_at, acquired_at) >= ?
                """,
                (str(lease_id), str(session_id), stale_before),
            ).fetchone()
        if row is None:
            raise PermissionDenied("The writable-agent lease is no longer active.")


def resolve_mcp_bridge(
    *,
    state: StateStore,
    settings: Settings,
    session_id: UUID,
    requested_mode: str | None,
    client_label: str | None,
    transport_factory: Callable[..., JsonLineTransport] = JsonLineTransport,
) -> McpBridgeContext:
    """Resolve private runtime credentials only inside the stdio bridge process."""
    with state.connect() as conn:
        row = conn.execute(
            """
            SELECT s.state, s.agent_mode, w.active_notebook_path
            FROM notebook_sessions AS s
            JOIN workspaces AS w ON w.id = s.workspace_id
            WHERE s.id = ?
            """,
            (str(session_id),),
        ).fetchone()
    if row is None:
        raise SystemExit("unknown_session")
    if row["state"] != "ready":
        raise SystemExit("session_not_ready")

    session_mode = AgentMode(row["agent_mode"])
    mode = AgentMode(requested_mode) if requested_mode else session_mode
    if session_mode is AgentMode.READONLY and mode is AgentMode.WRITE:
        raise SystemExit("permission_denied")

    private_path = settings.runtime_dir / str(session_id) / "mcp-private.json"
    runtime_root = settings.runtime_dir.resolve()
    if private_path.is_symlink():
        raise SystemExit("session_not_ready")
    try:
        resolved = private_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise SystemExit("session_not_ready") from exc
    if runtime_root not in resolved.parents or not resolved.is_file():
        raise SystemExit("session_not_ready")
    if resolved.stat().st_size > 64 * 1024:
        raise SystemExit("session_not_ready")
    if os.name != "nt" and stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise SystemExit("session_not_ready")
    private = json.loads(resolved.read_text(encoding="utf-8"))
    jupyter_url = str(private["jupyter_url"])
    jupyter_token = str(private["jupyter_token"])
    _require_loopback_jupyter_url(jupyter_url)
    if not jupyter_token or "\x00" in jupyter_token:
        raise SystemExit("session_not_ready")

    leases = WritableLeaseStore(
        state,
        stale_after_seconds=settings.writable_lease_stale_seconds,
    )
    lease_id = (
        leases.acquire(session_id, client_label)
        if mode is AgentMode.WRITE
        else None
    )
    env = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
        if key in os.environ
    }
    env.update(
        {
            "JUPYTER_URL": jupyter_url,
            "JUPYTER_TOKEN": jupyter_token,
            "DOCUMENT_ID": row["active_notebook_path"],
        }
    )
    argv = (
        sys.executable,
        "-m",
        "jupyter_mcp_server",
        "start",
        "--transport",
        "stdio",
        "--start-new-code-sandbox",
        "false",
        "--document-provider",
        "jupyter",
        "--sandbox-variant",
        "jupyter-server",
        "--open-notebook-in-ui",
        "false",
    )
    try:
        transport = transport_factory(
            argv,
            env=env,
            backend_identity="jupyter-mcp-server/2.x",
        )
    except Exception:
        if lease_id is not None:
            leases.release(lease_id)
        raise
    backend = DatalayerBackend(transport)
    try:
        backend.activate(row["active_notebook_path"])
    except Exception:
        try:
            transport.close()
        finally:
            if lease_id is not None:
                leases.release(lease_id)
        raise
    adapter = SemanticMcpAdapter(
        mode=mode,
        notebook=backend,
        workspace=backend,
        active_notebook_path=row["active_notebook_path"],
        session_id=session_id,
        lease_id=lease_id,
        lease_validator=SqliteLeaseValidator(
            state,
            stale_after_seconds=settings.writable_lease_stale_seconds,
        ),
        attachment_validator=SqliteAttachmentValidator(state),
        output_limit=settings.max_agent_output_bytes,
    )
    return McpBridgeContext(
        adapter,
        transport,
        leases,
        lease_id,
        settings.writable_lease_heartbeat_seconds,
    )


def _require_loopback_jupyter_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise SystemExit("session_not_ready")
    host = parsed.hostname
    if host == "localhost":
        return
    try:
        if host is not None and ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise SystemExit("session_not_ready")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()

    if args.command == "serve":
        uvicorn.run(
            "notebook_launcher.app:app",
            host=args.host if args.host is not None else settings.host,
            port=args.port if args.port is not None else settings.port,
            reload=False,
        )
        return 0

    if args.command == "mcp":
        context = resolve_mcp_bridge(
            state=state,
            settings=settings,
            session_id=args.session_id,
            requested_mode=args.mode,
            client_label=args.client_label,
        )
        try:
            return run_stdio_bridge(
                context.adapter,
                input_stream=sys.stdin,
                output_stream=sys.stdout,
            )
        finally:
            context.close()

    if args.command == "workspace":
        grants = UserDataGrantStore(state)
        if args.workspace_command == "mount":
            mounted_grant = grants.grant(
                args.workspace_id,
                args.path,
                mode=args.mode,
                home=Path.home(),
            )
            print(
                f"{mounted_grant.id} {mounted_grant.mode} "
                f"{mounted_grant.canonical_root}"
            )
            return 0
        if args.workspace_command == "unmount":
            if not grants.revoke(args.workspace_id):
                raise SystemExit("workspace has no active data grant")
            return 0
        if args.workspace_command == "mount-status":
            active_grant = grants.active(args.workspace_id)
            if active_grant is None:
                print("none")
            else:
                print(
                    f"{active_grant.id} {active_grant.mode} "
                    f"{active_grant.canonical_root}"
                )
            return 0

    if args.command == "trust":
        trust = TrustStore(state)
        if args.trust_command == "list":
            print(
                json.dumps(
                    [item.model_dump(mode="json") for item in trust.list_active()],
                    indent=2,
                )
            )
            return 0
        if args.trust_command == "revoke":
            if not trust.revoke(args.trust_id):
                raise SystemExit("trust_not_found")
            return 0

    raise SystemExit(f"{args.command!r} is not implemented in this tranche")


if __name__ == "__main__":
    raise SystemExit(main())
