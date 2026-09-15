# Notebook Launcher Constitution

## Core Principles

### I. Local-First and Safe by Default
The launcher MUST default to loopback-only access and MUST NOT expose a public network service unless explicitly configured. Repository and notebook inputs MUST be validated before any clone, build, or execution occurs. Untrusted repository code is an explicit trust boundary and MUST execute in an isolated environment rather than directly in the host Python environment.

### II. Reproducible Launches
Every launch MUST resolve the requested repository reference to an immutable revision when practical and record the repository, resolved revision, notebook path, and environment definition used for the session. Repeated launches of the same immutable source and environment SHOULD reuse cached artifacts rather than rebuild unnecessarily. Environment preparation MUST prefer declared project configuration over ad hoc host state.

### III. Smallest Useful POC
The project MUST first prove the complete local user journey: GitHub notebook reference -> local launcher -> prepared environment -> requested Jupyter notebook -> MCP-capable agent execution. Multi-user infrastructure, public hosting, institutional branding, distributed scheduling, and production platform concerns MUST NOT be introduced until the local single-user path works end to end. New infrastructure MUST justify why a simpler implementation cannot satisfy the current acceptance criteria.

### IV. Isolation and Explicit Resource Access
Notebook execution MUST be containerized or equivalently isolated from the host. GPU access MUST be explicitly requested and MUST degrade to a clear, diagnosable failure when unavailable; GPU capability MUST NOT be assumed for every notebook. Host credentials, SSH keys, tokens, Docker socket, and unrelated filesystem locations MUST NOT be mounted into notebook sessions by default.

### V. Observable and Testable Launch Pipeline
The launcher MUST expose clear phase-level status for source resolution, repository acquisition, environment preparation, session startup, MCP preparation, and notebook handoff. Failures MUST identify the phase and actionable cause without leaking secrets. Core URL parsing, validation, cache-key generation, process/container invocation, MCP attachment, and launch orchestration MUST be covered by automated tests; the complete GitHub-to-Jupyter-to-agent path MUST have an integration test.

### VI. Agent Control Must Not Widen the Trust Boundary
Agent-driven notebook execution through MCP MUST target the same launched Jupyter runtime, workspace, kernel lifecycle, filesystem scope, and compute policy as the interactive notebook session. MCP MUST NOT silently create a second privileged execution environment or grant access to host resources that the notebook runtime does not already possess. Raw Jupyter/backend credentials MUST remain internal to the launcher/bridge. Agent execution MUST be cancellable or bounded, failures MUST remain diagnosable, and execution/attachment lifecycle MUST produce sanitized audit metadata without copying sensitive notebook contents into logs by default.

## Technical and Security Constraints

- The initial supported host is Windows 11 with WSL2 Linux; native Linux compatibility SHOULD be preserved where it does not complicate the POC.
- The initial GPU path is NVIDIA GPU access exposed to WSL2 and containers through NVIDIA-supported tooling.
- The launcher MUST support public GitHub repositories for the POC. Private-repository authentication is out of scope unless separately specified.
- The launcher MUST reject unsupported hosts, malformed repository references, notebook paths that escape the repository root, and non-notebook launch targets.
- Arbitrary shell construction from URL, repository, session, or MCP parameters is prohibited. External commands MUST use structured argument invocation.
- Local MCP SHOULD default to stdio for the POC; any network MCP transport MUST remain loopback-only and authenticated by default.
- MCP implementation choice MUST remain replaceable behind a semantic capability contract rather than leaking backend-specific tool names into the public launcher contract.
- The BSD-3-Clause project license MUST be preserved.

## Development Workflow and Quality Gates

1. Every material capability starts with or updates a specification under `specs/`.
2. Implementation plans MUST pass the constitution checks before task breakdown.
3. Security-sensitive parsing and execution paths require negative tests, including malformed URLs, path traversal, command injection attempts, unsupported source hosts, cross-session attachment attempts, and secret leakage.
4. GPU behavior requires both a capability-detection test and a documented manual/integration smoke test on a configured WSL2 NVIDIA host.
5. MCP backends require capability-conformance tests against the semantic contract; backend-specific tool names alone are not acceptance criteria.
6. The end-to-end acceptance path MUST demonstrate an MCP-capable agent reading and executing the same notebook/kernel visible in JupyterLab, including one controlled failure/recovery case.
7. Documentation MUST distinguish required host prerequisites from dependencies installed inside generated notebook environments and from agent/MCP client prerequisites.
8. Implementation SHOULD remain replaceable at subsystem boundaries so later BinderHub/JupyterHub or alternate MCP backends can be introduced without changing the public launch or agent capability contracts.

## Governance

This constitution is the controlling engineering guidance for the repository. Specifications, plans, tasks, and implementation changes MUST conform to it. Deviations require an explicit justification in the relevant implementation plan under Complexity Tracking. Amendments require updating this file, documenting the reason, and reviewing active specifications for impact.

**Version**: 1.1.0 | **Ratified**: 2026-09-15 | **Last Amended**: 2026-09-15
