# Implementation Plan: Local Notebook Launcher

**Branch**: `001-local-notebook-launcher` | **Date**: 2026-09-15 | **Spec**: `specs/001-local-notebook-launcher/spec.md`

**Input**: Feature specification from `specs/001-local-notebook-launcher/spec.md`

**Note**: Generated/reconciled by `$speckit-plan` after the 2026-09-15 clarification pass and remediated after `$speckit-analyze`.

## Summary

Build a WSL2/Linux-first local launcher that opens a user-trusted public GitHub notebook as a persistent editable local workspace backed by a disposable sandboxed Jupyter runtime. Resolve mutable Git refs to immutable SHAs, prepare the repository environment reproducibly, apply the complete standard sandbox before notebook execution is possible, optionally expose a local NVIDIA GPU, and let an MCP-capable agent inspect, edit, execute, diagnose, repair, and cancel work in the same notebook/kernel the user sees.

The product follows a local Colab-like model: GitHub is immutable source input; work happens in a persistent local copy; stopping compute does not delete notebook edits or artifacts; Save a Copy remains local; and an explicitly selected local data directory may be attached. The launcher never commits, pushes, creates branches, or modifies GitHub.

The clarified concurrency model is strict and local: trust may be granted per exact commit or per repository; a workspace has at most one active notebook session; a session has at most one writable MCP agent attachment; writable agents may modify the entire workspace while active-notebook mutations remain notebook-aware; and stale writes are rejected instead of resolved by last-write-wins.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, Uvicorn, Pydantic; Python `sqlite3`; Git; Docker Engine; repo2docker; JupyterLab + `jupyter-collaboration`; launcher-managed Jupyter save/version integration; initial MCP adapter target: Datalayer `jupyter-mcp-server` 2.x behind the launcher semantic capability contract

**Storage**: Launcher-managed filesystem for immutable source material, persistent workspace files, outputs, and runtime-private files; local SQLite database for trust records, workspace/session metadata, active-session ownership, writable-agent leases, document/file version metadata, and bounded audit events; Docker image cache for prepared environments

**Testing**: pytest, pytest-asyncio/httpx, contract tests, Docker-backed integration tests, Jupyter/MCP conformance tests, browser↔agent shared-document/version-conflict tests, execution cancellation tests, persistence/reopen and storage-failure tests, sandbox-negative tests, bounded-output tests, and manual/integration WSL2 NVIDIA GPU validation

**Target Platform**: Windows 11 + WSL2 Ubuntu first; native Linux-compatible where practical

**Project Type**: Local web service + CLI + MCP bridge

**Performance Goals**: Long-running environment builds and notebook execution MUST run outside request handlers so health/status/control requests can complete independently of those operations; an environment cache hit MUST skip a rebuild; attaching an agent to a ready session MUST NOT rebuild or restart the runtime

**Constraints**: Loopback-only by default; single local user; public GitHub sources only; user trust required; no GitHub mutation; the complete standard sandbox is mandatory before notebook/agent execution; outbound notebook network enabled by default; inbound exposure limited to launcher-controlled loopback services; one active session per workspace; one active writable-agent lease per session

**Scale/Scope**: One local user, one Docker daemon, locally persisted workspaces, zero or one active session per workspace, one writable MCP agent per active session; the MVP guarantees the read-only profile but does not guarantee simultaneous multi-client read-only attachments; no distributed scheduling

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design and post-analysis remediation.*

- **I. Local-First and Safe by Default — PASS**: WSL2/Linux-first, loopback-only listeners, explicit source trust, validated inputs, no automatic host-data access.
- **II. Reproducible Launches — PASS**: mutable refs resolve to immutable SHAs; source/environment identity is recorded; reusable environment artifacts are cached independently of mutable workspaces.
- **III. Complete Local Agent-Operable POC — PASS**: GitHub → workspace → Jupyter → MCP agent execution/edit/repair remains P1; BinderHub/JupyterHub and multi-user infrastructure remain deferred.
- **IV. Sandboxed Execution and Explicit Resource Access — PASS**: the complete standard sandbox is constructed and asserted before Jupyter accepts notebook/agent execution; mounts, privileges, capabilities, syscall filtering/equivalent controls, resources, credentials, and network exposure are governed from first execution.
- **V. Observable and Testable Launch Pipeline — PASS**: phase-level status and automated negative/integration tests cover source, sandbox, persistence, concurrency, storage failures, output bounds, and end-to-end behavior.
- **VI. Agent Control Shares Notebook Trust Boundary — PASS**: browser and MCP use the same workspace, authoritative Jupyter document/kernel, GPU scope, filesystem, sandbox, and compute limits; execution has actual timeout/cancel control.
- **VII. Agent Editing Explicitly Governed — PASS**: writable is default; readonly blocks execution/mutation; write escalation requires explicit authorization; a single writable-agent lease prevents competing writable controllers; generic file operations cannot bypass active-notebook conflict semantics.

No constitution exception is required.

## Architecture

```text
                          Local control plane
                    +---------------------------+
                    | FastAPI / CLI / SQLite    |
                    | trust, state, leases,     |
                    | versions, audit           |
                    +-------------+-------------+
                                  |
GitHub notebook @ ref             |
        |                         |
        v                         v
Source resolver ----------> Workspace manager
  ref -> immutable SHA       source/ immutable
        |                    work/   persistent
        v                    outputs/ persistent
Environment builder               |
repo2docker -> cached image       |
        |                         |
        +------------+------------+
                     v
             Standard sandbox runtime
       (fully hardened before execution)
             JupyterLab + collaboration
        shared document + active kernel
          version/save integration
             optional NVIDIA GPU
                     |
             +-------+--------+
             |                |
             v                v
          Browser      MCP policy adapter
                          |
                          v
              Datalayer Jupyter MCP backend
                  (initial implementation)
                          |
                          v
              Codex / Claude / Gemini / other
```

Critical invariant:

```text
Browser notebook state === MCP notebook state === same workspace + same authoritative Jupyter document/kernel
```

### Control-plane state

Use a small SQLite database rather than scattered mutable JSON for metadata that needs atomicity. SQLite owns trust records, workspace/session lifecycle state, the one-active-session constraint, writable-agent leases, document/file version tokens, and bounded audit records. Notebook contents and generated artifacts remain ordinary files in the workspace.

The database is local-only and contains no raw notebook contents or Jupyter tokens. Runtime credentials remain owner-only ephemeral state and are invalidated when the session stops.

### Trust model

Before repository-supplied build/runtime code executes, a source not covered by prior trust enters `awaiting_trust`. The local prompt shows repository identity, resolved SHA, and notebook path and offers exactly two positive scopes:

- **exact commit** — trust only that immutable SHA;
- **repository** — trust future revisions from that same repository until revoked.

Cancel/deny executes nothing from the repository. Trust confirmation uses a launch-scoped one-time nonce rendered only by the local trust page; remote launch URLs cannot supply or pre-approve trust. Trust records can be listed/revoked through local management commands.

### Filesystem and persistence model

```text
~/.notebook-launcher/
├── state.db
├── sources/<source-id>/             # immutable/cached source material
├── workspaces/<workspace-id>/
│   ├── work/                        # persistent writable workspace
│   └── outputs/                     # persistent generated artifacts
└── runtime/<session-id>/            # owner-only ephemeral credentials/state
```

Runtime view:

```text
/source          read-only source snapshot when exposed
/workspace       persistent writable working tree
/outputs         persistent generated artifacts
/mnt/user-data   optional explicit local grant (ro or rw)
```

A fresh remote-source launch creates a fresh workspace. Reopening by workspace ID reuses the existing mutable files and does not reset from GitHub. Environment-image reuse is independent of workspace reuse.

The working copy is materialized without a writable GitHub remote/credential relationship. GitHub write operations are outside this feature.

### Workspace/session ownership

SQLite enforces at most one active notebook session per workspace. A request to reopen an already-active workspace returns/directs the user to that session rather than creating a second runtime. On launcher restart, state reconciliation checks recorded active sessions against the actual runtime and clears stale ownership before permitting replacement startup.

### Authoritative notebook, browser saves, and conflict handling

The active notebook has exactly one authoritative Jupyter collaborative document. `jupyter-collaboration` is explicitly provisioned, enabled, and readiness-checked before the session is considered ready.

Normal JupyterLab browser edits participate directly in the shared collaborative document and therefore do not perform an independent stale whole-document replacement. Every authoritative document change advances an opaque launcher-visible `document_version`.

Agent notebook mutations MUST carry `expected_document_version`. A mismatched version is rejected as `conflict` and must refresh/retry. Any stale independent full-document/file save path that could replace the active notebook outside the collaboration document is guarded by launcher/Jupyter save-version integration and is rejected rather than overwriting newer state.

Generic workspace file operations MUST NOT replace, move, or delete the active notebook behind Jupyter's back. Active-notebook rename/move is notebook-aware: it uses the authoritative Jupyter contents/document operation, preserves version semantics, and updates `active_notebook_path`. If the file disappears through an unsupported out-of-band move, reopen/status fails clearly instead of guessing a replacement.

Non-notebook file mutations use analogous opaque `file_version` preconditions for overwrite/move/delete. The initial MCP backend must pass a bidirectional browser↔agent persistence/conflict test; upstream behavior that silently loses either side's edits is a backend-conformance failure.

### Storage-write failure behavior

Launcher-managed persistent replacement writes use safe/atomic replacement patterns appropriate to the filesystem. Jupyter notebook saves must retain atomic-save behavior or equivalent protection. Disk-full and comparable write failures return actionable errors and MUST leave the previously saved file intact rather than accepting a partial replacement. A missing active notebook after an unsupported external move is reported, not auto-repaired by guesswork.

### Agent profiles and writable lease

`write` (default) may inspect/execute/mutate notebook cells and create/read/modify/rename/delete files anywhere inside `/workspace`, plus access explicit user-data grants according to their mount mode. The active notebook remains mutable through notebook-aware operations; generic file operations cannot bypass its document/version boundary. The profile cannot implicitly widen access beyond the sandbox.

`readonly` may inspect notebook/cell/output state only. Code execution, kernel restart, notebook mutation, workspace mutation, and file mutation are rejected at the launcher/MCP policy boundary.

An active session may have at most one writable MCP lease. A second writable attachment fails clearly until the current writable client disconnects or the lease is invalidated. Read-only attachments do not consume that writable lease, but the MVP does not guarantee concurrent multiple read-only clients; the backend/bridge may serialize them.

### MCP backend strategy

The initial adapter targets Datalayer `jupyter-mcp-server` 2.x because it can connect to an existing Jupyter server and exposes notebook/cell operations, execution, outputs, and kernel control. The launcher does not expose Datalayer-specific tool names as its public contract.

The exact patch version is pinned only after the conformance suite passes against the launcher Jupyter/Jupyter-collaboration versions. The conformance gate includes bidirectional edit persistence/conflict semantics, active-notebook file-boundary enforcement, cancellation, and bounded-output behavior. If a candidate backend/version cannot satisfy the semantic contract, it is rejected or adapted; launcher semantics are not weakened.

### Save a Copy

Save a Copy snapshots the current saved notebook to a validated destination inside the workspace or an explicitly granted writable user-data directory. Existing destinations are preserved unless overwrite is explicit. The operation never modifies source provenance or GitHub.

### Optional local data grant

Host paths are never accepted from a remote GitHub launch URL. The MVP grants/revokes a local host directory through local CLI/management flow, with explicit `ro`/`rw` mode; the runtime receives only the selected directory at the stable user-data mount path. Whole-home mounting is prohibited by default.

### Standard sandbox and network policy

The complete `standard` sandbox is applied and validated before Jupyter is allowed to accept notebook or agent execution. It includes:

- non-root notebook user where compatible;
- no privileged mode;
- `no-new-privileges`;
- dropped unnecessary Linux capabilities (target `ALL` unless a documented need exists);
- Docker/default seccomp or equivalent runtime syscall filtering;
- configurable CPU, memory, and PID limits;
- only explicit source/workspace/output/user-data mounts;
- no Docker socket, SSH keys, cloud/GitHub credentials, whole-home mount, or unrelated host path;
- a scrubbed runtime environment that does not inherit unrelated host secrets;
- Jupyter/launcher-controlled inbound services published only on loopback.

Outbound network is enabled by default for packages, model weights, datasets, and APIs. The standard sandbox is defense-in-depth for user-trusted repositories, not hostile-code containment.

### GPU policy

`gpu=auto` uses a usable NVIDIA GPU when available and otherwise runs CPU-only. `gpu=on` requires GPU support and fails before readiness if unavailable. `gpu=off` never grants GPU access. Browser and MCP probes must observe the same device scope.

### Execution timeout and cancellation semantics

Single-cell and whole-notebook execution use the active Jupyter kernel. Whole-notebook execution preserves cell order, defaults to stop-on-error, and never silently converts the notebook into an unrelated script/runtime.

Each agent execution has an operation identity and configurable deadline. Explicit cancellation or deadline expiry first attempts a Jupyter kernel interrupt. If the kernel does not become responsive within the configured grace interval, the launcher may restart/replace the kernel while preserving workspace files. `timeout`, `cancelled`, kernel exception, kernel death, permission denial, lease denial, and MCP transport failure remain distinct outcomes.

Cancellation controls the requested execution; it does not delete the workspace. After cancellation/recovery the session remains usable when the runtime itself is healthy.

### Bounded output policy

Agent-facing output retrieval is bounded independently from notebook persistence. The default maximum serialized MCP output payload is **1 MiB per operation**, configurable by the local launcher.

- Oversized text is truncated with `truncated=true`, original-size metadata when known, and a clear continuation/artifact hint where available.
- Binary/multimodal output returns MIME/type/size metadata and only a bounded preview/reference representation rather than forcing the full payload through control/audit channels.
- The authoritative full notebook output remains in the notebook/workspace according to normal Jupyter persistence.
- Audit records never copy the full output payload by default.

## Project Structure

### Documentation (this feature)

```text
specs/001-local-notebook-launcher/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   ├── cli.md
│   ├── mcp-capabilities.md
│   └── workspace-lifecycle.md
└── tasks.md
```

### Source Code (repository root)

```text
src/notebook_launcher/
├── __init__.py
├── app.py                    # loopback web launcher/status/trust routes
├── cli.py                    # serve, mcp, trust and workspace management
├── config.py                 # paths, limits, timeout/output/backend options
├── models.py                 # request/state/API models
├── source.py                 # GitHub parsing, validation, ref resolution
├── trust.py                  # exact-commit/repository trust policy
├── state.py                  # SQLite schema/repository + transactions
├── repository.py             # source acquisition/cache
├── workspace.py              # create/reopen/copy/mount/atomic persistence
├── versions.py               # opaque notebook/file version preconditions
├── jupyter.py                # collaboration, save/version, rename events
├── environment.py            # repo2docker image identity/build/cache
├── sandbox.py                # complete standard sandbox policy/argv
├── runtime.py                # Jupyter/container/GPU lifecycle
├── mcp.py                    # semantic MCP policy adapter/backend bridge
├── leases.py                 # session ownership + writable-agent leases
├── audit.py                  # sanitized bounded events
├── orchestration.py          # launch state machine/reconciliation
└── errors.py                 # typed user-safe failures

tests/
├── unit/
├── contract/
├── integration/
└── fixtures/
```

**Structure Decision**: One Python package keeps the POC local and small. Source resolution, persistent state, workspace management, Jupyter collaboration/versioning, sandbox/runtime, and MCP policy are distinct modules so later BinderHub/JupyterHub, strict sandbox, or alternate MCP backends can replace one subsystem without changing the public launch/agent contracts.

## Implementation Phases

### Phase 0 — Host/backend diagnostics

Validate Git, Docker, repo2docker, SQLite state directory, optional NVIDIA container capability, Jupyter collaboration availability, and the configured MCP backend independently. Record exact tested package/runtime versions.

### Phase 1 — Source resolution and trust control

Implement strict GitHub parsing/ref resolution, exact-commit/repository trust records, one-time confirmation nonce, deny/cancel behavior, and trust list/revoke management.

### Phase 2 — Durable control state and workspace lifecycle

Create SQLite schema/migrations, source provenance, fresh/reopen workspace behavior, outputs, Save a Copy, safe replacement/write-error behavior, active-notebook path reconciliation, explicit user-data grants, crash/stale-session reconciliation, and one-active-session-per-workspace enforcement.

### Phase 3 — Reproducible environment, complete sandbox, and Jupyter shared document

Build/cache with repo2docker; construct and assert the complete standard sandbox before notebook execution; start authenticated JupyterLab with collaboration explicitly provisioned/enabled; establish version-aware save/rename integration; apply outbound-enabled/loopback-only network policy; then implement GPU modes.

### Phase 4 — MCP policy, versions, cancellation, output bounds, and writable lease

Integrate the initial Datalayer adapter behind semantic capabilities. Enforce writable/readonly profiles, opaque document/file versions, active-notebook generic-file restrictions, conflict rejection, notebook-aware rename, full-workspace writable operations, one writable-agent lease, actual timeout/cancellation, bounded output retrieval, and sanitized audit events.

### Phase 5 — Local UX/status/management

Complete launch/status/trust pages, ready redirects, CLI management commands, clear conflict/lease/storage errors, stop/reopen behavior, and secret-safe diagnostics.

### Phase 6 — End-to-end and conformance acceptance

Prove GitHub → trust → workspace → full sandbox/Jupyter collaboration → optional GPU → writable MCP edit/execute/file operations → controlled conflict/failure/cancel/repair → notebook rename → stop → reopen. Verify source-declared dependency isolation, no GitHub mutation, no prohibited mounts, no second active workspace runtime, no second writable-agent lease, no silent human/agent edit loss, bounded large/multimodal output, and safe disk-full failure behavior.

## Post-Design Constitution Re-check

Post-analysis remediation preserves every gate and removes the two identified constitution risks: the complete standard sandbox now precedes first notebook execution, and MCP execution now has an explicit interrupt/recovery cancellation mechanism plus acceptance coverage. Jupyter collaboration/save integration makes the human+agent conflict rule implementable without weakening the clarified behavior. SQLite remains a local-only metadata dependency; the initial MCP backend remains replaceable. No constitution exception or Complexity Tracking entry is required.

## Complexity Tracking

No constitution violations require justification.
