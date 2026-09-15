# Workspace Lifecycle Contract

## Purpose

Define local launch authorization, stable source trust, workspace persistence, active-notebook targeting, canonical local-data grants, and runtime ownership without making GitHub a write target.

## Separation of concerns

```text
GET /open preview        non-executing
local POST authorization one-time request-bound compute start permission
source trust             persistent permission for repository code
source snapshot          immutable provenance @ stable repo ID + SHA
workspace                persistent mutable local state
runtime                  disposable sandbox/Jupyter/kernel/MCP
```

## Launch preview and authorization

### GET preview

`GET /open?...` may validate/normalize, resolve public GitHub metadata/ref, and render a local preview. It MUST NOT start build/setup/runtime/kernel/notebook execution.

The page contains a short-lived signed launch token bound to the normalized request. It is protected from framing and is not placed back into the public GET URL.

### POST authorization

A local user gesture submits the token to the side-effecting launch endpoint. The server verifies:
- token signature/expiry;
- request digest;
- one-time JTI/replay state;
- loopback/same-origin browser context according to local policy;
- stable repository/workspace identity has not changed.

Only after this gate may an executable launch be created.

Existing source trust removes only the source-trust prompt; launch authorization is still required.

Stopped workspace reopen also requires a new authorization. An already-active workspace may return/direct to its current session after POST without creating another runtime.

## Source trust lifecycle

Trust uses stable GitHub repository identity:
- exact commit: repository ID + SHA;
- repository-wide: repository ID.

Current owner/name is display/clone metadata, not the trust key.

- rename or transfer with same repository ID: trust continues;
- deletion/recreation under same owner/name with new repository ID: trust does not continue;
- trust revocation affects future launch authorization but does not silently terminate an already-running session;
- deny/cancel creates no positive trust grant and repository code/setup does not execute.

## Fresh source launch

After launch authorization and trust:

1. validate/reconfirm stable repository ID + immutable SHA + notebook path;
2. acquire immutable source material;
3. create a fresh workspace;
4. materialize editable `work/` + persistent `outputs/`;
5. prepare/reuse environment;
6. claim the workspace single-active-session slot transactionally;
7. construct/verify complete sandbox + egress policy;
8. start JupyterLab collaboration/save-version integration;
9. mark ready only after sandbox/network/collaboration/notebook readiness gates.

## Reopen

- GET preview is non-executing.
- local POST authorization is required before restarting compute.
- existing mutable work is reused; no reset from GitHub.
- live existing session is reused/directed to, not duplicated.
- stale session ownership is cleared only after runtime death is confirmed.
- missing active notebook path fails clearly rather than guessing another notebook.

## Stop

Stopping:
- stops/removes runtime;
- invalidates Jupyter/MCP credentials, writable lease, execution handles;
- releases active-session slot;
- preserves workspace, outputs, trust records, and reusable environment cache.

Workspace deletion is future scope.

## Active notebook semantics

`Workspace.active_notebook_path` is the launcher-designated MCP notebook target.

- Browser may open/focus other notebooks in JupyterLab.
- Browser focus/tab selection MUST NOT mutate `active_notebook_path`.
- MCP notebook/cell/execution operations continue to target the designated active notebook.
- Supported active-notebook rename/move uses notebook-aware operation and updates the path.
- Explicit retarget to another notebook is outside this MVP.

Generic workspace file write/move/delete rejects the active notebook path.

## Conflict semantics

Browser and MCP share one authoritative Jupyter collaborative document for the active notebook.

- normal browser edits advance authoritative document/version;
- agent mutation requires expected version;
- stale mutation → conflict/no write;
- stale independent full-file save that would replace active notebook is rejected;
- non-notebook files use analogous file versions.

## Execution lifecycle

Agent execution is mediated by a broker.

- Browser-owned busy kernel → agent request remains queued.
- Queue cancellation/timeout → no interrupt.
- Dispatched request is not considered active until Jupyter busy-parent/request identity matches its message ID.
- Cancel before ownership confirmation → `cancel_pending`, no interrupt of browser work.
- Once agent request becomes active-owned, cancel/timeout may interrupt it.
- Unresponsive agent-owned execution may trigger kernel restart after bounded grace; workspace persists.

## Local data grant

No extra host directory is exposed by default.

Grant creation:
1. local user selects directory + `ro|rw`;
2. resolve canonical root;
3. apply forbidden/whole-home checks to canonical root;
4. persist display path + canonical root;
5. revalidate before mount/use.

Remote launch references cannot create/change grants.

### Host-side containment

Host-side reads/writes/copies under a grant MUST be anchored to the canonical root using symlink/reparse-safe no-follow or dirfd-equivalent path resolution. String prefix is not sufficient.

Reject:
- `..` escape;
- symlink/junction/reparse path component that resolves outside the root;
- magic-link/equivalent escape;
- destination root/path swapped between validation and open;
- missing/replaced grant root.

Save a Copy to `user_data` follows these rules and requires writable grant.

## Persistent write safety

Atomic/safe replacement preserves last good file on `ENOSPC` or comparable failure. Metadata does not advance on failed replacement.

## Outputs

`outputs/` persists independently of runtime. Agent-facing output is separately bounded; full authoritative output remains in notebook/workspace and is not copied into audit logs by default.

## Network lifecycle

Runtime readiness includes default egress verification:
- public globally routable Internet destinations allowed;
- non-global/local destinations denied by default;
- configured DNS resolver endpoints explicitly allowed as infrastructure;
- no host-gateway alias/bypass;
- inbound launcher/Jupyter publication loopback-only.

Network policy is applied by launcher/runtime infrastructure and is not removable by notebook code.

## Git semantics

GitHub is immutable source input only. No Git commit/push/branch/write credentials are exposed.
