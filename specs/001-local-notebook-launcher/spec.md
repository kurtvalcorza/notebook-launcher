# Feature Specification: Local Notebook Launcher

**Feature Branch**: `001-local-notebook-launcher`

**Created**: 2026-09-15

**Status**: Draft

**Input**: User description: "Create a local, Colab-like launcher for user-trusted public GitHub notebooks. The user should be able to open, run, edit, save, stop, and reopen a persistent local copy; optionally use a local GPU; and let MCP-capable agents inspect, edit, execute, diagnose, and repair the same notebook session. The feature must never commit, push, create branches, or otherwise modify GitHub."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open a Trusted GitHub Notebook Locally (Priority: P1)

As a user, I can open a public GitHub notebook locally from its notebook reference without manually cloning the repository or starting a notebook server myself.

**Why this priority**: This is the primary entry point and the local equivalent of opening a GitHub notebook in a hosted notebook service.

**Independent Test**: Provide a valid public GitHub notebook reference, approve the source when prompted, and verify that the exact requested notebook opens locally.

**Acceptance Scenarios**:

1. **Given** a valid public GitHub notebook reference, **When** the user launches it, **Then** the exact requested notebook opens in a local notebook session.
2. **Given** a mutable source reference such as a branch or tag, **When** launch begins, **Then** the session records the exact immutable source revision used.
3. **Given** a source revision that has not yet been trusted locally, **When** execution would begin, **Then** the user must explicitly approve that revision before repository-supplied code or setup instructions run.
4. **Given** the user declines trust, **When** the request is cancelled, **Then** no notebook code or repository-supplied setup is executed.

---

### User Story 2 - Keep a Persistent, Editable Local Copy (Priority: P1)

As a user, I can work in a persistent local copy of the notebook, stop the running session, and later reopen my work without losing notebook edits, saved outputs, or generated artifacts.

**Why this priority**: A local notebook launcher should behave like a practical working environment rather than a disposable viewer.

**Independent Test**: Open a notebook, edit and run a cell, create an output artifact, stop the session, reopen the same workspace, and verify that the edit, saved output, and artifact are still present.

**Acceptance Scenarios**:

1. **Given** a first launch from a trusted source revision, **When** the workspace is created, **Then** the original source provenance is retained separately from the editable working copy.
2. **Given** an edited working notebook, **When** the running session stops, **Then** the workspace and its saved contents remain available.
3. **Given** an existing workspace, **When** the user reopens it, **Then** the user's existing working copy is reopened rather than reset from the remote source.
4. **Given** an active notebook, **When** the user chooses Save a Copy, **Then** a distinct notebook copy is created at a valid local destination without altering the original source.
5. **Given** a Save a Copy destination that already exists, **When** overwrite has not been explicitly requested, **Then** the existing file is preserved and the user is asked to choose another destination or explicitly overwrite it.
6. **Given** notebook-generated artifacts, **When** they are saved to the workspace's output area, **Then** they survive session shutdown and reopen.

---

### User Story 3 - Let an Agent Operate the Same Notebook (Priority: P1)

As a user of an MCP-capable coding agent, I can attach the agent to the same notebook session I am using and let it inspect, edit, execute, diagnose, and repair the notebook without creating a separate hidden execution context.

**Why this priority**: Agent-operated notebook execution is a mandatory part of the MVP and a core reason for the launcher to exist beyond ordinary local notebook use.

**Independent Test**: Attach a supported agent, have it read and edit a controlled cell, execute the notebook, encounter a deliberate failure, repair the failure, and continue in the same session visible to the user.

**Acceptance Scenarios**:

1. **Given** a ready notebook session, **When** the user connects a supported MCP-capable agent, **Then** the agent attaches to that same notebook workspace and execution state.
2. **Given** the default writable agent profile, **When** the agent operates the notebook, **Then** it can read, add, edit, delete, and execute notebook cells within the allowed workspace.
3. **Given** the read-only agent profile, **When** the agent inspects the notebook, **Then** it can read notebook, cell, and output state but cannot execute code or modify notebook or workspace contents.
4. **Given** a whole-notebook execution request, **When** the agent runs it, **Then** code cells execute in notebook order using the active notebook state and stop at the first error by default.
5. **Given** a cell failure, **When** execution returns, **Then** the failing cell and error context are available to the agent without automatically destroying the session.
6. **Given** an agent reconnects after a client disconnect, **When** the notebook session is still active, **Then** the same workspace and current notebook state remain available.
7. **Given** the notebook session has stopped, **When** an agent attempts to reconnect, **Then** the attachment is rejected rather than creating an orphan execution environment.

---

### User Story 4 - Use Local GPU Compute When Available (Priority: P1)

As a user with a supported local GPU configuration, I can choose whether notebook execution should use the GPU, require the GPU, or remain CPU-only, and agent-driven execution observes the same compute availability as interactive execution.

**Why this priority**: Local accelerator use is a primary advantage over remote notebook services for the intended workflows.

**Independent Test**: Launch a compatible notebook with GPU required, verify that interactive execution detects the local GPU, then verify the attached agent observes the same result.

**Acceptance Scenarios**:

1. **Given** automatic GPU selection, **When** a usable supported GPU is available, **Then** the notebook may use it; otherwise the notebook remains usable on CPU.
2. **Given** GPU-required mode, **When** no usable GPU is available, **Then** launch fails with a clear capability explanation rather than silently falling back to CPU.
3. **Given** CPU-only mode, **When** the notebook launches, **Then** GPU access is not granted to the session.
4. **Given** an agent attached to a GPU-enabled session, **When** it checks compute capability, **Then** it observes the same GPU availability as interactive notebook execution.

---

### User Story 5 - Keep Local Execution Contained (Priority: P1)

As a user, I can run a trusted public notebook with a constrained local execution boundary so that repository code and agent actions do not automatically gain unrelated access to my machine or remote source repository.

**Why this priority**: Running notebook code and allowing an agent to edit and execute it are intentionally powerful operations; the feature must constrain the blast radius by default.

**Independent Test**: Launch a trusted notebook and verify that it can access only the workspace and explicitly granted resources, cannot access unrelated host credentials or directories, and has no capability to mutate the remote GitHub repository.

**Acceptance Scenarios**:

1. **Given** a normal launch, **When** the notebook runs, **Then** only the workspace and explicitly granted local resources are available to it by default.
2. **Given** no explicit local data grant, **When** the notebook runs, **Then** the user's home directory, credentials, unrelated files, and local management interfaces are not implicitly exposed.
3. **Given** a remote GitHub source, **When** notebook or agent activity occurs, **Then** the feature does not commit, push, create branches, or otherwise modify GitHub.
4. **Given** malformed paths, unsupported sources, or cross-session attachment attempts, **When** they are submitted, **Then** they are rejected before they can widen the allowed execution scope.
5. **Given** a user-controlled value containing command-like text, **When** it is processed by the launcher, **Then** it is treated as data rather than as executable host commands.
6. **Given** the standard local isolation mode, **When** a repository is launched, **Then** the product presents it as defense-in-depth for user-trusted repositories and does not claim safe containment of arbitrary malicious code.

---

### User Story 6 - Attach Explicit Local Data Storage (Priority: P2)

As a user, I can explicitly make one selected local directory available to the notebook as persistent data storage, similar to attaching external storage in a hosted notebook, without exposing my entire home directory.

**Why this priority**: Persistent datasets and outputs are useful for real notebook work, but the core source-to-workspace-to-agent flow can operate without an additional host directory.

**Independent Test**: Explicitly select a local folder, grant it read-only or read-write access, verify that the selected folder is accessible from the notebook, and verify that unrelated host directories remain inaccessible.

**Acceptance Scenarios**:

1. **Given** no local folder has been selected, **When** a session launches, **Then** no additional user-data directory is exposed.
2. **Given** a selected folder with read-only access, **When** the notebook uses it, **Then** files can be read but not modified through the session.
3. **Given** a selected folder with read-write access, **When** the notebook uses it, **Then** files inside that folder may be created or changed according to normal filesystem permissions.
4. **Given** a remote notebook launch reference, **When** it is opened, **Then** it cannot select or change the host folder on the user's behalf.
5. **Given** a previously configured folder is no longer available, **When** the workspace reopens, **Then** the missing mount is reported clearly and unrelated host paths are not substituted automatically.

---

### User Story 7 - Stop and Reopen Work Predictably (Priority: P2)

As a user, I can see whether a launch is preparing, ready, failed, or stopped; stop disposable execution resources; and later reopen the persistent workspace.

**Why this priority**: Clear lifecycle behavior makes repeated local use understandable and prevents the user from having to manage hidden background processes manually.

**Independent Test**: Start a launch, observe its status, stop it, verify that active execution and agent attachment end, then reopen the workspace and verify the saved working copy remains.

**Acceptance Scenarios**:

1. **Given** a launch in progress, **When** the user checks status, **Then** the current high-level phase and any actionable failure reason are visible.
2. **Given** an active session, **When** the user stops it, **Then** active execution and agent attachment are invalidated while persistent workspace contents remain.
3. **Given** a stopped workspace, **When** the user reopens it, **Then** a new execution session starts against the existing working copy.

### Edge Cases

- The supplied source points to a directory, issue, pull request, raw non-notebook file, or another unsupported target.
- The requested notebook or source revision no longer exists.
- A source reference changes after it has been resolved for a launch.
- The user declines the trust prompt for a new source revision.
- A working notebook is renamed or moved within the allowed workspace.
- A Save a Copy destination already exists or is outside the allowed destination scope.
- The local workspace or output storage runs out of disk space.
- An explicitly attached local data folder disappears or becomes unavailable between sessions.
- A notebook or agent operation attempts to access files outside the allowed workspace and explicit data grants.
- A long-running or non-terminating cell exceeds its allowed execution time.
- The notebook kernel terminates unexpectedly during interactive or agent execution.
- The agent disconnects during execution or attempts to reconnect after the notebook session has stopped.
- Notebook output is unusually large, binary, or multimodal.
- A required GPU becomes unavailable between launch and execution.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a supported public GitHub notebook reference and open the exact requested notebook locally.
- **FR-002**: The system MUST record the exact immutable source revision used for each fresh remote-source workspace.
- **FR-003**: The system MUST require an explicit local trust decision before executing a source revision that has not previously been approved under the local trust policy.
- **FR-004**: Declining trust MUST prevent repository-supplied notebook code and setup instructions from executing.
- **FR-005**: A fresh remote-source launch MUST create a new persistent workspace by default rather than silently reuse a prior edited workspace.
- **FR-006**: Each workspace MUST retain source provenance separately from the editable working copy.
- **FR-007**: Notebook edits, saved cell outputs, workspace files, and generated artifacts MUST persist after the active execution session stops.
- **FR-008**: Users MUST be able to reopen an existing workspace without resetting its working copy from the remote source.
- **FR-009**: Users MUST be able to save a distinct copy of the current notebook within an allowed persistent destination.
- **FR-010**: Save a Copy MUST preserve an existing destination unless overwrite is explicitly requested.
- **FR-011**: The feature MUST NOT commit, push, create branches, or otherwise modify the remote GitHub repository.
- **FR-012**: The feature MUST NOT require or inject GitHub write credentials for ordinary notebook launch, workspace use, or agent operation.
- **FR-013**: Notebook dependencies declared by the source project MUST be usable without relying on unrelated packages installed in the user's host Python environment.
- **FR-014**: Reopening or relaunching an unchanged source environment SHOULD reuse previously prepared reusable artifacts when available.
- **FR-015**: The default agent capability profile MUST permit notebook-cell inspection, insertion, editing, deletion, and execution within the current workspace.
- **FR-016**: A read-only agent profile MUST permit inspection but MUST reject code execution and all notebook or workspace mutation.
- **FR-017**: Agent operations MUST use the same active notebook workspace and execution state visible to the user rather than creating an unrelated hidden notebook runtime.
- **FR-018**: Whole-notebook agent execution MUST process code cells in notebook order and stop on the first execution error by default.
- **FR-019**: Cell execution failures MUST identify the failing cell and provide actionable error context while leaving the session usable when the execution environment itself remains healthy.
- **FR-020**: Long-running agent execution MUST provide a timeout or cancellation path.
- **FR-021**: Stopping a notebook session MUST invalidate active agent attachment while preserving the persistent workspace.
- **FR-022**: Users MUST be able to choose automatic GPU use, required GPU use, or CPU-only execution.
- **FR-023**: Required GPU mode MUST fail clearly when a supported usable GPU is unavailable rather than silently switching to CPU.
- **FR-024**: Interactive and agent-driven execution in the same session MUST observe the same compute capability and workspace state.
- **FR-025**: Local execution MUST be isolated from unrelated host resources by default and MUST expose only the workspace and resources explicitly granted by the user.
- **FR-026**: Host credentials, unrelated host directories, local management interfaces, and other notebook sessions MUST NOT be exposed by default.
- **FR-027**: The standard isolation mode MUST be presented as defense-in-depth for user-trusted repositories, not as a guarantee for arbitrary adversarial code.
- **FR-028**: The system MUST reject unsupported remote hosts, malformed notebook references, path traversal outside allowed scopes, and cross-session agent attachment attempts.
- **FR-029**: User-controlled source, path, session, and permission values MUST be treated as data and MUST NOT become executable host command text.
- **FR-030**: Outbound network access from the notebook session MAY be allowed by default for package, model, and data retrieval, while inbound exposure MUST remain local to the user's machine unless explicitly configured otherwise.
- **FR-031**: No additional host data directory MUST be exposed by default.
- **FR-032**: Users MAY explicitly grant one selected local directory to a workspace as read-only or read-write persistent data storage.
- **FR-033**: A remote notebook reference MUST NOT be able to select, replace, or broaden the host data directory grant.
- **FR-034**: The entire home directory MUST NOT be granted automatically merely for convenience.
- **FR-035**: The system MUST provide a persistent output area for generated files and artifacts that is not removed when the execution session stops.
- **FR-036**: The system MUST expose clear launch/session status and actionable, secret-safe failure information.
- **FR-037**: Agent attachment and execution activity MUST produce bounded audit metadata sufficient to identify the workspace/session, operation, timing, and success or failure without copying complete notebook content into logs by default.
- **FR-038**: Permission enforcement for writable and read-only agent profiles MUST occur at the launcher/agent-control boundary and MUST NOT rely only on agent instructions or prompt compliance.
- **FR-039**: A read-only agent MUST NOT be able to promote itself to writable access without a new explicit local authorization decision.
- **FR-040**: Persistent workspace data MUST remain until the user explicitly deletes or replaces it; stopping an execution session MUST NOT be treated as workspace deletion.

### Key Entities *(include if feature involves data)*

- **Remote Notebook Source**: The public GitHub repository, requested reference, notebook path, and immutable source revision used to establish provenance.
- **Trust Decision**: The user's local approval or rejection of executing a specific previously unseen source revision.
- **Workspace**: Persistent local state for one launched source, including provenance, editable files, notebook copies, outputs, and workspace preferences.
- **Working Notebook**: The editable local notebook opened and saved by the user and agent; it is separate from immutable source provenance.
- **Output Artifact**: A persistent file produced by notebook execution, such as a model, table, image, checkpoint, report, or exported data file.
- **Notebook Session**: The disposable active execution context for a workspace; it may be stopped and recreated without deleting workspace data.
- **Agent Capability Profile**: The effective permission set for an attached agent, including the default writable profile and the read-only inspection profile.
- **Local Data Grant**: An optional explicit user-selected local directory and its read-only or read-write access mode.
- **Agent Activity Record**: Sanitized metadata describing an agent attachment or execution operation without storing full notebook contents by default.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can go from a supported public GitHub notebook reference to the exact local working notebook without manually cloning the repository or manually starting notebook infrastructure.
- **SC-002**: In the persistence acceptance test, 100% of the tested notebook edit, saved cell output, and generated artifact remain after session stop and workspace reopen.
- **SC-003**: Save a Copy produces a distinct persistent notebook containing the current saved notebook state while the original remote source remains unchanged.
- **SC-004**: A representative source-declared dependency that is absent from the user's unrelated host environment is available in the launched notebook workspace.
- **SC-005**: A writable MCP-capable agent can read, edit, execute, diagnose a controlled failure, repair it, and continue in the same notebook state observed by the user.
- **SC-006**: In read-only mode, 100% of acceptance-test attempts to execute code or mutate notebook/workspace contents are denied while notebook inspection remains available.
- **SC-007**: On a supported configured GPU host, interactive and agent-driven checks in the same session report the same GPU availability and device identity.
- **SC-008**: In the security acceptance suite, unsupported sources, path traversal, cross-session attachment, prohibited host-resource access, and command-injection attempts are rejected or remain outside the granted execution scope.
- **SC-009**: Stopping an active session invalidates agent access and releases disposable execution resources while preserving the tested workspace contents and reusable prepared artifacts.
- **SC-010**: An explicitly granted local data folder exposes only the selected folder at the requested access level; unrelated host directories remain unavailable through the feature.
- **SC-011**: For an unseen source revision, execution does not begin until the user approves it; rejecting the prompt results in zero repository-supplied notebook or setup execution.
- **SC-012**: A user can complete the primary source-to-open, edit/run, agent-assist, stop, and reopen workflow without performing Git write operations or managing notebook-server credentials manually.

## Assumptions

- The initial user is a single developer operating the launcher on their own machine.
- WSL2/Linux is the first supported local execution environment; native Linux remains an intended compatible target.
- Public GitHub repositories are sufficient for the MVP; private-repository authentication is out of scope.
- Users decide which public repositories they trust enough to execute locally; public availability alone does not imply trust.
- The user's supported GPU environment, when used, is configured before GPU-specific acceptance testing.
- Internet access is normally available for source retrieval and notebook-required packages, models, or data.
- Persistent workspaces are stored locally and remain until the user explicitly deletes them.
- Additional host data access is opt-in and limited to a user-selected directory.
- Multi-user hosting, public Internet exposure, distributed scheduling, cloud deployment, remote agents, and hardened arbitrary-hostile-code execution are outside the MVP scope.
