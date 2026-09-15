# Feature Specification: Local Notebook Launcher

**Feature Branch**: `001-local-notebook-launcher`

**Created**: 2026-09-15

**Status**: Draft

**Input**: Launch a public GitHub-hosted `.ipynb` into a local Jupyter environment from a localhost URL, with access to a local NVIDIA GPU when configured, and expose the running notebook/kernel to MCP-capable agents so they can inspect and execute it.

## User Scenarios & Testing

### User Story 1 - Launch a GitHub Notebook Locally (Priority: P1)

As a user, I can give the local launcher either a GitHub notebook URL or an explicit repository/ref/path tuple and have the requested notebook open in a local Jupyter interface without manually cloning the repository or navigating to the notebook.

**Why this priority**: This is the core product proposition and the local equivalent of the GitHub-to-hosted-notebook launch flow.

**Independent Test**: Start the launcher, request a known public GitHub `.ipynb`, and verify that the browser reaches a running Jupyter session with that exact notebook open.

**Acceptance Scenarios**:

1. **Given** a valid public GitHub notebook URL, **When** the user opens `http://localhost:<port>/open?url=<github-notebook-url>`, **Then** the launcher resolves the repository/ref/path, prepares the session, and opens the requested notebook.
2. **Given** valid `repo`, `ref`, and `path` parameters, **When** the user calls `/open`, **Then** the same notebook is launched without requiring a full GitHub URL.
3. **Given** a branch or tag reference, **When** the source is resolved, **Then** the launch record identifies the immutable source revision actually used.

---

### User Story 2 - Reproduce the Repository Environment (Priority: P1)

As a user, I can launch a notebook with the dependencies declared by its repository rather than relying on packages installed in my host Python environment.

**Why this priority**: A one-click launcher is only useful when notebooks run in a predictable environment.

**Independent Test**: Launch a repository whose notebook imports a dependency absent from the host but declared by the repository; verify the import succeeds in the launched session.

**Acceptance Scenarios**:

1. **Given** a repository with a supported environment declaration, **When** it is launched for the first time, **Then** the launcher prepares an isolated environment from that declaration.
2. **Given** the same immutable repository revision and unchanged environment inputs, **When** it is launched again, **Then** the launcher may reuse a cached prepared environment rather than rebuilding it.
3. **Given** environment preparation fails, **When** the launch cannot continue, **Then** the user receives a phase-specific error rather than a generic notebook failure.

---

### User Story 3 - Use the Local NVIDIA GPU (Priority: P1)

As a user with a correctly configured NVIDIA GPU environment, I can run a launched notebook whose kernel can access that GPU.

**Why this priority**: Local GPU execution is a primary reason to use this launcher instead of a remote notebook service.

**Independent Test**: From a launched compatible notebook, verify `torch.cuda.is_available()` is true and that the detected GPU matches the local device.

**Acceptance Scenarios**:

1. **Given** a host with working WSL2 GPU support and GPU-enabled containers, **When** a GPU-capable notebook session is launched with GPU access, **Then** CUDA is available inside the notebook environment.
2. **Given** GPU access is requested but unavailable, **When** the launcher validates or starts the session, **Then** it reports a clear GPU capability error and does not falsely report successful GPU enablement.
3. **Given** a notebook does not require GPU access, **When** it is launched, **Then** the launcher can run it without making GPU availability a mandatory prerequisite.

---

### User Story 4 - Let an Agent Run the Launched Notebook (Priority: P1)

As a user of an MCP-capable coding agent, I can attach that agent to the same launched notebook/kernel and let it inspect cells, execute the notebook, observe outputs and failures, and continue interacting with the kernel without copying notebook content into the agent manually.

**Why this priority**: Agent-operable execution is a first-class goal of the launcher, not an optional integration. The useful end state is GitHub notebook -> local compute -> agent-controlled execution.

**Independent Test**: Launch a known notebook, attach an MCP client through the launcher-provided session descriptor, have the client read the notebook and execute it, and verify that outputs are produced by the same kernel visible in JupyterLab.

**Acceptance Scenarios**:

1. **Given** a ready notebook session, **When** the user requests its MCP attachment descriptor, **Then** the launcher returns enough non-secret information for a supported MCP client to attach to that session without exposing raw Jupyter credentials.
2. **Given** an attached MCP client, **When** the agent reads the notebook, **Then** it can inspect notebook cells and current outputs from the launched session.
3. **Given** an attached MCP client, **When** the agent executes one cell or the complete notebook, **Then** execution occurs in the same Jupyter kernel used by the browser session and the resulting outputs are observable through MCP and JupyterLab.
4. **Given** a cell raises an exception, **When** execution finishes, **Then** the agent receives the failed cell context and error output and may continue interacting with or restart the kernel.
5. **Given** a ready GPU-enabled session, **When** the agent executes a GPU capability check through MCP, **Then** it observes the same GPU availability as code run interactively in the notebook.
6. **Given** an MCP client disconnects, **When** the notebook session remains running, **Then** the Jupyter session and kernel remain usable and may be reattached.

---

### User Story 5 - Fail Safely on Invalid or Unsafe Inputs (Priority: P1)

As a user, I receive clear errors for invalid launch or attachment requests, while malformed parameters cannot escape the repository workspace, become arbitrary host commands, or grant the agent broader host access than the launched notebook runtime.

**Why this priority**: The launcher intentionally fetches and executes external code and permits agents to drive that execution; input validation and isolation are baseline requirements.

**Independent Test**: Submit unsupported hosts, malformed GitHub URLs, traversal attempts, shell-metacharacter payloads, invalid session IDs, and attempted MCP access outside the launched workspace; verify each is rejected or remains confined to the notebook runtime.

**Acceptance Scenarios**:

1. **Given** a source URL from an unsupported host, **When** it is submitted, **Then** the launcher rejects it before cloning or executing anything.
2. **Given** a notebook path containing traversal outside the repository root, **When** it is submitted, **Then** the launcher rejects it.
3. **Given** shell metacharacters or command-like text in parameters, **When** the request is processed, **Then** those values are handled as data and cannot cause arbitrary host command execution.
4. **Given** the requested repository, ref, notebook, or session does not exist, **When** resolution or attachment fails, **Then** the user receives a specific, actionable error.
5. **Given** an MCP-capable agent attached to a notebook session, **When** it executes notebook/kernel operations, **Then** it has no implicit access to host credentials, the Docker socket, unrelated host directories, or another notebook session.

---

### User Story 6 - Observe and Clean Up Sessions (Priority: P2)

As a user, I can understand which phase a launch is in and can stop or clean up a notebook session and its MCP attachment resources without manually identifying background processes or containers.

**Why this priority**: This makes repeated local use practical, but it is secondary to proving launch, GPU, and agent execution.

**Independent Test**: Start a launch, observe phase/status output, attach an MCP client, then terminate the session and verify runtime and attachment resources are released while reusable cache artifacts remain intact.

**Acceptance Scenarios**:

1. **Given** a launch is in progress, **When** status is inspected, **Then** the current phase is identifiable as source resolution, repository acquisition, environment preparation, session startup, MCP preparation, or ready.
2. **Given** a running session, **When** it is stopped, **Then** session-specific notebook and MCP resources are removed without deleting unrelated cached environments.
3. **Given** an MCP attachment attempt after the owning notebook session has stopped, **When** the client connects, **Then** attachment fails cleanly rather than creating an orphan execution context.

### Edge Cases

- The GitHub URL points to a directory, raw file, non-`.ipynb` file, issue, pull request, or other unsupported resource.
- The branch name contains `/` characters or otherwise makes naive URL parsing ambiguous.
- The requested ref changes between resolution and acquisition.
- The repository is large or contains Git LFS/submodule references.
- The repository has no recognized environment declaration.
- Multiple launch requests target the same repository/revision simultaneously.
- The requested notebook exists in Git but not in the prepared runtime workspace.
- Jupyter starts but the requested kernel cannot be created.
- The configured GPU runtime exists but the notebook image lacks compatible CUDA/PyTorch dependencies.
- The browser is unavailable or automatic browser opening is disabled.
- An MCP client requests attachment before the Jupyter server/kernel is ready.
- An MCP backend is installed but incompatible with the running Jupyter version.
- The agent executes long-running or non-terminating cells.
- The agent restarts the kernel while another browser execution is active.
- Notebook outputs are large, binary, or multimodal.
- The MCP client disconnects during execution.
- The notebook session stops while an MCP tool call is in flight.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST expose a localhost HTTP launch endpoint.
- **FR-002**: The launch endpoint MUST accept a full GitHub notebook URL using a `url` parameter.
- **FR-003**: The launch endpoint MUST also accept explicit `repo`, `ref`, and `path` parameters.
- **FR-004**: The POC MUST support public repositories hosted on `github.com` and MUST reject unsupported source hosts.
- **FR-005**: The system MUST validate that the requested target is a repository-relative `.ipynb` path before environment preparation or notebook execution.
- **FR-006**: The system MUST prevent repository-relative paths from escaping the acquired repository workspace.
- **FR-007**: The system MUST resolve mutable Git references to an immutable source revision when practical and retain that revision in launch metadata.
- **FR-008**: The system MUST acquire the requested repository/revision without requiring the user to clone it manually.
- **FR-009**: The system MUST prepare an isolated execution environment using supported repository environment declarations.
- **FR-010**: The system MUST avoid depending on the host Python environment for notebook dependencies, except for launcher/runtime prerequisites.
- **FR-011**: The system SHOULD reuse an existing prepared environment when its cache identity matches the same immutable source and relevant environment inputs.
- **FR-012**: The system MUST start a local Jupyter-compatible session and route the user directly to the requested notebook.
- **FR-013**: The system MUST support explicit GPU-enabled session launch on compatible hosts.
- **FR-014**: The system MUST report GPU unavailability or runtime incompatibility explicitly when GPU access is requested.
- **FR-015**: The system MUST permit CPU-only launches without requiring an NVIDIA GPU.
- **FR-016**: The system MUST invoke external tools without interpolating user-controlled values into shell command strings.
- **FR-017**: The system MUST NOT mount host credentials, SSH keys, tokens, Docker socket, or unrelated host directories into notebook sessions by default.
- **FR-018**: The system MUST provide phase-level launch status and phase-specific error information.
- **FR-019**: The system MUST support stopping a launched session and cleaning up its session-specific runtime resources.
- **FR-020**: The system MUST bind network listeners to loopback by default.
- **FR-021**: The system MUST preserve reusable cache artifacts independently from session cleanup.
- **FR-022**: The system MUST emit sufficient local logs to diagnose source, environment-build, session-startup, GPU, and MCP failures without exposing secrets.
- **FR-023**: Every ready notebook session MUST expose an MCP attachment capability or an explicit reason that MCP is unavailable.
- **FR-024**: MCP attachment MUST target the same Jupyter server, notebook workspace, and kernel lifecycle as the interactive notebook session; the system MUST NOT silently create a second execution environment for agent operations.
- **FR-025**: The MCP capability contract MUST be implementation-neutral so the launcher can use a Jupyter-native MCP server, an adapted Colab-style implementation, or another compliant backend without changing the launcher/session contract.
- **FR-026**: The P1 MCP capability profile MUST support notebook inspection, cell inspection, cell execution, whole-notebook execution, arbitrary code execution in the active kernel, output/error retrieval, and kernel restart/reconnect.
- **FR-027**: The system SHOULD support cell insertion/edit/delete operations for agent-assisted remediation; if the selected MCP backend does not provide mutation, it MUST advertise that capability as unavailable rather than emulate it unsafely.
- **FR-028**: MCP attachment metadata MUST NOT expose raw Jupyter authentication tokens in ordinary API responses, logs, or browser-visible status pages.
- **FR-029**: Local MCP transport SHOULD default to stdio for the POC so no additional externally reachable listener is required; alternative MCP transports MAY be added behind the same attachment descriptor.
- **FR-030**: The launcher MUST provide a stable session-scoped MCP invocation or descriptor that supported clients can configure without directly managing Jupyter credentials.
- **FR-031**: MCP operations MUST inherit the notebook session's compute policy, including GPU access, filesystem scope, and container isolation.
- **FR-032**: Stopping a notebook session MUST invalidate its MCP attachment path and terminate launcher-owned MCP bridge processes/resources associated with that session.
- **FR-033**: The system MUST record an audit event for MCP attachment lifecycle and code/cell execution requests sufficient to identify session, operation, timestamp, success/failure, and duration without logging secrets or complete sensitive notebook contents by default.
- **FR-034**: MCP execution MUST surface cell/code failures as structured tool errors or execution results rather than collapsing them into generic transport failure.
- **FR-035**: Long-running MCP executions MUST support a bounded timeout or cancellation path so an agent cannot permanently wedge launcher orchestration.

### Key Entities

- **Launch Request**: User-supplied source reference consisting of either a GitHub notebook URL or repository/ref/path fields, plus runtime options such as GPU request.
- **Resolved Source**: Canonical repository identity, requested ref, immutable resolved revision, and normalized notebook path.
- **Environment Identity**: Stable identity for a prepared runtime environment derived from the source revision and environment-defining inputs.
- **Prepared Environment**: Isolated, reusable runtime capable of starting the repository notebook.
- **Notebook Session**: A running Jupyter-compatible server/kernel environment associated with a resolved source and prepared environment.
- **MCP Attachment**: Session-scoped description of how an MCP client reaches the notebook runtime, which capability profile is available, and which backend/transport is active without exposing raw Jupyter credentials.
- **Agent Execution Event**: Sanitized audit record for an MCP-driven notebook or kernel operation.
- **Launch Status**: Current lifecycle state and diagnostic information for a launch/session.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A user can open a supported public GitHub `.ipynb` from a localhost launch URL and reach that exact notebook without manually cloning the repository or starting Jupyter.
- **SC-002**: A notebook can successfully import at least one dependency supplied by its repository environment that is not installed in the launcher host environment.
- **SC-003**: On a configured WSL2 NVIDIA host, a launched compatible notebook reports CUDA available and identifies the local GPU.
- **SC-004**: A repeat launch of an unchanged immutable source can reuse the previously prepared environment, demonstrated by skipping the environment-build phase.
- **SC-005**: A supported MCP client can attach to a ready session, read the requested notebook, execute at least one cell and the full notebook, and retrieve execution output from the same Jupyter kernel visible in the browser.
- **SC-006**: On a GPU-enabled session, an MCP-driven `torch.cuda.is_available()` check returns the same GPU capability observed from interactive notebook execution.
- **SC-007**: A deliberately failing notebook cell produces a cell-scoped execution failure visible to the agent without destroying the notebook session; the agent can subsequently execute another cell or restart the kernel.
- **SC-008**: All defined invalid/unsafe input and session-attachment tests are rejected before unintended host commands or cross-session access can occur.
- **SC-009**: A failed launch or MCP attachment identifies the failing lifecycle phase and an actionable cause.
- **SC-010**: Stopping a session releases its runtime and launcher-owned MCP resources while leaving reusable environment cache data intact.

## Assumptions

- The initial user is a single developer operating the launcher locally.
- Windows 11 with WSL2 Ubuntu is the primary POC host; native Linux should remain feasible.
- NVIDIA GPU support is already functional in WSL2 and the container runtime before GPU-specific acceptance testing.
- Internet access to GitHub and dependency/package sources is available during source acquisition and initial environment preparation.
- Public GitHub repositories are sufficient for the first POC; private repository authentication is not required.
- At least one MCP-capable client such as a coding agent is available for acceptance testing.
- The first implementation may select an existing Jupyter MCP server as the default backend; that implementation choice is not part of the public launcher contract.
- The first release does not provide multi-user authentication, public Internet exposure, distributed scheduling, GPU quotas, collaborative notebook editing, or cloud deployment.
- BinderHub/JupyterHub integration is a future-compatible backend direction, not a requirement for proving the local single-user launch and MCP execution path.
