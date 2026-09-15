from __future__ import annotations

import html
import ipaddress
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import Settings
from .github import GitHubResolver
from .launch_auth import LaunchTokenCodec, request_digest
from .launches import StateLaunchStarter
from .models import LaunchRequest, ResolvedSource
from .state import StateStore
from .trust import TrustStore


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


@dataclass(slots=True)
class AppServices:
    state: StateStore
    token_codec: LaunchTokenCodec
    resolver: LaunchResolver
    starter: LaunchStarter


class LaunchPost(BaseModel):
    request: LaunchRequest
    launch_token: str


def _normalized_digest(request: LaunchRequest, source: ResolvedSource | None) -> str:
    payload = request.model_dump(mode="json", exclude_none=True)
    if source is not None:
        payload["resolved_repository_id"] = source.repository_id
        payload["resolved_commit_sha"] = source.commit_sha
        payload["resolved_notebook_path"] = source.notebook_path
    return request_digest(payload)


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
        raise HTTPException(status_code=403, detail="launch authorization requires a local client")

    origin = request.headers.get("origin")
    if origin is not None:
        allowed = {
            f"http://{settings.host}:{settings.port}",
            f"http://localhost:{settings.port}",
        }
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="cross-origin launch authorization denied")


def _default_services(settings: Settings) -> AppServices:
    settings.ensure_directories()
    state = StateStore(settings.state_db)
    state.initialize()
    return AppServices(
        state=state,
        token_codec=LaunchTokenCodec(
            secrets.token_bytes(32),
            ttl_seconds=settings.launch_token_ttl_seconds,
        ),
        resolver=GitHubResolver(),
        starter=StateLaunchStarter(state),
    )


def create_app(
    settings: Settings | None = None,
    services: AppServices | None = None,
) -> FastAPI:
    settings = settings or Settings()
    services = services or _default_services(settings)
    trust = TrustStore(services.state)
    app = FastAPI(title="Notebook Launcher", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "host": settings.host, "port": settings.port}

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
        request_json = json.dumps(request_model.model_dump(mode="json", exclude_none=True))
        safe_request = html.escape(request_json, quote=True)
        safe_token = html.escape(token, quote=True)
        html_body = f"""<!doctype html>
<html><body>
<h1>Notebook launch preview</h1>
<p>Trust covered: {str(trust_covered).lower()}</p>
<p>Authorization expires: {html.escape(expires.isoformat())}</p>
<button id="launch">Launch locally</button>
<pre id="status"></pre>
<script>
const launchRequest = JSON.parse('{safe_request}');
const launchToken = '{safe_token}';
document.getElementById('launch').addEventListener('click', async () => {{
  const response = await fetch('/api/launches', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{request: launchRequest, launch_token: launchToken}})
  }});
  document.getElementById('status').textContent = await response.text();
}});
</script>
</body></html>"""
        response = HTMLResponse(html_body)
        response.headers["X-Frame-Options"] = "DENY"
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
        except Exception as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if not secrets.compare_digest(claims.request_digest, digest):
            raise HTTPException(status_code=403, detail="launch token request mismatch")

        consumed = services.state.consume_launch_jti(
            jti_hash=services.token_codec.hash_jti(claims.jti),
            request_digest=digest,
            consumed_at=datetime.now(UTC).isoformat(),
        )
        if not consumed:
            raise HTTPException(status_code=409, detail="launch token already used")

        trust_covered = bool(source and trust.find(source).trusted)
        return services.starter.start(
            request=body.request,
            source=source,
            trust_covered=trust_covered,
        )

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
        }

    return app


app = create_app()
