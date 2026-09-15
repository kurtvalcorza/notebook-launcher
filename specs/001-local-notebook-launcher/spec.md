# Feature Specification: Local Notebook Launcher

**Feature Branch**: `001-local-notebook-launcher`

**Created**: 2026-09-15

**Status**: Draft

**Input**: Launch a public GitHub-hosted `.ipynb` into a local Jupyter environment from a localhost URL, with access to a local NVIDIA GPU when the host is configured for GPU containers.

## User Scenarios & Testing

### User Story 1 - Launch a GitHub Notebook Locally (Priority: P1)

As a user, I can give the local launcher either a GitHub notebook URL or an explicit repository/ref/path tuple and have the requested notebook open in a local Jupyter interface without manually cloning the repository or navigating to the notebook.

**Why this priority**: This is the core product proposition and the local equivalent of the GitHub-to-Colab launch flow.

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

### User Story 4 - Fail Safely on Invalid or Unsafe Inputs (Priority: P1)

As a user, I receive clear errors for invalid launch requests, while malformed parameters cannot escape the repository workspace or become arbitrary host commands.

**Why this priority**: The launcher intentionally fetches and executes external code; input validation and isolation are baseline requirements, not later hardening.

**Independent Test**: Submit unsupported hosts, malformed GitHub URLs, path traversal attempts, nonexistent notebooks, and shell-metacharacter payloads; verify each is rejected before execution.

**Acceptance Scenarios**:

1. **Given** a source URL from an unsupported host, **When** it is submitted, **Then** the launcher rejects it before cloning or executing anything.
2. **Given** a notebook path containing traversal outside the repository root, **When** it is submitted, **Then** the launcher rejects it.
3. **Given** shell metacharacters or command-like text in parameters, **When** the request is processed, **Then** those values are handled as data and cannot cause arbitrary host command execution.
4. **Given** the requested repository, ref, or notebook does not exist, **When** launch resolution fails, **Then** the user receives a specific, actionable error.

---

### User Story 5 - Observe and Clean Up Sessions (Priority: P2)

As a user, I can understand which phase a launch is in and can stop or clean up a notebook session without manually identifying background processes or containers.

**Why this priority**: This makes repeated local use practical, but it is secondary to proving the launch path itself.

**Independent Test**: Start a launch, observe phase/status output, then terminate the session and verify its runtime resources are released while reusable cache artifacts remain intact.

**Acceptance Scenarios**:

1. **Given** a launch is in progress, **When** status is inspected, **Then** the current phase is identifiable as source resolution, repository acquisition, environment preparation, session startup, or ready.
2. **Given** a running session, **When** it is stopped, **Then** session-specific runtime resources are removed without deleting unrelated cached environments.

### Edge Cases

- The GitHub URL points to a directory, raw file, non-`.ipynb` file, issue, pull request, or other unsupported resource.
- The branch name contains `/` characters or otherwise makes naïve URL parsing ambiguous.
- The requested ref changes between resolution and acquisition.
- The repository is large or contains Git LFS/submodule references.
- The repository has no recognized environment declaration.
- Multiple launch requests target the same repository/revision simultaneously.
- The requested notebook exists in Git but not in the prepared runtime workspace.
- Jupyter starts but the requested kernel cannot be created.
- The configured GPU runtime exists but the notebook image lacks compatible CUDA/PyTorch dependencies.
- The browser is not available or automatic browser opening is disabled.

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
- **FR-017**: The system MUST NOT mount host credentials, SSH keys, tokens, or unrelated host directories into notebook sessions by default.
- **FR-018**: The system MUST provide phase-level launch status and phase-specific error information.
- **FR-019**: The system MUST support stopping a launched session and cleaning up its session-specific runtime resources.
- **FR-020**: The system MUST bind to loopback by default.
- **FR-021**: The system MUST preserve reusable cache artifacts independently from session cleanup.
- **FR-022**: The system MUST emit sufficient local logs to diagnose source, environment-build, session-startup, and GPU failures without exposing secrets.

### Key Entities

- **Launch Request**: User-supplied source reference consisting of either a GitHub notebook URL or repository/ref/path fields, plus runtime options such as GPU request.
- **Resolved Source**: Canonical repository identity, requested ref, immutable resolved revision, and normalized notebook path.
- **Environment Identity**: Stable identity for a prepared runtime environment derived from the source revision and environment-defining inputs.
- **Prepared Environment**: Isolated, reusable runtime capable of starting the repository notebook.
- **Notebook Session**: A running Jupyter-compatible server/kernel environment associated with a resolved source and prepared environment.
- **Launch Status**: Current lifecycle state and diagnostic information for a launch/session.

## Success Criteria

### Measurable Outcomes

- **SC-001**: A user can open a supported public GitHub `.ipynb` from a localhost launch URL and reach that exact notebook without manually cloning the repository or starting Jupyter.
- **SC-002**: A notebook can successfully import at least one dependency supplied by its repository environment that is not installed in the launcher host environment.
- **SC-003**: On a configured WSL2 NVIDIA host, a launched compatible notebook reports CUDA available and identifies the local GPU.
- **SC-004**: A repeat launch of an unchanged immutable source can reuse the previously prepared environment, demonstrated by skipping the environment-build phase.
- **SC-005**: All defined invalid/unsafe input tests are rejected before arbitrary repository code or unintended host commands can execute.
- **SC-006**: A failed launch identifies the failing lifecycle phase and an actionable cause.
- **SC-007**: Stopping a session releases its runtime resources while leaving reusable environment cache data intact.

## Assumptions

- The initial user is a single developer operating the launcher locally.
- Windows 11 with WSL2 Ubuntu is the primary POC host; native Linux should remain feasible.
- NVIDIA GPU support is already functional in WSL2 and the container runtime before GPU-specific acceptance testing.
- Internet access to GitHub and dependency/package sources is available during source acquisition and initial environment preparation.
- Public GitHub repositories are sufficient for the first POC; private repository authentication is not required.
- The first release does not provide multi-user authentication, public Internet exposure, distributed scheduling, GPU quotas, collaborative notebook editing, or cloud deployment.
- BinderHub/JupyterHub integration is a future-compatible backend direction, not a requirement for proving the smallest local single-user launch path.
