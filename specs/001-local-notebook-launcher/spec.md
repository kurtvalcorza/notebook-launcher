# Feature Specification: Local Notebook Launcher

**Feature Branch**: `001-local-notebook-launcher`

**Created**: 2026-09-15

**Status**: Draft — post-review remediation

**Input**: "Create a local, Colab-like launcher for user-trusted public GitHub notebooks. The user should be able to open, run, edit, save, stop, and reopen a persistent local copy; optionally use a local GPU; and let MCP-capable agents inspect, edit, execute, diagnose, and repair the same notebook session. The feature must never commit, push, create branches, or otherwise modify GitHub."

## Clarifications

### Session 2026-09-15

- Q: When a user approves a GitHub source as trusted, should that trust apply only to the exact resolved commit, or to the whole repository? → A: The trust prompt offers both choices: trust this exact commit or trust this repository for future revisions.
- Q: If the user and an attached agent edit the same notebook or cell at nearly the same time, how should the launcher handle the conflict? → A: Detect stale/conflicting edits and require refresh/retry rather than silently overwriting either edit.
- Q: In the default writable agent mode, should the agent be allowed to modify any file inside the persistent workspace, or only notebook files and designated output files? → A: The writable agent may modify any file inside the persistent workspace; host paths outside the workspace remain protected unless explicitly granted.
- Q: Should a single persistent workspace allow more than one active notebook session at the same time, or only one active session per workspace? → A: Only one active notebook session is allowed per workspace.
- Q: Should more than one MCP-capable agent be allowed to attach to the same active notebook session at the same time? → A: Only one writable MCP agent may be attached to an active notebook session at a time; another writable agent must wait until the current writable agent disconnects.

### Review remediation 2026-09-15

- Opening a launcher URL is separated from authorizing execution. A GET may resolve/render a local preview but MUST NOT start repository build/runtime execution. Every fresh launch or stopped-workspace reopen requires a short-lived one-time local launch authorization gesture, even when source trust already exists.
- Repository-wide trust follows the immutable GitHub repository object identity, not mutable `owner/name` text. Rename/transfer preserves trust; delete-and-recreate at the same namespace does not.
- Agent execution is serialized through an ownership-aware broker. Queued cancellation never interrupts the kernel. A kernel interrupt is permitted only while the active Jupyter request is owned by the cancelling agent operation.
- Default notebook Internet access is limited to globally routable destinations plus explicitly approved infrastructure such as the configured DNS resolver. Host gateway, loopback, private/LAN, link-local, metadata, multicast, and other non-global local ranges are denied by default.
- Host-side operations under an explicit local-data grant use canonical, symlink-safe containment; path text/prefix checks alone are insufficient.
- The launcher-designated `active_notebook_path` is the MCP notebook target. Opening/focusing another JupyterLab notebook does not silently retarget the agent.

## User Scenarios & Testing

### User Story 1 — Open a Trusted GitHub Notebook Locally (Priority: P1)

As a user, I can paste/open a public GitHub notebook reference, review the locally resolved source, explicitly authorize this launch, and open the requested notebook without manually cloning or starting Jupyter.

**Independent Test**: Navigate to a supported `/open?...` URL, confirm that GET alone does not build/start a runtime, use the local authorization control, complete trust if needed, and verify the exact notebook opens inside the standard sandbox.

**Acceptance Scenarios**:

1. A valid public GitHub notebook resolves to repository metadata, immutable commit SHA, and normalized notebook path before execution.
2. `GET /open` may show a preview/authorization page but starts no repo2docker build, repository setup, notebook server, kernel, or notebook code.
3. A fresh launch requires a one-time local launch authorization token/gesture tied to the normalized request, even when exact-commit or repository-wide trust already covers the source.
4. A source not already trusted additionally prompts for exact-commit trust or repository-wide trust before repository-supplied setup/code can run.
5. Denying launch authorization or source trust produces zero repository-supplied setup/notebook execution.
6. Exact-commit trust is reused only for the same stable repository object + commit SHA.
7. Repository-wide trust is reused for future revisions of the same stable repository object, including after rename/transfer, until revoked.
8. A repository recreated at the same `owner/name` but with a different GitHub repository ID is not covered by the prior repository-wide trust record.
9. A source-declared dependency absent from unrelated host Python is available in the prepared notebook environment without modifying host Python.
10. The complete standard sandbox, collaboration readiness, and default egress policy are active before notebook/agent execution becomes available.

---

### User Story 2 — Keep a Persistent, Editable Local Copy (Priority: P1)

As a user, I can edit a persistent local workspace, stop compute, and later reopen it without losing notebook edits, saved outputs, or generated artifacts.

**Acceptance Scenarios**:

1. Immutable source provenance remains separate from the editable working copy.
2. A fresh remote-source launch creates a new persistent workspace by default.
3. A stopped workspace reopens its existing working copy rather than resetting from GitHub, but restart of compute still requires a new local launch authorization gesture.
4. Save a Copy creates a distinct local notebook and preserves an existing destination unless overwrite is explicit.
5. Outputs and arbitrary workspace files survive runtime teardown/reopen.
6. Supported active-notebook rename/move updates `active_notebook_path`; unsupported disappearance fails clearly.
7. Disk-full/comparable replacement failure preserves the last good saved file and does not advance metadata as if successful.

---

### User Story 3 — Let an Agent Operate the Same Notebook (Priority: P1)

As a user of an MCP-capable coding agent, I can attach it to the same authoritative notebook/kernel I am using and let it inspect, edit, execute, diagnose, repair, and cancel its own work without silently disrupting unrelated browser execution.

**Acceptance Scenarios**:

1. The agent targets the same workspace, Jupyter server, authoritative notebook document, kernel, sandbox, GPU scope, and resource limits as the browser.
2. Writable mode can inspect/insert/edit/delete notebook cells, execute cells/notebook/diagnostic code, restart the kernel, and manipulate ordinary workspace files.
3. Readonly mode can inspect notebook/cells/outputs but cannot execute, cancel execution, restart, mutate notebook/workspace, or self-escalate.
4. Whole-notebook execution uses the active kernel, preserves cell order, and stops at first error by default.
5. Stale agent mutations or stale independent full-document saves are rejected rather than last-write-wins.
6. Generic file operations cannot overwrite/move/delete the active notebook; active-notebook move uses notebook-aware semantics.
7. At most one writable MCP attachment is active per session.
8. Agent execution operations are serialized through an execution broker with states including queued/dispatched/running/cancelling/terminal and a Jupyter request message ID when dispatched.
9. If browser execution owns the busy kernel, an agent operation waits/queues. Cancelling or timing out a queued agent operation does not interrupt the browser execution.
10. If an agent request has been dispatched but is not the active busy-parent request, cancellation marks it for cancellation without interrupting the current browser-owned request; when/if that agent request becomes active it is interrupted immediately.
11. A kernel-wide interrupt for agent cancellation/timeout is issued only when the current active Jupyter request/message ID is owned by that agent operation.
12. Oversized text and binary/multimodal output returns a bounded agent representation while the authoritative notebook output remains persisted.
13. Opening or focusing another notebook in JupyterLab does not change the MCP target; MCP remains bound to `active_notebook_path` unless a separately specified retarget feature is introduced.

---

### User Story 4 — Use Local GPU Compute When Available (Priority: P1)

1. `gpu=auto` uses a supported usable GPU when available and otherwise remains usable on CPU.
2. `gpu=on` fails clearly if a usable GPU is unavailable.
3. `gpu=off` does not expose GPU access.
4. Browser and MCP execution observe the same GPU availability/device identity.

---

### User Story 5 — Keep Local Execution Contained (Priority: P1)

As a user, I can run a user-trusted notebook within a constrained local boundary that does not automatically expose unrelated host filesystem, credentials, local network services, Docker control, or GitHub mutation capability.

**Acceptance Scenarios**:

1. Only explicit workspace/source/output/user-data mounts are available; host home, credentials, Docker socket, and unrelated paths are absent by default.
2. Inbound published services are launcher-controlled and loopback-only.
3. Outbound Internet access to globally routable destinations is enabled by default for packages, models, datasets, and APIs.
4. Default egress denies loopback, host gateway, RFC1918/private/LAN, IPv6 ULA, link-local, cloud metadata, multicast, and other non-global local destinations, including when reached through DNS; only explicitly approved infrastructure such as the configured DNS resolver is exempted.
5. The runtime does not add a host-gateway alias or equivalent bypass by default.
6. Unsupported/malformed sources, traversal, cross-session access, permission escalation, and command-injection attempts are rejected.
7. No Git commit/push/branch/GitHub write-credential surface exists.
8. The standard sandbox is described as defense-in-depth for user-trusted repositories, not hostile-code containment.

---

### User Story 6 — Attach Explicit Local Data Storage (Priority: P2)

As a user, I can explicitly grant one local directory as `ro` or `rw` persistent data without exposing my whole home or allowing path/symlink escapes.

**Acceptance Scenarios**:

1. No extra host data directory is mounted by default.
2. Remote launch URLs cannot choose, replace, or broaden the host directory grant.
3. At grant time the launcher resolves and records a canonical root and checks forbidden/whole-home rules after canonicalization.
4. Host-side read/write/copy operations are anchored to the granted canonical root and use symlink/reparse-safe no-follow semantics; path components that would escape the grant are rejected.
5. Save a Copy to `user_data` cannot escape through `..`, symlink, junction, reparse point, race-prone string-prefix checks, or path replacement.
6. Missing/replaced grant roots fail clearly rather than substituting another path.

---

### User Story 7 — Stop and Reopen Work Predictably (Priority: P2)

1. Launch/session status exposes high-level phase and actionable secret-safe failure information.
2. Stop invalidates runtime credentials, MCP state, writable lease, and execution handles but preserves workspace/cache.
3. Reopening a stopped workspace requires a fresh local launch authorization gesture and starts against the existing working copy.
4. A workspace has at most one active runtime; repeated start requests reuse/direct to the existing session rather than create a second one.

## Edge Cases

- Cross-site navigation/image/iframe/form attempts target `127.0.0.1` launcher URLs.
- A one-time launch token is expired, replayed, altered, used with a different normalized request, or submitted from a non-loopback/non-same-origin context.
- GitHub repository is renamed, transferred, deleted and recreated under the same namespace, or returns inconsistent metadata during resolution.
- Browser code is running while an agent execution is queued and then cancelled/times out.
- Browser and agent requests race at the transition from kernel idle to busy.
- DNS resolves a public-looking hostname to a private/link-local/metadata address.
- IPv4-mapped IPv6 or alternate address forms attempt to bypass egress filtering.
- A user-data directory or destination contains symlinks/reparse points or is swapped between validation and open.
- Browser focus switches to another notebook while MCP remains attached.
- Workspace disk is full, kernel dies, GPU disappears, or output is unusually large/binary/multimodal.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST accept a supported public GitHub notebook reference and open the exact requested notebook locally after required local authorization/trust gates.
- **FR-002**: Every remote source MUST resolve to an immutable commit SHA and stable GitHub repository identity before execution authorization.
- **FR-003**: Before executing a source not covered by trust, the system MUST offer exact-commit and repository-wide trust scopes.
- **FR-004**: Denying/cancelling trust MUST prevent repository-supplied setup and notebook execution.
- **FR-005**: Exact-commit trust MUST authorize only the approved stable repository ID + commit SHA.
- **FR-006**: Repository-wide trust MUST be keyed by stable GitHub repository ID (with current owner/name as display metadata), follow the same repository object across rename/transfer, and MUST NOT authorize a different repository object recreated at the same namespace.
- **FR-007**: A fresh remote launch MUST create a new persistent workspace by default.
- **FR-008**: Source provenance MUST remain separate from the editable workspace.
- **FR-009**: Notebook edits, outputs, workspace files, and artifacts MUST persist after session stop.
- **FR-010**: Reopen MUST reuse the existing working copy rather than reset from GitHub.
- **FR-011**: Save a Copy MUST create a distinct notebook at a validated persistent destination and preserve existing destinations unless overwrite is explicit.
- **FR-012**: The feature MUST NOT commit, push, create branches, or otherwise modify GitHub.
- **FR-013**: GitHub write credentials MUST NOT be required/injected for ordinary use.
- **FR-014**: Source-declared dependencies MUST work without relying on unrelated host Python packages.
- **FR-015**: Unchanged environment artifacts SHOULD be reused when available.
- **FR-016**: Writable agent mode MUST support notebook inspection/mutation/execution plus ordinary workspace file operations inside the allowed workspace.
- **FR-017**: Readonly agent mode MUST reject execution, cancellation, restart, and all notebook/workspace mutation while permitting inspection.
- **FR-018**: Agent operations MUST use the same active notebook workspace/execution state visible to the user.
- **FR-019**: Whole-notebook execution MUST preserve code-cell order and default stop-on-error.
- **FR-020**: Execution failures MUST identify failing cell/context while preserving the session when healthy.
- **FR-021**: Agent execution MUST provide real timeout/cancellation and distinct outcomes.
- **FR-022**: Agent execution MUST be ownership-aware: queued cancellation MUST NOT interrupt the kernel, and kernel interrupt MUST occur only when the active Jupyter request is owned by the cancelling/timed-out agent operation.
- **FR-023**: The execution broker MUST track enough Jupyter request/message identity and kernel busy state to distinguish agent-owned active work from browser-owned work.
- **FR-024**: Session stop MUST invalidate agent attachment/execution handles while preserving workspace.
- **FR-025**: Users MUST be able to select `gpu=auto|on|off` with clear required-GPU failure.
- **FR-026**: Browser and agent execution in the same session MUST observe the same compute capability/workspace state.
- **FR-027**: Local execution MUST expose only explicitly granted filesystem/resources by default.
- **FR-028**: Host credentials, unrelated host directories, Docker socket, local management interfaces, and other sessions MUST NOT be exposed by default.
- **FR-029**: The standard isolation mode MUST be described as defense-in-depth, not arbitrary malicious-code containment.
- **FR-030**: Unsupported hosts/references, traversal, cross-session access, and command-injection attempts MUST be rejected.
- **FR-031**: User-controlled values MUST be passed as data/structured arguments, not shell text.
- **FR-032**: Public Internet egress to globally routable destinations MUST be allowed by default for package/model/data/API retrieval.
- **FR-033**: Default egress MUST deny loopback, host gateway, private/LAN, link-local, metadata, multicast, reserved/non-global local ranges (IPv4 and IPv6), except explicitly approved infrastructure required by the runtime such as configured DNS resolver endpoints.
- **FR-034**: Inbound service exposure MUST be launcher-controlled and loopback-only by default.
- **FR-035**: No additional host data directory MUST be exposed by default.
- **FR-036**: Users MAY grant one selected local directory as `ro|rw`; remote sources MUST NOT select/change it and whole-home grant MUST NOT be automatic.
- **FR-037**: Host data grants MUST store/validate a canonical root; host-side operations MUST enforce symlink/reparse-safe containment with no-follow/dirfd-equivalent semantics rather than string-prefix validation alone.
- **FR-038**: Save a Copy and other host-side operations under a user-data grant MUST reject any path that escapes the canonical root through traversal, symlink/junction/reparse point, or path substitution race.
- **FR-039**: The system MUST provide persistent output storage that survives runtime stop.
- **FR-040**: Launch/session status and failures MUST be clear and secret-safe.
- **FR-041**: Agent audit metadata MUST be bounded and omit raw credentials, full notebook content, and full large/binary output payloads by default.
- **FR-042**: Agent permission enforcement MUST occur at the launcher/MCP boundary; readonly cannot self-promote without explicit local authorization/new session policy.
- **FR-043**: Persistent workspace data MUST NOT be automatically deleted by session stop; user-facing workspace deletion is out of MVP scope.
- **FR-044**: The active notebook MUST have one authoritative shared document; normal browser edits participate in it and stale independent/agent writes are rejected.
- **FR-045**: Generic file operations MUST NOT bypass notebook-aware semantics for the active notebook.
- **FR-046**: A workspace MUST have at most one active notebook session.
- **FR-047**: An active session MUST permit at most one writable MCP attachment.
- **FR-048**: Supported active-notebook rename/move MUST update `active_notebook_path`; unsupported disappearance fails clearly.
- **FR-049**: Persistent replacement writes MUST fail safely on disk-full/comparable errors without replacing the last good file.
- **FR-050**: Agent-facing output retrieval MUST use configurable bounded payloads while preserving authoritative notebook output.
- **FR-051**: The complete standard sandbox MUST be applied and verified before notebook/agent execution.
- **FR-052**: `GET /open` MUST be non-executing: it may validate/resolve/render a local preview but MUST NOT initiate repository build/setup, runtime, kernel, or notebook code.
- **FR-053**: Every fresh launch or stopped-workspace reopen MUST require a short-lived one-time local launch authorization gesture/token bound to the normalized request, regardless of whether source trust already exists.
- **FR-054**: Launch authorization MUST be separate from source trust, resistant to cross-site drive-by invocation, not accepted from the original GET query, and protected by same-origin/local checks plus anti-framing behavior.
- **FR-055**: MCP MUST remain bound to the launcher-designated `active_notebook_path`; browser tab/focus changes or opening another notebook MUST NOT silently retarget the agent.

### Key Entities

- **Remote Notebook Source**: current owner/name, stable GitHub repository ID/node ID, requested ref, normalized notebook path, resolved commit SHA.
- **Launch Preview/Authorization**: normalized launch/reopen request plus short-lived signed one-time authorization token/request digest; it authorizes starting local compute but is not a source-trust grant.
- **Trust Decision**: local exact-commit or repository-wide authorization keyed to stable repository identity.
- **Workspace**: persistent provenance + mutable files/outputs/preferences with zero or one active session.
- **Working Notebook**: launcher-designated active notebook represented by one authoritative collaborative document/version.
- **Notebook Session**: disposable runtime with verified sandbox/network policy and zero or one writable MCP lease.
- **Execution Operation**: agent execution record with queue state, operation ID, Jupyter request message ID when dispatched, deadline/cancel state, and ownership of active kernel work.
- **Agent Capability Profile**: `write` or `readonly` policy.
- **Local Data Grant**: user-selected original path + canonical root + `ro|rw` mode.
- **Output Artifact / Output Envelope**: persistent full artifact/output vs bounded agent-facing representation.
- **Agent Activity Record**: sanitized bounded metadata.

## Success Criteria

- **SC-001**: A supported public GitHub notebook can be opened locally without manual clone/server startup after explicit local launch authorization.
- **SC-002**: GET-only/cross-site navigation to `/open` causes zero repository build/setup/runtime/kernel/notebook execution, including for an already repository-trusted source.
- **SC-003**: 100% of fresh-launch and stopped-workspace-reopen acceptance cases require a valid one-time local launch authorization token; token replay/request substitution fails.
- **SC-004**: Trust tests prove repository rename/transfer retains repository-wide trust via stable ID, while delete/recreate under the same namespace requires new trust.
- **SC-005**: Persistence test retains notebook edit, saved output, ordinary workspace file, and generated artifact after stop/reopen.
- **SC-006**: Source-declared dependency absent from host Python works inside the launched environment.
- **SC-007**: Writable MCP agent can read/edit/execute/fail/repair/continue in the same notebook state as the browser.
- **SC-008**: Readonly acceptance denies 100% of execution/mutation/escalation attempts while allowing inspection.
- **SC-009**: Deliberately stale agent/independent notebook writes are rejected without overwriting newer authoritative state.
- **SC-010**: A browser-running + queued-agent cancellation test proves cancelling/timing out the agent operation does not interrupt the browser-owned request.
- **SC-011**: A running-agent cancellation test proves interrupt occurs only after the kernel busy parent/message ID is the agent-owned request and returns `cancelled`/`timeout` distinctly.
- **SC-012**: On configured GPU host, browser and MCP report the same GPU availability/device identity.
- **SC-013**: Security tests reject prohibited mounts, secret inheritance, Docker socket, traversal, cross-session access, command injection, and GitHub mutation surfaces.
- **SC-014**: Public Internet connectivity works while direct and DNS-mediated attempts to reach host gateway/private/LAN/link-local/metadata/non-global destinations fail by default.
- **SC-015**: Explicit user-data grant exposes only the selected canonical directory/mode; symlink/junction/reparse escape tests fail safely.
- **SC-016**: Save a Copy to user-data cannot escape the grant by traversal, symlink, or path-swap race in the tested host path model.
- **SC-017**: Stopping a session invalidates agent/runtime state while preserving workspace and reusable environment cache.
- **SC-018**: Repeated starts cannot create more than one active session for a workspace.
- **SC-019**: A second writable MCP agent cannot attach while the first writable lease is active.
- **SC-020**: Active-notebook rename persists across stop/reopen; disk-full replacement failure preserves prior saved contents.
- **SC-021**: Oversized/binary/multimodal output yields bounded agent responses while full authoritative output remains persisted and absent from audit payloads.
- **SC-022**: Before first execution, sandbox assertions cover privileges/capabilities/syscall filtering/resources/mounts/secrets/network policy.
- **SC-023**: Opening/focusing another JupyterLab notebook does not change MCP `active_notebook_path` target.

## Assumptions

- Single developer/local machine; WSL2/Linux first, native Linux compatible where practical.
- Public GitHub only for MVP; private authentication is out of scope.
- GitHub stable repository numeric ID/node ID can be resolved before trust evaluation.
- Users choose which public repositories to trust; public availability is not trust.
- Repository-wide trust intentionally follows the same repository object across rename/transfer.
- Public Internet access is normally available; local/private network service access is not required by the MVP and would need a separately explicit network grant/policy.
- GPU environment, when used, is preconfigured by the user.
- Workspaces persist locally and are not automatically deleted by this MVP.
- Extra host data access is opt-in and limited to one canonical user-selected directory.
- The MVP does not provide agent retargeting based on browser focus and does not guarantee simultaneous multiple readonly MCP clients.
- Multi-user hosting, public Internet exposure, distributed scheduling, cloud deployment, remote agents, private repositories, hostile-code-grade isolation, and arbitrary local/LAN egress grants are outside MVP scope.
