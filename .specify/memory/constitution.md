# Notebook Launcher Constitution

## Core Principles

### I. Local-First and Safe by Default
The launcher MUST default to loopback-only access and MUST NOT expose a public network service unless explicitly configured. Repository and notebook inputs MUST be validated before any clone, build, or execution occurs. Untrusted repository code is an explicit trust boundary and MUST execute in an isolated environment rather than directly in the host Python environment.

### II. Reproducible Launches
Every launch MUST resolve the requested repository reference to an immutable revision when practical and record the repository, resolved revision, notebook path, and environment definition used for the session. Repeated launches of the same immutable source and environment SHOULD reuse cached artifacts rather than rebuild unnecessarily. Environment preparation MUST prefer declared project configuration over ad hoc host state.

### III. Smallest Useful POC
The project MUST first prove the complete local user journey: GitHub notebook reference -> local launcher -> prepared environment -> requested Jupyter notebook. Multi-user infrastructure, public hosting, institutional branding, distributed scheduling, and production platform concerns MUST NOT be introduced until the local single-user path works end to end. New infrastructure MUST justify why a simpler implementation cannot satisfy the current acceptance criteria.

### IV. Isolation and Explicit Resource Access
Notebook execution MUST be containerized or equivalently isolated from the host. GPU access MUST be explicitly requested and MUST degrade to a clear, diagnosable failure when unavailable; GPU capability MUST NOT be assumed for every notebook. Host credentials, SSH keys, tokens, and unrelated filesystem locations MUST NOT be mounted into notebook sessions by default.

### V. Observable and Testable Launch Pipeline
The launcher MUST expose clear phase-level status for source resolution, repository acquisition, environment preparation, session startup, and notebook handoff. Failures MUST identify the phase and actionable cause without leaking secrets. Core URL parsing, validation, cache-key generation, process/container invocation, and launch orchestration MUST be covered by automated tests; the complete GitHub-to-Jupyter path MUST have an integration test.

## Technical and Security Constraints

- The initial supported host is Windows 11 with WSL2 Linux; native Linux compatibility SHOULD be preserved where it does not complicate the POC.
- The initial GPU path is NVIDIA GPU access exposed to WSL2 and containers through NVIDIA-supported tooling.
- The launcher MUST support public GitHub repositories for the POC. Private-repository authentication is out of scope unless separately specified.
- The launcher MUST reject unsupported hosts, malformed repository references, notebook paths that escape the repository root, and non-notebook launch targets.
- Arbitrary shell construction from URL parameters is prohibited. External commands MUST use structured argument invocation.
- The BSD-3-Clause project license MUST be preserved.

## Development Workflow and Quality Gates

1. Every material capability starts with or updates a specification under `specs/`.
2. Implementation plans MUST pass the constitution checks before task breakdown.
3. Security-sensitive parsing and execution paths require negative tests, including malformed URLs, path traversal, command injection attempts, and unsupported source hosts.
4. GPU behavior requires both a capability-detection test and a documented manual/integration smoke test on a configured WSL2 NVIDIA host.
5. Documentation MUST distinguish required host prerequisites from dependencies installed inside generated notebook environments.
6. Implementation SHOULD remain replaceable at subsystem boundaries so a later BinderHub/JupyterHub backend can be introduced without changing the public launch contract.

## Governance

This constitution is the controlling engineering guidance for the repository. Specifications, plans, tasks, and implementation changes MUST conform to it. Deviations require an explicit justification in the relevant implementation plan under Complexity Tracking. Amendments require updating this file, documenting the reason, and reviewing active specifications for impact.

**Version**: 1.0.0 | **Ratified**: 2026-09-15 | **Last Amended**: 2026-09-15
