# Tasks: Local Notebook Launcher

**Input**: `specs/001-local-notebook-launcher/spec.md`, `plan.md`, and contracts under `contracts/`

**Tests**: Required for security-sensitive parsing/execution paths, MCP capability conformance, and the complete launch-to-agent-execution path under the project constitution.

## Phase 1: Project Setup

- [ ] T001 Create Python 3.12+ project metadata, package layout, CLI entry point, and dev dependency groups in `pyproject.toml`
- [ ] T002 Create `src/notebook_launcher/` package and initial FastAPI app in `src/notebook_launcher/app.py`
- [ ] T003 [P] Add centralized configuration for loopback host, launcher port, cache/state directory, command paths, and MCP backend selection in `src/notebook_launcher/config.py`
- [ ] T004 [P] Add typed launcher errors and stable error codes in `src/notebook_launcher/errors.py`
- [ ] T005 Create unit, contract, integration, and fixture test layouts under `tests/`
- [ ] T006 Document host prerequisites, repository trust model, agent-execution trust model, and local-only assumptions in `README.md`

## Phase 2: Foundational Models, Process Safety, and Diagnostics

- [ ] T007 Define `LaunchRequest`, `ResolvedSource`, `EnvironmentIdentity`, `Launch`, `NotebookSession`, `McpAttachment`, `McpRuntimeState`, and `AgentExecutionEvent` models in `src/notebook_launcher/models.py`
- [ ] T008 Define launch and MCP lifecycle states/transitions, including `mcp_preparing` and attachment invalidation
- [ ] T009 Implement structured subprocess helper that forbids shell interpolation and captures sanitized stdout/stderr
- [ ] T010 Implement host diagnostics for Git, Docker, repo2docker, and Docker daemon availability
- [ ] T011 [P] Implement NVIDIA container capability probe used by `gpu=auto|on` without making GPU a global requirement
- [ ] T012 [P] Implement MCP backend availability/capability probe without starting a notebook session
- [ ] T013 [P] Unit-test model validation, state transitions, subprocess safety, and diagnostic failure mapping

## Phase 3: User Story 1 — Launch a GitHub Notebook Locally (P1)

**Goal**: Resolve a public GitHub notebook request and identify the exact notebook/revision to run.

- [ ] T014 [US1] Implement full GitHub notebook URL parser for `github.com/<owner>/<repo>/blob/<ref>/<path>` in `src/notebook_launcher/source.py`
- [ ] T015 [US1] Implement explicit `repo`/`ref`/`path` request parsing and mutual-exclusion validation
- [ ] T016 [US1] Resolve Git refs to immutable commit SHAs, including slash-containing branch names by matching remote refs
- [ ] T017 [US1] Normalize and validate repository-relative notebook paths; require `.ipynb`
- [ ] T018 [US1] Implement repository cache acquisition/fetch/checkout at the resolved commit in `src/notebook_launcher/repository.py`
- [ ] T019 [P] [US1] Add positive parser/resolution tests for URL and explicit forms
- [ ] T020 [P] [US1] Add tests for branch names containing `/`, tags, direct commit SHAs, and ref/path ambiguity

## Phase 4: User Story 2 — Reproduce the Repository Environment (P1)

**Goal**: Build or reuse a containerized notebook environment from repository declarations.

- [ ] T021 [US2] Implement deterministic environment/image identity from immutable source and builder version in `src/notebook_launcher/environment.py`
- [ ] T022 [US2] Detect whether the corresponding local Docker image already exists
- [ ] T023 [US2] Invoke repo2docker with structured arguments to build a tagged local image on cache miss
- [ ] T024 [US2] Capture repo2docker build progress and map failures to the `building` phase without leaking secrets
- [ ] T025 [P] [US2] Unit-test image identity, cache hit, and builder failure mapping
- [ ] T026 [US2] Add Docker-backed integration fixture containing a dependency absent from the launcher host environment

## Phase 5: User Story 3 — Start the Local Jupyter/GPU Runtime (P1)

**Goal**: Start the requested notebook session with optional local NVIDIA GPU access.

- [ ] T027 [US3] Implement Docker runtime manager in `src/notebook_launcher/runtime.py` with loopback-only dynamic port publishing
- [ ] T028 [US3] Generate a unique Jupyter token per session and prevent it from appearing in logs/API status
- [ ] T029 [US3] Implement `gpu=off`, `gpu=auto`, and `gpu=on` device-request behavior
- [ ] T030 [US3] Implement Jupyter server and kernel readiness probing
- [ ] T031 [US3] Construct the `/lab/tree/<notebook>` ready URL and verify the requested notebook exists in the runtime workspace
- [ ] T032 [P] [US3] Unit-test Docker argv construction for CPU, auto-GPU, required-GPU, loopback binding, and forbidden mounts
- [ ] T033 [US3] Add documented WSL2/NVIDIA manual smoke test verifying `torch.cuda.is_available()` and device name

## Phase 6: User Story 4 — Agent-Operable Notebook via MCP (P1)

**Goal**: Attach an MCP-capable agent to the same launched Jupyter runtime and satisfy the required semantic capability profile.

- [ ] T034 [US4] Implement `src/notebook_launcher/mcp.py` with a semantic adapter interface matching `contracts/mcp-capabilities.md`
- [ ] T035 [US4] Spike/select the initial maintained MCP backend and document exact package/version plus capability mapping in `research.md` or implementation notes
- [ ] T036 [US4] Implement session-scoped MCP runtime metadata that resolves Jupyter URL/token/notebook path without exposing raw credentials to ordinary clients
- [ ] T037 [US4] If private MCP runtime state is persisted across processes, create owner-only state files and deletion/invalidation logic
- [ ] T038 [US4] Implement `notebook-launcher mcp <session-id>` stdio bridge entry point in `src/notebook_launcher/cli.py`
- [ ] T039 [US4] Map backend operations to required semantic capabilities: `notebook.read`, `cell.read`, `cell.execute`, `notebook.execute_all`, `kernel.execute_code`, `output.read`, `kernel.restart`
- [ ] T040 [US4] Detect and advertise optional remediation capabilities (`cell.insert`, `cell.update`, `cell.delete`, etc.) without claiming unsupported operations
- [ ] T041 [US4] Guarantee the MCP backend attaches to the existing Jupyter server/kernel lifecycle rather than creating an independent runtime
- [ ] T042 [US4] Implement bounded timeout/cancellation handling for long-running MCP execution
- [ ] T043 [US4] Normalize cell exceptions into structured execution results while keeping the MCP/Jupyter session usable when possible
- [ ] T044 [US4] Implement sanitized MCP attachment/execution audit events in `src/notebook_launcher/audit.py`
- [ ] T045 [P] [US4] Add contract tests for the semantic MCP capability profile independent of backend-specific tool names
- [ ] T046 [P] [US4] Add tests proving MCP descriptors/logs do not expose Jupyter tokens
- [ ] T047 [P] [US4] Add tests proving stopped/unknown sessions cannot be attached and Session A cannot resolve Session B private state accidentally
- [ ] T048 [US4] Add integration test where MCP reads and executes a deterministic cell in the same running Jupyter session
- [ ] T049 [US4] Add integration test for whole-notebook execution with a deliberate failing cell, followed by continued diagnostic execution or kernel restart
- [ ] T050 [US4] Add GPU consistency acceptance test: MCP-driven and browser/kernel-driven GPU probes report the same device availability

## Phase 7: User Story 5 — Fail Safely on Invalid or Unsafe Inputs (P1)

**Goal**: Reject unsafe requests and preserve the host/session boundary for both browser and agent execution.

- [ ] T051 [P] [US5] Add negative tests for unsupported hosts, malformed GitHub URLs, non-notebook paths, and missing fields
- [ ] T052 [P] [US5] Add path traversal tests covering `..`, encoded traversal, absolute paths, and normalization edge cases
- [ ] T053 [P] [US5] Add command-injection tests using shell metacharacters in repo/ref/path/session values and assert no shell execution path exists
- [ ] T054 [US5] Enforce GitHub HTTPS clone URL construction from validated owner/repository components rather than accepting arbitrary clone URLs
- [ ] T055 [US5] Ensure containers/MCP bridges receive no host credentials, SSH directories, Docker socket, or unrelated host mounts by default
- [ ] T056 [US5] Sanitize user-visible errors/logs and redact Jupyter/backend secrets

## Phase 8: User Story 6 — Observe and Clean Up Sessions (P2)

**Goal**: Expose lifecycle status and clean notebook/MCP resources predictably.

- [ ] T057 [US6] Implement launch orchestration/state machine in `src/notebook_launcher/orchestration.py`, including `mcp_preparing`
- [ ] T058 [US6] Implement `GET /open` to validate input, create an asynchronous launch, and redirect to `/launches/{id}`
- [ ] T059 [US6] Implement human-facing launch status page with automatic transition/redirect when ready
- [ ] T060 [US6] Implement `GET /api/launches/{id}` according to `contracts/openapi.yaml`
- [ ] T061 [US6] Implement `GET /api/sessions/{id}/mcp` returning only the non-secret attachment descriptor
- [ ] T062 [US6] Implement `DELETE /api/sessions/{id}` to stop the runtime, invalidate MCP state, and terminate launcher-owned bridge resources without deleting reusable images
- [ ] T063 [P] [US6] Implement `GET /health`
- [ ] T064 [US6] Add orchestration tests covering success, cache hit, source/build/startup/MCP preparation failure, stop, repeated stop, and attachment invalidation

## Phase 9: End-to-End Acceptance and Documentation

- [ ] T065 Add end-to-end integration test launching a small public GitHub notebook and verifying the requested notebook path is reachable
- [ ] T066 Add cached relaunch test demonstrating image build is skipped for the same immutable environment identity
- [ ] T067 Validate CPU launch against a representative tutorial notebook such as `kurtvalcorza/swin-segmentation-pipeline` where practical
- [ ] T068 Validate GPU launch on WSL2 with NVIDIA Container Toolkit and record tested host/runtime versions
- [ ] T069 Run an MCP-capable agent against the launched representative notebook and record proof that it reads and executes the same notebook/kernel
- [ ] T070 Run the agent through a controlled notebook failure and verify traceback/context, continued interaction, and restart behavior
- [ ] T071 Verify default listener/Jupyter ports are loopback-only and stdio MCP adds no network listener
- [ ] T072 Verify session cleanup invalidates MCP attachment/private state while leaving the reusable environment image intact
- [ ] T073 Update `README.md` with launch examples, GPU modes, MCP attachment/config examples, agent acceptance flow, cache/cleanup behavior, troubleshooting, and trust warning
- [ ] T074 Run spec/plan/tasks/contracts consistency review and resolve any requirement without an implementation task or acceptance test

## Dependencies

- Phase 1 precedes all implementation phases.
- Phase 2 is foundational for source/build/runtime/MCP work.
- User Story 1 source resolution precedes environment preparation.
- User Story 2 environment preparation precedes Jupyter runtime launch.
- User Story 3 runtime launch precedes MCP attachment because MCP must target an existing Jupyter session.
- User Story 4 MCP conformance is part of the P1 MVP, not a post-MVP integration.
- User Story 5 security tests should be implemented alongside the components they protect rather than postponed until the end.
- User Story 6 orchestration integrates source/environment/runtime/MCP components.
- End-to-end acceptance follows all P1 stories and orchestration.

## MVP Cut

The first accepted MVP is complete when the following path works end-to-end:

```text
GitHub .ipynb
  -> localhost /open
  -> reproducible container/Jupyter session
  -> optional local GPU
  -> MCP attachment
  -> agent reads and executes the notebook
  -> outputs/errors are observable in the same session
```

CPU acceptance requires T001-T066 plus T069-T074 where applicable. GPU acceptance additionally requires T068 and T050. MCP execution is **not** deferred from MVP.
