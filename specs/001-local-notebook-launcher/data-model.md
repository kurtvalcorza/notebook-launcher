# Data Model: Local Notebook Launcher

## Persistence overview

The launcher uses two persistence classes:

- **Filesystem**: immutable source material, mutable workspace files, notebook copies, outputs, and owner-only runtime credential files.
- **SQLite control state**: trust records, workspace/session lifecycle metadata, active-session ownership, writable-agent leases, document/file version metadata, and bounded audit events.

SQLite rows never contain full notebook/cell contents, large/binary output payloads, or raw Jupyter credentials.

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
| `owner` | string | Canonical GitHub owner |
| `repository` | string | Canonical repository name |
| `requested_ref` | string | User input ref |
| `commit_sha` | string | Immutable resolved SHA |
| `notebook_path` | string | Normalized repo-relative `.ipynb` |
| `clone_url` | string | Constructed validated HTTPS GitHub URL |

Invariant: the normalized notebook path remains inside the repository root and ends in `.ipynb`.

## TrustRecord

Persistent local trust grant.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Trust record ID |
| `owner` | string | Canonical GitHub owner |
| `repository` | string | Canonical repository |
| `scope` | enum | `exact_commit` or `repository` |
| `commit_sha` | string? | Required for `exact_commit`; null for repository scope |
| `created_at` | datetime | Grant time |
| `revoked_at` | datetime? | Null while active |

Constraints:

- active exact-commit grants are unique by repository + commit SHA;
- active repository-wide grant is unique by repository;
- repository-wide grant covers future SHAs only for that same repository identity;
- revoked records do not authorize execution.

## TrustChallenge

Ephemeral challenge for a pending launch.

| Field | Type | Notes |
|---|---|---|
| `launch_id` | UUID | Pending launch |
| `confirmation_nonce_hash` | string | Hash only; raw nonce rendered to local page |
| `expires_at` | datetime | Short-lived |
| `used_at` | datetime? | One-time use |

The remote launch URL cannot provide a valid confirmation nonce.

## EnvironmentIdentity

| Field | Type | Notes |
|---|---|---|
| `commit_sha` | string | Immutable source revision |
| `builder_version` | string | repo2docker/strategy version |
| `environment_digest` | string | Hash of environment-defining inputs |
| `image_tag` | string | Local image identifier |

## Workspace

Persistent user state independent from runtime lifetime.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Workspace identity |
| `source_owner` | string | Provenance owner |
| `source_repository` | string | Provenance repository |
| `source_commit_sha` | string | Provenance SHA |
| `source_notebook_path` | string | Original notebook path |
| `root_path` | local path | Launcher-managed root |
| `work_path` | local path | Persistent mutable workspace |
| `outputs_path` | local path | Persistent generated artifacts |
| `active_notebook_path` | string | Workspace-relative current notebook; updated by supported Jupyter rename/move |
| `created_at` | datetime | Creation time |
| `updated_at` | datetime | Last meaningful update |
| `user_data_grant_id` | UUID? | Optional explicit mount grant |

Invariants:

- stopping/removing a runtime never deletes the workspace;
- a workspace owns zero or one active `NotebookSession`;
- a supported active-notebook rename/move updates `active_notebook_path` before the operation is considered complete;
- if the recorded active path disappears through an unsupported external move, the launcher reports the missing notebook rather than guessing another file.

## SourceSnapshot

Immutable source material/provenance associated with a workspace. It MUST NOT be modified by browser/MCP operations. The mutable working tree is materialized separately and is not a GitHub publishing mechanism.

## UserDataGrant

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Grant ID |
| `workspace_id` | UUID | Owning workspace |
| `host_path` | local path | Explicitly user-selected by local management flow |
| `container_path` | string | Stable path, default `/mnt/user-data` |
| `mode` | enum | `ro` or `rw` |
| `created_at` | datetime | Grant time |
| `revoked_at` | datetime? | Null while active |

Remote source URLs cannot create/change this grant.

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
| `created_at` | datetime | Creation time |
| `updated_at` | datetime | Last transition |

Phases: `received`, `resolving`, `awaiting_trust`, `acquiring`, `workspace_preparing`, `building`, `cache_hit`, `starting`, `mcp_preparing`, `ready`, `failed`, `stopping`, `stopped`.

## NotebookSession

Disposable runtime record.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Session identity |
| `workspace_id` | UUID | Persistent workspace |
| `runtime_id` | string | Container/runtime identifier |
| `host_port` | integer | Loopback Jupyter port |
| `gpu_enabled` | boolean | Effective GPU allocation |
| `agent_mode` | enum | `write` or `readonly` maximum policy |
| `sandbox_profile` | string | MVP: `standard` |
| `sandbox_verified` | boolean | True only after mandatory standard-sandbox assertions succeed |
| `collaboration_ready` | boolean | True only after shared-document collaboration readiness succeeds |
| `notebook_url` | string | Browser target/redirect data |
| `state` | enum | `starting`, `ready`, `stopping`, `stopped`, `failed` |
| `started_at` | datetime | Start time |
| `stopped_at` | datetime? | Terminal time |

Database invariant: at most one session in an active state may reference the same workspace. A session cannot become `ready` until the mandatory sandbox and collaboration readiness gates have passed. Raw Jupyter token/URL credentials are held only in owner-only runtime-private state, not ordinary database/API records.

## DocumentVersion

Opaque optimistic-concurrency state for a mutable notebook/file.

| Field | Type | Notes |
|---|---|---|
| `workspace_id` | UUID | Workspace |
| `path` | string | Normalized workspace-relative path |
| `kind` | enum | `notebook` or `file` |
| `version` | string | Opaque version token returned to clients |
| `content_hash` | string? | Internal integrity aid when appropriate |
| `updated_at` | datetime | Last authoritative change observed |

Rules:

- active notebook version derives from the authoritative Jupyter collaborative document;
- normal JupyterLab browser edits update that authoritative document/version rather than replacing it from an independent stale copy;
- agent notebook mutations require `expected_document_version`;
- any independent full-document/file save path capable of replacing the active notebook must carry/derive a version precondition and is rejected if stale;
- non-notebook existing-object mutations require `expected_file_version`;
- a mismatched version is rejected as conflict before mutation;
- generic workspace file write/move/delete does not operate on the active notebook path; active-notebook mutation/move is notebook-aware.

## WritableAgentLease

Transactional ownership record for writable MCP control.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Lease ID |
| `session_id` | UUID | Notebook session |
| `client_label` | string? | Optional sanitized caller label |
| `acquired_at` | datetime | Acquire time |
| `heartbeat_at` | datetime? | Optional liveness update |
| `released_at` | datetime? | Null while active |

Invariant: at most one unreleased writable lease exists for an active session. Session stop invalidates/releases it. Read-only attachments do not consume the writable lease; simultaneous multi-readonly attachment is not an MVP guarantee.

## McpAttachment

Public non-secret descriptor for attaching to a ready session.

| Field | Type | Notes |
|---|---|---|
| `session_id` | UUID | Owning runtime |
| `available` | boolean | Whether configured backend satisfies profile |
| `status` | enum | `unavailable`, `available`, `attaching`, `attached`, `error`, `invalidated` |
| `transport` | string | MVP: `stdio` |
| `command` | string? | Local non-secret command |
| `args` | list[string] | Non-secret args |
| `backend` | string? | Informational tested backend/version |
| `agent_mode` | enum | `write` or `readonly` |
| `capabilities` | list[string] | Semantic capabilities actually exposed |
| `write_lease_available` | boolean? | Whether a writable attachment can currently acquire the lease |

Raw Jupyter credentials are forbidden from this descriptor.

## ExecutionControl

Ephemeral runtime state for one agent execution request.

| Field | Type | Notes |
|---|---|---|
| `operation_id` | UUID | Execution identity |
| `session_id` | UUID | Owning notebook session |
| `started_at` | datetime | Start time |
| `deadline_at` | datetime? | Configured timeout deadline |
| `cancel_requested_at` | datetime? | Explicit cancel request time |
| `state` | enum | `running`, `interrupting`, `completed`, `failed`, `timeout`, `cancelled` |

Rules:

- cancellation/deadline expiry interrupts the active kernel execution;
- if the kernel does not recover inside the grace interval, runtime policy may restart/replace the kernel while preserving workspace files;
- cancellation state is ephemeral and need not be retained after bounded audit metadata is written.

## McpRuntimeState

Owner-only ephemeral state used by `notebook-launcher mcp <session-id>`: session ID, loopback Jupyter URL, Jupyter token, workspace/notebook path, backend configuration, effective permission profile, lease ID when writable, and active execution-control handles. It expires with session stop.

## NotebookCopyRequest

| Field | Type | Notes |
|---|---|---|
| `source_path` | string | Workspace-relative `.ipynb` |
| `destination_scope` | enum | `workspace` or `user_data` |
| `destination_path` | string | Validated relative destination |
| `include_outputs` | boolean | Preserve current saved outputs when true |
| `overwrite` | boolean | Default false |

## AgentExecutionEvent

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Event identity |
| `session_id` | UUID | Notebook session |
| `lease_id` | UUID? | Writable lease when applicable |
| `operation` | string | Semantic operation |
| `target` | string? | Bounded cell/path identifier |
| `started_at` | datetime | Start |
| `duration_ms` | integer? | Duration |
| `outcome` | enum? | `success`, `failure`, `conflict`, `timeout`, `cancelled` |
| `error_type` | string? | Sanitized category |

Full cell source/output, binary/multimodal payloads, and raw credentials are not logged by default.

## OutputEnvelope

Bounded agent-facing representation of notebook output.

| Field | Type | Notes |
|---|---|---|
| `mime_type` | string? | Output MIME/type when applicable |
| `serialized_size` | integer? | Size when known |
| `payload` | string/object? | Bounded text/preview/reference representation |
| `truncated` | boolean | True when full payload exceeds configured bound |
| `authoritative_location` | string? | Non-secret notebook/cell/artifact reference when available |

Default launcher limit: 1 MiB serialized agent response payload per operation, configurable locally. The full authoritative notebook output remains in the notebook/workspace.

## Persistent-write safety

Launcher-managed replacement writes and Jupyter notebook saves use atomic/safe replacement behavior appropriate to the filesystem. On `ENOSPC` or comparable write failure, the operation reports failure and leaves the prior saved file intact. Partial temporary files may be cleaned up but MUST NOT become the authoritative replacement.

## Lifecycle invariants

1. A fresh remote launch creates a new workspace after trust approval.
2. A workspace has zero or one active notebook session.
3. An active session has zero or one writable agent lease.
4. A session is not `ready` until the complete standard sandbox and Jupyter collaboration gates pass.
5. Browser and MCP use the same authoritative notebook document/kernel.
6. A stale notebook/file mutation or stale independent active-notebook save is rejected rather than overwriting newer state.
7. Generic workspace file operations cannot bypass the active notebook document/version boundary.
8. A supported active-notebook rename/move updates workspace metadata; unsupported disappearance fails clearly.
9. Runtime stop invalidates credentials and writable lease but preserves workspace and environment cache.
10. Timeout/cancellation terminates the requested execution without deleting the workspace.
11. Trust revocation affects future launch authorization; it does not silently terminate an already-running trusted session.

## Cache vs persistence

- **Environment cache**: reusable image/build artifacts; independently removable.
- **Source cache/snapshot**: immutable provenance material.
- **Workspace**: persistent user edits/outputs; survives runtime stop.
- **Control database**: durable local metadata, trust, versions, leases, audit.
- **Runtime private state**: ephemeral credentials/process/cancellation metadata; invalidated on stop.
