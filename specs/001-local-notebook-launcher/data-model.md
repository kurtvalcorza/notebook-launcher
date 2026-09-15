# Data Model: Local Notebook Launcher

## LaunchRequest

| Field | Type | Notes |
|---|---|---|
| `url` | string? | Full GitHub notebook URL |
| `repo` | string? | `owner/repository` |
| `ref` | string? | Branch/tag/SHA |
| `path` | string? | Repository-relative `.ipynb` |
| `workspace_id` | UUID? | Reopen existing workspace; mutually exclusive with remote source fields |
| `gpu` | enum | `auto`, `on`, `off`; default `auto` |
| `agent_mode` | enum | `write`, `readonly`; default `write` |

## ResolvedSource

| Field | Type | Notes |
|---|---|---|
| `owner` | string | GitHub owner |
| `repository` | string | Repository name |
| `requested_ref` | string | User input ref |
| `commit_sha` | string | Immutable resolved SHA |
| `notebook_path` | string | Normalized repo-relative `.ipynb` |
| `clone_url` | string | Constructed validated HTTPS GitHub URL |

## TrustDecision

| Field | Type | Notes |
|---|---|---|
| `source_key` | string | Repository/revision policy identity |
| `status` | enum | `pending`, `trusted`, `denied` |
| `decided_at` | datetime? | Local decision time |
| `scope` | enum | e.g. exact revision or repository policy |

## EnvironmentIdentity

| Field | Type | Notes |
|---|---|---|
| `commit_sha` | string | Immutable source revision |
| `builder_version` | string | repo2docker/strategy version |
| `environment_digest` | string | Hash of environment-defining inputs |
| `image_tag` | string | Local image identifier |

## Workspace

Persistent user state independent from runtime/container lifetime.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Workspace identity |
| `source` | ResolvedSource | Provenance of original source |
| `root_path` | local path | Launcher-managed workspace root |
| `work_path` | local path | Persistent editable copy |
| `outputs_path` | local path | Persistent generated artifacts |
| `active_notebook_path` | string | Workspace-relative active notebook |
| `created_at` | datetime | Creation time |
| `updated_at` | datetime | Last meaningful update |
| `user_data_mount` | UserDataMount? | Explicit optional host-data mount policy |

Invariant: deleting/stopping a runtime does not delete the workspace unless the user explicitly requests workspace deletion.

## SourceSnapshot

Immutable source material/provenance associated with a workspace. It MUST NOT be modified by Jupyter/MCP operations. The working copy is materialized separately and SHOULD NOT carry a writable Git remote relationship.

## UserDataMount

| Field | Type | Notes |
|---|---|---|
| `host_path` | local path | Explicitly user-selected; never derived from remote URL |
| `container_path` | string | Stable path, default `/mnt/user-data` |
| `mode` | enum | `ro` or `rw` |

## Launch

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Launch attempt |
| `request` | LaunchRequest | Sanitized input |
| `source` | ResolvedSource? | For remote-source launch |
| `workspace_id` | UUID? | Created/resolved workspace |
| `environment` | EnvironmentIdentity? | Runtime environment identity |
| `phase` | enum | lifecycle phase |
| `message` | string? | Safe progress/error text |
| `error_code` | string? | Stable machine-readable category |
| `session_id` | UUID? | Runtime session when started |

Phases: `received`, `resolving`, `awaiting_trust`, `acquiring`, `workspace_preparing`, `building`, `cache_hit`, `starting`, `mcp_preparing`, `ready`, `failed`, `stopping`, `stopped`.

## NotebookSession

Disposable runtime record.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Session identity |
| `workspace_id` | UUID | Persistent workspace |
| `container_id` | string | Runtime container |
| `host_port` | integer | Loopback Jupyter port |
| `jupyter_token` | secret | Private runtime state only |
| `gpu_enabled` | boolean | Effective GPU allocation |
| `agent_mode` | enum | `write` or `readonly` |
| `sandbox_profile` | string | MVP: `standard` |
| `notebook_url` | string | Browser target |
| `mcp_attachment` | McpAttachment? | Agent descriptor |

## McpAttachment

| Field | Type | Notes |
|---|---|---|
| `session_id` | UUID | Owning runtime |
| `available` | boolean | Whether selected backend satisfies profile |
| `status` | enum | `unavailable`, `available`, `attaching`, `attached`, `error`, `invalidated` |
| `transport` | string | MVP: `stdio` |
| `command` | string? | Local non-secret command |
| `args` | list[string] | Non-secret args |
| `backend` | string? | Informational backend/version |
| `agent_mode` | enum | `write` or `readonly` |
| `capabilities` | list[string] | Semantic capabilities actually exposed |

Raw Jupyter credentials are forbidden from this descriptor.

## McpRuntimeState

Private owner-only state used by the local bridge: session ID, loopback Jupyter URL, Jupyter token, workspace/notebook path, backend, permission profile. It expires with session stop.

## NotebookCopyRequest

| Field | Type | Notes |
|---|---|---|
| `source_path` | string | Workspace-relative `.ipynb` |
| `destination_scope` | enum | `workspace` or `user_data` |
| `destination_path` | string | Validated relative destination path |
| `include_outputs` | boolean | Preserve current outputs when true |
| `overwrite` | boolean | Default false |

## AgentExecutionEvent

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Event identity |
| `session_id` | UUID | Notebook session |
| `operation` | string | Semantic operation |
| `target` | string? | Cell ID/index etc. |
| `started_at` | datetime | Start |
| `duration_ms` | integer? | Duration |
| `outcome` | enum? | success/failure/timeout/cancelled |
| `error_type` | string? | Sanitized category |

Full cell source/output is not logged by default.

## Cache vs Persistence

- **Environment cache**: reusable image/build artifacts; may be deleted independently.
- **Source cache/snapshot**: immutable provenance material.
- **Workspace**: persistent user edits/outputs; survives runtime stop.
- **Runtime state**: ephemeral credentials/process metadata; invalidated on session stop.
