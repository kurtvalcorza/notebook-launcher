# Research: Local Notebook Launcher

## Decision 1: Prove the local path without BinderHub first

**Decision**: Use `repo2docker` + local Docker + JupyterLab for the first POC. Keep BinderHub/JupyterHub as a later runtime backend.

**Rationale**: The unresolved risks are lower-level: resolve a GitHub notebook automatically, produce a reusable runtime image, open the exact notebook, expose the local WSL2 GPU, and allow an agent to operate the same kernel. Those can be proven with fewer moving parts than a Kubernetes stack.

**Alternatives considered**:
- **BinderHub + JupyterHub + k3s immediately**: representative but adds Kubernetes, Helm, registry, spawning, proxy, and cluster GPU scheduling before the core flow is proven.
- **Plain host virtualenv + Jupyter**: simpler but weak isolation and dependency reproducibility.

## Decision 2: Keep stable launcher and agent contracts independent of backend

**Decision**: `/open` is the notebook launch contract, and `McpAttachment` plus the semantic capability profile is the agent-control contract. Backend-specific URLs/tool names are not embedded into badges or user workflows.

**Rationale**: Local Docker can later be replaced by BinderHub/JupyterHub, and one MCP implementation can be replaced by another, without changing the user's launch URL or the agent capabilities expected from a ready session.

## Decision 3: Resolve mutable Git refs to commit SHAs

**Decision**: Every launch records an immutable commit SHA before building/running.

**Rationale**: Branches/tags are user-friendly inputs but poor cache/provenance identities. Resolving first prevents a cache entry from silently representing different source states.

For `/blob/...` URLs where branch names contain `/`, compare candidate prefixes against remote refs and choose the longest valid ref match. Explicit `repo`/`ref`/`path` remains the canonical programmatic form.

## Decision 4: Use repo2docker for environment preparation

**Decision**: Build notebook images through repo2docker instead of creating a new environment-spec format.

**Rationale**: It already understands Binder-style configuration and common Python/Conda/package declarations and is compatible with the eventual Binder ecosystem direction.

## Decision 5: GPU policy is explicit but optional

**Decision**: Support `gpu=auto|on|off`, defaulting to `auto`.

**Rationale**: The POC should prove local GPU execution, but CPU notebooks remain useful. `gpu=on` gives a strict acceptance mode.

Host prerequisite chain:

```text
Windows NVIDIA driver
  -> WSL2 GPU exposure
  -> Docker/container runtime
  -> NVIDIA Container Toolkit
  -> container GPU device request
  -> CUDA/framework inside notebook image
```

The launcher validates where observable but does not install/manage drivers or toolkit components.

## Decision 6: Long builds use asynchronous launch state

**Decision**: `/open` creates a launch and redirects to a local status page rather than holding one HTTP request open through clone/build/start.

**Rationale**: First builds may take minutes. A launch state machine keeps the service responsive and provides useful phases/cancellation points.

## Decision 7: Jupyter remains authenticated even on loopback

**Decision**: Generate a per-session Jupyter token and bind published ports only to `127.0.0.1`.

**Rationale**: Loopback reduces exposure, but disabling notebook authentication is unnecessary. The launcher can use the token internally while preventing ordinary UI/API/log surfaces from revealing it.

## Decision 8: Agent execution is a P1 requirement, not an optional plugin

**Decision**: The MVP is not accepted until an MCP-capable agent can attach to a launched notebook and execute it.

**Rationale**: The intended product value is not merely one-click local Jupyter. It is one-click local compute that can be operated by modern coding agents. This changes the POC acceptance path from `GitHub -> Jupyter -> GPU` to `GitHub -> Jupyter -> GPU -> MCP -> agent execution`.

## Decision 9: Specify MCP capabilities, not one project's tool names

**Decision**: Define an implementation-neutral semantic MCP capability profile and isolate the concrete backend in `mcp.py`.

**Rationale**: Existing Jupyter-focused MCP servers already provide notebook/cell inspection, execution, kernel control, and JupyterLab integration. A Colab-style MCP implementation demonstrates a related browser/runtime bridge pattern. The launcher should be free to adopt, wrap, or replace an implementation without changing its public contract.

**Initial backend strategy**: Prefer adapting an existing Jupyter-native MCP server that can target `JUPYTER_URL`/credentials and the active notebook. Do not build MCP protocol framing from scratch unless no maintained backend satisfies the capability contract.

**Alternatives considered**:
- **Fork a Colab-specific MCP implementation wholesale**: possible, but includes Colab frontend/session bridging that is unnecessary when the launcher controls Jupyter directly.
- **Implement a new MCP server from scratch**: maximum control but unnecessary scope and protocol-maintenance burden for the POC.

## Decision 10: Use stdio as the default local MCP transport

**Decision**: Expose a launcher-owned command such as `notebook-launcher mcp <session-id>` and use stdio by default.

**Rationale**: Stdio keeps the MCP endpoint local to the client process, requires no additional listening port, and fits CLI coding-agent integrations. A future Streamable HTTP transport can be added without changing semantic capabilities.

## Decision 11: MCP controls the same Jupyter runtime the user sees

**Decision**: MCP must attach to the existing Jupyter server/workspace/kernel lifecycle created by the launcher.

**Rationale**: A second hidden kernel would make execution state, outputs, GPU allocation, and debugging diverge between agent and browser. The same-runtime invariant makes agent operations observable and predictable.

## Decision 12: Keep raw Jupyter credentials inside the launcher/bridge

**Decision**: Ordinary session/MCP descriptors expose command, session ID, transport, backend, and capabilities but not raw Jupyter tokens.

**Rationale**: MCP clients should not need to store notebook credentials. A launcher-owned bridge can resolve session metadata locally and inject credentials directly into the selected backend process.

If cross-process state is required, store session metadata under an owner-only local state directory and invalidate/remove it when the session stops.

## Decision 13: Audit execution metadata without copying notebook contents by default

**Decision**: Record MCP attachment and execution events including session ID, semantic operation, timestamp, duration, success/failure, and bounded diagnostic metadata. Do not persist full cell source/output by default.

**Rationale**: Agent-driven arbitrary code execution benefits from traceability, but copying entire notebooks/outputs into logs can leak data and create large logs. Detailed source/output can remain available in the notebook itself.

## Decision 14: Execution failure is an application result, not necessarily a transport failure

**Decision**: A cell exception returns structured execution failure/output while preserving the MCP/Jupyter session whenever possible.

**Rationale**: Agents need traceback/cell context to diagnose and continue. Treating every Python exception as a broken MCP transport would make autonomous notebook repair impractical.

## Deferred questions

- Persistent user workspace semantics versus disposable session files.
- Private GitHub repositories and credential forwarding.
- Git LFS and submodule policy.
- Multi-user identity and quota policy.
- Kubernetes/BinderHub/JupyterHub deployment topology and registry choice.
- GPU sharing, MIG, time-slicing, or queueing.
- Exact MCP backend package/version after capability-spike validation.
- Streamable HTTP MCP transport and remote-agent scenarios.
- Whether notebook mutation becomes mandatory after the first execution-focused MVP.
