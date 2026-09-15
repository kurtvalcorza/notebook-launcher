# Research: Local Notebook Launcher

## Decision 1: Prove the local path without BinderHub first

Use repo2docker + local Docker + JupyterLab for the POC; keep BinderHub/JupyterHub as later runtime backends. This proves source resolution, reproducible environment creation, sandboxing, GPU passthrough, persistent workspace semantics, and MCP agent control without Kubernetes overhead.

## Decision 2: Keep public contracts backend-neutral

`/open` and workspace semantics are launcher contracts; semantic MCP capabilities are the agent contract. Docker, repo2docker, BinderHub, JupyterHub, Colab MCP, Jupyter MCP, gVisor, and Kata remain replaceable implementation details.

## Decision 3: Resolve Git refs to immutable SHAs

Branches/tags are convenient inputs but poor provenance/cache identities. Every remote launch records an immutable commit SHA before workspace/environment creation. Slash-containing branch names are resolved against remote refs rather than parsed naïvely.

## Decision 4: Use repo2docker for environment preparation

Do not invent another repository environment format. Use repo2docker/Binder-style declarations and cache the resulting runtime image by immutable source/environment identity.

## Decision 5: Separate immutable source, persistent workspace, and disposable runtime

**Decision**:

```text
source snapshot   immutable provenance/reference
workspace         persistent mutable user state
runtime/kernel    disposable compute attached to workspace
```

A fresh GitHub launch creates a fresh workspace. Runtime shutdown does not delete the workspace. Reopening a workspace starts a new runtime against the existing working copy.

**Rationale**: This matches the useful part of hosted notebook behavior while avoiding the ambiguity of mutating a Git checkout. It also lets environment cache reuse remain independent from user work persistence.

## Decision 6: GitHub is source input, never a write target

The launcher does not commit, push, create branches, or inject GitHub credentials. The editable work tree should not depend on a writable Git remote relationship; source provenance is retained separately. Any future Git publishing capability would be a separate explicitly specified feature.

## Decision 7: Save a Copy is local persistence, not Git publishing

Save a Copy duplicates the current saved notebook into another validated workspace path or an explicitly mounted user-data location. It may include current outputs. It never modifies the source snapshot or GitHub.

## Decision 8: Optional local user-data mount is explicit

No home directory or arbitrary host directory is mounted automatically. The user may explicitly select one local directory and `ro`/`rw` mode, exposed at a stable location such as `/mnt/user-data`. Remote launch URLs cannot choose host paths.

## Decision 9: Outbound network allowed; inbound tightly limited

The POC permits outbound sandbox network access because notebooks routinely need PyPI, Hugging Face, model weights, datasets, and APIs. Inbound exposure is limited to launcher-controlled services published on loopback. Stronger egress filtering is deferred.

## Decision 10: Standard sandbox is defense-in-depth, not hostile-code containment

Use non-root execution where compatible, no-new-privileges, dropped unnecessary capabilities, seccomp/equivalent filtering, resource limits, minimal mounts, and no Docker socket/host credentials. The trust model remains user-trusted public repositories; stronger `strict` sandbox backends remain future work.

## Decision 11: Require explicit trust before first remote execution

A localhost service can be reached by browser navigation and should not silently execute a newly supplied repository. Before repository-supplied build/runtime code runs, an unseen/untrusted source enters a local confirmation step showing repository, SHA, and notebook path. Trust can be remembered locally according to policy.

## Decision 12: GPU policy is explicit

Support `gpu=auto|on|off`, default `auto`. The launcher validates observable GPU/container prerequisites but does not install Windows drivers, CUDA drivers, or NVIDIA Container Toolkit.

## Decision 13: Agent execution is P1 and uses the same Jupyter runtime

MCP attaches to the already-running Jupyter server/workspace/kernel. A second hidden runtime is prohibited because it would diverge state, outputs, filesystem, and GPU behavior.

## Decision 14: Writable default plus true readonly profile

The default agent profile supports read/edit/execute/restart. `readonly` is inspection-only: no code execution and no mutation. This is intentional because arbitrary code execution can itself mutate files even if direct cell-edit tools are hidden.

## Decision 15: Specify semantic MCP capabilities, not tool names

Prefer adapting an existing maintained Jupyter-capable MCP backend. Conformance is measured against notebook/cell read, mutation, execution, outputs/errors, kernel restart, and permission enforcement—not package-specific tool names.

## Decision 16: Use stdio as default MCP transport

A command such as `notebook-launcher mcp <session-id>` resolves private session credentials locally and bridges the client to the configured backend. Raw Jupyter credentials are not returned in ordinary API/status output.

## Decision 17: Notebook execution remains notebook execution

`notebook.execute_all` uses the active Jupyter kernel and notebook cell order, default stop-on-error, and preserves notebook/kernel state. The launcher does not silently convert the notebook to an unrelated script/runtime for agent execution.

## Decision 18: Audit metadata, not notebook contents

Record bounded session/operation/timing/success/error metadata. Full cell sources, binary outputs, and credentials stay out of logs by default.

## Deferred questions

- Private repositories and authentication.
- Git LFS/submodule policy beyond basic detection/reporting.
- Workspace quotas, retention, garbage collection, and export/archive UX.
- Stronger `strict` sandbox backend and egress restrictions.
- Multi-user identity, quotas, collaboration, distributed scheduling.
- Exact MCP backend/version after capability spike.
- Streamable HTTP MCP and remote-agent scenarios.
