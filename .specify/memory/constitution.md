# Notebook Launcher Constitution

## Core Principles

### I. Local-First and Safe by Default
The launcher MUST be designed for WSL2/Linux-first local execution and MUST default to
loopback-only access. It MUST NOT expose a public network service unless explicitly configured.
Repository and notebook inputs MUST be validated before any clone, build, or execution occurs.
The POC trust model is limited to user-trusted public repositories; private-repository
credentials and arbitrary hostile-code execution are outside the initial trust model.

### II. Reproducible Launches
Every launch MUST resolve the requested repository reference to an immutable revision when
practical and record the repository, resolved revision, notebook path, and environment definition
used for the session. Repeated launches of the same immutable source and environment SHOULD reuse
cached artifacts rather than rebuild unnecessarily. Environment preparation MUST prefer declared
project configuration over ad hoc host state.

### III. Complete Local Agent-Operable POC
The first complete POC MUST prove the entire local user journey: GitHub notebook reference -> local
launcher -> prepared environment -> requested Jupyter notebook -> MCP-capable agent execution in
the same notebook runtime. Agent execution of notebooks is mandatory for the MVP, not a deferred
enhancement. Multi-user infrastructure, public hosting, institutional branding, distributed
scheduling, and production platform concerns MUST NOT be introduced until this local single-user
path works end to end. New infrastructure MUST justify why a simpler implementation cannot satisfy
the current acceptance criteria.

### IV. Sandboxed Execution and Explicit Resource Access
Notebook and agent execution MUST occur in a sandboxed runtime rather than directly in the host
Python environment. The default `standard` sandbox MUST use defense-in-depth appropriate to the
chosen container/runtime technology, including non-root execution where supported, no new
privileges, dropped unnecessary capabilities, syscall filtering or equivalent runtime controls,
resource limits, minimal explicit mounts, and no host credentials, SSH keys, Docker socket, or
unrelated filesystem access by default. GPU access MUST be explicit and MUST fail clearly when
requested but unavailable.

The standard sandbox reduces blast radius but MUST NOT be represented as sufficient isolation for
arbitrary adversarial repositories. The architecture MUST permit a future `strict` sandbox backend
with stronger isolation without changing the public launch contract. The strict mode is not an MVP
implementation requirement.

### V. Observable and Testable Launch Pipeline
The launcher MUST expose clear phase-level status for source resolution, repository acquisition,
environment preparation, session startup, MCP preparation, agent attachment, and notebook handoff.
Failures MUST identify the phase and actionable cause without leaking secrets. Core URL parsing,
validation, cache-key generation, process/container invocation, sandbox configuration, MCP
attachment, and launch orchestration MUST be covered by automated tests. The complete
GitHub-to-Jupyter-to-agent path MUST have an integration test.

### VI. Agent Control Must Share the Notebook Trust Boundary
Agent-driven execution through MCP MUST target the same launched Jupyter runtime, workspace, kernel
lifecycle, filesystem scope, sandbox policy, GPU policy, and compute limits as the interactive
notebook session. MCP MUST NOT silently create a second privileged execution environment or grant
access to host resources that the notebook runtime does not already possess. Raw Jupyter/backend
credentials MUST remain internal to the launcher or MCP bridge. Agent execution MUST be cancellable
or bounded, failures MUST remain diagnosable, and execution and attachment lifecycle events MUST
produce sanitized audit metadata without copying sensitive notebook contents into logs by default.

### VII. Agent Editing Is Explicitly Governed
The default agent capability profile MUST allow reading, executing, adding, editing, and deleting
notebook cells within the launched notebook workspace because iterative repair is part of the core
agent workflow. The launcher MUST also provide a read-only agent mode that prevents notebook or
workspace mutation while still permitting inspection and, when separately allowed by the selected
profile, execution. Capability enforcement MUST occur in the launcher/MCP boundary rather than rely
on agent prompt compliance alone. An agent MUST NOT be able to escalate from a read-only profile to
a writable profile without an explicit new session or authorization decision.

## Technical and Security Constraints

- WSL2/Linux is the primary POC execution environment; native Linux compatibility MUST remain a
  supported architectural target where practical.
- The initial GPU path is NVIDIA GPU access exposed to WSL2/Linux containers through
  NVIDIA-supported tooling.
- The POC MUST accept only user-trusted public GitHub repositories. Public availability MUST NOT be
  interpreted as trust; the user is responsible for deciding which repositories to launch.
- Sandboxing is mandatory defense-in-depth even for trusted repositories. The project MUST NOT claim
  that the default sandbox safely contains arbitrary malicious repositories.
- The launcher MUST reject unsupported hosts, malformed repository references, notebook paths that
  escape the repository root, and non-notebook launch targets.
- Arbitrary shell construction from URL, repository, session, sandbox, permission, or MCP parameters
  is prohibited. External commands MUST use structured argument invocation.
- Local MCP MUST default to a non-network transport such as stdio for the POC where supported. Any
  network MCP transport MUST remain loopback-only and authenticated by default.
- MCP implementation choice MUST remain replaceable behind a semantic capability contract rather
  than leak backend-specific tool names into the public launcher contract.
- Runtime implementation details including Docker, repo2docker, BinderHub, JupyterHub, Colab MCP,
  Jupyter MCP, gVisor, or Kata MUST remain replaceable behind launcher, runtime, sandbox, and MCP
  interfaces. User-facing launch and agent capability contracts MUST NOT depend on one such backend.
- The BSD-3-Clause project license MUST be preserved.

## Development Workflow and Quality Gates

1. Every material capability starts with or updates a specification under `specs/`.
2. Implementation plans MUST pass the constitution checks before task breakdown.
3. Security-sensitive parsing and execution paths require negative tests, including malformed URLs,
   path traversal, command injection attempts, unsupported source hosts, cross-session attachment
   attempts, permission escalation attempts, and secret leakage.
4. The standard sandbox requires tests or runtime assertions for non-root execution where supported,
   prohibited host mounts, restricted privileges/capabilities, resource limits, and loopback-only
   service exposure.
5. GPU behavior requires both a capability-detection test and a documented manual/integration smoke
   test on a configured WSL2/Linux NVIDIA host.
6. MCP backends require capability-conformance tests against the semantic contract; backend-specific
   tool names alone are not acceptance criteria.
7. Agent permission profiles require tests proving the default writable profile can modify notebook
   cells and the read-only profile cannot mutate notebook or workspace state.
8. The end-to-end acceptance path MUST demonstrate an MCP-capable agent reading, editing, and
   executing the same notebook/kernel visible in JupyterLab, including one controlled
   failure-and-repair case.
9. Documentation MUST distinguish host prerequisites, notebook-environment dependencies, sandbox
   guarantees and limitations, and agent/MCP client prerequisites.
10. Implementation MUST preserve replaceable subsystem boundaries so alternate Jupyter,
    BinderHub/JupyterHub, sandbox, and MCP backends can be introduced without changing the public
    launch or agent capability contracts.

## Governance

This constitution is the controlling engineering guidance for the repository. Specifications,
plans, tasks, and implementation changes MUST conform to it. Deviations require an explicit
justification in the relevant implementation plan under Complexity Tracking.

Amendments require documenting the reason, reviewing active specifications for impact, and applying
semantic versioning to this document: MAJOR for incompatible governance changes or principle
removals/redefinitions, MINOR for new principles or materially expanded requirements, and PATCH for
non-semantic clarifications. Every implementation or review pass MUST verify constitution
compliance before accepting material changes.

**Version**: 1.2.0 | **Ratified**: 2026-09-15 | **Last Amended**: 2026-09-15
