# Implementation Plan: Local Notebook Launcher

**Branch**: `001-local-notebook-launcher` | **Date**: 2026-09-15 | **Spec**: `specs/001-local-notebook-launcher/spec.md`

**Status**: Design-only; implementation intentionally deferred pending review.

## Summary

Build a WSL2/Linux-first local launcher for user-trusted public GitHub notebooks. A stable GET launch URL resolves and previews a notebook locally but is non-executing; the user must perform a separate same-origin one-time local launch authorization before any repository build/runtime code executes. Source trust is a distinct persistent decision and can be exact-commit or repository-wide.

After authorization/trust, mutable refs resolve to immutable SHAs, source/environment provenance is recorded, a fresh persistent workspace is materialized, repo2docker prepares/reuses the environment, the complete standard sandbox plus egress policy is applied, JupyterLab collaboration becomes the authoritative notebook state, and an MCP-capable agent may attach to the same notebook/kernel under explicit write/readonly policy.

GitHub is immutable input only: no commit/push/branch creation or write credentials.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, Uvicorn, Pydantic, Python `sqlite3`, Git, Docker Engine, repo2docker, JupyterLab + `jupyter-collaboration`, launcher-managed Jupyter save/version/execution observation, initial MCP adapter target Datalayer `jupyter-mcp-server` 2.x behind launcher semantic capabilities.

**Storage**:
- filesystem: immutable source cache, persistent workspace/output data, runtime-private credentials;
- SQLite: trust, launch/session/workspace state, one-time authorization replay records, stable repository identity, active-session ownership, writable-agent leases, document/file versions, bounded audit metadata;
- Docker image cache: prepared environments.

**Target Platform**: Windows 11 + WSL2 Ubuntu first; native Linux compatible where practical.

**Project Type**: Local loopback web service + CLI + stdio MCP bridge.

## Constitution Check

- **Local-first/safe by default — PASS**: loopback-only management; GET launch URLs cannot execute; local authorization required; public-source validation.
- **Reproducible launches — PASS**: repository stable ID + resolved commit SHA + environment identity are recorded.
- **Complete local agent-operable POC — PASS**: source → workspace → sandboxed Jupyter → MCP same-runtime path remains P1.
- **Sandbox/resource access — PASS**: mandatory standard sandbox and non-global egress denial are active before execution.
- **Observable/testable — PASS**: launch/trust/authorization phases, queue ownership, sandbox/network assertions, and negative tests are explicit.
- **Agent shares notebook trust boundary — PASS**: same Jupyter document/kernel/filesystem/GPU/sandbox; cancellation is ownership-aware.
- **Agent editing governed — PASS**: write/readonly enforced at adapter boundary; one writable lease; active-notebook file bypass blocked.

No constitution exception is required.

## Architecture

```text
External link / browser navigation
          |
          v
 GET /open  ---------------------------+
 preview only: validate/resolve         |
 stable repo ID + commit SHA            |
 signed request-bound launch token      |
 no build/runtime/code                  |
          | local user gesture          |
          v                             |
 POST /api/launches                     |
 launch authorization gate              |
          |                             |
          +---- if trust absent ----> trust challenge
          |                         exact commit / repository ID
          v
 persistent workspace + source provenance
          |
 repo2docker environment/cache
          |
 complete standard sandbox + egress filter
          |
 JupyterLab + collaboration + kernel
 authoritative active_notebook_path
          |
     +----+------------------+
     |                       |
 Browser                MCP adapter/bridge
                           |
                    execution broker
              queue + Jupyter message ownership
                           |
                         kernel
```

## 1. Launch Authorization vs Source Trust

### GET `/open` is non-executing

The stable user-facing link remains:

```text
http://127.0.0.1:8080/open?url=https://github.com/OWNER/REPO/blob/REF/path/notebook.ipynb
```

GET may:
- parse/normalize input;
- fetch public GitHub metadata needed to resolve stable repository ID, current owner/name, ref → SHA, and notebook existence;
- render a local preview page;
- generate a short-lived signed launch-authorization token bound to the normalized request digest and browser-local session context.

GET MUST NOT:
- run repo2docker or repository setup;
- clone/execute repository hooks/scripts beyond safe metadata/content acquisition needed for preview;
- create/start a notebook runtime/kernel;
- execute notebook code.

### Launch authorization

Execution begins only after local POST authorization. The launch token:
- is short-lived and one-time;
- is not accepted as a GET query parameter;
- is bound to the normalized source/workspace request, GPU mode, agent mode, and request digest;
- is submitted from the local preview page;
- is tied to a SameSite local browser session and checked against loopback/same-origin `Origin`/`Sec-Fetch-Site` policy where available;
- is protected against framing/clickjacking (`frame-ancestors 'none'`, `X-Frame-Options: DENY`);
- records replay/JTI use only on POST, allowing GET to remain non-executing.

Existing trust skips only the trust-choice step. It never skips launch authorization.

Stopped-workspace reopen follows the same GET-preview + local POST authorization flow before compute restarts. If the workspace is already active, the POST may return/direct to the existing session without creating a second runtime.

## 2. Stable Repository Identity and Trust

Source resolution obtains:
- GitHub numeric repository `id` (required trust key);
- `node_id` when available (diagnostic/cross-check);
- current canonical owner/name for display and clone URL;
- requested ref;
- immutable commit SHA;
- normalized notebook path.

Trust keys:
- exact commit: `(repository_id, commit_sha)`;
- repository-wide: `repository_id`.

Rename/transfer: same repository ID, trust continues. UI should display current owner/name and may retain grant-time owner/name for audit/history.

Delete/recreate at same `owner/name`: new repository ID, prior trust does not apply.

If repository identity changes between preview and POST/trust resolution, authorization is invalidated and must be regenerated.

## 3. Source, Workspace, and Runtime Separation

```text
source snapshot @ repository_id + SHA  immutable provenance
workspace/work/                        persistent editable state
workspace/outputs/                     persistent artifacts
runtime/session                        disposable Jupyter/kernel/MCP state
```

Fresh remote launches create fresh workspaces. Reopen never resets workspace from GitHub. Environment cache reuse is independent from workspace reuse.

`active_notebook_path` is launcher-designated. Opening/focusing another JupyterLab notebook does not retarget MCP. The only MVP path-changing operation for the agent target is supported rename/move of that active notebook; explicit retargeting is future scope.

## 4. Authoritative Notebook and Conflict Model

`jupyter-collaboration` is explicitly provisioned/enabled/probed. Browser and MCP operate on one authoritative active-notebook document.

- normal browser edits update the shared document;
- every authoritative change advances opaque `document_version`;
- agent mutations require `expected_document_version`;
- stale agent/independent saves return conflict and do not overwrite;
- generic workspace file write/move/delete rejects the active notebook path;
- active-notebook rename/move goes through notebook-aware Jupyter contents/document operations and updates workspace metadata.

## 5. Execution Broker and Cancellation Ownership

A session-local execution broker serializes agent execution and observes kernel activity. `ExecutionOperation` states:

```text
queued -> dispatched -> running -> completed|failed
   |          |           |
 cancelled  cancel_pending -> cancelling -> cancelled|timeout
```

Each dispatched agent request records its Jupyter request/message ID. The broker observes kernel status messages and their parent request IDs.

Rules:
1. If kernel is busy with browser-owned work, agent requests stay queued and are not dispatched.
2. Cancelling/timing out a queued agent request removes/terminates it without kernel interrupt.
3. A narrow idle→dispatch race is handled by ownership observation: if another request becomes active, the agent operation does not claim ownership merely because it was dispatched.
4. Cancelling a dispatched-but-not-active agent request sets `cancel_pending`; it MUST NOT interrupt a browser-owned active request.
5. A kernel-wide interrupt may be issued only while the active busy-parent/request ID matches the agent operation’s recorded request ID.
6. If a cancelled agent request later becomes active, interrupt it immediately once ownership is observed.
7. After interrupt, wait bounded grace; if the agent-owned request/kernel remains unresponsive, restart/replace kernel according to policy while preserving workspace files.
8. Browser execution is not cancelled by an agent timeout/cancel API.

Agent execute-all is brokered cell-by-cell or operation-by-operation with the same ownership rule.

## 6. MCP Profiles and Writable Lease

`write` (default): inspect/mutate active notebook, execute, restart, notebook-aware move, ordinary workspace file operations, bounded output retrieval.

`readonly`: inspect only; reject execution/cancel/restart/notebook/workspace mutation and escalation.

At most one writable MCP lease per session. Readonly does not consume the writable lease; multi-readonly concurrency is not guaranteed.

MCP descriptor contains no Jupyter token/secret. Default transport is stdio.

## 7. Standard Sandbox

Before Jupyter accepts execution:
- non-root user where compatible;
- no privileged mode;
- `no-new-privileges`;
- drop unnecessary capabilities (target `ALL` unless documented need);
- default seccomp/equivalent syscall filtering;
- CPU/memory/PID limits;
- only explicit mounts;
- no Docker socket, host SSH/GitHub/cloud credentials, whole home, or unrelated paths;
- scrub unrelated host environment secrets;
- only launcher-controlled loopback inbound publication;
- GPU device access only per `auto|on|off` policy;
- default egress policy applied and verified.

Fail closed if mandatory controls cannot be applied.

## 8. Network Policy

Goal: preserve ordinary Internet retrieval without exposing host/LAN services by default.

Default egress allows globally routable public destinations and explicitly approved infrastructure. It denies at IP layer, for IPv4/IPv6 and resolved DNS destinations:
- loopback;
- Docker/WSL host gateway and any explicit host-gateway alias;
- RFC1918/private ranges;
- IPv6 ULA;
- link-local;
- cloud metadata/link-local service ranges;
- multicast;
- unspecified/documentation/reserved/non-global local ranges where reaching them would widen the local trust boundary.

DNS is permitted only to configured resolver endpoints needed by the runtime, with resolver addresses explicitly allowlisted even if locally addressed. DNS answers resolving to denied destination ranges remain denied at egress.

The launcher does not add `host.docker.internal`, `--add-host=host-gateway`, or equivalent bypass by default.

An explicit local/LAN network-access grant is outside MVP scope.

Implementation may use a launcher-managed network namespace/firewall/host-side rules rather than granting firewall capabilities inside the notebook container. The notebook runtime must not receive privileges allowing it to remove the policy.

## 9. Local Data Grant and Symlink-Safe Containment

Grant workflow:
1. user provides local host directory through local CLI;
2. launcher resolves canonical real path;
3. forbidden/whole-home checks run on canonical root;
4. store original path for display and canonical root for enforcement;
5. revalidate root before runtime mount/use.

Host-side read/write/copy operations, including Save a Copy to user-data, are rooted beneath a directory descriptor/canonical root and use no-follow/component-safe semantics. On Linux, implementation should prefer `openat2`-style `RESOLVE_BENEATH`/`NO_MAGICLINKS`/no-follow guarantees where available or a component-by-component dirfd `O_NOFOLLOW` equivalent. String-prefix checks are insufficient.

A symlink/junction/reparse point or root substitution that would escape the grant is rejected. Destination checks occur at the actual open/create operation to avoid validation/open TOCTOU.

Container user-data mount uses the canonical validated root and requested `ro|rw` mode. Whole-home mount remains prohibited by default.

## 10. Storage and Output Safety

Launcher-managed replacement writes and notebook saves use atomic/safe replacement. Disk-full/comparable failures preserve the previous authoritative file.

Agent output payload default maximum: 1 MiB serialized per operation, configurable. Oversized text carries truncation metadata. Binary/multimodal outputs return MIME/type/size + bounded preview/reference. Full notebook output remains in notebook/workspace; audit does not copy it.

## 11. GPU Policy

- `auto`: use usable NVIDIA GPU when available else CPU;
- `on`: require usable GPU, fail before ready if absent;
- `off`: expose no GPU;
- browser and MCP observe the same device scope.

## Project Structure

```text
src/notebook_launcher/
├── app.py            # preview/authorization/status/trust routes
├── cli.py            # serve/mcp/trust/workspace management
├── config.py
├── models.py
├── source.py         # GitHub parsing + stable repository identity/ref resolution
├── launch_auth.py    # one-time request-bound local launch authorization
├── trust.py
├── state.py
├── repository.py
├── workspace.py      # persistence + canonical safe path operations
├── versions.py
├── jupyter.py        # collaboration + request ownership observation
├── environment.py
├── sandbox.py
├── network.py        # default public-Internet/non-global-deny egress policy
├── runtime.py
├── execution.py      # agent queue/message ownership/cancellation broker
├── mcp.py
├── leases.py
├── audit.py
├── orchestration.py
└── errors.py

tests/
├── unit/
├── contract/
├── integration/
└── fixtures/
```

## Implementation Phases

### Phase 0 — Host/backend diagnostics
Git, Docker, repo2docker, state directory, Jupyter collaboration, MCP backend, optional GPU, and network-policy capability diagnostics.

### Phase 1 — Source preview, stable identity, launch authorization, trust
Implement non-executing GET preview, request-bound one-time local launch POST authorization, stable repository ID resolution, trust scopes/revocation/replay protection.

### Phase 2 — Durable workspace and path safety
SQLite schema, source/workspace separation, persistence, safe writes, active notebook path, canonical user-data grants and symlink-safe host-side operations.

### Phase 3 — Environment + full sandbox/network + Jupyter collaboration
repo2docker build/cache; sandbox and public-Internet/non-global-deny egress before execution; Jupyter collaboration/version/rename integration; GPU allocation.

### Phase 4 — MCP + execution broker
Write/readonly policy, writable lease, document/file conflict rules, active-notebook guards, queue/message-ID ownership, safe timeout/cancel, output bounds.

### Phase 5 — UX/status/lifecycle
Status/trust pages, stop/reopen, launch preview/authorization UX, CLI management, secret-safe diagnostics.

### Phase 6 — End-to-end/conformance acceptance
Cross-site GET-only no-execution, trust identity rename/recreate, full sandbox/network, persistence, same-runtime MCP, browser-running queued-agent cancel, active-agent interrupt ownership, symlink escape denial, GPU parity, output bounds, stop/reopen.

## Post-Review Gate

Implementation MUST NOT start until independent review confirms:
- GET launch URLs cannot execute;
- launch authorization remains required despite existing trust;
- repository trust uses stable ID;
- agent cancellation cannot interrupt browser-owned work;
- private/local egress is denied by default while public Internet works;
- user-data host operations are symlink-safe;
- browser focus does not retarget MCP.
