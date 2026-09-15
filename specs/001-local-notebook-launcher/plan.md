# Implementation Plan: Local Notebook Launcher

**Branch**: `001-local-notebook-launcher` | **Date**: 2026-09-15 | **Spec**: `specs/001-local-notebook-launcher/spec.md`

## Summary

Build a WSL2/Linux-first local launcher that turns a user-trusted public GitHub notebook into a persistent editable local workspace and a disposable sandboxed Jupyter runtime. Resolve Git refs to immutable SHAs, use repo2docker for reproducible environments, optionally expose a local NVIDIA GPU, and attach MCP-capable agents to the same Jupyter notebook/kernel. The default agent profile is writable; a true inspection-only `readonly` profile is also required.

The product behaves like a local Colab-style workflow: GitHub is immutable source input; the user works on a local persistent copy; Jupyter autosave/edits/outputs survive runtime shutdown; Save a Copy duplicates the working notebook; optional explicitly selected local storage can be mounted like external drive storage. The launcher never commits, pushes, branches, or writes back to GitHub.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, Uvicorn, Pydantic; Git, Docker Engine, repo2docker; JupyterLab inside generated images; pluggable Jupyter-capable MCP backend

**Storage**: Launcher-managed local workspace root; immutable source cache/snapshot; persistent working copies and outputs; Docker image cache; owner-only private runtime/MCP state

**Testing**: pytest + contract/integration tests; Docker-backed sandbox tests; MCP capability tests; workspace persistence/save-copy tests; manual WSL2/NVIDIA smoke test

**Target Platform**: WSL2/Linux-first, native Linux-compatible where practical

**Constraints**: Loopback-only services by default; single-user; user-trusted public GitHub only; no GitHub mutation; outbound sandbox network allowed; inbound blocked except launcher-published loopback services

## Constitution Check

- **Local-first and safe**: PASS — WSL2/Linux-first, loopback listeners, explicit trust confirmation, user-trusted source model.
- **Reproducibility**: PASS — immutable source SHA and environment identity recorded.
- **Complete local agent POC**: PASS — MCP edit/execute path is P1.
- **Sandboxing**: PASS — disposable runtime with minimal mounts, restricted privileges/resources, and no host credentials/Docker socket.
- **Agent trust boundary**: PASS — MCP uses same workspace/kernel/sandbox/GPU policy.
- **Agent editing**: PASS — writable default plus enforced readonly inspection profile.

## Architecture

```text
GitHub notebook @ ref
        |
        v
Source resolver -> immutable commit SHA
        |
        +-----------------------+
        |                       |
        v                       v
immutable source/cache      Environment builder
        |                  repo2docker -> image
        v                       |
Workspace manager               |
  source/ (immutable)           |
  work/   (persistent) <--------+
  outputs/ (persistent)
  optional user-data mount
        |
        v
Standard sandbox runtime (disposable)
  JupyterLab + kernel
  optional NVIDIA GPU
        |
        +-------------------+
        |                   |
        v                   v
     Browser             MCP adapter
                            |
                            v
                Codex / Claude / Gemini / other
```

### Filesystem model

Conceptual launcher storage:

```text
~/.notebook-launcher/
├── sources/<source-id>/          # immutable source material/cache
├── workspaces/<workspace-id>/
│   ├── metadata.json             # provenance + policy, no secrets
│   ├── work/                     # persistent editable working copy
│   └── outputs/                  # persistent generated artifacts
└── runtime/<session-id>/         # private ephemeral state/credentials
```

Runtime mounts:

```text
/source        read-only immutable source snapshot (optional diagnostic access)
/workspace     persistent writable working copy
/outputs       persistent workspace outputs
/mnt/user-data optional explicit user-selected mount (ro or rw)
```

The working copy SHOULD be materialized without a writable `.git` remote relationship. The launcher does not expose GitHub credentials or Git mutation operations.

### Workspace lifecycle

```text
source launch
 -> resolve SHA
 -> trust confirmation if needed
 -> create workspace
 -> copy/materialize editable work tree
 -> start disposable runtime
 -> autosave/edit/execute
 -> stop runtime (workspace survives)
 -> reopen workspace -> new runtime against same work tree
```

A new source launch creates a fresh workspace by default. Reuse of environment images is independent of workspace reuse.

### Save a Copy

Save a Copy duplicates a selected notebook from the current workspace to a validated destination:

- another path inside the workspace; or
- an explicitly configured user-data mount.

The copy may preserve current notebook outputs. It never commits/pushes/branches or alters the immutable source snapshot.

### Sandbox baseline

The `standard` sandbox targets:

- non-root notebook user where compatible with repo2docker image;
- no privileged mode;
- `no-new-privileges`;
- drop unnecessary Linux capabilities (target `ALL` unless a documented runtime need exists);
- Docker/default seccomp or equivalent runtime syscall filtering;
- CPU, memory, and PID limits configurable with safe defaults;
- only explicit workspace/output/user-data mounts;
- no Docker socket, SSH keys, cloud/GitHub credentials, or whole-home mounts;
- outbound network permitted by default;
- inbound connectivity blocked except Jupyter/launcher-controlled ports published to `127.0.0.1`.

Exact compatible flags are validated during implementation; any relaxation requires explicit documentation and tests.

### Trust decision

Because `/open` is reachable through a browser, a new remote source must not silently proceed to code/environment execution. First-time/untrusted source launches enter a confirmation state showing repository, resolved SHA, and notebook path. A local trust record may suppress repeated prompts according to configured policy. Trust does not make sandboxing optional.

### Agent profiles

`write` (default): inspect notebook/cells/outputs, mutate notebook cells, execute cells/notebook/diagnostics, restart kernel.

`readonly`: inspect notebook/cells/outputs only. Execution and mutation are rejected at the launcher/MCP adapter boundary. This is intentionally stricter than “execute but don't edit,” because arbitrary code execution can mutate workspace state.

### Execution semantics

- Same Jupyter kernel for browser and MCP.
- Single-cell and whole-notebook execution use Jupyter kernel APIs, not conversion to a separate Python script.
- Whole-notebook execution preserves notebook order and defaults to stop-on-error.
- Cell outputs/tracebacks remain visible through Jupyter/MCP and are persisted when the working notebook is saved.
- Timeouts/cancellation distinguish kernel exception, timeout, cancellation, and MCP transport failure.

### Network policy

Outbound network is allowed in the POC for packages, model weights, datasets, and APIs. No additional inbound sandbox port is exposed unless explicitly designed; Jupyter is published only on loopback. Stronger egress policy is deferred.

## Project Structure

```text
src/notebook_launcher/
├── app.py
├── cli.py
├── config.py
├── models.py
├── source.py
├── trust.py
├── repository.py
├── workspace.py
├── environment.py
├── sandbox.py
├── runtime.py
├── mcp.py
├── audit.py
├── orchestration.py
└── errors.py

tests/
├── unit/
├── contract/
├── integration/
└── fixtures/

specs/001-local-notebook-launcher/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   ├── mcp-capabilities.md
│   └── workspace-lifecycle.md
└── tasks.md
```

## Implementation Phases

### Phase 0 — Host and backend diagnostics

Verify Git, Docker, repo2docker, optional NVIDIA container capability, and MCP backend availability.

### Phase 1 — Source resolution and trust

Parse GitHub source, resolve SHA, validate notebook path, and implement explicit first-time trust confirmation before remote code/environment execution.

### Phase 2 — Persistent workspace

Create immutable source provenance plus persistent editable work/output directories. Implement reopen and Save a Copy. Ensure runtime teardown never deletes workspace by default.

### Phase 3 — Reproducible environment and standard sandbox

Build/cache image through repo2docker and start it with baseline sandbox restrictions, explicit mounts, outbound-only network policy, and optional GPU.

### Phase 4 — Jupyter runtime

Start authenticated Jupyter on a loopback-published port against the workspace, probe server/kernel readiness, and open the requested working notebook.

### Phase 5 — MCP agent control

Attach selected MCP backend to the same Jupyter runtime. Implement writable default capability set and enforced readonly inspection set.

### Phase 6 — Persistence/mount/status/cleanup

Add optional explicit user-data mount, status APIs, runtime stop/reopen, MCP invalidation, and artifact persistence verification.

### Phase 7 — End-to-end acceptance

Prove GitHub -> trust -> workspace -> sandbox/Jupyter -> GPU optional -> MCP edit/execute/repair -> stop -> workspace reopen.

## Complexity Tracking

Persistent workspace and MCP are required by the desired Colab-like local UX and agent-operable MVP. They remain separated from runtime orchestration so future BinderHub/JupyterHub, alternate sandbox, and alternate MCP backends can replace implementation details without changing user-facing semantics.
