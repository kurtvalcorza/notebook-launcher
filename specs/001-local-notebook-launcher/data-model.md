# Data Model: Local Notebook Launcher

## Persistence classes

- **Filesystem**: immutable source cache, persistent workspaces/outputs, owner-only runtime private state.
- **SQLite control state**: launch authorization replay records, stable source identity, trust, workspace/session lifecycle, writable leases, document/file versions, bounded audit metadata.

SQLite MUST NOT contain raw notebook contents, full large/binary outputs, or raw Jupyter credentials.

## LaunchRequest

| Field | Type | Notes |
|---|---|---|
| `url` | string? | GitHub notebook URL |
| `repo` | string? | `owner/repository` |
| `ref` | string? | branch/tag/SHA |
| `path` | string? | repo-relative `.ipynb` |
| `workspace_id` | UUID? | reopen existing workspace; exclusive with remote-source fields |
| `gpu` | enum | `auto|on|off` |
| `agent_mode` | enum | `write|readonly` |

## ResolvedSource

| Field | Type | Notes |
|---|---|---|
| `repository_id` | integer | Stable GitHub numeric repository ID; trust key |
| `repository_node_id` | string? | Optional GitHub node ID cross-check |
| `owner` | string | Current canonical owner |
| `repository` | string | Current canonical repo name |
| `requested_ref` | string | User input ref |
| `commit_sha` | string | Immutable resolved commit |
| `notebook_path` | string | Normalized repo-relative `.ipynb` |
| `clone_url` | string | Constructed validated HTTPS URL |
| `resolved_at` | datetime | Resolution time |

Invariant: preview/POST authorization MUST re-check that stable repository ID and normalized request still match.

## LaunchPreview

Non-executing representation rendered by GET `/open`.

| Field | Type | Notes |
|---|---|---|
| `request_digest` | string | Digest of normalized launch/reopen request including GPU/agent policy |
| `source` | ResolvedSource? | Remote-source preview |
| `workspace_id` | UUID? | Reopen preview |
| `trust_covered` | boolean | Whether persistent source trust currently covers the source |
| `launch_token` | string | Short-lived signed token; not a source-trust grant |
| `expires_at` | datetime | Token expiry |

GET preview creates no executable launch/runtime state.

## LaunchAuthorizationReplay

Persisted only when a launch token is consumed.

| Field | Type | Notes |
|---|---|---|
| `jti_hash` | string | One-time token identifier hash |
| `request_digest` | string | Must match token/request |
| `consumed_at` | datetime | First accepted POST |

A repeated JTI or mismatched digest is rejected.

## TrustRecord

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Trust ID |
| `repository_id` | integer | Stable GitHub repo ID |
| `repository_node_id` | string? | Optional diagnostic cross-check |
| `grant_owner` | string | Owner at grant time |
| `grant_repository` | string | Name at grant time |
| `scope` | enum | `exact_commit|repository` |
| `commit_sha` | string? | Required for exact commit |
| `created_at` | datetime | Grant time |
| `revoked_at` | datetime? | Null when active |

Constraints:
- active exact-commit uniqueness: `(repository_id, commit_sha)`;
- active repository-wide uniqueness: `repository_id`;
- current owner/name is display metadata only;
- rename/transfer with same repository ID remains trusted;
- a recreated namespace with a new repository ID is untrusted.

## TrustChallenge

| Field | Type | Notes |
|---|---|---|
| `launch_id` | UUID | Authorized pending launch |
| `confirmation_nonce_hash` | string | Hash only |
| `expires_at` | datetime | Short-lived |
| `used_at` | datetime? | One-time |

Trust challenge is distinct from launch authorization.

## EnvironmentIdentity

`repository_id`, `commit_sha`, builder/strategy version, environment digest, image tag.

## Workspace

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Workspace ID |
| `source_repository_id` | integer | Stable source identity |
| `source_owner` | string | Current/grant-time display owner |
| `source_repository` | string | Display name |
| `source_commit_sha` | string | Provenance SHA |
| `source_notebook_path` | string | Original notebook path |
| `root_path` | local path | Launcher-managed root |
| `work_path` | local path | Persistent writable tree |
| `outputs_path` | local path | Persistent artifact area |
| `active_notebook_path` | string | Launcher-designated MCP notebook target |
| `user_data_grant_id` | UUID? | Optional grant |
| `created_at`/`updated_at` | datetime | lifecycle timestamps |

Invariants:
- stop never deletes workspace;
- zero or one active session;
- browser focus/opening another notebook does not change `active_notebook_path`;
- only supported active-notebook move updates it.

## UserDataGrant

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Grant ID |
| `workspace_id` | UUID | Owner workspace |
| `display_path` | local path | User-entered path for display |
| `canonical_root` | local path | Real/canonical enforcement root |
| `container_path` | string | `/mnt/user-data` |
| `mode` | enum | `ro|rw` |
| `created_at` | datetime | Grant time |
| `revoked_at` | datetime? | Null while active |

Rules:
- forbidden/whole-home checks run on canonical root;
- root is revalidated before mount/host-side use;
- host-side paths are opened relative to the canonical root using no-follow/dirfd-equivalent semantics;
- symlink/junction/reparse/path-swap escape is rejected.

## NotebookSession

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Session ID |
| `workspace_id` | UUID | Persistent workspace |
| `runtime_id` | string | container/runtime ID |
| `host_port` | integer | loopback Jupyter port |
| `gpu_enabled` | boolean | effective allocation |
| `agent_mode` | enum | max `write|readonly` policy |
| `sandbox_verified` | boolean | mandatory sandbox gate passed |
| `network_verified` | boolean | default egress/inbound policy gate passed |
| `collaboration_ready` | boolean | authoritative shared document ready |
| `state` | enum | `starting|ready|stopping|stopped|failed` |
| `notebook_url` | string | browser target |
| timestamps | datetime | start/stop |

At most one active session per workspace. A session cannot be ready until sandbox/network/collaboration gates pass.

## DocumentVersion

Opaque version state keyed by `(workspace_id,path,kind)`.

- Active notebook version comes from authoritative shared Jupyter document state.
- Agent mutations require expected document version.
- Stale independent full-save attempts are rejected.
- Non-active files use analogous file versions.
- Generic file write/move/delete rejects active notebook path.

## WritableAgentLease

At most one unreleased writable lease per active session. Disconnect/stop/reconciliation releases or invalidates it.

## ExecutionOperation

| Field | Type | Notes |
|---|---|---|
| `operation_id` | UUID | Agent execution identity |
| `session_id` | UUID | owning session |
| `lease_id` | UUID? | writable controller |
| `state` | enum | `queued|dispatched|running|cancel_pending|cancelling|completed|failed|timeout|cancelled` |
| `jupyter_msg_id` | string? | assigned on dispatch |
| `active_parent_msg_id` | string? | observed busy-parent when owned |
| `queued_at` | datetime | queue time |
| `dispatched_at` | datetime? | sent to Jupyter |
| `started_at` | datetime? | ownership confirmed active |
| `deadline_at` | datetime? | timeout |
| `cancel_requested_at` | datetime? | explicit cancel |
| `finished_at` | datetime? | terminal |

Rules:
- queued cancellation removes/terminates without interrupt;
- dispatched but non-active cancellation never interrupts another request;
- kernel interrupt allowed only when observed active/busy parent ID equals this operation’s Jupyter message ID;
- if cancel-pending operation later becomes active, interrupt then;
- ownership metadata is ephemeral except bounded audit outcome.

## McpAttachment

Non-secret descriptor: session, availability/status, stdio command/args, backend/version, mode, semantic capabilities, writable-lease availability. No raw token.

## OutputEnvelope

MIME/type, serialized size, bounded payload/preview/reference, `truncated`, authoritative notebook/artifact reference. Default MCP bound 1 MiB per operation.

## AgentExecutionEvent

Bounded session/lease/operation/target/timing/outcome/error category only. No full cell source, output payload, or credentials.

## NetworkPolicyState

Ephemeral/diagnostic representation of effective policy:

| Field | Type | Notes |
|---|---|---|
| `public_internet_allowed` | boolean | true by default |
| `approved_dns_endpoints` | list[address] | explicit exceptions |
| `deny_non_global` | boolean | true by default |
| `host_gateway_alias_present` | boolean | must be false by default |
| `verified_at` | datetime | readiness assertion time |

Denied destination classes include loopback, private/RFC1918, IPv6 ULA, link-local, metadata, multicast, host gateway, and other non-global local/reserved ranges per runtime policy.

## Lifecycle invariants

1. GET `/open` is non-executing.
2. Execution/reopen compute requires one-time local launch authorization even if source trust exists.
3. Trust is keyed by stable GitHub repository identity.
4. Fresh remote launch creates a fresh workspace.
5. Workspace has zero or one active session.
6. Session has zero or one writable agent lease.
7. Session readiness requires sandbox, egress, and collaboration gates.
8. Browser/MCP share one authoritative active notebook/kernel; browser focus does not retarget MCP.
9. Agent execution interrupt is ownership-aware and cannot intentionally cancel browser-owned work.
10. User-data host operations cannot escape canonical grant root through symlink/reparse/traversal/path swap.
11. Runtime stop preserves workspace/cache and invalidates private runtime/agent state.
