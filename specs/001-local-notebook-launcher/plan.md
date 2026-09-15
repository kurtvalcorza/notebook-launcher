# Implementation Plan: Local Notebook Launcher

**Branch**: `001-local-notebook-launcher` | **Date**: 2026-09-15 | **Spec**: `specs/001-local-notebook-launcher/spec.md`

## Summary

Build a loopback-only local web launcher that accepts a public GitHub notebook URL or `repo`/`ref`/`path`, resolves it to an immutable commit, prepares a reproducible container image with `repo2docker`, starts JupyterLab in an isolated Docker container, and redirects the browser to the requested notebook. On WSL2 hosts configured with NVIDIA Container Toolkit, the session can receive local GPU access through Docker's GPU device request.

The MVP intentionally does **not** require Kubernetes, BinderHub, or JupyterHub. It proves the same critical lower-level path BinderHub depends on—repository resolution, repo2docker environment construction, containerized Jupyter startup, caching, and GPU passthrough—behind a public launch contract that can later target a BinderHub/JupyterHub backend without changing notebook links.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, Uvicorn, Pydantic; external runtime dependencies: Git, Docker Engine, `repo2docker`; JupyterLab runs inside generated images

**Storage**: Local filesystem cache and Docker image store; in-memory launch/session state for the POC

**Testing**: pytest, pytest-asyncio, FastAPI TestClient/httpx; Docker-backed integration tests; manual WSL2/NVIDIA GPU smoke test

**Target Platform**: Primary: Windows 11 + WSL2 Ubuntu 24.04; compatible native Linux where practical

**Project Type**: Local web service / launcher

**Performance Goals**: Cached launch should bypass image build and reach Jupyter startup directly; launcher request/status endpoints should remain responsive during long builds

**Constraints**: Loopback binding by default; single-user POC; public GitHub only; no shell interpolation of user input; notebook runtime isolated from host; GPU optional

**Scale/Scope**: One local user, a small number of concurrent launches/sessions, one local Docker daemon, zero distributed scheduling

## Constitution Check

- **Local-first and safe by default**: PASS — service binds to `127.0.0.1`; external code runs in containers; host secrets are not mounted by default.
- **Reproducible launches**: PASS — mutable refs resolve to commit SHA; image/cache identity includes immutable source revision.
- **Smallest useful POC**: PASS — Docker + repo2docker is selected before Kubernetes/BinderHub to prove the end-to-end path with fewer moving parts.
- **Isolation and explicit resource access**: PASS — Docker provides runtime isolation; GPU is an explicit launch policy (`auto`, `on`, `off`).
- **Observable and testable pipeline**: PASS — lifecycle phases are modeled and surfaced through status endpoints; parsers/orchestrators receive unit/integration coverage.

## Architecture

```text
Browser
  |
  | GET /open?url=...&gpu=auto
  v
FastAPI launcher (127.0.0.1)
  |
  +--> Source resolver
  |      +--> validate github.com URL / repo-ref-path
  |      +--> inspect git refs
  |      `--> resolve immutable commit SHA
  |
  +--> Repository cache
  |      `--> clone/fetch/checkout resolved commit
  |
  +--> Environment builder
  |      +--> compute image identity
  |      `--> repo2docker -> local Docker image
  |
  +--> Runtime manager
  |      +--> Docker container
  |      +--> optional --gpus all
  |      +--> random loopback host port
  |      `--> generated Jupyter token
  |
  `--> Status/redirect
         `--> http://127.0.0.1:<port>/lab/tree/<notebook>
```

### Launch lifecycle

`received -> resolving -> acquiring -> building|cache_hit -> starting -> ready`

Terminal failure states retain the failed phase and diagnostic message. Stopping a session transitions `ready -> stopping -> stopped` without deleting the reusable image cache.

### GPU policy

- `gpu=auto` (default): use GPU if Docker reports a usable NVIDIA runtime/device; otherwise launch CPU-only.
- `gpu=on`: require GPU capability; fail before notebook readiness if unavailable.
- `gpu=off`: never request GPU devices.

GPU support is provided through the host Docker/NVIDIA Container Toolkit configuration. The launcher does not install Windows drivers, CUDA drivers, or NVIDIA Container Toolkit.

## Project Structure

```text
src/notebook_launcher/
├── __init__.py
├── app.py                 # FastAPI application and routes
├── config.py              # local paths, bind host/port, runtime options
├── models.py              # request/source/status/session models
├── source.py              # GitHub parsing, validation, ref resolution
├── repository.py          # clone/fetch/cache/checkout operations
├── environment.py         # repo2docker image identity/build/cache
├── runtime.py             # Docker/Jupyter session lifecycle and GPU flags
├── orchestration.py       # launch state machine
└── errors.py              # typed user-facing failures

tests/
├── unit/
│   ├── test_source.py
│   ├── test_environment.py
│   ├── test_runtime.py
│   └── test_orchestration.py
├── integration/
│   ├── test_public_repo_launch.py
│   └── test_cached_launch.py
└── fixtures/

specs/001-local-notebook-launcher/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── openapi.yaml
└── tasks.md

pyproject.toml
README.md
```

**Structure Decision**: Single Python service. External process/container interactions are isolated behind repository, environment, and runtime modules so a future BinderHub/JupyterHub runtime backend can replace local Docker orchestration without changing the HTTP launch contract.

## Implementation Phases

### Phase 0 — Host/runtime validation

Implement a diagnostic path that verifies Git, Docker, repo2docker, and optional NVIDIA container GPU capability independently. Do not mix host bootstrap logic into notebook launch orchestration.

### Phase 1 — Source and launch contract

Implement strict parsing for full GitHub notebook URLs and explicit repository/ref/path requests. Resolve refs against Git data, normalize notebook paths, and produce immutable `ResolvedSource` records.

### Phase 2 — Reproducible environment build

Acquire the repository at the resolved commit and use repo2docker to build/tag a local image. Cache by immutable environment identity. Build commands use structured argv and never a shell-composed command.

### Phase 3 — Jupyter session runtime

Launch the prepared image with a generated token and a dynamically allocated loopback port. Optionally request NVIDIA GPU devices. Detect notebook-server readiness before redirecting the user.

### Phase 4 — Status, cleanup, and tests

Add launch/session status, phase-specific errors, stop/cleanup, cached-launch verification, unsafe-input tests, and the WSL2 GPU smoke procedure.

### Phase 5 — Backend evolution (out of MVP)

Introduce a runtime backend interface only when a second backend is actually implemented. A BinderHub/JupyterHub backend can then preserve `/open` while replacing local image/session orchestration with Kubernetes-backed spawning.

## Complexity Tracking

No constitution violations are required for the MVP. BinderHub/Kubernetes is explicitly deferred because it increases operational complexity without being necessary to prove the first acceptance criteria.
