# Implementation Plan: Local Notebook Launcher

**Branch**: `001-local-notebook-launcher` | **Date**: 2026-09-15 | **Spec**: `specs/001-local-notebook-launcher/spec.md`

**Input**: Feature specification from `specs/001-local-notebook-launcher/spec.md`

**Note**: Generated/reconciled by `$speckit-plan` after the 2026-09-15 clarification pass.

## Summary

Build a WSL2/Linux-first local launcher that opens a user-trusted public GitHub notebook as a persistent editable local workspace backed by a disposable sandboxed Jupyter runtime. Resolve mutable Git refs to immutable SHAs, prepare the repository environment reproducibly, optionally expose a local NVIDIA GPU, and let an MCP-capable agent inspect, edit, execute, diagnose, and repair the same notebook/kernel the user sees.

The product follows a local Colab-like model: GitHub is immutable source input; work happens in a persistent local copy; stopping compute does not delete notebook edits or artifacts; Save a Copy remains local; and an explicitly selected local data directory may be attached. The launcher never commits, pushes, creates branches, or modifies GitHub.

The clarified concurrency model is strict and local: trust may be granted per exact commit or per repository; a workspace has at most one active notebook session; a session has at most one writable MCP agent attachment; writable agents may modify the entire workspace; and stale human/agent mutations are rejected by optimistic document/file version checks rather than resolved by last-write-wins.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, Uvicorn, Pydantic; Python `sqlite3`; Git; Docker Engine; repo2docker; JupyterLab with Jupyter collaboration support; initial MCP adapter target: Datalayer `jupyter-mcp-server` 2.x behind the launcher semantic capability contract

**Storage**: Launcher-managed filesystem for immutable source material, persistent workspace files, outputs, and runtime-private files; local SQLite database for trust records, workspace/session metadata, active-session ownership, writable-agent leases, document/file version metadata, and bounded audit events; Docker image cache for prepared environments

**Testing**: pytest, pytest-asyncio/httpx, contract tests, Docker-backed integration tests, Jupyter/MCP conformance tests, concurrent-edit/version-conflict tests, persistence/reopen tests, sandbox-negative tests, and manual/integration WSL2 NVIDIA GPU validation

**Target Platform**: Windows 11 + WSL2 Ubuntu first; native Linux-compatible where practical

**Project Type**: Local web service + CLI + MCP bridge

**Performance Goals**: Local status/control operations remain responsive while builds and notebook execution run asynchronously; cached immutable environments bypass rebuild; attaching an agent to a ready session does not rebuild or restart the runtime

**Constraints**: Loopback-only by default; single local user; public GitHub sources only; user trust required; no GitHub mutation; standard sandbox mandatory; outbound notebook network allowed by default; inbound exposure limited to launcher-controlled loopback services; one active session per workspace; one active writable agent lease per session

**Scale/Scope**: One local user, one Docker daemon, a small number of persistent workspaces, zero or one active session per workspace, one writable MCP agent per active session, optional concurrent read-only agent attachments, no distributed scheduling

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

- **I. Local-First and Safe by Default — PASS**: WSL2/Linux-first, loopback-only listeners, explicit source trust, validated inputs, no automatic host-data access.
- **II. Reproducible Launches — PASS**: mutable refs resolve to immutable SHAs; source/environment identity is recorded; reusable environment artifacts are cached independently of mutable workspaces.
- **III. Complete Local Agent-Operable POC — PASS**: GitHub → workspace → Jupyter → MCP agent execution/edit/repair remains P1; BinderHub/JupyterHub and multi-user infrastructure remain deferred.
- **IV. Sandboxed Execution and Explicit Resource Access — PASS**: standard sandbox, minimal mounts, no Docker socket/host credentials, explicit GPU and user-data grants.
- **V. Observable and Testable Launch Pipeline — PASS**: phase-level status and automated negative/integration tests are designed into each subsystem.
- **VI. Agent Control Shares Notebook Trust Boundary — PASS**: browser and MCP use the same workspace, Jupyter server/kernel, GPU scope, filesystem, sandbox, and compute limits.
- **VII. Agent Editing Explicitly Governed — PASS**: writable is default; readonly blocks execution/mutation; write escalation requires explicit authorization; a single writable-agent lease prevents competing writable controllers.

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
             JupyterLab + collaboration
             active kernel + optional GPU
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
Browser notebook state === MCP notebook state === same workspace + same Jupyter document/kernel
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

### Human/agent conflict handling

Notebook and mutable workspace reads expose an opaque version token derived from the authoritative current document/file state. Every agent mutation must supply the version it was based on. If the authoritative version changed, the mutation is rejected as `conflict` and the agent must refresh/retry.

For notebook cells, the authoritative state is the shared Jupyter document when collaboration support is active. The initial MCP backend must pass a bidirectional browser↔agent persistence test; upstream behavior that silently loses either side's edits is a backend-conformance failure, never a reason to fall back to last-write-wins.

The adapter may use Jupyter collaboration/YDoc metadata internally, but the public contract exposes only an opaque `document_version`. Non-notebook file mutations use analogous opaque `file_version` preconditions.

### Agent profiles and writable lease

`write` (default) may inspect/execute/mutate notebook cells and create/read/modify/rename/delete files anywhere inside `/workspace`, plus access explicit user-data grants according to their mount mode. It cannot implicitly widen access beyond the sandbox.

`readonly` may inspect notebook/cell/output state only. Code execution, kernel restart, notebook mutation, workspace mutation, and file mutation are rejected at the launcher/MCP policy boundary.

An active session may have at most one writable MCP lease. A second writable attachment fails clearly until the current writable client disconnects or the lease is invalidated. Read-only attachments are not serialized by this rule but remain session-scoped and non-mutating.

### MCP backend strategy

The initial adapter targets Datalayer `jupyter-mcp-server` 2.x because it can connect to an existing Jupyter server and exposes notebook/cell operations, execution, outputs, and kernel control. The launcher does not expose Datalayer-specific tool names as its public contract.

The exact patch version is pinned only after the conformance suite passes against the launcher Jupyter/Jupyter-collaboration versions. The conformance gate includes bidirectional edit persistence because upstream real-time-collaboration regressions have existed. If a candidate backend/version cannot satisfy the semantic contract, it is rejected or adapted; launcher semantics are not weakened.

### Save a Copy

Save a Copy snapshots the current saved notebook to a validated destination inside the workspace or an explicitly granted writable user-data directory. Existing destinations are preserved unless overwrite is explicit. The operation never modifies source provenance or GitHub.

### Optional local data grant

Host paths are never accepted from a remote GitHub launch URL. The MVP grants/revokes a local host directory through local CLI/management flow, with explicit `ro`/`rw` mode; the runtime receives only the selected directory at the stable user-data mount path. Whole-home mounting is prohibited by default.

### Standard sandbox and network policy

The standard sandbox targets non-root execution where compatible, `no-new-privileges`, dropped unnecessary capabilities, seccomp/equivalent syscall filtering, CPU/memory/PID limits, minimal explicit mounts, no Docker socket, no SSH/cloud/GitHub credentials, and loopback-only published services. Outbound network remains enabled for packages, models, datasets, and APIs. The standard sandbox is defense-in-depth for trusted repositories, not hostile-code containment.

### GPU policy

`gpu=auto` uses a usable NVIDIA GPU when available and otherwise runs CPU-only. `gpu=on` requires GPU support and fails before readiness if unavailable. `gpu=off` never grants GPU access. Browser and MCP probes must observe the same device scope.

### Execution semantics

Single-cell and whole-notebook execution use the active Jupyter kernel. Whole-notebook execution preserves cell order, defaults to stop-on-error, and never silently converts the notebook into an unrelated script/runtime. Kernel exceptions, timeout, cancellation, kernel death, and MCP transport failure remain distinguishable.

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
└── tasks.md                  # regenerated later by $speckit-tasks
```

### Source Code (repository root)

```text
src/notebook_launcher/
├── __init__.py
├── app.py                    # loopback web launcher/status/trust routes
├── cli.py                    # serve, mcp, trust and workspace management
├── config.py                 # local paths, limits, backend/runtime options
├── models.py                 # request/state/API models
├── source.py                 # GitHub parsing, validation, ref resolution
├── trust.py                  # exact-commit/repository trust policy
├── state.py                  # SQLite schema/repository + transactions
├── repository.py             # source acquisition/cache
├── workspace.py              # create/reopen/copy/mount metadata
├── versions.py               # opaque notebook/file version preconditions
├── environment.py            # repo2docker image identity/build/cache
├── sandbox.py                # standard sandbox policy/argv
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

**Structure Decision**: One Python package keeps the POC local and small. Source resolution, persistent state, workspace management, sandbox/runtime, versioning, and MCP policy are distinct modules so later BinderHub/JupyterHub, strict sandbox, or alternate MCP backends can replace one subsystem without changing the public launch/agent contracts.

## Implementation Phases

### Phase 0 — Host/backend diagnostics

Validate Git, Docker, repo2docker, SQLite state directory, optional NVIDIA container capability, and the configured MCP backend independently. Record exact tested package/runtime versions.

### Phase 1 — Source resolution and trust control

Implement strict GitHub parsing/ref resolution, exact-commit/repository trust records, one-time confirmation nonce, deny/cancel behavior, and trust list/revoke management.

### Phase 2 — Durable control state and workspace lifecycle

Create SQLite schema/migrations, source provenance, fresh/reopen workspace behavior, outputs, Save a Copy, explicit user-data grants, crash/stale-session reconciliation, and one-active-session-per-workspace enforcement.

### Phase 3 — Reproducible environment and sandbox runtime

Build/cache with repo2docker, start authenticated Jupyter/Jupyter collaboration in the standard sandbox, apply resource/network/mount restrictions, and implement GPU modes.

### Phase 4 — MCP policy, versions, and writable lease

Integrate the initial Datalayer adapter behind semantic capabilities. Enforce writable/readonly profiles, opaque document/file versions, conflict rejection, full-workspace writable operations, one writable-agent lease, timeouts/cancellation, and sanitized audit events.

### Phase 5 — Local UX/status/management

Complete launch/status/trust pages, ready redirects, CLI management commands, clear conflict/lease errors, stop/reopen behavior, and secret-safe diagnostics.

### Phase 6 — End-to-end and conformance acceptance

Prove GitHub → trust → workspace → sandbox/Jupyter → optional GPU → writable MCP edit/execute/file operations → controlled conflict/failure/repair → stop → reopen. Verify no GitHub mutation, no prohibited mounts, no second active workspace runtime, no second writable-agent lease, and no silent human/agent edit loss.

## Post-Design Constitution Re-check

Phase 1 design preserves every pre-research gate. SQLite adds no service dependency and is used only where atomic local metadata is required; filesystem notebook data remains transparent. The initial MCP backend remains replaceable, and its known collaboration risks are converted into explicit conformance tests rather than accepted as product behavior. No constitution exception or Complexity Tracking entry is required.

## Complexity Tracking

No constitution violations require justification.
