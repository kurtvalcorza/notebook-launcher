# Tasks: Local Notebook Launcher

**Input**: feature spec, plan, data model, and contracts under `specs/001-local-notebook-launcher/`

**Tests**: Required for source/trust parsing, workspace persistence, sandbox policy, MCP permissions, and the complete GitHub-to-agent persistence path.

## Phase 1: Project Setup

- [ ] T001 Create Python 3.12+ project metadata, CLI, package layout, and dev dependencies
- [ ] T002 Create FastAPI launcher app and loopback-default configuration
- [ ] T003 Add typed errors/stable error codes
- [ ] T004 Add test layouts for unit, contract, integration, and fixtures
- [ ] T005 Document WSL2/Linux prerequisites and trust/sandbox model

## Phase 2: Models, Safe Process Invocation, and Diagnostics

- [ ] T006 Define source, trust, environment, workspace, session, MCP, copy-request, and audit models
- [ ] T007 Define launch lifecycle including `awaiting_trust` and `workspace_preparing`
- [ ] T008 Implement structured subprocess helper with no shell interpolation
- [ ] T009 Implement Git/Docker/repo2docker diagnostics
- [ ] T010 Implement optional NVIDIA container capability probe
- [ ] T011 Implement MCP backend capability probe
- [ ] T012 Unit-test model/state/diagnostic validation

## Phase 3: Source Resolution and Trust (P1)

- [ ] T013 Parse GitHub `/blob/` notebook URLs and explicit repo/ref/path input
- [ ] T014 Resolve branches/tags/slash-containing refs to immutable SHA
- [ ] T015 Normalize and reject traversal/non-ipynb paths
- [ ] T016 Construct validated HTTPS clone URL from owner/repository components
- [ ] T017 Implement local trust store and `awaiting_trust` state
- [ ] T018 Implement explicit trust/deny decision with revision/repository scope
- [ ] T019 Ensure repository-supplied build/runtime code does not execute before trust approval
- [ ] T020 Test malformed URLs, ref ambiguity, traversal, and trust transitions

## Phase 4: Persistent Workspace and Colab-Like Copy Semantics (P1)

- [ ] T021 Implement workspace manager and launcher-managed workspace root
- [ ] T022 Acquire immutable source snapshot/provenance at resolved SHA
- [ ] T023 Materialize fresh persistent editable `work/` separately from source snapshot
- [ ] T024 Materialize persistent `outputs/`
- [ ] T025 Ensure working copy does not depend on a writable Git remote and no GitHub credentials are injected
- [ ] T026 Implement workspace metadata/provenance serialization
- [ ] T027 Implement reopen-by-workspace-ID without resetting from GitHub
- [ ] T028 Implement Save a Copy within workspace with traversal/overwrite validation
- [ ] T029 Implement Save a Copy to explicitly configured user-data mount when writable
- [ ] T030 Test edit/output/file persistence across runtime stop/reopen
- [ ] T031 Test fresh source launch creates a new workspace rather than silently reusing edited work
- [ ] T032 Test Save a Copy preserves requested outputs and never touches source snapshot/GitHub

## Phase 5: Reproducible Environment (P1)

- [ ] T033 Compute deterministic image/environment identity
- [ ] T034 Detect local image cache hit
- [ ] T035 Invoke repo2docker with structured arguments on cache miss
- [ ] T036 Capture/redact build progress and failures
- [ ] T037 Integration-test repo-declared dependency absent from host
- [ ] T038 Test cached environment reuse independent of workspace identity

## Phase 6: Standard Sandbox, Network, GPU, and Jupyter Runtime (P1)

- [ ] T039 Implement sandbox runtime configuration module
- [ ] T040 Configure non-root runtime where compatible, no-new-privileges, and no privileged mode
- [ ] T041 Drop unnecessary Linux capabilities and retain default/equivalent seccomp filtering
- [ ] T042 Add configurable CPU, memory, and PID limits
- [ ] T043 Mount only workspace/output/source and explicit user-data locations; prohibit Docker socket/credential/home mounts
- [ ] T044 Implement user-data mount configuration with explicit `ro`/`rw` and stable `/mnt/user-data`
- [ ] T045 Ensure remote URL/query cannot select arbitrary host mount paths
- [ ] T046 Allow outbound network by default while publishing Jupyter only on `127.0.0.1`
- [ ] T047 Implement generated Jupyter token and prevent it from normal logs/API output
- [ ] T048 Implement `gpu=off|auto|on`
- [ ] T049 Start Jupyter against `/workspace` and probe server/kernel readiness
- [ ] T050 Open requested working notebook directly in JupyterLab
- [ ] T051 Unit-test sandbox argv/mounts/limits/network/GPU policies
- [ ] T052 Manual/integration GPU smoke test on WSL2/Linux + NVIDIA Container Toolkit

## Phase 7: MCP Writable and Readonly Profiles (P1)

- [ ] T053 Implement semantic MCP adapter interface
- [ ] T054 Select/pin initial maintained backend and document capability mapping
- [ ] T055 Implement private session state and `notebook-launcher mcp <session-id>` stdio bridge
- [ ] T056 Implement required write capabilities: read, insert, update, delete, execute cell/all, diagnostic code, output, restart
- [ ] T057 Enforce same Jupyter server/workspace/kernel lifecycle
- [ ] T058 Implement `readonly` descriptor/capability set
- [ ] T059 Enforce readonly rejection of execution, mutation, and restart at adapter boundary
- [ ] T060 Implement ordered whole-notebook execution with active kernel and default stop-on-error
- [ ] T061 Ensure whole-notebook execution is not implemented as unrelated script conversion
- [ ] T062 Implement timeout/cancellation and structured failure distinctions
- [ ] T063 Implement sanitized attachment/execution audit events
- [ ] T064 Test writable edit/execute/repair path in same Jupyter kernel
- [ ] T065 Test readonly can inspect but cannot execute/mutate/restart
- [ ] T066 Test cross-session/private-state isolation and token redaction
- [ ] T067 Test MCP/browser GPU consistency

## Phase 8: API, Status, Stop, and Reopen (P2 integration surface)

- [ ] T068 Implement `/open` remote-source and `workspace_id` modes
- [ ] T069 Implement human status/trust-confirmation page
- [ ] T070 Implement launch status API
- [ ] T071 Implement trust-decision API
- [ ] T072 Implement workspace metadata API
- [ ] T073 Implement Save-a-Copy API
- [ ] T074 Implement non-secret MCP descriptor API
- [ ] T075 Implement session stop preserving workspace/environment cache and invalidating MCP/private runtime state
- [ ] T076 Implement health endpoint
- [ ] T077 Add orchestration tests for trust denial, workspace creation/reopen, build/start/MCP failure, stop/reopen

## Phase 9: Security/No-GitHub-Mutation Tests

- [ ] T078 Test command-injection payloads in source/session/copy fields
- [ ] T079 Test prohibited mounts and no Docker socket/SSH/cloud/GitHub credentials
- [ ] T080 Test no launcher/MCP commit, push, branch-create, or GitHub mutation surface exists
- [ ] T081 Test source snapshot remains immutable during browser/agent edits
- [ ] T082 Test remote launch cannot configure local user-data mount path
- [ ] T083 Test loopback-only inbound exposure and absence of extra MCP network listener under stdio

## Phase 10: End-to-End Acceptance and Documentation

- [ ] T084 Launch representative public notebook through trust flow into persistent workspace
- [ ] T085 Verify repository dependency and image cache reuse
- [ ] T086 Edit/run notebook, create `/outputs` artifact, stop, reopen workspace, verify persistence
- [ ] T087 Save a Copy and verify original working notebook/source provenance remain intact
- [ ] T088 Attach writable MCP agent, edit/execute/fail/repair/restart in same kernel
- [ ] T089 Attach readonly MCP agent and prove inspection-only enforcement
- [ ] T090 Validate GPU end-to-end when configured
- [ ] T091 Validate explicit user-data mount without whole-home exposure
- [ ] T092 Update README with launch, workspace, Save a Copy, mounts, GPU, MCP profiles, sandbox/network, and trust guidance
- [ ] T093 Run spec/plan/tasks/contracts consistency review and resolve all uncovered requirements

## MVP Cut

MVP requires the complete path:

```text
GitHub public notebook
 -> explicit trust decision when needed
 -> immutable source provenance
 -> persistent editable workspace
 -> reproducible standard sandbox/Jupyter runtime
 -> optional local GPU
 -> writable MCP agent edits + executes + repairs notebook
 -> runtime stop
 -> workspace reopen with edits/outputs preserved
```

Readonly MCP enforcement, Save a Copy, no-GitHub-mutation proof, and sandbox baseline tests are also MVP acceptance requirements. Explicit user-data mount is P2 but should fit the MVP architecture without redesign.
