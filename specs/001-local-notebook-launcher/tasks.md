# Tasks: Local Notebook Launcher

**Input**: Design documents from `specs/001-local-notebook-launcher/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required by the project constitution. Test tasks are included before implementation in each story where the constitution or feature acceptance criteria require automated/contract/integration coverage.

**Organization**: Tasks are grouped by the seven user stories in `spec.md`, with shared setup/foundational work first and cross-cutting acceptance work last.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it changes different files and does not depend on another incomplete task in the same phase.
- **[Story]**: Maps directly to `US1`–`US7` from `spec.md`.
- Every task includes an exact repository path.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Initialize the Python package, test harness, local configuration, and developer-facing entry points.

- [ ] T001 Create `pyproject.toml` for Python 3.12+ with FastAPI, Uvicorn, Pydantic, pytest, pytest-asyncio, httpx, repo2docker integration dependencies, and console script `notebook-launcher = notebook_launcher.cli:main`
- [ ] T002 Create package bootstrap and version metadata in `src/notebook_launcher/__init__.py`
- [ ] T003 [P] Create test bootstrap, temporary launcher-root fixtures, temporary SQLite fixtures, and marker definitions in `tests/conftest.py`
- [ ] T004 [P] Implement launcher-root/state/workspace/runtime path settings, loopback defaults, resource-limit defaults, and backend configuration in `src/notebook_launcher/config.py`
- [ ] T005 [P] Create the FastAPI application factory and startup/shutdown lifecycle skeleton in `src/notebook_launcher/app.py`
- [ ] T006 [P] Create CLI command skeletons for `serve`, `mcp`, `trust`, and `workspace` groups in `src/notebook_launcher/cli.py`
- [ ] T007 [P] Create initial host-prerequisite, trust-boundary, and sandbox-limitations documentation in `README.md`

**Checkpoint**: Package imports, test discovery, CLI help, and app startup work without implementing feature behavior.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the durable local control-plane primitives that every user story depends on.

**⚠️ CRITICAL**: Complete this phase before user-story implementation.

### Tests

- [ ] T008 [P] Add unit tests for typed public error identifiers, safe-message serialization, and secret redaction in `tests/unit/test_errors.py`
- [ ] T009 [P] Add SQLite schema/invariant tests proving active exact-commit trust uniqueness, active repository-trust uniqueness, at-most-one active session per workspace, at-most-one unreleased writable lease per session, and transactional rollback in `tests/unit/test_state.py`
- [ ] T010 [P] Add model validation tests for launch-source mutual exclusivity, `gpu=auto|on|off`, `agent_mode=write|readonly`, normalized relative paths, and lifecycle enums in `tests/unit/test_models.py`
- [ ] T011 [P] Add audit tests proving bounded metadata excludes raw notebook contents, Jupyter tokens, and credentials in `tests/unit/test_audit.py`

### Implementation

- [ ] T012 [P] Implement stable typed launcher errors including `conflict`, `permission_denied`, `writable_agent_busy`, `workspace_active`, `unknown_session`, and secret-safe rendering in `src/notebook_launcher/errors.py`
- [ ] T013 [P] Implement Pydantic/domain models for `LaunchRequest`, `ResolvedSource`, `Launch`, `Workspace`, `NotebookSession`, `McpAttachment`, `NotebookCopyRequest`, `UserDataGrant`, and execution outcomes in `src/notebook_launcher/models.py`; preserve constraints `gpu ∈ {auto,on,off}`, `agent_mode ∈ {write,readonly}`, and remote-source fields mutually exclusive with `workspace_id`
- [ ] T014 Implement SQLite schema/bootstrap and transactional repository helpers in `src/notebook_launcher/state.py`, including partial/transactional uniqueness for one active session per workspace and one unreleased writable lease per session and excluding raw notebook/Jupyter-secret storage
- [ ] T015 [P] Implement sanitized bounded audit-event persistence with operation, target identifier, timing, outcome, and error category only in `src/notebook_launcher/audit.py`
- [ ] T016 [P] Implement opaque notebook/file version token primitives and compare-before-write helpers in `src/notebook_launcher/versions.py`
- [ ] T017 [P] Implement transactional writable-agent lease acquire/release/invalidate/reconcile primitives in `src/notebook_launcher/leases.py`
- [ ] T018 [P] Implement structured external-command execution helpers using argv APIs only, cancellation, bounded stdout/stderr capture, and redaction in `src/notebook_launcher/orchestration.py`
- [ ] T019 Implement base launch-state persistence/transitions for `received`, `resolving`, `awaiting_trust`, `acquiring`, `workspace_preparing`, `building`, `cache_hit`, `starting`, `mcp_preparing`, `ready`, `failed`, `stopping`, and `stopped` in `src/notebook_launcher/state.py`
- [ ] T020 [P] Implement host diagnostics for Git, Docker Engine, repo2docker, writable launcher state directory, and configured MCP backend availability in `src/notebook_launcher/environment.py`
- [ ] T021 Run and fix foundational unit suite in `tests/unit/test_errors.py`, `tests/unit/test_state.py`, `tests/unit/test_models.py`, and `tests/unit/test_audit.py`

**Checkpoint**: Atomic local metadata, versions, leases, errors, audit, diagnostics, and lifecycle primitives are ready for story work.

---

## Phase 3: User Story 1 - Open a Trusted GitHub Notebook Locally (Priority: P1) 🎯 MVP Entry

**Goal**: Accept a supported public GitHub notebook reference, resolve immutable provenance, obtain explicit trust when needed, prepare an isolated environment, and open the exact notebook locally.

**Independent Test**: Submit a known public GitHub `.ipynb`, choose exact-commit or repository trust, and verify the exact working notebook opens in a loopback Jupyter session; declining trust executes no repository-supplied setup/code.

### Tests for User Story 1

- [ ] T022 [P] [US1] Add GitHub URL/repo-ref-path parsing, slash-containing ref resolution, unsupported-host, non-`.ipynb`, and traversal tests in `tests/unit/test_source.py`
- [ ] T023 [P] [US1] Add trust-policy tests for `scope=exact_commit|repository`, exact-commit requiring `commit_sha`, repository scope using null commit SHA, revocation, expired/used nonce rejection, and remote inability to pre-authorize trust in `tests/unit/test_trust.py`
- [ ] T024 [P] [US1] Add `/open`, launch-status, trust-challenge, trust-list, and trust-revoke OpenAPI contract tests in `tests/contract/test_launch_trust_api.py`
- [ ] T025 [P] [US1] Add integration test proving no repository-supplied build/runtime command executes before trust and deny/cancel results in zero repository execution in `tests/integration/test_trust_gate.py`
- [ ] T026 [P] [US1] Add Docker-backed source-to-Jupyter integration test that opens the exact requested working notebook and records immutable commit provenance in `tests/integration/test_open_notebook.py`
- [ ] T027 [P] [US1] Add environment-cache integration test proving an unchanged immutable source/environment reuses its prepared image without reusing a prior mutable workspace in `tests/integration/test_environment_cache.py`

### Implementation for User Story 1

- [ ] T028 [P] [US1] Implement strict `github.com` notebook parsing, explicit `owner/repository + ref + path` parsing, path normalization, non-notebook rejection, and validated HTTPS clone URL construction in `src/notebook_launcher/source.py`
- [ ] T029 [US1] Implement remote-ref resolution to immutable commit SHA, including slash-containing branch names resolved against remote refs rather than naïve `/blob/` splitting, in `src/notebook_launcher/source.py`
- [ ] T030 [P] [US1] Implement active trust-record lookup/create/revoke in `src/notebook_launcher/trust.py` with scopes exactly `exact_commit` and `repository`; exact-commit grants authorize one SHA and repository grants authorize future SHAs only for the same canonical repository
- [ ] T031 [US1] Implement short-lived one-time `TrustChallenge` nonce creation/verification in `src/notebook_launcher/trust.py`, storing only the nonce hash and ensuring `/open` query parameters cannot supply authorization
- [ ] T032 [US1] Implement immutable source acquisition/cache at the resolved SHA in `src/notebook_launcher/repository.py` using structured Git argv and validating the requested notebook exists after acquisition
- [ ] T033 [US1] Implement fresh workspace materialization for a newly trusted source in `src/notebook_launcher/workspace.py`, creating separate persistent `work/` and `outputs/` while retaining immutable source provenance
- [ ] T034 [P] [US1] Implement deterministic environment identity from commit SHA, repo2docker strategy/version, and environment-defining inputs in `src/notebook_launcher/environment.py`
- [ ] T035 [US1] Implement prepared-image cache lookup and repo2docker build on cache miss using structured argv, redacted progress, and actionable build failures in `src/notebook_launcher/environment.py`
- [ ] T036 [P] [US1] Implement baseline standard-sandbox runtime command construction with explicit workspace/source/output mounts and loopback-only Jupyter port publication in `src/notebook_launcher/sandbox.py`
- [ ] T037 [US1] Implement authenticated Jupyter/JupyterLab startup, owner-only runtime credential persistence, readiness probing, and requested working-notebook URL generation in `src/notebook_launcher/runtime.py`
- [ ] T038 [US1] Implement remote-source launch orchestration from `received` through `ready`, including trust pause/resume and failure-phase reporting, in `src/notebook_launcher/orchestration.py`
- [ ] T039 [US1] Implement `/open`, local trust-confirmation page, `GET /api/launches/{launch_id}`, `POST /api/launches/{launch_id}/trust`, `GET /api/trust`, and `DELETE /api/trust/{trust_id}` in `src/notebook_launcher/app.py`
- [ ] T040 [US1] Implement `notebook-launcher serve`, `trust list`, and `trust revoke <trust-id>` behavior from `contracts/cli.md` in `src/notebook_launcher/cli.py`
- [ ] T041 [US1] Ensure fresh mutable work trees are not dependent on a writable GitHub remote and no GitHub write credentials are injected during source/workspace creation in `src/notebook_launcher/workspace.py`
- [ ] T042 [US1] Run and fix US1 unit/contract/integration tests in `tests/unit/test_source.py`, `tests/unit/test_trust.py`, `tests/contract/test_launch_trust_api.py`, `tests/integration/test_trust_gate.py`, `tests/integration/test_open_notebook.py`, and `tests/integration/test_environment_cache.py`
- [ ] T043 [US1] Validate the US1 independent flow from `specs/001-local-notebook-launcher/quickstart.md` and record any command corrections in `specs/001-local-notebook-launcher/quickstart.md`

**Checkpoint**: A trusted public GitHub notebook can be opened locally at an immutable source revision with no manual clone/Jupyter startup.

---

## Phase 4: User Story 2 - Keep a Persistent, Editable Local Copy (Priority: P1)

**Goal**: Preserve notebook edits, saved outputs, workspace files, and generated artifacts across runtime shutdown/reopen, and support local Save a Copy without GitHub mutation.

**Independent Test**: Edit/run a notebook, create an artifact, stop the runtime through the service layer, reopen the workspace, and verify the edit/output/artifact; then Save a Copy and verify both notebooks persist while source provenance remains unchanged.

### Tests for User Story 2

- [ ] T044 [P] [US2] Add workspace create/reopen persistence tests proving a fresh remote launch creates a new workspace and reopen never resets it from GitHub in `tests/integration/test_workspace_persistence.py`
- [ ] T045 [P] [US2] Add Save-a-Copy tests for `.ipynb` destination validation, traversal rejection, `overwrite=false` preservation, optional output preservation, and source snapshot immutability in `tests/unit/test_workspace_copy.py`
- [ ] T046 [P] [US2] Add workspace metadata and Save-a-Copy API contract tests in `tests/contract/test_workspace_api.py`
- [ ] T047 [P] [US2] Add persistence test proving arbitrary non-notebook files and `/outputs` artifacts survive runtime teardown/reopen in `tests/integration/test_workspace_files.py`

### Implementation for User Story 2

- [ ] T048 [P] [US2] Implement persisted `Workspace` metadata/provenance fields and lookup helpers in `src/notebook_launcher/state.py`, including `root_path`, `work_path`, `outputs_path`, `active_notebook_path`, timestamps, and optional `user_data_grant_id`
- [ ] T049 [US2] Implement reopen-by-workspace-ID using the existing mutable `work/` tree and recorded environment provenance without source reset in `src/notebook_launcher/workspace.py`
- [ ] T050 [US2] Implement workspace/output durability across runtime removal and explicit workspace lifecycle boundaries in `src/notebook_launcher/workspace.py`
- [ ] T051 [US2] Implement Save a Copy to validated workspace-relative destinations with `.ipynb` requirement, `overwrite=false` default, and optional output stripping/preservation in `src/notebook_launcher/workspace.py`
- [ ] T052 [US2] Implement low-level runtime stop/recreate hooks that preserve workspace/environment cache for persistence tests in `src/notebook_launcher/runtime.py`
- [ ] T053 [US2] Implement `GET /api/workspaces/{workspace_id}` and `POST /api/workspaces/{workspace_id}/notebooks/copy` according to `contracts/openapi.yaml` in `src/notebook_launcher/app.py`
- [ ] T054 [US2] Ensure source snapshots remain read-only/unchanged across browser saves, Save a Copy, and reopen operations in `src/notebook_launcher/repository.py`
- [ ] T055 [US2] Run and fix US2 tests in `tests/integration/test_workspace_persistence.py`, `tests/unit/test_workspace_copy.py`, `tests/contract/test_workspace_api.py`, and `tests/integration/test_workspace_files.py`
- [ ] T056 [US2] Validate persistence and Save-a-Copy scenarios in `specs/001-local-notebook-launcher/quickstart.md` against the implemented behavior

**Checkpoint**: Local work behaves like a persistent Colab-style copy while GitHub remains immutable input.

---

## Phase 5: User Story 3 - Let an Agent Operate the Same Notebook (Priority: P1)

**Goal**: Attach an MCP-capable agent to the same Jupyter document/kernel, support writable repair workflows and strict readonly inspection, reject stale edits, permit full-workspace writable file operations, and serialize writable agents with one lease.

**Independent Test**: Attach a writable agent, read/edit/execute/fail/repair the active notebook, modify representative workspace files, reject a deliberately stale edit, reject a second writable agent, and verify the browser sees the same document/kernel; then attach readonly and prove all execution/mutation attempts fail.

### Tests for User Story 3

- [ ] T057 [P] [US3] Add semantic MCP contract tests for required writable/readonly capability sets and secret-free attachment descriptors in `tests/contract/test_mcp_contract.py`
- [ ] T058 [P] [US3] Add writable-lease tests for atomic acquisition, second-writer `writable_agent_busy`, release on disconnect, invalidation on session stop, and stale-lease reconciliation in `tests/unit/test_leases.py`
- [ ] T059 [P] [US3] Add optimistic notebook/file version tests proving existing-object mutations require expected versions and stale mutations return `conflict` without changing authoritative state in `tests/unit/test_versions.py`
- [ ] T060 [P] [US3] Add same-kernel writable MCP integration test for notebook read/insert/update/delete, cell execution, ordered execute-all, diagnostic code, output retrieval, deliberate failure/repair, and kernel restart in `tests/integration/test_mcp_writable.py`
- [ ] T061 [P] [US3] Add readonly integration test proving notebook/cell/output inspection works while execution, kernel restart, notebook mutation, workspace-file mutation, and write escalation are denied in `tests/integration/test_mcp_readonly.py`
- [ ] T062 [P] [US3] Add browser↔agent concurrent-edit test proving stale agent/browser mutations are rejected/refreshable and no last-write-wins silent loss occurs in `tests/integration/test_mcp_conflicts.py`
- [ ] T063 [P] [US3] Add persistent workspace-file MCP tests for read/list/create/write/move/delete with `file_version` preconditions in `tests/integration/test_mcp_workspace_files.py`
- [ ] T064 [P] [US3] Add cross-session private-state isolation and Jupyter-token redaction tests in `tests/integration/test_mcp_isolation.py`

### Implementation for User Story 3

- [ ] T065 [P] [US3] Define the implementation-neutral MCP policy adapter and semantic capability registry in `src/notebook_launcher/mcp.py`
- [ ] T066 [US3] Implement the initial Datalayer `jupyter-mcp-server` 2.x adapter against the existing launcher-created Jupyter server in `src/notebook_launcher/mcp.py`, without exposing backend-specific tool names as launcher contract
- [ ] T067 [US3] Implement `notebook-launcher mcp <session-id> [--mode write|readonly] [--client-label]` stdio bridge with internal Jupyter credential resolution in `src/notebook_launcher/cli.py`
- [ ] T068 [US3] Enforce writable-lease acquisition before exposing writable MCP operations and explicit `writable_agent_busy` failure for a second writer in `src/notebook_launcher/mcp.py`
- [ ] T069 [US3] Integrate lease heartbeat/disconnect/crash/session-stop reconciliation with `WritableAgentLease` state in `src/notebook_launcher/leases.py`
- [ ] T070 [US3] Implement `notebook.read`, `cell.read`, `cell.insert`, `cell.update`, and `cell.delete` with opaque `document_version`/`expected_document_version` conflict preconditions in `src/notebook_launcher/mcp.py`
- [ ] T071 [US3] Implement `cell.execute`, `notebook.execute_all`, `kernel.execute_code`, `output.read`, and `kernel.restart` against the active Jupyter kernel in `src/notebook_launcher/mcp.py`
- [ ] T072 [US3] Implement execute-all ordering, default stop-on-error, cell-scoped failure results, and explicit exception/timeout/cancel/kernel-death/transport outcome distinctions in `src/notebook_launcher/mcp.py`
- [ ] T073 [US3] Implement full-workspace `workspace.file.read/list/write/move/delete` semantics with path containment and opaque `file_version` preconditions in `src/notebook_launcher/mcp.py`
- [ ] T074 [US3] Enforce readonly inspection-only policy at the launcher/MCP boundary, including rejection of all code execution, kernel restart, notebook mutation, workspace-file mutation, and read-only-to-write escalation in `src/notebook_launcher/mcp.py`
- [ ] T075 [US3] Bind browser and MCP to the same workspace, shared Jupyter document, kernel lifecycle, sandbox, GPU policy, and resource limits in `src/notebook_launcher/runtime.py`
- [ ] T076 [US3] Implement non-secret `GET /api/sessions/{session_id}/mcp` descriptor including `write_lease_available` without acquiring the lease in `src/notebook_launcher/app.py`
- [ ] T077 [US3] Emit sanitized attachment/execution/conflict/lease audit events without full cell source/output in `src/notebook_launcher/audit.py`
- [ ] T078 [US3] Run the Datalayer/Jupyter-collaboration conformance suite and pin the exact passing backend/collaboration versions in `pyproject.toml`; reject candidate versions that lose bidirectional edits in `tests/integration/test_mcp_conflicts.py`
- [ ] T079 [US3] Run and fix all US3 contract/integration tests in `tests/contract/test_mcp_contract.py`, `tests/unit/test_leases.py`, `tests/unit/test_versions.py`, `tests/integration/test_mcp_writable.py`, `tests/integration/test_mcp_readonly.py`, `tests/integration/test_mcp_conflicts.py`, `tests/integration/test_mcp_workspace_files.py`, and `tests/integration/test_mcp_isolation.py`

**Checkpoint**: One writable agent can safely operate and repair the same notebook/kernel the user sees; readonly remains inspection-only and stale writes cannot silently overwrite newer work.

---

## Phase 6: User Story 4 - Use Local GPU Compute When Available (Priority: P1)

**Goal**: Support `gpu=auto|on|off` with explicit failure/fallback semantics and identical GPU visibility for browser and MCP execution.

**Independent Test**: On a configured WSL2/Linux NVIDIA host, launch each GPU mode and verify `on` requires GPU, `off` exposes none, `auto` uses GPU when available and CPU otherwise, and browser/MCP device probes agree.

### Tests for User Story 4

- [ ] T080 [P] [US4] Add unit tests for GPU capability detection and `auto|on|off` decision semantics in `tests/unit/test_gpu_policy.py`
- [ ] T081 [P] [US4] Add sandbox-command tests proving GPU device flags are absent for `off`, required for `on`, and conditional for `auto` in `tests/unit/test_sandbox_gpu.py`
- [ ] T082 [P] [US4] Add browser/MCP same-device integration test guarded by GPU availability in `tests/integration/test_gpu_consistency.py`

### Implementation for User Story 4

- [ ] T083 [P] [US4] Implement NVIDIA host/container capability probe without attempting driver/toolkit installation in `src/notebook_launcher/runtime.py`
- [ ] T084 [US4] Implement effective `gpu=auto|on|off` allocation and clear required-GPU failure before readiness in `src/notebook_launcher/runtime.py`
- [ ] T085 [US4] Add GPU device exposure to sandbox runtime argv only when effective policy enables it in `src/notebook_launcher/sandbox.py`
- [ ] T086 [US4] Expose effective `gpu_enabled` consistently in launch/session status and MCP-visible runtime state in `src/notebook_launcher/models.py`
- [ ] T087 [US4] Run and fix GPU unit/integration tests in `tests/unit/test_gpu_policy.py`, `tests/unit/test_sandbox_gpu.py`, and `tests/integration/test_gpu_consistency.py`
- [ ] T088 [US4] Execute and document the WSL2/Linux NVIDIA smoke procedure and expected browser/MCP output in `specs/001-local-notebook-launcher/quickstart.md`

**Checkpoint**: GPU use is explicit, observable, and identical across interactive and agent execution.

---

## Phase 7: User Story 5 - Keep Local Execution Contained (Priority: P1)

**Goal**: Enforce the standard sandbox as defense-in-depth for user-trusted repositories, prevent unrelated host/credential access, keep inbound services loopback-only, and expose no GitHub mutation capability.

**Independent Test**: Launch a trusted notebook and verify only allowed mounts/resources are visible; command-injection/traversal/cross-session/prohibited-mount attempts fail; no Docker socket/host credentials/GitHub write surface exists; outbound package/model access still works.

### Tests for User Story 5

- [ ] T089 [P] [US5] Add sandbox argv assertions for non-root where supported, no privileged mode, `no-new-privileges`, dropped unnecessary capabilities, seccomp/equivalent filtering, and CPU/memory/PID limits in `tests/unit/test_sandbox_policy.py`
- [ ] T090 [P] [US5] Add prohibited-mount tests for Docker socket, SSH keys, cloud/GitHub credentials, whole-home mounts, and unrelated host paths in `tests/unit/test_sandbox_mounts.py`
- [ ] T091 [P] [US5] Add command-injection tests across source/ref/path/session/copy/permission fields proving structured argv treatment in `tests/unit/test_injection.py`
- [ ] T092 [P] [US5] Add integration test proving Jupyter/launcher bind only to loopback, no extra inbound MCP listener exists under stdio, and outbound package/model/data access remains available in `tests/integration/test_network_policy.py`
- [ ] T093 [P] [US5] Add no-GitHub-mutation surface test proving launcher/MCP expose no commit, push, branch-create, or credential-backed remote-write path in `tests/integration/test_no_github_mutation.py`
- [ ] T094 [P] [US5] Add cross-session attachment/private-state access denial tests in `tests/integration/test_session_isolation.py`

### Implementation for User Story 5

- [ ] T095 [US5] Harden standard sandbox defaults for non-root execution where compatible, `no-new-privileges`, capability dropping, seccomp/equivalent filtering, and configurable CPU/memory/PID limits in `src/notebook_launcher/sandbox.py`
- [ ] T096 [US5] Implement explicit mount allowlist and forbidden-path validation preventing Docker socket, credential directories, whole-home, and unrelated host mounts in `src/notebook_launcher/sandbox.py`
- [ ] T097 [US5] Enforce loopback-only launcher/Jupyter publication and default outbound-enabled/no-general-inbound sandbox network policy in `src/notebook_launcher/runtime.py`
- [ ] T098 [US5] Ensure all Git, repo2docker, Docker, and Jupyter subprocess calls use structured argv without user-controlled shell interpolation in `src/notebook_launcher/source.py`, `src/notebook_launcher/repository.py`, `src/notebook_launcher/environment.py`, and `src/notebook_launcher/runtime.py`
- [ ] T099 [US5] Ensure host environment secrets, SSH/GitHub/cloud credentials, Docker socket, and unrelated host environment variables are not inherited into runtime/MCP by default in `src/notebook_launcher/runtime.py`
- [ ] T100 [US5] Ensure no launcher or MCP route/capability implements commit/push/branch/GitHub mutation and no GitHub write credential is requested in `src/notebook_launcher/app.py`, `src/notebook_launcher/cli.py`, and `src/notebook_launcher/mcp.py`
- [ ] T101 [US5] Run and fix US5 security tests in `tests/unit/test_sandbox_policy.py`, `tests/unit/test_sandbox_mounts.py`, `tests/unit/test_injection.py`, `tests/integration/test_network_policy.py`, `tests/integration/test_no_github_mutation.py`, and `tests/integration/test_session_isolation.py`
- [ ] T102 [US5] Update sandbox guarantees/limitations and trusted-repository warning to match tested behavior in `README.md`

**Checkpoint**: The runtime has a tested, minimal local trust boundary without claiming containment of arbitrary hostile code.

---

## Phase 8: User Story 6 - Attach Explicit Local Data Storage (Priority: P2)

**Goal**: Let the local user explicitly grant one host directory to a workspace as `ro` or `rw`, with no default mount and no way for a remote GitHub launch to choose the host path.

**Independent Test**: Grant a temporary local directory read-only and read-write, verify it appears at `/mnt/user-data` with the requested mode, verify unrelated host paths remain inaccessible, revoke it, and verify a GitHub URL cannot create/change the grant.

### Tests for User Story 6

- [ ] T103 [P] [US6] Add `UserDataGrant` state tests for one selected host directory, stable `/mnt/user-data`, `mode ∈ {ro,rw}`, revocation, and missing-path behavior in `tests/unit/test_user_data_grant.py`
- [ ] T104 [P] [US6] Add CLI contract tests for `workspace mount <workspace-id> <host-path> --mode ro|rw` and `workspace unmount <workspace-id>` including whole-home rejection and active-workspace handling in `tests/contract/test_workspace_mount_cli.py`
- [ ] T105 [P] [US6] Add Docker-backed mount isolation test for no default mount, read-only enforcement, read-write persistence, and unrelated host-path denial in `tests/integration/test_user_data_mount.py`

### Implementation for User Story 6

- [ ] T106 [US6] Implement local `UserDataGrant` persistence/revocation in `src/notebook_launcher/state.py`, with `host_path` accepted only from local management flow, stable container path `/mnt/user-data`, and mode exactly `ro|rw`
- [ ] T107 [US6] Implement `workspace mount` and `workspace unmount` CLI commands with existing-directory validation, forbidden/whole-home checks, and explicit restart requirement for active sessions in `src/notebook_launcher/cli.py`
- [ ] T108 [US6] Apply the active user-data grant as one explicit `ro`/`rw` sandbox mount and report missing host directories without substituting other paths in `src/notebook_launcher/sandbox.py`
- [ ] T109 [US6] Extend Save a Copy to `destination_scope=user_data` only when an active writable user-data grant exists and destination remains beneath that grant in `src/notebook_launcher/workspace.py`
- [ ] T110 [US6] Run and fix US6 tests in `tests/unit/test_user_data_grant.py`, `tests/contract/test_workspace_mount_cli.py`, and `tests/integration/test_user_data_mount.py`
- [ ] T111 [US6] Update local-storage mount and Save-a-Copy examples in `specs/001-local-notebook-launcher/quickstart.md`

**Checkpoint**: Explicit local data behaves like an opt-in external drive mount without widening the default host boundary.

---

## Phase 9: User Story 7 - Stop and Reopen Work Predictably (Priority: P2)

**Goal**: Expose clear launch/session status, stop disposable runtime resources without deleting work, reopen existing work, reconcile stale runtime ownership, and enforce one active notebook session per workspace.

**Independent Test**: Start a workspace, observe phases, issue a second start and confirm no second runtime is created, stop the session, verify MCP credentials/leases are invalidated while files/cache persist, restart the launcher if needed, and reopen the same workspace successfully.

### Tests for User Story 7

- [ ] T112 [P] [US7] Add launch/session status and stop endpoint contract tests including `existing_session_id`, `404`, `409`, and `410` behavior in `tests/contract/test_lifecycle_api.py`
- [ ] T113 [P] [US7] Add concurrent start test proving repeated requests cannot create more than one active notebook session for one workspace in `tests/integration/test_single_active_session.py`
- [ ] T114 [P] [US7] Add launcher-restart reconciliation test that clears stale recorded ownership only after the runtime is confirmed dead and preserves live ownership otherwise in `tests/integration/test_session_reconciliation.py`
- [ ] T115 [P] [US7] Add stop/reopen test proving runtime credentials and writable lease are invalidated while workspace files and reusable environment cache remain in `tests/integration/test_stop_reopen.py`

### Implementation for User Story 7

- [ ] T116 [US7] Enforce transactional one-active-session-per-workspace acquisition before runtime creation and return/reuse the existing active session when appropriate in `src/notebook_launcher/state.py`
- [ ] T117 [US7] Implement launcher-start reconciliation between SQLite session ownership and actual runtime/container liveness in `src/notebook_launcher/orchestration.py`
- [ ] T118 [US7] Implement idempotent session stop that terminates runtime, invalidates Jupyter/MCP private state and writable lease, records terminal lifecycle, and preserves workspace/cache in `src/notebook_launcher/runtime.py`
- [ ] T119 [US7] Implement reopen orchestration that starts a replacement runtime only when no live session owns the workspace in `src/notebook_launcher/orchestration.py`
- [ ] T120 [US7] Implement launch/status/existing-session local pages plus `DELETE /api/sessions/{session_id}` and `/health` in `src/notebook_launcher/app.py`
- [ ] T121 [US7] Include `active_session_id` in workspace metadata and `existing_session_id` in launch status without exposing private runtime credentials in `src/notebook_launcher/models.py`
- [ ] T122 [US7] Add clear CLI/status output for `workspace_active`, stopped/invalidated session, and reopen behavior in `src/notebook_launcher/cli.py`
- [ ] T123 [US7] Run and fix US7 tests in `tests/contract/test_lifecycle_api.py`, `tests/integration/test_single_active_session.py`, `tests/integration/test_session_reconciliation.py`, and `tests/integration/test_stop_reopen.py`
- [ ] T124 [US7] Validate stop/reopen/status workflow from `specs/001-local-notebook-launcher/quickstart.md` and update expected responses where required

**Checkpoint**: Runtime lifecycle is predictable, crash-recoverable, and cannot create competing runtimes for one mutable workspace.

---

## Phase 10: Polish & Cross-Cutting Acceptance

**Purpose**: Prove the complete POC, finalize documentation, and close cross-story quality/security gates.

- [ ] T125 [P] Add end-to-end fixture repository/notebook definitions and controlled failure cells in `tests/fixtures/e2e/README.md`
- [ ] T126 Add full GitHub → trust → workspace → Jupyter → writable MCP edit/execute/file-op/conflict/failure/repair → stop → reopen acceptance test in `tests/integration/test_e2e_local_notebook_launcher.py`
- [ ] T127 [P] Add end-to-end readonly acceptance path to `tests/integration/test_e2e_readonly.py`
- [ ] T128 [P] Add regression test proving source snapshot, GitHub remote, and trust records do not change during browser/MCP workspace mutations in `tests/integration/test_e2e_no_remote_mutation.py`
- [ ] T129 Run the complete automated suite and record platform/backend versions needed for reproducibility in `README.md`
- [ ] T130 Run the configured WSL2/Linux NVIDIA acceptance path when available and record the tested GPU/runtime matrix in `README.md`
- [ ] T131 Reconcile public API behavior with `specs/001-local-notebook-launcher/contracts/openapi.yaml` and update only contract mismatches discovered by passing tests in `specs/001-local-notebook-launcher/contracts/openapi.yaml`
- [ ] T132 Reconcile CLI/MCP behavior with `specs/001-local-notebook-launcher/contracts/cli.md` and `specs/001-local-notebook-launcher/contracts/mcp-capabilities.md` after conformance testing
- [ ] T133 Complete user-facing install, launch, trust, persistence, Save-a-Copy, GPU, MCP, mount, lifecycle, and security documentation in `README.md`
- [ ] T134 Perform final constitution/spec/plan/data-model/contracts/tasks consistency review and record any non-product tooling follow-ups separately without expanding feature scope in `specs/001-local-notebook-launcher/tasks.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 — Setup**: No dependencies.
- **Phase 2 — Foundational**: Depends on Phase 1 and blocks every user story.
- **Phase 3 — US1**: Depends on Phase 2 and establishes the first working source-to-Jupyter vertical slice.
- **Phase 4 — US2**: Depends on US1 workspace/runtime creation behavior.
- **Phase 5 — US3**: Depends on US1 ready Jupyter sessions and foundational version/lease/state primitives; it can proceed in parallel with later US2 work that does not change runtime/MCP files.
- **Phase 6 — US4**: Depends on US1 runtime and US3 for the browser/MCP consistency acceptance check.
- **Phase 7 — US5**: Depends on US1 runtime construction; security hardening should complete before declaring the P1 MVP acceptable.
- **Phase 8 — US6**: Depends on US2 workspace persistence and US5 mount-boundary enforcement.
- **Phase 9 — US7**: Depends on US1 runtime and foundational active-session state; it integrates with US3 lease invalidation and US2 persistence.
- **Phase 10 — Polish**: Depends on all stories included in the release cut.

### User Story Dependency Graph

```text
Foundation
   |
   v
 US1 Open trusted notebook
   |\
   | +---------> US3 MCP agent ---------> US4 GPU consistency
   |                 |
   |                 +--------------------------+
   v                                            |
 US2 Persistence ----------------> US6 Data mount|
   |                                ^           |
   |                                |           |
   +-----------------------------> US7 Lifecycle|
   |                                            |
   +-----------------> US5 Containment ---------+
```

US1 is the first vertical slice. US2, US3, and US5 can be developed after US1 with limited parallelism across distinct modules/tests. US4 requires the MCP path for its full acceptance criterion. US6 intentionally waits for persistence + sandbox mount controls. US7 can begin after US1 but final acceptance includes US2 persistence and US3 lease invalidation.

### Within Each User Story

- Write story tests first and verify they fail for the intended reason.
- Implement models/state constraints before services that consume them.
- Implement services/runtime behavior before HTTP/CLI integration points.
- Run story-specific tests before the checkpoint.
- Do not weaken a contract to make a backend pass; adapt or reject the backend instead.

---

## Parallel Opportunities

- Phase 1 tasks T003–T007 can run in parallel after T001/T002 where they do not edit the same file.
- Phase 2 test tasks T008–T011 and implementation tasks T012, T013, T015–T018, T020 can be split across different files, with T014/T019 serialized around `state.py`.
- US1 tests T022–T027 can be authored in parallel; source/trust/environment/sandbox implementation can be split by module before orchestration integration.
- US2 persistence tests T044–T047 can be authored in parallel.
- US3 tests T057–T064 can be authored in parallel; lease/version/MCP integration then converges in `mcp.py`.
- US4 tests T080–T082 can run in parallel before GPU implementation.
- US5 negative/security tests T089–T094 can be authored in parallel.
- US6 tests T103–T105 can be authored in parallel.
- US7 tests T112–T115 can be authored in parallel.
- Phase 10 regression/documentation work marked `[P]` can proceed alongside the main end-to-end acceptance test.

---

## Parallel Example: User Story 3

```text
Task T058: tests/unit/test_leases.py
Task T059: tests/unit/test_versions.py
Task T060: tests/integration/test_mcp_writable.py
Task T061: tests/integration/test_mcp_readonly.py
Task T062: tests/integration/test_mcp_conflicts.py
Task T063: tests/integration/test_mcp_workspace_files.py
Task T064: tests/integration/test_mcp_isolation.py
```

These can be authored concurrently because they target separate test files. Implementation then proceeds through lease/version primitives before converging on `src/notebook_launcher/mcp.py`.

---

## Implementation Strategy

### MVP First

The practical MVP is not US1 alone because the constitution explicitly requires the complete local agent-operable path. Deliver in this order:

1. Phase 1 — Setup
2. Phase 2 — Foundational
3. Phase 3 — US1 trusted GitHub notebook open
4. Phase 4 — US2 persistence/reopen/Save a Copy
5. Phase 5 — US3 writable + readonly MCP, conflicts, workspace files, writable lease
6. Phase 7 — US5 standard sandbox/security gates
7. Phase 9 — US7 lifecycle/one-active-session behavior
8. Phase 10 core end-to-end acceptance

US4 GPU is required when a configured GPU host is part of the acceptance environment; CPU-only operation remains valid for non-GPU hosts. US6 explicit user-data mount is P2 and may follow the core MVP without architectural redesign.

### Incremental Delivery

- **Increment A**: Setup + foundation + US1 → trusted source opens locally.
- **Increment B**: US2 → persistent editable local notebook and Save a Copy.
- **Increment C**: US3 → same-kernel agent operation with conflict/lease enforcement.
- **Increment D**: US5 + US7 → hardened runtime and predictable lifecycle.
- **Increment E**: US4 → GPU parity where configured.
- **Increment F**: US6 → explicit local external-data grant.
- **Final**: Cross-story end-to-end/conformance acceptance and documentation.

---

## Notes

- `[P]` means different files/no unresolved dependency, not merely “could be done by another person.”
- All story tasks carry `[US#]` labels; setup/foundational/polish tasks intentionally do not.
- SQLite stores control metadata only; notebook/cell contents and raw Jupyter credentials must not be stored there.
- The initial MCP implementation targets Datalayer `jupyter-mcp-server` 2.x, but the semantic launcher contract remains backend-neutral.
- Backend real-time-collaboration behavior must pass the explicit browser↔agent persistence/conflict suite; silent last-write-wins is a conformance failure.
- GitHub remains immutable source input throughout this feature.
- The separate Spec Kit/no-shell connector tooling limitation is a follow-up development-tooling concern, not an implementation task for the Notebook Launcher feature.