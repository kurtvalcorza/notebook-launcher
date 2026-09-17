from __future__ import annotations

import html
import ipaddress
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import Settings
from .errors import LaunchAuthorizationError, LauncherError
from .github import GitHubResolver
from .grants import UserDataGrantStore
from .launch_auth import LaunchTokenCodec, request_digest
from .launches import (
    PipelineLaunchStarter,
    StateLaunchStarter,
    StateMcpDescriptorProvider,
)
from .models import LaunchRequest, NotebookCopyRequest, ResolvedSource, TrustScope
from .pipeline import LaunchPipeline
from .state import StateStore
from .trust import TrustStore
from .trust_flow import TrustFlowStore
from .workspace import WorkspaceManager


class LaunchResolver(Protocol):
    def resolve(self, request: LaunchRequest) -> ResolvedSource: ...


class LaunchStarter(Protocol):
    def start(
        self,
        *,
        request: LaunchRequest,
        source: ResolvedSource | None,
        trust_covered: bool,
    ) -> dict[str, object]: ...


class McpDescriptorProvider(Protocol):
    def describe(self, session_id: UUID) -> dict[str, object] | None: ...


class SessionLifecycle(Protocol):
    def stop(self, session_id: UUID) -> bool: ...


@dataclass(slots=True)
class AppServices:
    state: StateStore
    token_codec: LaunchTokenCodec
    resolver: LaunchResolver
    starter: LaunchStarter
    lifecycle: SessionLifecycle | None = None
    mcp_descriptors: McpDescriptorProvider | None = None
    pipeline: LaunchPipeline | None = None


class LaunchPost(BaseModel):
    request: LaunchRequest
    launch_token: str


class TrustPost(BaseModel):
    decision: Literal["trust_exact_commit", "trust_repository", "deny"] | None = None
    confirmation_nonce: str | None = None
    # Backward-compatible input while the local API contract is upgraded.
    nonce: str | None = None
    scope: TrustScope | None = None

    def normalized(self) -> tuple[str, TrustScope | None]:
        nonce = self.confirmation_nonce or self.nonce
        if not nonce:
            raise ValueError("confirmation_nonce is required")
        if self.decision == "deny":
            return nonce, None
        if self.decision == "trust_exact_commit":
            return nonce, TrustScope.EXACT_COMMIT
        if self.decision == "trust_repository":
            return nonce, TrustScope.REPOSITORY
        if self.scope is not None:
            return nonce, self.scope
        raise ValueError("trust decision is required")


def _normalized_digest(request: LaunchRequest, source: ResolvedSource | None) -> str:
    payload = request.model_dump(mode="json", exclude_none=True)
    if source is not None:
        payload["resolved_repository_id"] = source.repository_id
        payload["resolved_commit_sha"] = source.commit_sha
        payload["resolved_notebook_path"] = source.notebook_path
    return request_digest(payload)


def _safe_script_json(value: object) -> str:
    return (
        json.dumps(value, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _is_loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_local_request(request: Request, settings: Settings) -> None:
    client_host = request.client.host if request.client else None
    if not _is_loopback(client_host) and client_host != "testclient":
        raise HTTPException(status_code=400, detail="local client required")
    origin = request.headers.get("origin")
    if origin is not None:
        allowed = {
            f"http://{settings.host}:{settings.port}",
            f"http://localhost:{settings.port}",
        }
        if origin not in allowed:
            raise HTTPException(status_code=400, detail="cross-origin request denied")


def _default_services(settings: Settings) -> AppServices:
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    pipeline = LaunchPipeline(
        settings=settings,
        state=state,
        approved_dns_endpoints=settings.approved_dns_endpoints,
    )
    return AppServices(
        state=state,
        token_codec=LaunchTokenCodec(
            secrets.token_bytes(32),
            ttl_seconds=settings.launch_token_ttl_seconds,
        ),
        resolver=GitHubResolver(),
        starter=PipelineLaunchStarter(StateLaunchStarter(state, settings), pipeline),
        lifecycle=pipeline,
        mcp_descriptors=StateMcpDescriptorProvider(state, settings),
        pipeline=pipeline,
    )


def _verify_trust_source_unchanged(
    services: AppServices,
    trust_flow: TrustFlowStore,
    launch_id: str,
) -> None:
    saved = trust_flow.source_for_launch(launch_id)
    if saved is None:
        raise ValueError("launch source identity missing")
    current = services.resolver.resolve(
        LaunchRequest(
            repo=f"{saved.owner}/{saved.repository}",
            ref=saved.requested_ref,
            path=saved.notebook_path,
        )
    )
    expected = (
        saved.repository_id,
        saved.repository_node_id,
        saved.commit_sha,
        saved.notebook_path,
    )
    observed = (
        current.repository_id,
        current.repository_node_id,
        current.commit_sha,
        current.notebook_path,
    )
    if observed != expected:
        raise ValueError("source identity changed; refresh the preview and authorize again")


def create_app(
    settings: Settings | None = None,
    services: AppServices | None = None,
) -> FastAPI:
    settings = settings or Settings()
    services = services or _default_services(settings)
    trust = TrustStore(services.state)
    trust_flow = TrustFlowStore(services.state)
    workspace_manager = WorkspaceManager(
        services.state,
        settings.workspaces_dir,
        grants=UserDataGrantStore(services.state),
    )
    app = FastAPI(title="Notebook Launcher", version="0.1.0")
    if services.pipeline is not None:
        app.router.add_event_handler(
            "startup",
            services.pipeline.reconcile_starting_sessions,
        )
        app.router.add_event_handler("shutdown", services.pipeline.close)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "host": settings.host, "port": settings.port}

    @app.get("/status/{launch_id}", response_class=HTMLResponse)
    async def status_page(launch_id: str) -> HTMLResponse:
        row = services.state.get_launch(launch_id)
        if row is None:
            raise HTTPException(status_code=404, detail="launch not found")
        body = f"""<!doctype html><html><body>
<h1>Notebook launch status</h1>
<p>Launch: {html.escape(launch_id)}</p>
<p>State: {html.escape(row['state'])}</p>
<p>Workspace: {html.escape(row['workspace_id'] or 'pending')}</p>
</body></html>"""
        response = HTMLResponse(body)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/open", response_class=HTMLResponse)
    async def preview(
        url: str | None = None,
        repo: str | None = None,
        ref: str | None = None,
        path: str | None = None,
        workspace_id: str | None = None,
        gpu: str = "auto",
        agent_mode: str = "write",
    ) -> HTMLResponse:
        try:
            request_model = LaunchRequest(
                url=url,
                repo=repo,
                ref=ref,
                path=path,
                workspace_id=workspace_id,
                gpu=gpu,
                agent_mode=agent_mode,
            )
            source = None if request_model.workspace_id else services.resolver.resolve(request_model)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        trust_covered = bool(source and trust.find(source).trusted)
        digest = _normalized_digest(request_model, source)
        token, expires = services.token_codec.issue(digest)
        request_js = _safe_script_json(request_model.model_dump(mode="json", exclude_none=True))
        token_js = _safe_script_json(token)
        body = f"""<!doctype html><html><body>
<h1>Notebook launch preview</h1>
<p>Trust covered: {str(trust_covered).lower()}</p>
<p>Authorization expires: {html.escape(expires.isoformat())}</p>
<button id="launch">Launch locally</button><pre id="status"></pre>
<script>
const launchRequest={request_js};const launchToken={token_js};
document.getElementById('launch').addEventListener('click',async()=>{{
 const r=await fetch('/api/launches',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{request:launchRequest,launch_token:launchToken}})}});
 document.getElementById('status').textContent=await r.text();
}});
</script></body></html>"""
        response = HTMLResponse(body)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/launches", status_code=202)
    async def launch(body: LaunchPost, http_request: Request) -> dict[str, object]:
        _check_local_request(http_request, settings)
        try:
            source = None if body.request.workspace_id else services.resolver.resolve(body.request)
            digest = _normalized_digest(body.request, source)
            claims = services.token_codec.decode(body.launch_token)
        except LaunchAuthorizationError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail=exc.public_dict(),
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not secrets.compare_digest(claims.request_digest, digest):
            error = LaunchAuthorizationError(
                "launch_request_changed",
                "The launch request changed; refresh the preview and retry.",
            )
            raise HTTPException(status_code=error.http_status, detail=error.public_dict())
        if not services.state.consume_launch_jti(
            jti_hash=services.token_codec.hash_jti(claims.jti),
            request_digest=digest,
            consumed_at=datetime.now(UTC).isoformat(),
        ):
            error = LaunchAuthorizationError(
                "launch_token_replayed",
                "The launch authorization token was already used.",
            )
            raise HTTPException(status_code=error.http_status, detail=error.public_dict())
        trust_covered = bool(source and trust.find(source).trusted)
        return services.starter.start(
            request=body.request,
            source=source,
            trust_covered=trust_covered,
        )

    @app.get("/trust/{launch_id}", response_class=HTMLResponse)
    async def trust_page(launch_id: str, nonce: str) -> HTMLResponse:
        if not trust_flow.validate_challenge(launch_id, nonce):
            raise HTTPException(status_code=403, detail="invalid or expired trust challenge")
        source = trust_flow.source_for_launch(launch_id)
        if source is None:
            raise HTTPException(status_code=404, detail="launch source not found")
        nonce_js = _safe_script_json(nonce)
        launch_js = _safe_script_json(launch_id)
        body = f"""<!doctype html><html><body>
<h1>Trust source</h1>
<p>{html.escape(source.owner)}/{html.escape(source.repository)}</p>
<p>Commit: {html.escape(source.commit_sha[:12])}</p>
<p>Notebook: {html.escape(source.notebook_path)}</p>
<button id="commit">Trust this commit</button>
<button id="repository">Trust repository</button><pre id="status"></pre>
<script>
const launchId={launch_js};const nonce={nonce_js};
async function grant(decision){{const r=await fetch(`/api/launches/${{launchId}}/trust`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{confirmation_nonce:nonce,decision}})}});document.getElementById('status').textContent=await r.text();}}
document.getElementById('commit').onclick=()=>grant('trust_exact_commit');
document.getElementById('repository').onclick=()=>grant('trust_repository');
</script></body></html>"""
        response = HTMLResponse(body)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/launches/{launch_id}/trust")
    async def confirm_trust(
        launch_id: str,
        body: TrustPost,
        http_request: Request,
    ) -> dict[str, object]:
        _check_local_request(http_request, settings)
        try:
            nonce, scope = body.normalized()
            if scope is None:
                trust_flow.deny(launch_id, nonce)
                return {"launch_id": launch_id, "state": "failed", "decision": "deny"}
            _verify_trust_source_unchanged(services, trust_flow, launch_id)
            trust_id = trust_flow.complete_trust(launch_id, nonce, scope)
            continuation = getattr(services.starter, "continue_authorized", None)
            if continuation is not None:
                continuation(launch_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "launch_id": launch_id,
            "state": "authorized",
            "trust_id": str(trust_id),
            "scope": scope.value,
        }

    @app.get("/api/launches/{launch_id}")
    async def launch_status(launch_id: str) -> dict[str, object]:
        row = services.state.get_launch(launch_id)
        if row is None:
            raise HTTPException(status_code=404, detail="launch not found")
        return {
            "launch_id": row["id"],
            "state": row["state"],
            "trust_covered": bool(row["trust_covered"]),
            "workspace_id": row["workspace_id"],
            "session_id": row["session_id"],
            "message": row["message"],
            "error_code": row["error_code"],
        }

    @app.get("/api/trust")
    async def list_trust() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in trust.list_active()]

    @app.delete("/api/trust/{trust_id}", status_code=204)
    async def revoke_trust(trust_id: UUID, http_request: Request) -> Response:
        _check_local_request(http_request, settings)
        if not trust.revoke(trust_id):
            raise HTTPException(status_code=404, detail="trust grant not found")
        return Response(status_code=204)

    @app.get("/api/workspaces/{workspace_id}")
    async def workspace_metadata(workspace_id: UUID) -> dict[str, object]:
        record = services.state.get_workspace(workspace_id)
        if record is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        return {
            "id": str(record.id),
            "source_repository_id": record.source_repository_id,
            "source_owner": record.source_owner,
            "source_repository": record.source_repository,
            "source_commit_sha": record.source_commit_sha,
            "source_notebook_path": record.source_notebook_path,
            "root_path": str(record.root_path),
            "work_path": str(record.work_path),
            "outputs_path": str(record.outputs_path),
            "active_notebook_path": record.active_notebook_path,
            "user_data_grant_id": (
                str(record.user_data_grant_id)
                if record.user_data_grant_id is not None
                else None
            ),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @app.post("/api/workspaces/{workspace_id}/notebooks/copy", status_code=201)
    async def copy_notebook(
        workspace_id: UUID,
        body: NotebookCopyRequest,
        http_request: Request,
    ) -> dict[str, object]:
        _check_local_request(http_request, settings)
        try:
            workspace_manager.save_copy(
                workspace_id,
                source_path=body.source_path,
                destination_scope=body.destination_scope.value,
                destination_path=body.destination_path,
                include_outputs=body.include_outputs,
                overwrite=body.overwrite,
            )
        except LauncherError as exc:
            raise HTTPException(status_code=exc.http_status, detail=exc.public_dict()) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail="destination exists") from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "workspace_id": str(workspace_id),
            "destination_scope": body.destination_scope.value,
            "destination_path": body.destination_path,
        }

    @app.get("/api/sessions/{session_id}/mcp")
    async def mcp_descriptor(session_id: UUID) -> dict[str, object]:
        if services.mcp_descriptors is None:
            raise HTTPException(status_code=503, detail="MCP bridge is unavailable")
        descriptor = services.mcp_descriptors.describe(session_id)
        if descriptor is None:
            raise HTTPException(status_code=404, detail="unknown session")
        status = descriptor.get("status")
        if status == "stopped":
            raise HTTPException(status_code=410, detail="session stopped")
        if not descriptor.get("available", False):
            raise HTTPException(status_code=409, detail="session not ready")
        return descriptor

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def stop_session(session_id: UUID, http_request: Request) -> Response:
        _check_local_request(http_request, settings)
        if services.lifecycle is None:
            raise HTTPException(status_code=503, detail="runtime lifecycle is unavailable")
        if not services.lifecycle.stop(session_id):
            raise HTTPException(status_code=404, detail="unknown session")
        return Response(status_code=204)

    return app


app = create_app()
