# Tasks: Local Notebook Launcher

**Input**: `specs/001-local-notebook-launcher/spec.md` and `plan.md`

**Tests**: Required for security-sensitive parsing/execution paths and the complete launch path under the project constitution.

## Phase 1: Project Setup

- [ ] T001 Create Python 3.12+ project metadata, package layout, CLI entry point, and dev dependency groups in `pyproject.toml`
- [ ] T002 Create `src/notebook_launcher/` package and initial FastAPI app in `src/notebook_launcher/app.py`
- [ ] T003 [P] Add centralized configuration for loopback host, launcher port, cache directory, and command paths in `src/notebook_launcher/config.py`
- [ ] T004 [P] Add typed launcher errors and stable error codes in `src/notebook_launcher/errors.py`
- [ ] T005 Create test layout and shared fixtures under `tests/`
- [ ] T006 Document host prerequisites and trust model in `README.md`, linking to the feature quickstart

## Phase 2: Foundational Models and Host Diagnostics

- [ ] T007 Define `LaunchRequest`, `ResolvedSource`, `EnvironmentIdentity`, `Launch`, and `NotebookSession` models in `src/notebook_launcher/models.py`
- [ ] T008 Define launch phase transitions and terminal states in `src/notebook_launcher/models.py`
- [ ] T009 Implement structured subprocess helper that forbids shell interpolation and captures sanitized stdout/stderr
- [ ] T010 Implement host diagnostics for Git, Docker, repo2docker, and Docker daemon availability
- [ ] T011 [P] Implement NVIDIA container capability probe used by `gpu=auto|on` without making GPU a global requirement
- [ ] T012 [P] Unit-test model validation, state transitions, command invocation, and diagnostic failure mapping

## Phase 3: User Story 1 — Launch a GitHub Notebook Locally (P1)

**Goal**: Resolve a public GitHub notebook request and open that exact notebook in a local Jupyter session.

- [ ] T013 [US1] Implement full GitHub notebook URL parser for `github.com/<owner>/<repo>/blob/<ref>/<path>` in `src/notebook_launcher/source.py`
- [ ] T014 [US1] Implement explicit `repo`/`ref`/`path` request parsing and mutual-exclusion validation
- [ ] T015 [US1] Resolve Git refs to immutable commit SHAs, including slash-containing branch names by matching remote refs
- [ ] T016 [US1] Normalize and validate repository-relative notebook paths; require `.ipynb`
- [ ] T017 [US1] Implement repository cache acquisition/fetch/checkout at the resolved commit in `src/notebook_launcher/repository.py`
- [ ] T018 [P] [US1] Add positive parser/resolution tests for URL and explicit forms in `tests/unit/test_source.py`
- [ ] T019 [P] [US1] Add resolution tests for branch names containing `/`, tags, and direct commit SHAs

## Phase 4: User Story 2 — Reproduce the Repository Environment (P1)

**Goal**: Build or reuse a containerized notebook environment from repository declarations.

- [ ] T020 [US2] Implement deterministic environment/image identity from immutable source and builder version in `src/notebook_launcher/environment.py`
- [ ] T021 [US2] Detect whether the corresponding local Docker image already exists
- [ ] T022 [US2] Invoke repo2docker with structured arguments to build a tagged local image on cache miss
- [ ] T023 [US2] Capture repo2docker build progress and map failures to the `building` phase without leaking secrets
- [ ] T024 [P] [US2] Unit-test image identity and cache-hit behavior
- [ ] T025 [US2] Add Docker-backed integration fixture containing a dependency absent from the launcher host environment

## Phase 5: User Story 3 — Use the Local NVIDIA GPU (P1)

**Goal**: Start notebook sessions with optional local NVIDIA GPU access.

- [ ] T026 [US3] Implement Docker runtime manager in `src/notebook_launcher/runtime.py` with loopback-only dynamic port publishing
- [ ] T027 [US3] Generate a unique Jupyter token per session and prevent it from appearing in logs
- [ ] T028 [US3] Implement `gpu=off`, `gpu=auto`, and `gpu=on` device-request behavior
- [ ] T029 [US3] Implement Jupyter readiness probing and construction of the `/lab/tree/<notebook>` ready URL
- [ ] T030 [P] [US3] Unit-test Docker argv construction for CPU, auto-GPU, and required-GPU modes
- [ ] T031 [US3] Add documented WSL2/NVIDIA manual smoke test verifying `torch.cuda.is_available()` and device name

## Phase 6: User Story 4 — Fail Safely on Invalid or Unsafe Inputs (P1)

**Goal**: Reject unsafe requests before arbitrary host execution.

- [ ] T032 [P] [US4] Add negative tests for unsupported hosts, malformed GitHub URLs, non-notebook paths, and missing fields
- [ ] T033 [P] [US4] Add path traversal tests covering `..`, encoded traversal, absolute paths, and path normalization edge cases
- [ ] T034 [P] [US4] Add command-injection tests using shell metacharacters in repo/ref/path values and assert no shell execution path exists
- [ ] T035 [US4] Enforce GitHub HTTPS clone URL construction from validated owner/repository components rather than accepting arbitrary clone URLs
- [ ] T036 [US4] Ensure containers receive no host credentials, SSH directories, Docker socket, or unrelated host mounts by default
- [ ] T037 [US4] Sanitize user-visible errors/logs and redact Jupyter tokens

## Phase 7: User Story 5 — Observe and Clean Up Sessions (P2)

**Goal**: Expose lifecycle status and clean session resources predictably.

- [ ] T038 [US5] Implement launch orchestration/state machine in `src/notebook_launcher/orchestration.py`
- [ ] T039 [US5] Implement `GET /open` to validate input, create an asynchronous launch, and redirect to `/launches/{id}`
- [ ] T040 [US5] Implement human-facing launch status page with automatic transition/redirect when ready
- [ ] T041 [US5] Implement `GET /api/launches/{id}` according to `contracts/openapi.yaml`
- [ ] T042 [US5] Implement `DELETE /api/sessions/{id}` and runtime cleanup without deleting reusable images
- [ ] T043 [P] [US5] Implement `GET /health`
- [ ] T044 [US5] Add orchestration tests covering success, cache hit, source failure, build failure, startup failure, stop, and repeated stop

## Phase 8: End-to-End Acceptance and Documentation

- [ ] T045 Add end-to-end integration test launching a small public GitHub notebook and verifying the requested notebook path is reachable
- [ ] T046 Add cached relaunch integration test demonstrating the image-build phase is skipped for the same immutable environment identity
- [ ] T047 Validate the launcher against `kurtvalcorza/swin-segmentation-pipeline` tutorial notebook on CPU where practical
- [ ] T048 Validate the GPU acceptance path on WSL2 with NVIDIA Container Toolkit and record the tested host/runtime versions
- [ ] T049 Verify default listener is loopback-only and Jupyter-published ports are also loopback-only
- [ ] T050 Update `README.md` with full-URL and explicit-field launch examples, GPU modes, cache/cleanup behavior, troubleshooting, and security warning
- [ ] T051 Run the spec/plan/tasks consistency review and resolve any requirement without an implementation task or acceptance test

## Dependencies

- Phase 1 precedes all implementation phases.
- Phase 2 is foundational for source/build/runtime work.
- User Story 1 source resolution is required before User Story 2 environment preparation.
- User Story 2 environment preparation is required before User Story 3 runtime launch.
- User Story 4 security tests should be implemented alongside User Story 1 rather than postponed until the end.
- User Story 5 orchestration integrates the completed source/environment/runtime components.
- End-to-end acceptance follows the P1 stories and orchestration.

## MVP Cut

The first demonstrable MVP is complete when T001–T046 pass for CPU operation and a supported GitHub notebook launches through `/open`. T048 is the additional acceptance gate proving the intended local-GPU use case on the configured WSL2 host.
