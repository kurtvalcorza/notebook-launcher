# Feature Specification: Local Notebook Launcher

**Feature Branch**: `001-local-notebook-launcher`

**Created**: 2026-09-15

**Status**: Draft

**Input**: Launch a user-trusted public GitHub-hosted `.ipynb` into a persistent local workspace and disposable sandboxed Jupyter runtime, optionally use a local NVIDIA GPU, and expose the same notebook/kernel to MCP-capable agents that can inspect, edit, and execute it.

## User Scenarios & Testing

### User Story 1 - Launch a GitHub Notebook Locally (Priority: P1)

As a user, I can give the launcher a GitHub notebook URL or repository/ref/path tuple and open that notebook locally without manually cloning the repository or starting Jupyter.

**Independent Test**: Request a known public GitHub notebook, approve the source trust prompt when required, and verify the browser reaches that notebook in a local Jupyter session.

**Acceptance Scenarios**:

1. **Given** a valid public GitHub notebook URL, **When** the user launches it, **Then** the launcher resolves the requested ref to an immutable commit and opens the requested notebook.
2. **Given** valid `repo`, `ref`, and `path` fields, **When** the user launches them, **Then** the same flow works without a full GitHub URL.
3. **Given** a repository/revision that has not previously been trusted locally, **When** launch would begin code/environment execution, **Then** the launcher requires an explicit user trust decision before proceeding.
4. **Given** a previously trusted source under the configured trust policy, **When** it is launched again, **Then** the user does not have to repeat unnecessary setup steps.

### User Story 2 - Work in a Persistent Colab-Like Local Copy (Priority: P1)

As a user, I can edit and run a local working copy of the notebook, stop its runtime, and return later without losing notebook edits, execution outputs, or generated artifacts.

**Independent Test**: Launch a GitHub notebook, edit a cell, execute it, create an output file, stop the runtime, reopen the workspace, and verify the edit/output/file remain.

**Acceptance Scenarios**:

1. **Given** a new source launch, **When** the workspace is created, **Then** the launcher retains an immutable source snapshot and creates a separate persistent editable working copy.
2. **Given** an edited working notebook, **When** its runtime stops, **Then** the workspace survives independently of the runtime/container.
3. **Given** an existing workspace, **When** the user reopens it, **Then** the launcher starts a new runtime against the existing working copy instead of silently resetting it from GitHub.
4. **Given** an active notebook, **When** the user chooses Save a Copy, **Then** the current notebook including its current saved edits and optionally outputs is duplicated to a validated destination without modifying GitHub.
5. **Given** code that generates files, **When** it writes to the documented outputs location, **Then** those files persist with the workspace after runtime shutdown.

### User Story 3 - Reproduce the Repository Environment (Priority: P1)

As a user, I can run the notebook with dependencies declared by its repository rather than relying on the host Python environment.

**Independent Test**: Launch a repository whose notebook imports a dependency absent from the host but declared by the repository; verify the import succeeds.

**Acceptance Scenarios**:

1. **Given** a supported environment declaration, **When** launched for the first time, **Then** an isolated environment is prepared from that declaration.
2. **Given** the same immutable source and environment inputs, **When** launched again, **Then** the environment may be reused from cache.
3. **Given** environment preparation failure, **When** launch stops, **Then** the failure identifies the build phase and actionable cause.

### User Story 4 - Use the Local NVIDIA GPU (Priority: P1)

As a user with a configured NVIDIA container runtime, I can run the notebook and agent operations on the local GPU.

**Independent Test**: In the launched notebook verify `torch.cuda.is_available()` and device identity, then verify the same result through MCP.

**Acceptance Scenarios**:

1. `gpu=on` MUST require GPU capability and fail clearly when unavailable.
2. `gpu=auto` MUST use a GPU when usable and otherwise permit CPU execution.
3. `gpu=off` MUST not request GPU devices.

### User Story 5 - Let an Agent Inspect, Edit, and Run the Notebook (Priority: P1)

As a user of an MCP-capable coding agent, I can attach the agent to the same Jupyter runtime and let it inspect cells, edit the working notebook, execute cells/the notebook, observe outputs and failures, and repair problems.

**Independent Test**: Attach a supported MCP client, read the notebook, edit a controlled cell, run it, encounter a deliberate failure, repair it, and continue in the same kernel visible from JupyterLab.

**Acceptance Scenarios**:

1. A ready session exposes a non-secret MCP attachment descriptor.
2. The default writable agent profile can read, add, edit, delete, and execute notebook cells through the same Jupyter runtime/kernel.
3. A `readonly` agent profile can inspect notebook/cell/output state but cannot execute code or mutate notebook/workspace state.
4. Agent execution uses the same kernel, GPU policy, filesystem scope, sandbox, and persistent workspace as interactive execution.
5. Whole-notebook execution occurs in notebook cell order, preserves kernel state, records outputs in the working notebook when saved, and defaults to stop-on-error.
6. A cell exception is returned with cell-scoped context/traceback and does not automatically destroy the MCP/Jupyter session.
7. Kernel restart/reconnect remains available after failures.

### User Story 6 - Use Local Persistent Data Explicitly (Priority: P2)

As a user, I can explicitly mount a selected local directory into the sandbox as persistent user data, analogous to attaching external storage in a hosted notebook, without exposing my entire home directory.

**Independent Test**: Start a workspace with an explicitly selected local folder, verify only that folder is visible at the documented mount point, create/read a file according to the selected mount mode, and confirm unrelated host paths remain inaccessible.

**Acceptance Scenarios**:

1. No host user-data directory is mounted by default.
2. A user-selected local path may be mounted explicitly as read-only or read-write.
3. The mounted path appears at a stable sandbox location such as `/mnt/user-data`.
4. A launch URL from a repository MUST NOT be able to choose an arbitrary host mount path on the user's behalf.

### User Story 7 - Fail Safely on Invalid or Unsafe Inputs (Priority: P1)

As a user, malformed requests, untrusted sources, traversal attempts, or agent attachment errors cannot silently widen host access or mutate GitHub.

**Acceptance Scenarios**:

1. Unsupported hosts, malformed GitHub URLs, path traversal, and non-notebook targets are rejected before execution.
2. User-controlled values are passed to external processes as structured arguments, never shell-interpolated command strings.
3. The sandbox receives no host credentials, SSH keys, GitHub credentials, Docker socket, or unrelated host directories by default.
4. The launcher and MCP layer provide no commit, push, branch-creation, or GitHub mutation capability.
5. The editable workspace MUST NOT depend on a writable Git checkout connected to the source remote; GitHub is treated as immutable source input for this feature.
6. Cross-session MCP attachment and read-only-to-writable permission escalation are rejected.

### User Story 8 - Observe, Stop, and Reopen Work (Priority: P2)

As a user, I can observe launch/runtime/MCP state, stop disposable runtime resources, and later reopen the persistent workspace.

**Acceptance Scenarios**:

1. Status identifies source resolution, acquisition, workspace preparation, build/cache, sandbox startup, MCP preparation, and ready/failure phases.
2. Stopping a session invalidates MCP attachment and removes runtime-specific resources but does not delete the workspace or reusable environment cache.
3. Reopening the workspace creates a new runtime against the existing working copy.

### Edge Cases

- Branch names contain `/` and make naïve GitHub `/blob/` parsing ambiguous.
- Repository uses Git LFS or submodules.
- A source ref changes after resolution.
- A workspace exists but its cached runtime image was deleted.
- Working notebook was renamed or Save a Copy destination already exists.
- Workspace or output disk quota is exhausted.
- A user-data mount disappears between sessions.
- Notebook writes outside `/workspace`, `/outputs`, or explicitly mounted writable paths.
- Long-running/non-terminating cells, kernel death, MCP disconnect, or session stop during execution.
- Notebook output is very large, binary, or multimodal.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST expose a loopback-only local launch service by default.
- **FR-002**: The system MUST accept either a full GitHub notebook URL or explicit `repo`, `ref`, and `path` source fields.
- **FR-003**: The POC MUST accept only public `github.com` repositories as remote sources.
- **FR-004**: Mutable refs MUST resolve to an immutable commit SHA retained in provenance metadata.
- **FR-005**: New/untrusted source execution MUST require an explicit local trust decision before repository-supplied code or environment setup runs.
- **FR-006**: A fresh source launch MUST create a persistent `Workspace` separate from disposable runtime state.
- **FR-007**: A workspace MUST contain or reference an immutable source snapshot plus a separate editable working copy.
- **FR-008**: The working copy MUST persist notebook edits, saved cell outputs, generated files, and an `outputs/` area across runtime shutdown/restart.
- **FR-009**: The system MUST support reopening an existing workspace without resetting it from GitHub.
- **FR-010**: The system MUST support Save a Copy for a notebook to another validated path in the workspace and, when explicitly configured, to a mounted user-data location.
- **FR-011**: Save a Copy MUST NOT modify the original GitHub repository or source snapshot.
- **FR-012**: The launcher MUST NOT commit, push, create branches, or otherwise mutate GitHub for this feature.
- **FR-013**: The system MUST NOT inject GitHub credentials into notebook runtimes by default.
- **FR-014**: Environment preparation MUST be isolated from the host Python environment and SHOULD reuse cached environments for matching immutable identities.
- **FR-015**: The runtime MUST start a local Jupyter-compatible server and route the user directly to the requested working notebook.
- **FR-016**: GPU policy MUST support `auto`, `on`, and `off` and report GPU capability failures explicitly.
- **FR-017**: The `standard` sandbox MUST enforce the constitution's baseline isolation controls: non-root execution where supported, no-new-privileges, unnecessary capability removal, resource limits, minimal explicit mounts, no Docker socket, and loopback-only published services.
- **FR-018**: The standard sandbox MUST allow outbound network access by default for package/model/data retrieval while blocking inbound exposure except launcher-controlled loopback services.
- **FR-019**: No user-data directory is mounted by default; an optional user-selected local directory MAY be explicitly mounted at a stable path with an explicit `ro` or `rw` mode.
- **FR-020**: Remote launch URLs MUST NOT be able to select host mount paths.
- **FR-021**: External commands MUST use structured argument invocation without shell interpolation of untrusted values.
- **FR-022**: Every ready writable session MUST expose MCP attachment or a specific MCP-unavailable reason.
- **FR-023**: MCP MUST attach to the same Jupyter server/workspace/kernel lifecycle as the browser session.
- **FR-024**: The MCP contract MUST remain implementation-neutral.
- **FR-025**: The default agent profile MUST support notebook/cell read, insert, update, delete, single-cell execution, ordered whole-notebook execution, diagnostic kernel execution, output/error retrieval, and kernel restart/reconnect.
- **FR-026**: A `readonly` agent profile MUST support inspection only and MUST reject code execution and all mutation operations at the launcher/MCP boundary.
- **FR-027**: MCP attachment metadata MUST NOT expose raw Jupyter credentials.
- **FR-028**: Local MCP SHOULD default to stdio and MUST inherit the session sandbox/GPU/filesystem policy.
- **FR-029**: Whole-notebook execution MUST use the active Jupyter kernel, execute in notebook order, default to stop-on-error, and MUST NOT silently translate the notebook into an unrelated script/runtime.
- **FR-030**: Long-running agent execution MUST support timeout or cancellation.
- **FR-031**: MCP execution failures MUST be represented as structured execution results distinct from transport failure.
- **FR-032**: Agent attachment/execution lifecycle MUST produce sanitized audit metadata without copying full sensitive cell source/output into logs by default.
- **FR-033**: Stopping a runtime MUST invalidate MCP/private runtime credentials while preserving workspace and reusable environment artifacts.
- **FR-034**: The system MUST expose phase-level launch status and actionable, secret-safe errors.

### Key Entities

- **Launch Request**: Remote source or existing workspace selection plus runtime options such as GPU and agent profile.
- **Resolved Source**: Immutable GitHub repository/revision/notebook identity.
- **Workspace**: Persistent local state containing provenance, editable working files, outputs, and optional explicit user-data mount configuration.
- **Source Snapshot**: Immutable representation/check-out of the resolved source revision.
- **Working Copy**: Persistent mutable files used by Jupyter and MCP; not a mechanism for GitHub mutation.
- **Prepared Environment**: Reusable isolated runtime image/environment.
- **Notebook Session**: Disposable running Jupyter/container/kernel instance bound to a workspace.
- **MCP Attachment**: Non-secret session-scoped agent connection descriptor and capability profile.
- **Agent Execution Event**: Sanitized audit metadata for agent operations.

## Success Criteria

- **SC-001**: A supported GitHub notebook reaches the exact requested working notebook without manual clone/Jupyter startup.
- **SC-002**: An edit, saved output, and generated artifact survive runtime shutdown and workspace reopen.
- **SC-003**: Save a Copy creates a second persistent notebook without GitHub mutation.
- **SC-004**: A repository-declared dependency absent from the host imports successfully in the runtime.
- **SC-005**: Repeated immutable environment launches can skip rebuild on cache hit.
- **SC-006**: A configured WSL2/Linux NVIDIA host exposes the same GPU through browser and MCP execution.
- **SC-007**: A writable MCP agent reads, edits, executes, encounters a controlled failure, repairs it, and continues in the same notebook/kernel.
- **SC-008**: A readonly MCP agent cannot execute or mutate notebook/workspace state.
- **SC-009**: Runtime stop removes runtime/MCP resources while preserving workspace and environment cache.
- **SC-010**: Tests prove no GitHub mutation path, prohibited host mounts, cross-session attachment, path traversal, or shell injection through launcher-controlled interfaces.

## Assumptions

- Single local developer; WSL2/Linux-first.
- Public GitHub source only; private GitHub authentication is out of scope.
- User explicitly decides which public repositories are trusted enough to execute.
- Outbound Internet access is normally available from the sandbox.
- Persistent workspaces are stored on local disk under launcher-managed storage unless the user explicitly configures another local directory.
- BinderHub/JupyterHub, stronger `strict` sandboxes, multi-user identity/quotas, remote agents, and cloud deployment remain future-compatible but out of MVP scope.
