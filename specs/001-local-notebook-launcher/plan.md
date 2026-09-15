# Implementation Plan: Local Notebook Launcher

**Branch**: `001-local-notebook-launcher` | **Date**: 2026-09-15 | **Spec**: `specs/001-local-notebook-launcher/spec.md`

## Summary

Build a loopback-only local web launcher that accepts a public GitHub notebook URL or `repo`/`ref`/`path`, resolves it to an immutable commit, prepares a reproducible container image with `repo2docker`, starts JupyterLab in an isolated Docker container, and redirects the browser to the requested notebook. On WSL2 hosts configured with NVIDIA Container Toolkit, the session can receive local GPU access through Docker's GPU device request.

Every ready notebook session also exposes an implementation-neutral MCP attachment contract. An MCP-capable agent can attach to the same Jupyter server/kernel, inspect the notebook, execute cells or the complete notebook, retrieve outputs/errors, execute diagnostic code, and restart/reconnect the kernel. The launcher owns the session credentials and presents a session-scoped attachment command/descriptor so agents do not need raw Jupyter tokens.

The MVP intentionally does **not** require Kubernetes, BinderHub, or JupyterHub. It proves the critical path—repository resolution, repo2docker environment construction, containerized Jupyter startup, caching, GPU passthrough, and agent-driven execution—behind stable launch and MCP contracts that can later target BinderHub/JupyterHub without changing notebook links or agent semantics.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, Uvicorn, Pydantic; external runtime dependencies: Git, Docker Engine, `repo2docker`; JupyterLab inside generated images; one MCP backend/adapter capable of controlling the running Jupyter server

**Storage**: Local filesystem cache and Docker image store; in-memory launch/session state for the POC; per-session owner-readable runtime metadata for local MCP attachment if required by separate MCP subprocesses

**Testing**: pytest, pytest-asyncio, FastAPI TestClient/httpx; Docker-backed integration tests; MCP adapter contract tests; end-to-end agent execution test; manual WSL2/NVIDIA GPU smoke test

**Target Platform**: Primary: Windows 11 + WSL2 Ubuntu 24.04; compatible native Linux where practical

**Project Type**: Local web service / launcher with local MCP bridge

**Performance Goals**: Cached launch bypasses image build; launcher endpoints remain responsive during builds/execution; MCP attach to an already-ready session should not rebuild/restart the notebook runtime

**Constraints**: Loopback binding by default; single-user POC; public GitHub only; no shell interpolation of user input; notebook runtime isolated from host; GPU optional; MCP must not widen the notebook's trust boundary

**Scale/Scope**: One local user, a small number of concurrent launches/sessions, one local Docker daemon, one or more local MCP clients, zero distributed scheduling

## Constitution Check

- **Local-first and safe by default**: PASS — service binds to `127.0.0.1`; external code runs in containers; host secrets are not mounted by default; MCP defaults to local stdio attachment.
- **Reproducible launches**: PASS — mutable refs resolve to commit SHA; image/cache identity includes immutable source revision.
- **Smallest useful POC**: PASS — Docker + repo2docker is selected before Kubernetes/BinderHub; an existing MCP implementation may be adapted rather than building a protocol server from scratch.
- **Isolation and explicit resource access**: PASS — Docker provides runtime isolation; GPU is explicit; MCP inherits the same session filesystem/device scope and does not receive the Docker socket.
- **Observable and testable pipeline**: PASS — launch phases and MCP lifecycle/execution events are surfaced/tested separately.

## Architecture

```text
                           +-------------------+
GitHub .ipynb ------------>|  notebook-launcher|
                           +---------+---------+
                                     |
              +----------------------+----------------------+
              |                      |                      |
              v                      v                      v
       Source resolver        Environment builder      Status/API
       Git ref -> SHA         repo2docker -> image     /open /api/...
              |                      |
              +-----------+----------+
                          v
                   Runtime manager
                          |
             Docker + JupyterLab + kernel
                          |
                 optional NVIDIA GPU
                          |
             +------------+------------+
             |                         |
             v                         v
          Browser                  MCP adapter
      JupyterLab UI                    |
                                       v
                            Codex / Claude / Gemini /
                            other MCP-capable clients
```

Critical invariant:

```text
Browser JupyterLab session === MCP-controlled Jupyter session/kernel
```

The MCP path MUST attach to the already-launched runtime. It must not silently create a separate kernel/container that diverges from what the user sees.

### Launch lifecycle

`received -> resolving -> acquiring -> building|cache_hit -> starting -> mcp_preparing -> ready`

Terminal failure states retain the failed phase and diagnostic message. MCP may be independently `available`, `unavailable`, `attaching`, `attached`, or `error` while a ready notebook remains usable. Stopping a session transitions `ready -> stopping -> stopped`, invalidates MCP attachment, and preserves reusable image cache.

### GPU policy

- `gpu=auto` (default): use GPU if Docker reports a usable NVIDIA runtime/device; otherwise launch CPU-only.
- `gpu=on`: require GPU capability; fail before notebook readiness if unavailable.
- `gpu=off`: never request GPU devices.

GPU support is provided through the host Docker/NVIDIA Container Toolkit configuration. The launcher does not install Windows drivers, CUDA drivers, or NVIDIA Container Toolkit.

### MCP capability profile

The launcher defines semantic capabilities rather than implementation-specific MCP tool names. P1 requires:

- attach to the launched session
- list/read the active notebook and cells
- execute one cell
- execute all notebook cells in order
- execute arbitrary diagnostic code in the active kernel
- retrieve text/error/multimodal outputs as supported by the backend/client
- restart/reconnect the kernel
- report backend capability availability explicitly

Cell create/edit/delete is desirable for remediation and SHOULD be exposed when supported, but the adapter must report it honestly rather than inventing unsafe fallback behavior.

### MCP transport and credential model

For the local POC, stdio is the preferred transport because it does not require another listening port and is widely supported by coding agents. The launcher should expose a stable command shape such as:

```text
notebook-launcher mcp <session-id>
```

That command resolves local, session-scoped runtime metadata and starts/connects the selected MCP backend to the existing Jupyter server. Raw Jupyter tokens remain internal to the launcher/MCP bridge and are not emitted in normal API responses or logs.

If per-session metadata must survive across launcher/MCP subprocess boundaries, store it under the launcher's local state directory with owner-only permissions and delete/invalidate it on session stop.

A future Streamable HTTP transport may be added behind the same `McpAttachment` descriptor without changing semantic capabilities.

## Project Structure

```text
src/notebook_launcher/
├── __init__.py
├── app.py                 # FastAPI application and routes
├── cli.py                 # service CLI + `mcp <session-id>` bridge entry point
├── config.py              # local paths, bind host/port, runtime/MCP options
├── models.py              # request/source/status/session/MCP models
├── source.py              # GitHub parsing, validation, ref resolution
├── repository.py          # clone/fetch/cache/checkout operations
├── environment.py         # repo2docker image identity/build/cache
├── runtime.py             # Docker/Jupyter session lifecycle and GPU flags
├── mcp.py                 # semantic MCP adapter + backend process bridge
├── audit.py               # sanitized launch/MCP execution events
├── orchestration.py       # launch state machine
└── errors.py              # typed user-facing failures

tests/
├── unit/
│   ├── test_source.py
│   ├── test_environment.py
│   ├── test_runtime.py
│   ├── test_mcp.py
│   └── test_orchestration.py
├── contract/
│   ├── test_openapi.py
│   └── test_mcp_capabilities.py
├── integration/
│   ├── test_public_repo_launch.py
│   ├── test_cached_launch.py
│   └── test_agent_notebook_execution.py
└── fixtures/

specs/001-local-notebook-launcher/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   └── mcp-capabilities.md
└── tasks.md

pyproject.toml
README.md
```

**Structure Decision**: Single Python launcher plus a contained MCP adapter module. Repository, environment, runtime, and MCP responsibilities remain separated so a future BinderHub/JupyterHub runtime or different MCP implementation can replace the corresponding backend without changing `/open` or the semantic MCP capability contract.

## Implementation Phases

### Phase 0 — Host/runtime validation

Verify Git, Docker, repo2docker, optional NVIDIA container GPU capability, and configured MCP backend availability independently. Do not mix host bootstrap into notebook launch orchestration.

### Phase 1 — Source and launch contract

Implement strict parsing for full GitHub notebook URLs and explicit repository/ref/path requests. Resolve refs, normalize notebook paths, and produce immutable `ResolvedSource` records.

### Phase 2 — Reproducible environment build

Acquire the repository at the resolved commit and use repo2docker to build/tag a local image. Cache by immutable environment identity. Use structured argv and never shell-composed user input.

### Phase 3 — Jupyter session runtime

Launch the prepared image with a generated token and dynamically allocated loopback port. Optionally request NVIDIA GPU devices. Detect Jupyter and kernel readiness before marking the runtime attachable.

### Phase 4 — MCP attachment and agent execution

Implement the semantic MCP adapter and session descriptor. Start/connect the chosen MCP backend against the existing Jupyter server, prove notebook/cell inspection, cell/full-notebook execution, output/error propagation, arbitrary kernel diagnostics, and restart/reconnect behavior. Add audit events for attachment and execution lifecycle.

### Phase 5 — Status, cleanup, and security tests

Expose launch/session/MCP status, phase-specific errors, stop/cleanup, cached-launch verification, unsafe-input tests, cross-session/credential-boundary tests, and WSL2 GPU procedures.

### Phase 6 — End-to-end acceptance

Run the complete path:

```text
GitHub notebook
  -> /open
  -> repo2docker image
  -> local Jupyter session
  -> optional local GPU
  -> MCP attachment
  -> agent executes notebook
  -> outputs/errors visible in same session
```

### Phase 7 — Backend evolution (out of MVP)

Add BinderHub/JupyterHub and/or alternative MCP backends behind the established contracts. Do not change existing launch links or semantic MCP capability requirements merely because backend implementation changes.

## Complexity Tracking

The addition of MCP is justified because agent-driven execution is now a P1 product requirement. The design avoids a second orchestration platform: MCP attaches to the same Jupyter session and defaults to stdio. Kubernetes/BinderHub and custom protocol implementation remain deferred.
