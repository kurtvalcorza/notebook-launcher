# MCP Capability Contract: Local Notebook Launcher

## Purpose

Define implementation-neutral agent capabilities for a ready `NotebookSession`. Backend tool names are private adapter details.

## Same-runtime invariant

All MCP operations target the same Jupyter server, persistent workspace, active notebook, kernel lifecycle, sandbox, GPU policy, and resource limits visible to the browser. No hidden second execution environment is permitted.

## Agent profiles

### `write` (default)

Required capabilities:

| Capability | Semantics |
|---|---|
| `notebook.read` | Read active notebook structure/cells/outputs and current `document_version` |
| `cell.read` | Read a cell and current `document_version` |
| `cell.insert` | Insert a cell using `expected_document_version` |
| `cell.update` | Edit cell source using `expected_document_version` |
| `cell.delete` | Delete a cell using `expected_document_version` |
| `cell.execute` | Execute one code cell in active kernel |
| `notebook.execute_all` | Execute code cells in notebook order |
| `kernel.execute_code` | Execute diagnostic code in active kernel |
| `output.read` | Retrieve execution/cell output |
| `kernel.restart` | Restart/reconnect session kernel |
| `workspace.file.read` | Read a file inside the persistent workspace and return `file_version` |
| `workspace.file.write` | Create/replace a workspace file; existing files require `expected_file_version` |
| `workspace.file.move` | Rename/move a workspace file with version/precondition checks |
| `workspace.file.delete` | Delete a workspace file using `expected_file_version` |
| `workspace.file.list` | List bounded workspace paths/metadata |

A backend/adapter missing any required writable capability is not conformant for the default MVP profile.

### `readonly`

Required capabilities are inspection-only:

- `notebook.read`
- `cell.read`
- `output.read`

The launcher/MCP boundary MUST reject cell execution, whole-notebook execution, arbitrary kernel code, kernel restart, notebook mutation, and workspace-file mutation for readonly attachments. Readonly does not consume the writable-agent lease.

## Optimistic concurrency contract

Notebook reads return an opaque `document_version`. Every notebook/cell mutation MUST provide `expected_document_version` from the state the mutation was based on. If it does not match the authoritative current document, the operation returns a structured `conflict` result containing the new current version and performs no mutation.

Workspace file reads return an opaque `file_version`. Mutations of existing files require the expected version. Create operations may use a `must_not_exist` precondition. The version format is intentionally opaque so the implementation can use Jupyter collaboration revisions, hashes, or another safe mechanism without changing the semantic contract.

No adapter may silently resolve stale writes with last-write-wins.

## Writable-agent lease

A session may have at most one active writable MCP attachment. `notebook-launcher mcp <session-id>` in writable mode MUST acquire a transactional write lease before exposing mutation/execution capabilities.

- second writable attachment → `writable_agent_busy`;
- normal disconnect → release lease;
- bridge crash → lease becomes reclaimable only after liveness/reconciliation rules confirm it is stale;
- session stop → invalidate lease;
- read-only attachment → no write lease.

The lease serializes writable agents; it does not block normal interactive browser use. Browser/agent conflicting saves are handled by version preconditions/shared document state.

## Attachment descriptor

```json
{
  "session_id": "<uuid>",
  "available": true,
  "status": "available",
  "transport": "stdio",
  "command": "notebook-launcher",
  "args": ["mcp", "<session-id>"],
  "agent_mode": "write",
  "backend": "name/version",
  "write_lease_available": true,
  "capabilities": ["notebook.read", "cell.update", "cell.execute", "workspace.file.write"]
}
```

The descriptor MUST NOT contain raw Jupyter/backend credentials. Retrieving it does not acquire the writable-agent lease.

## Initial backend adapter

The first implementation targets Datalayer `jupyter-mcp-server` 2.x connected to the launcher-created Jupyter server. The adapter MAY filter, wrap, or supplement backend tools to satisfy this contract, including workspace file operations, permission checks, version preconditions, and lease enforcement.

The exact backend/Jupyter collaboration patch versions are pinned only after conformance tests pass. Backend-specific tool names and schemas are never the public launcher contract.

## Local stdio bridge

Preferred MVP invocation:

```text
notebook-launcher mcp <session-id>
```

The bridge resolves private state, validates the session/profile, acquires a writable lease when required, starts/connects the selected backend, injects Jupyter credentials internally, enforces semantic permissions/version checks, proxies MCP over stdio, and exits/reconciles when the client disconnects or session becomes invalid.

## Execution semantics

### Single cell

Execution occurs through the active Jupyter kernel. Result identifies the target cell and returns success/failure plus stdout/stderr/display/traceback as supported.

### Whole notebook

`notebook.execute_all`:

- executes code cells in notebook order;
- uses the active Jupyter kernel;
- defaults to stop-on-error;
- identifies the failing cell;
- preserves notebook/kernel state;
- MUST NOT silently convert the notebook into an unrelated script/runtime.

### Failure

A kernel exception is an execution result, not automatically an MCP transport failure. Conflict, Python exception, timeout, cancellation, kernel death, permission denial, lease denial, and MCP transport failure are distinct outcomes.

### Persistence

Agent edits target the persistent working notebook/shared Jupyter document. Agent-generated files anywhere inside `/workspace` persist with the workspace. `/outputs` is the recommended conventional artifact location, not the only writable path.

### Save a Copy

Save a Copy remains a launcher/workspace operation. If exposed to agents later, it maps to an explicit semantic launcher capability and never to Git commit/push.

## Security invariants

- MCP inherits the runtime sandbox and explicit mounts.
- No Docker socket, host SSH keys, GitHub/cloud credentials, or unrelated host directories.
- No Git commit/push/branch/GitHub mutation capability in this feature.
- Session A cannot resolve Session B private state.
- Readonly cannot escalate to write without explicit authorization/session policy.
- A second writable agent cannot bypass the active write lease.
- Stopping the session invalidates MCP private state/bridges/leases.
- Audit logs omit raw credentials and full cell contents by default.

## Writable conformance test

A backend/adapter is accepted only when it can:

1. attach without manual Jupyter token entry;
2. read the active notebook and version;
3. edit a cell with a valid version precondition;
4. reject a deliberately stale cell edit without overwriting the newer browser edit;
5. execute a cell and the full notebook in order;
6. surface a deliberate failure, repair it, and re-execute;
7. create/edit/rename/delete representative non-notebook workspace files;
8. restart the kernel;
9. preserve bidirectional browser↔agent notebook edits on disk;
10. reject a second simultaneous writable agent attachment;
11. on GPU sessions, observe the same GPU as browser execution.

## Readonly conformance test

A readonly attachment can inspect notebook/cells/outputs and receives explicit permission errors for execution, mutation, kernel restart, workspace-file mutation, and write escalation.
