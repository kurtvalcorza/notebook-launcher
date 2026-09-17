# Tasks: Local Notebook Launcher

**Input**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required by constitution and specification. Story tests are written before corresponding implementation.

## Execution update — 2026-09-17

The original checklist below is retained as the specification decomposition.
Current completion and evidence maturity are governed by this execution update
and the repository's current test results; an unchecked original line does not
override recorded implementation evidence.

Completed locally: setup/foundational control state; preview and one-time launch
authorization; trust create/list/revoke/deny plus trust-time identity
re-resolution; exact-SHA source cache; repo2docker build/cache; persistent
workspace and Save a Copy; sandbox argv; IPv4 firewall policy; GPU policy;
canonical grants and safe paths; production launch/stop/reopen pipeline; private
MCP credential bridge; writable lease; readonly enforcement; bounded output; and
the E2E fixture/image/Jupyter/MCP component smoke.

Open implementation/acceptance task IDs:

- backend and shared-state semantics: T072, T080–T089, T091, T094–T098,
  T100, T103–T105;
- environment acceptance: T030, T045, T051, T053–T054, T062, T075, T108,
  T113, T117, T124, T134, T141–T142;
- lifecycle/E2E/contracts: T126–T128, T130, T136–T140, T143, T145;
- additional identity/drive-by coverage: T023, T027–T029, T031.

Release-blocking findings are the missing conforming backend capabilities,
atomic shared-document conflict control, live message-owned cancellation,
dual-client collaboration/YDoc acceptance, process-restart integration,
same-namespace Linux firewall acceptance, and configured GPU acceptance.
Authoritative collaboration-session readiness and stale persisted-start
reconciliation are implemented and covered by focused tests. Documentation task
T144 is complete for the current candid status; it must be refreshed again when
those gates close.

## Format

`[ID] [P?] [Story?] Description with exact path`

---

## Phase 1 — Setup

- [ ] T001 Create Python 3.12+ project/dependency/console-script configuration in `pyproject.toml`
- [ ] T002 Create package/version bootstrap in `src/notebook_launcher/__init__.py`
- [ ] T003 [P] Create pytest fixtures for temporary launcher roots, SQLite, fake GitHub source metadata, Docker markers, and Jupyter markers in `tests/conftest.py`
- [ ] T004 [P] Implement paths, loopback defaults, resource limits, execution timeout/grace, output bound, launch-token lifetime, and backend settings in `src/notebook_launcher/config.py`
- [ ] T005 [P] Create FastAPI app/lifecycle skeleton in `src/notebook_launcher/app.py`
- [ ] T006 [P] Create CLI skeleton for `serve`, `mcp`, `trust`, `workspace` in `src/notebook_launcher/cli.py`
- [ ] T007 [P] Document host prerequisites and trust/sandbox/network limitations in `README.md`

## Phase 2 — Foundational control state

### Tests

- [ ] T008 [P] Test typed secret-safe errors including launch-token, repo-identity, network-policy, host-path-escape, execution-cancel errors in `tests/unit/test_errors.py`
- [ ] T009 [P] Test SQLite invariants for trust, launch-token replay, one active session/workspace, one writable lease/session, rollback in `tests/unit/test_state.py`
- [ ] T010 [P] Test request/model enums, normalized paths, stable repo ID, execution states, bounded output models in `tests/unit/test_models.py`
- [ ] T011 [P] Test bounded audit redaction for notebook/output/credential payloads in `tests/unit/test_audit.py`

### Implementation

- [ ] T012 [P] Implement public typed errors in `src/notebook_launcher/errors.py`
- [ ] T013 [P] Implement domain/API models including `LaunchPreview`, `ResolvedSource`, `TrustRecord`, `ExecutionOperation`, `NetworkPolicyState`, and canonical `UserDataGrant` in `src/notebook_launcher/models.py`
- [ ] T014 Implement SQLite schema/repositories including token replay, stable repo trust, session ownership, leases, versions, audit metadata in `src/notebook_launcher/state.py`
- [ ] T015 [P] Implement bounded sanitized audit persistence in `src/notebook_launcher/audit.py`
- [ ] T016 [P] Implement document/file version primitives and active-notebook generic-file guard in `src/notebook_launcher/versions.py`
- [ ] T017 [P] Implement writable lease acquire/release/reconcile in `src/notebook_launcher/leases.py`
- [X] T018 [P] Implement structured argv subprocess helper with cancellation/redaction/bounded logs in `src/notebook_launcher/orchestration.py`
- [ ] T019 Implement launch/session state transitions in `src/notebook_launcher/state.py`
- [X] T020 [P] Implement host diagnostics for Git/Docker/repo2docker/Jupyter collaboration/MCP/GPU/network-policy capability in `src/notebook_launcher/environment.py`
- [ ] T021 Run/fix foundational tests in `tests/unit/`

---

## Phase 3 — US1 Trusted source preview, authorization, trust, first open (P1)

### Tests

- [ ] T022 [P] [US1] Test GitHub URL/ref/path parsing, traversal, unsupported hosts, slash-containing refs in `tests/unit/test_source.py`
- [ ] T023 [P] [US1] Test stable GitHub repository ID resolution and owner/name rename/transfer/recreation cases in `tests/unit/test_source_identity.py`
- [ ] T024 [P] [US1] Test launch-token signature, expiry, request binding, JTI replay, token-not-in-GET, same-origin/local checks in `tests/unit/test_launch_auth.py`
- [ ] T025 [P] [US1] Test exact-commit/repository trust keyed by stable repository ID and rename/transfer/recreation behavior in `tests/unit/test_trust.py`
- [ ] T026 [P] [US1] Contract-test non-executing `GET /open`, side-effecting `POST /api/launches`, trust/status routes in `tests/contract/test_launch_api.py`
- [ ] T027 [P] [US1] Integration-test that GET `/open` causes zero build/setup/runtime/kernel/notebook execution for untrusted and already-trusted sources in `tests/integration/test_open_preview_no_execution.py`
- [ ] T028 [P] [US1] Integration-test cross-site navigation/form/iframe-style drive-by attempts cannot execute and preview is anti-frame protected in `tests/integration/test_launch_driveby.py`
- [ ] T029 [P] [US1] Integration-test deny/cancel trust gives zero repository setup/notebook execution in `tests/integration/test_trust_gate.py`
- [ ] T030 [P] [US1] Docker-backed integration-test exact notebook/provenance/source-dependency isolation/full sandbox+network gate before first cell in `tests/integration/test_open_notebook.py`
- [ ] T031 [P] [US1] Test environment cache reuse without mutable workspace reuse in `tests/integration/test_environment_cache.py`

### Implementation

- [ ] T032 [P] [US1] Implement strict GitHub source parsing and validated clone URL construction in `src/notebook_launcher/source.py`
- [ ] T033 [US1] Implement repository metadata lookup including stable numeric ID/node ID and immutable ref resolution in `src/notebook_launcher/source.py`
- [ ] T034 [P] [US1] Implement signed request-bound one-time local launch authorization tokens and replay checks in `src/notebook_launcher/launch_auth.py`
- [ ] T035 [P] [US1] Implement stable-ID trust lookup/create/revoke and grant-time/current-name handling in `src/notebook_launcher/trust.py`
- [X] T036 [US1] Implement immutable source acquisition/cache and notebook existence validation in `src/notebook_launcher/repository.py`
- [ ] T037 [US1] Implement fresh workspace materialization with immutable provenance in `src/notebook_launcher/workspace.py`
- [X] T038 [P] [US1] Implement deterministic environment identity/build cache in `src/notebook_launcher/environment.py`
- [ ] T039 [US1] Implement repo2docker build/cache miss behavior and redacted failures in `src/notebook_launcher/environment.py`
- [ ] T040 [US1] Implement non-executing GET `/open` preview page and anti-framing headers in `src/notebook_launcher/app.py`
- [ ] T041 [US1] Implement `POST /api/launches` token consumption, same-origin/local checks, request/source identity revalidation, and transition to trust/acquisition in `src/notebook_launcher/app.py`
- [ ] T042 [US1] Implement trust confirmation routes/page only after launch authorization in `src/notebook_launcher/app.py`
- [ ] T043 [US1] Ensure trusted source never bypasses launch authorization in `src/notebook_launcher/orchestration.py`
- [ ] T044 [US1] Ensure no GitHub write credentials/remote publishing relationship in `src/notebook_launcher/workspace.py`
- [ ] T045 [US1] Run/fix US1 tests and validate quickstart launch flow in `specs/001-local-notebook-launcher/quickstart.md`

---

## Phase 4 — US5 Standard sandbox + egress boundary (P1, before runtime ready)

### Tests

- [ ] T046 [P] [US5] Test non-root/no-privileged/no-new-privileges/capability/seccomp/resource-limit argv in `tests/unit/test_sandbox_policy.py`
- [ ] T047 [P] [US5] Test prohibited mounts/secrets/Docker socket/whole-home in `tests/unit/test_sandbox_mounts.py`
- [ ] T048 [P] [US5] Test denied IPv4/IPv6 destination classification including loopback/private/ULA/link-local/metadata/multicast/non-global and allowed global IPs in `tests/unit/test_network_policy.py`
- [ ] T049 [P] [US5] Test configured DNS resolver exception without permitting DNS-resolved private targets in `tests/unit/test_network_policy.py`
- [ ] T050 [P] [US5] Test no host-gateway alias/bypass is added in `tests/unit/test_network_policy.py`
- [ ] T051 [P] [US5] Integration-test public Internet succeeds while host-gateway/RFC1918/link-local/metadata/private-DNS destinations fail in `tests/integration/test_network_policy.py`
- [ ] T052 [P] [US5] Test structured argv command-injection rejection in `tests/unit/test_injection.py`
- [ ] T053 [P] [US5] Test no GitHub mutation surface in `tests/integration/test_no_github_mutation.py`
- [ ] T054 [P] [US5] Test cross-session/private-state denial in `tests/integration/test_session_isolation.py`

### Implementation

- [ ] T055 [US5] Implement complete standard sandbox argv/policy and fail-closed assertions in `src/notebook_launcher/sandbox.py`
- [ ] T056 [US5] Implement mount allowlist and host secret/environment scrubbing in `src/notebook_launcher/sandbox.py` and `src/notebook_launcher/runtime.py`
- [ ] T057 [US5] Implement public-Internet/non-global-deny destination policy in `src/notebook_launcher/network.py`
- [ ] T058 [US5] Implement launcher-managed network/firewall application that notebook code cannot remove in `src/notebook_launcher/network.py`
- [ ] T059 [US5] Implement explicit configured DNS resolver exception and IP-layer post-resolution deny rules in `src/notebook_launcher/network.py`
- [ ] T060 [US5] Ensure no host-gateway alias and loopback-only inbound publication in `src/notebook_launcher/runtime.py`
- [ ] T061 [US5] Gate runtime readiness on sandbox+network policy assertions in `src/notebook_launcher/runtime.py`
- [ ] T062 [US5] Run/fix US5 tests and update `README.md` security/network guarantees

---

## Phase 5 — US2 Persistent workspace, active path, safe writes (P1)

### Tests

- [ ] T063 [P] [US2] Test workspace persistence/reopen and no Git reset in `tests/integration/test_workspace_persistence.py`
- [ ] T064 [P] [US2] Test active-notebook rename/move and missing-path behavior in `tests/integration/test_workspace_persistence.py`
- [ ] T065 [P] [US2] Test Save a Copy validation/overwrite/output/source immutability in `tests/unit/test_workspace_copy.py`
- [ ] T066 [P] [US2] Test `ENOSPC`/replacement failure preserves prior file and metadata in `tests/integration/test_workspace_files.py`
- [ ] T067 [P] [US2] Contract-test workspace metadata/copy API in `tests/contract/test_workspace_api.py`

### Implementation

- [ ] T068 [US2] Implement persisted workspace/provenance/active-notebook metadata in `src/notebook_launcher/state.py`
- [ ] T069 [US2] Implement reopen existing mutable work without source reset in `src/notebook_launcher/workspace.py`
- [ ] T070 [US2] Implement atomic/safe replacement and storage errors in `src/notebook_launcher/workspace.py`
- [ ] T071 [US2] Implement Save a Copy workspace destinations in `src/notebook_launcher/workspace.py`
- [ ] T072 [US2] Implement active-notebook rename/move reconciliation in `src/notebook_launcher/jupyter.py` and `src/notebook_launcher/workspace.py`
- [ ] T073 [US2] Implement workspace metadata/copy endpoints in `src/notebook_launcher/app.py`
- [ ] T074 [US2] Verify source snapshot remains unchanged in `src/notebook_launcher/repository.py`
- [ ] T075 [US2] Run/fix US2 tests and quickstart persistence/storage cases

---

## Phase 6 — US3 Same-runtime MCP + execution ownership (P1)

### Tests

- [ ] T076 [P] [US3] Contract-test write/readonly capabilities, fixed notebook target, cancel semantics, bounded output in `tests/contract/test_mcp_contract.py`
- [ ] T077 [P] [US3] Test writable lease acquisition/release/stale reconciliation in `tests/unit/test_leases.py`
- [ ] T078 [P] [US3] Test document/file versions and active-notebook file guards in `tests/unit/test_versions.py`
- [ ] T079 [P] [US3] Test execution broker queue/state/message ownership transitions in `tests/unit/test_execution_broker.py`
- [ ] T080 [P] [US3] Integration-test writable notebook edit/execute/fail/repair/restart/output in `tests/integration/test_mcp_writable.py`
- [ ] T081 [P] [US3] Integration-test running-agent non-terminating cancellation/timeout in `tests/integration/test_mcp_cancellation.py`
- [ ] T082 [P] [US3] Integration-test browser-running + queued-agent cancel/timeout leaves browser execution uninterrupted in `tests/integration/test_mcp_cancellation_ownership.py`
- [ ] T083 [P] [US3] Integration-test dispatched idle→busy race: cancel_pending does not interrupt non-agent parent request in `tests/integration/test_mcp_cancellation_ownership.py`
- [ ] T084 [P] [US3] Integration-test readonly denials in `tests/integration/test_mcp_readonly.py`
- [ ] T085 [P] [US3] Integration-test browser↔agent conflict/persistence in `tests/integration/test_mcp_conflicts.py`
- [ ] T086 [P] [US3] Integration-test ordinary workspace files and active-notebook generic-file rejection in `tests/integration/test_mcp_workspace_files.py`
- [ ] T087 [P] [US3] Integration-test browser opens/focuses notebook B but MCP remains bound to active notebook A in `tests/integration/test_mcp_fixed_target.py`
- [ ] T088 [P] [US3] Test bounded large/binary/multimodal output and audit exclusion in `tests/integration/test_mcp_output_bounds.py`
- [ ] T089 [P] [US3] Test cross-session private-state/token isolation in `tests/integration/test_mcp_isolation.py`

### Implementation

- [ ] T090 [P] [US3] Define backend-neutral semantic MCP adapter in `src/notebook_launcher/mcp.py`
- [ ] T091 [US3] Implement initial Datalayer adapter against existing launcher Jupyter in `src/notebook_launcher/mcp.py`
- [ ] T092 [US3] Implement stdio bridge and private credential resolution in `src/notebook_launcher/cli.py`
- [ ] T093 [US3] Enforce writable lease before writable capabilities in `src/notebook_launcher/mcp.py`
- [ ] T094 [US3] Implement authoritative Jupyter collaboration/version/fixed-target observations in `src/notebook_launcher/jupyter.py`
- [ ] T095 [US3] Implement notebook/cell operations and notebook-aware move in `src/notebook_launcher/mcp.py`
- [ ] T096 [US3] Implement session execution broker queue, Jupyter message IDs, kernel busy-parent observation, and serialized dispatch in `src/notebook_launcher/execution.py`
- [ ] T097 [US3] Implement ownership-safe timeout/cancel and bounded grace/restart recovery in `src/notebook_launcher/execution.py`
- [ ] T098 [US3] Implement cell/whole-notebook/diagnostic execution through broker in `src/notebook_launcher/mcp.py`
- [ ] T099 [US3] Implement bounded output envelopes in `src/notebook_launcher/mcp.py`
- [ ] T100 [US3] Implement ordinary workspace file operations with active-notebook guard in `src/notebook_launcher/mcp.py`
- [ ] T101 [US3] Enforce readonly policy at launcher/MCP boundary in `src/notebook_launcher/mcp.py`
- [ ] T102 [US3] Implement non-secret MCP descriptor including fixed notebook target in `src/notebook_launcher/app.py`
- [ ] T103 [US3] Emit bounded attachment/execution/conflict/cancellation audit events in `src/notebook_launcher/audit.py`
- [ ] T104 [US3] Run backend/Jupyter conformance and pin exact passing versions in `pyproject.toml`
- [ ] T105 [US3] Run/fix all US3 tests

---

## Phase 7 — US4 GPU policy (P1 where GPU available)

- [ ] T106 [P] [US4] Test GPU detection and `auto|on|off` policy in `tests/unit/test_gpu_policy.py`
- [ ] T107 [P] [US4] Test GPU sandbox flags without weakening sandbox/network policy in `tests/unit/test_sandbox_gpu.py`
- [ ] T108 [P] [US4] Test browser/MCP same-device identity on configured GPU host in `tests/integration/test_gpu_consistency.py`
- [ ] T109 [US4] Implement NVIDIA host/container capability probe in `src/notebook_launcher/runtime.py`
- [ ] T110 [US4] Implement effective GPU allocation/failure semantics in `src/notebook_launcher/runtime.py`
- [ ] T111 [US4] Add GPU device exposure only when enabled in `src/notebook_launcher/sandbox.py`
- [ ] T112 [US4] Expose effective GPU status in `src/notebook_launcher/models.py`
- [ ] T113 [US4] Run/fix GPU tests and document smoke procedure in `specs/001-local-notebook-launcher/quickstart.md`

---

## Phase 8 — US6 Canonical local data grant (P2)

### Tests

- [ ] T114 [P] [US6] Test canonical-root grant persistence/revocation/missing-root behavior in `tests/unit/test_user_data_grant.py`
- [ ] T115 [P] [US6] Contract-test mount/unmount CLI including canonical whole-home rejection in `tests/contract/test_workspace_mount_cli.py`
- [ ] T116 [P] [US6] Test traversal/symlink/junction/reparse/path-swap escapes for host-side operations in `tests/unit/test_safe_host_paths.py`
- [ ] T117 [P] [US6] Docker-backed test no-default/ro/rw mount and unrelated host denial in `tests/integration/test_user_data_mount.py`
- [ ] T118 [P] [US6] Test Save a Copy to user-data cannot escape via symlink/race and respects `ro` in `tests/integration/test_user_data_copy.py`

### Implementation

- [ ] T119 [US6] Implement canonical grant root state/revalidation in `src/notebook_launcher/state.py` and `src/notebook_launcher/workspace.py`
- [ ] T120 [US6] Implement component-safe dirfd/no-follow host path resolver (openat2-style where available) in `src/notebook_launcher/safe_paths.py`
- [ ] T121 [US6] Implement mount/unmount CLI using canonical root validation in `src/notebook_launcher/cli.py`
- [ ] T122 [US6] Apply canonical grant as one `ro|rw` mount in `src/notebook_launcher/sandbox.py`
- [ ] T123 [US6] Implement user-data Save a Copy through safe host path resolver in `src/notebook_launcher/workspace.py`
- [ ] T124 [US6] Run/fix US6 tests and quickstart symlink-escape cases

---

## Phase 9 — US7 Lifecycle/reopen (P2)

- [ ] T125 [P] [US7] Contract-test status/stop/existing-session behavior in `tests/contract/test_lifecycle_api.py`
- [ ] T126 [P] [US7] Test repeated start cannot create second active runtime in `tests/integration/test_single_active_session.py`
- [ ] T127 [P] [US7] Test launcher restart stale-session reconciliation in `tests/integration/test_session_reconciliation.py`
- [ ] T128 [P] [US7] Test stop/reopen requires fresh launch authorization and preserves workspace/cache/current active path in `tests/integration/test_stop_reopen.py`
- [ ] T129 [US7] Enforce transactional single active session in `src/notebook_launcher/state.py`
- [ ] T130 [US7] Implement runtime liveness reconciliation in `src/notebook_launcher/orchestration.py`
- [ ] T131 [US7] Implement idempotent stop/invalidation/preservation in `src/notebook_launcher/runtime.py`
- [ ] T132 [US7] Implement authorized reopen orchestration and active-path validation in `src/notebook_launcher/orchestration.py`
- [ ] T133 [US7] Implement status/stop/health pages/routes in `src/notebook_launcher/app.py`
- [ ] T134 [US7] Run/fix lifecycle tests and quickstart reopen flow

---

## Phase 10 — Cross-cutting acceptance/polish

- [ ] T135 [P] Define E2E fixture repo with dependency, controlled failure, non-terminating cell, large/binary output in `tests/fixtures/e2e/README.md`
- [ ] T136 Add full preview → local authorize → trust → sandbox/network → Jupyter → MCP → cancel/repair → stop → authorized reopen E2E test in `tests/integration/test_e2e_local_notebook_launcher.py`
- [ ] T137 [P] Add E2E GET-only drive-by regression for already-trusted source in `tests/integration/test_e2e_launch_driveby.py`
- [ ] T138 [P] Add E2E stable-repo-ID trust rename/transfer/recreation regression in `tests/integration/test_e2e_trust_identity.py`
- [ ] T139 [P] Add E2E readonly path in `tests/integration/test_e2e_readonly.py`
- [ ] T140 [P] Add E2E no-GitHub-write/source-immutability regression in `tests/integration/test_e2e_no_remote_mutation.py`
- [ ] T141 Run complete automated suite and verify health/status responsiveness during long build/execution; record tested versions in `README.md`
- [ ] T142 Run configured WSL2/Linux GPU acceptance when available and record matrix in `README.md`
- [ ] T143 Reconcile OpenAPI/CLI/MCP/workspace contracts after passing conformance tests in `specs/001-local-notebook-launcher/contracts/`
- [ ] T144 Complete user-facing documentation for launch authorization vs trust, network policy, canonical data grants, execution ownership, persistence, GPU, MCP, lifecycle in `README.md`
- [ ] T145 Perform final constitution/spec/plan/data-model/contracts/tasks consistency review in `specs/001-local-notebook-launcher/tasks.md`

## Dependency notes

- Phase 2 blocks all stories.
- US1 source preview/authorization/trust precedes executable launch.
- US5 sandbox/network policy must be complete before US1 runtime can become ready.
- US2 persistence and US3 MCP can proceed after first safe ready runtime, with module-aware parallelism.
- US3 cancellation acceptance depends on Jupyter request ownership observation, not merely an interrupt API.
- US6 depends on workspace persistence + sandbox mount boundary.
- US7 reopen depends on launch authorization, persistence, and session ownership.
- Implementation MUST NOT weaken fixed review-remediation invariants to fit an upstream backend.
