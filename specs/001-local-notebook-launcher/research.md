# Research: Local Notebook Launcher

## Decision 1: Prove the local path without BinderHub first

**Decision**: Use local Docker + repo2docker + JupyterLab for the MVP. Keep BinderHub/JupyterHub behind the future runtime boundary.

**Rationale**: The MVP risks are source resolution, reproducible environment construction, persistence, sandboxing, GPU passthrough, and same-runtime agent operation. Kubernetes would add unrelated moving parts.

**Alternatives considered**: BinderHub/JupyterHub immediately; direct host Python/virtualenv execution.

## Decision 2: Keep public contracts backend-neutral

**Decision**: `/open`, workspace semantics, and semantic MCP capabilities are launcher contracts. Runtime, sandbox, notebook server, and MCP implementation names remain internal.

**Rationale**: Future backend replacement must not break notebook links or agent expectations.

## Decision 3: Resolve mutable Git refs to immutable SHAs

**Decision**: Resolve every remote launch to a commit SHA before trust, provenance, environment identity, or workspace creation.

**Rationale**: Branches/tags are convenient but unstable identities. Immutable SHAs make trust scope, caching, and provenance precise.

## Decision 4: Use repo2docker for environment preparation

**Decision**: Use repo2docker/Binder-style repository declarations instead of inventing a launcher environment format.

**Rationale**: It already maps common repository environment declarations to reproducible Jupyter-capable images and leaves a future BinderHub path open.

**Reference**: https://repo2docker.readthedocs.io/

## Decision 5: Use filesystem data plus SQLite control metadata

**Decision**: Keep notebook/source/workspace/artifact bytes as ordinary files, but store trust records, workspace/session lifecycle metadata, active ownership, writable-agent leases, version metadata, and bounded audit records in local SQLite.

**Rationale**: The clarification pass introduced atomic uniqueness and lease requirements. SQLite provides transactions and uniqueness constraints without adding a server dependency or hiding notebook files in a database.

**Alternatives considered**: mutable JSON files plus ad-hoc locks; external Postgres/Redis.

## Decision 6: Separate immutable source, persistent workspace, and disposable runtime

**Decision**:

```text
source snapshot   immutable provenance/reference
workspace         persistent mutable user state
runtime/kernel    disposable compute attached to workspace
```

A fresh remote launch creates a fresh workspace. Runtime shutdown does not delete it. Reopen starts a new runtime against the existing working copy.

## Decision 7: GitHub is source input, never a write target

**Decision**: Do not commit, push, create branches, inject GitHub write credentials, or rely on a writable source remote.

**Rationale**: Local persistence is a separate concern from source publishing. Future Git publishing would be a separately specified feature.

## Decision 8: Trust prompt offers exact-commit and repository-wide scopes

**Decision**: When a source is not already covered by trust, present two positive choices: trust the resolved commit only, or trust future revisions from that repository. Deny/cancel executes nothing.

**Rationale**: This matches the clarified UX while preserving an immutable safe default option. Repository-wide trust is explicit and revocable.

## Decision 9: Trust confirmation requires an interactive local nonce

**Decision**: The trust action requires a launch-scoped one-time confirmation nonce rendered by the local trust page and not accepted from remote launch query parameters.

**Rationale**: A localhost web service can be navigated to from arbitrary web pages. An explicit local confirmation must not be forgeable by merely constructing an `/open` URL.

## Decision 10: Save a Copy is local persistence, not Git publishing

**Decision**: Duplicate the current saved notebook into a validated workspace-relative destination or explicitly granted writable user-data directory; default to no overwrite.

## Decision 11: Optional local user-data access is granted through local management flow

**Decision**: No host folder is mounted by default. The user grants one selected directory with `ro` or `rw` mode through local CLI/management, exposed at a stable sandbox path. Remote launch URLs cannot provide a host path.

**Rationale**: This preserves the sandbox boundary and avoids turning a browser-facing localhost endpoint into an arbitrary host-path mount primitive.

## Decision 12: Outbound network enabled by default; inbound tightly limited

**Decision**: Allow outbound notebook network access by default for packages, models, datasets, and APIs. Publish only launcher-controlled services on loopback by default.

**Rationale**: This is the expected notebook workflow and is now a normative feature requirement rather than an implementation-optional behavior.

## Decision 13: Standard sandbox is defense-in-depth and is complete before first notebook execution

**Decision**: Before Jupyter accepts notebook/agent execution, apply non-root execution where compatible, no-new-privileges, dropped unnecessary capabilities, seccomp/equivalent filtering, CPU/memory/PID limits, minimal mounts, no Docker socket, no host credentials/unrelated secret environment, and loopback-only inbound exposure.

**Rationale**: These are constitution-mandated properties of the default standard sandbox, not a later hardening increment. The trust model remains user-trusted public repositories; a future strict runtime can provide stronger isolation.

## Decision 14: GPU policy is explicit

**Decision**: Support `gpu=auto|on|off`, default `auto`. Validate the configured NVIDIA container path but do not install drivers/toolkit.

**Rationale**: CPU remains useful; `on` gives deterministic GPU-required acceptance.

**References**: https://docs.nvidia.com/cuda/wsl-user-guide/ and https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/

## Decision 15: Jupyter collaboration state is the authoritative shared notebook state

**Decision**: Explicitly provision and enable JupyterLab collaboration support for the human+agent notebook document. The browser and MCP backend operate on the same shared document/kernel.

**Rationale**: Independent file-edit paths invite lost updates and divergent visible state. Collaboration must be a readiness-gated runtime capability, not an assumed incidental package.

## Decision 16: Initial MCP backend is Datalayer Jupyter MCP Server 2.x

**Decision**: Adapt Datalayer `jupyter-mcp-server` 2.x as the first backend, connected to the launcher-created Jupyter server, while exposing only launcher semantic capabilities publicly.

**Rationale**: It is BSD-3-Clause, maintained, can target an existing local Jupyter deployment, and exposes notebook/cell inspection and mutation, execution, outputs, and kernel control.

**Reference**: https://github.com/datalayer/jupyter-mcp-server

**Alternative considered**: build MCP framing/tools from scratch; fork a Colab-specific bridge; defer MCP to post-MVP.

## Decision 17: Pin the exact MCP/Jupyter collaboration versions only after conformance

**Decision**: The implementation targets the 2.x backend line, but the exact patch plus JupyterLab/collaboration versions are pinned in the project lockfile only after they pass the launcher conformance suite.

**Rationale**: Upstream real-time collaboration regressions have been reported, including cases where MCP edits and browser edits stop persisting bidirectionally or produce invalid notebook state. Version pinning must follow acceptance evidence, not `latest` optimism.

**References**:
- https://github.com/datalayer/jupyter-mcp-server/issues/146
- https://github.com/datalayer/jupyter-mcp-server/issues/263

## Decision 18: Reject stale mutations with opaque version preconditions

**Decision**: Notebook reads expose an opaque `document_version`; agent notebook mutations require the version they were based on. Mutable non-notebook files use analogous `file_version` preconditions. A mismatch returns a conflict and requires refresh/retry.

**Rationale**: The user explicitly rejected last-write-wins. Opaque tokens let the implementation use collaboration revisions/hashes without coupling the semantic contract to YDoc internals.

## Decision 19: Normal browser edits use the shared document; stale independent saves are guarded

**Decision**: Normal JupyterLab edits participate in the authoritative collaboration document and advance the shared document version. Any separate full-document/file replacement path that could overwrite the active notebook is guarded by launcher/Jupyter save-version integration and rejects stale state.

**Rationale**: This resolves the human half of the conflict requirement without inventing a second browser-side editing model. Collaborative edits are not stale independent writes; stale replacement paths still need explicit rejection.

## Decision 20: Generic workspace file operations cannot bypass the active notebook

**Decision**: Writable agents retain full-workspace capability, but generic `workspace.file.write|move|delete` rejects the active notebook path. Active-notebook move/rename uses a notebook-aware operation that updates workspace metadata and preserves document semantics.

**Rationale**: “Writable anywhere in the workspace” must not create a back door around the conflict model for the one file currently governed by Jupyter's authoritative document.

## Decision 21: One active notebook session per workspace

**Decision**: Enforce zero-or-one active runtime per workspace using transactional control state. Reopen of an active workspace returns/directs to the existing session instead of starting another.

**Rationale**: This makes kernel, GPU, workspace and agent ownership deterministic and reduces conflict surface.

## Decision 22: Reconcile stale session ownership after crashes

**Decision**: At launcher startup and before claiming a workspace, compare recorded active session ownership with the actual runtime. Clear stale ownership only when the runtime is confirmed absent/dead.

**Rationale**: A process crash must not permanently lock a workspace or accidentally create duplicate live runtimes.

## Decision 23: One writable MCP agent lease per active session

**Decision**: Acquire a transactional writable-agent lease when a writable MCP bridge attaches. A second writable attachment fails until the lease is released/invalidated. Read-only attachments do not consume the writable lease, but simultaneous multiple readonly clients are not an MVP guarantee.

**Rationale**: Execution and mutation ordering remains deterministic while preserving the read-only profile without expanding scope into multi-client collaboration.

## Decision 24: Writable mode covers the persistent workspace with an active-notebook exception to generic file operations

**Decision**: A writable agent may create, read, modify, rename and delete workspace files and execute code in the active kernel. The active notebook remains fully writable through notebook-aware operations; generic file operations cannot overwrite/move/delete it. Access cannot implicitly reach outside the sandbox or exceed an explicit user-data mount's mode.

**Rationale**: Real notebooks create configs, scripts, checkpoints, generated data, and helper files. Notebook-only write permission would break normal workflows, but active-notebook bypass would break conflict safety.

## Decision 25: Readonly is inspection-only

**Decision**: `readonly` permits notebook/cell/output inspection but blocks code execution, execution cancellation, kernel restart, notebook mutation/move, and workspace/file mutation.

**Rationale**: Arbitrary code execution is itself a write capability because it can mutate persistent files.

## Decision 26: Use stdio as the default local MCP transport

**Decision**: `notebook-launcher mcp <session-id>` resolves private state, acquires the appropriate lease, starts/connects the backend, and proxies over stdio. Raw Jupyter credentials stay private.

## Decision 27: Notebook execution remains notebook execution and is genuinely cancellable

**Decision**: Whole-notebook execution uses the active Jupyter kernel, preserves cell order/state, defaults to stop-on-error, and reports the failing cell. Each agent execution gets an operation ID/deadline. Explicit cancellation or timeout interrupts the kernel; an unresponsive kernel may be restarted after a bounded grace interval while preserving workspace files.

**Rationale**: A timeout label without stopping the execution would not satisfy the constitution's cancellable/bounded requirement.

## Decision 28: Bound agent-facing output without truncating authoritative notebook state

**Decision**: Default agent control-channel output to 1 MiB serialized per operation, locally configurable. Truncate oversized text with metadata; represent binary/multimodal output with MIME/type/size plus bounded preview/reference; keep the full authoritative output in the notebook/workspace.

**Rationale**: Large embedded outputs should not explode MCP/audit payloads, but the launcher should not destroy notebook results to protect the control plane.

## Decision 29: Audit metadata, not notebook contents or large output payloads

**Decision**: Record bounded session/operation/timing/outcome/lease/conflict metadata. Do not copy full cell source, large/binary output, tokens, or credentials to logs by default.

## Decision 30: Persistent replacement writes fail safely

**Decision**: Use atomic/safe replacement behavior for launcher-managed writes and preserve Jupyter atomic-save behavior or equivalent. `ENOSPC`/comparable failure leaves the prior saved file authoritative and does not advance metadata as though the write succeeded.

**Rationale**: Persistence claims are incomplete if a storage failure can corrupt the last good copy.

## Decision 31: Supported active-notebook rename/move updates lifecycle metadata

**Decision**: A supported Jupyter/notebook rename/move validates scope, updates `active_notebook_path`, and preserves document/version semantics. An unsupported out-of-band disappearance fails clearly rather than guessing a replacement notebook.

**Rationale**: Reopen and MCP attachment need a deterministic active notebook path after user-driven rename/move operations.

## Deferred non-MVP work

- Private repositories and credential forwarding.
- Full Git LFS/submodule support beyond detection/actionable errors.
- Workspace quotas, retention policy, garbage collection, archive/export UX, and user-facing workspace deletion.
- Stronger `strict` sandbox and egress filtering.
- Multi-user identity/collaboration/distributed scheduling.
- Guaranteed concurrent multiple readonly MCP clients.
- Streamable HTTP/remote-agent MCP transport.
- BinderHub/JupyterHub runtime backend.
