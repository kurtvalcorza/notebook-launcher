# Data Model: Local Notebook Launcher

## LaunchRequest

Represents validated user intent before Git resolution.

| Field | Type | Notes |
|---|---|---|
| `url` | string? | Full GitHub notebook URL; mutually exclusive with explicit source fields |
| `repo` | string? | `owner/repository` |
| `ref` | string? | Branch, tag, or commit supplied by user |
| `path` | string? | Repository-relative `.ipynb` path |
| `gpu` | enum | `auto`, `on`, `off`; default `auto` |

## ResolvedSource

Canonical, immutable source identity used downstream.

| Field | Type | Notes |
|---|---|---|
| `owner` | string | GitHub owner |
| `repository` | string | GitHub repository |
| `requested_ref` | string | Original branch/tag/SHA |
| `commit_sha` | string | Resolved immutable commit |
| `notebook_path` | string | Normalized repository-relative path |
| `clone_url` | string | Validated HTTPS GitHub clone URL |

Invariant: `notebook_path` is relative, normalized, ends in `.ipynb`, and cannot escape the repository root.

## EnvironmentIdentity

Stable cache identity for a built execution environment.

| Field | Type | Notes |
|---|---|---|
| `source` | ResolvedSource ref | Immutable repository revision |
| `builder_version` | string | repo2docker/versioned launcher build strategy |
| `digest` | string | Hash used in local image tag/cache lookup |
| `image_tag` | string | Local Docker image name/tag |

## Launch

Tracks orchestration before and during session creation.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Public local identifier |
| `request` | LaunchRequest | Sanitized request |
| `source` | ResolvedSource? | Populated after resolution |
| `environment` | EnvironmentIdentity? | Populated before build/cache check |
| `phase` | enum | lifecycle phase |
| `message` | string? | User-safe progress/error description |
| `error_code` | string? | Stable machine-readable failure category |
| `session_id` | UUID? | Populated after runtime starts |
| `created_at` | datetime | Local launch creation time |
| `updated_at` | datetime | Last state transition |

### Launch phases

`received`, `resolving`, `acquiring`, `building`, `cache_hit`, `starting`, `mcp_preparing`, `ready`, `failed`, `stopping`, `stopped`.

## NotebookSession

Runtime record for a running local notebook server.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Session identity |
| `launch_id` | UUID | Owning launch |
| `container_id` | string | Docker container identifier |
| `host_port` | integer | Random loopback-published port |
| `jupyter_token` | secret string | Internal only; never returned by ordinary APIs or logs |
| `gpu_enabled` | boolean | Whether GPU devices were requested successfully |
| `notebook_url` | string | Local JupyterLab target URL; API may return redacted/redirect form |
| `mcp_attachment` | McpAttachment? | Agent attachment capability for this session |
| `started_at` | datetime | Session start time |

Invariant: the MCP backend identified by `mcp_attachment` targets this session's Jupyter server/workspace/kernel lifecycle and does not create an unrelated runtime.

## McpAttachment

Public, non-secret description of how an MCP-capable client attaches to a ready notebook session.

| Field | Type | Notes |
|---|---|---|
| `session_id` | UUID | Owning notebook session |
| `available` | boolean | Whether the configured backend can satisfy the minimum profile |
| `status` | enum | `unavailable`, `available`, `attaching`, `attached`, `error`, `invalidated` |
| `transport` | enum/string | `stdio` for MVP; extensible later |
| `command` | string? | Stable local launcher command, e.g. `notebook-launcher` |
| `args` | list[string] | Non-secret args, e.g. `mcp <session-id>` |
| `backend` | string? | Informational backend identifier/version |
| `capabilities` | list[string] | Semantic capability IDs supported for this session |
| `message` | string? | Safe explanation when unavailable/error |

The descriptor MUST NOT contain `jupyter_token` or backend secrets.

### Minimum semantic MCP capability IDs

- `notebook.read`
- `cell.read`
- `cell.execute`
- `notebook.execute_all`
- `kernel.execute_code`
- `kernel.restart`
- `output.read`

Optional remediation capabilities:

- `cell.insert`
- `cell.update`
- `cell.delete`

## McpRuntimeState

Private local state used by `notebook-launcher mcp <session-id>` to connect the selected backend to the existing Jupyter session.

| Field | Type | Notes |
|---|---|---|
| `session_id` | UUID | Session identity |
| `jupyter_url` | string | Loopback URL for target server |
| `jupyter_token` | secret string | Injected directly into backend environment/config |
| `notebook_path` | string | Active document path |
| `backend` | string | Configured MCP adapter/backend |
| `expires_with_session` | boolean | Always true in MVP |

If persisted across processes, storage MUST be owner-readable only and deleted/invalidated on session stop.

## AgentExecutionEvent

Sanitized audit event for MCP-driven operations.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Event identity |
| `session_id` | UUID | Notebook session |
| `operation` | string | Semantic capability/operation |
| `target` | string? | Bounded identifier such as cell index/id; avoid full source by default |
| `started_at` | datetime | Start time |
| `duration_ms` | integer? | Completion duration |
| `success` | boolean? | Null while active |
| `error_type` | string? | Sanitized failure class/category |
| `client_label` | string? | Optional caller label when available |

Full cell source/output is not logged by default; it remains in notebook/Jupyter state and MCP response payloads.

## LaunchStatus

User/API view of launch state. In addition to launch fields it may include:

| Field | Type | Notes |
|---|---|---|
| `mcp_available` | boolean? | Null until session/MCP preparation occurs |
| `mcp_status` | string? | Safe MCP lifecycle summary |

## Cache

The MVP relies on Docker image storage plus repository work directories rather than a separate database. Cache deletion is an explicit maintenance operation and is not coupled to stopping a notebook session. Per-session private runtime metadata is not cache and MUST be invalidated during session cleanup.
