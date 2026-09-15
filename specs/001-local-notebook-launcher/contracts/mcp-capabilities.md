# MCP Capability Contract: Local Notebook Launcher

## Purpose

Define implementation-neutral agent capabilities for a ready `NotebookSession`.

## Same-runtime invariant

All MCP operations target the same Jupyter server, persistent workspace, active notebook, kernel lifecycle, sandbox, and GPU policy visible to the browser. No hidden second execution environment is permitted.

## Agent profiles

### `write` (default)

Required capabilities:

| Capability | Semantics |
|---|---|
| `notebook.read` | Read active notebook structure/cells/outputs |
| `cell.read` | Read a cell |
| `cell.insert` | Insert a code/markdown cell |
| `cell.update` | Edit cell source |
| `cell.delete` | Delete an identified cell |
| `cell.execute` | Execute one code cell in active kernel |
| `notebook.execute_all` | Execute code cells in notebook order |
| `kernel.execute_code` | Execute diagnostic code in active kernel |
| `output.read` | Retrieve execution/cell output |
| `kernel.restart` | Restart/reconnect session kernel |

A backend missing any required writable capability is not conformant for the default MVP profile.

### `readonly`

Required capabilities are inspection-only:

- `notebook.read`
- `cell.read`
- `output.read`

The launcher/MCP boundary MUST reject `cell.execute`, `notebook.execute_all`, `kernel.execute_code`, kernel restart, and all mutation operations for readonly sessions. This is deliberately strict because arbitrary code execution can mutate persistent workspace state.

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
  "capabilities": ["notebook.read", "cell.read", "cell.update", "cell.execute"]
}
```

The descriptor MUST NOT contain raw Jupyter/backend credentials.

## Local stdio bridge

Preferred MVP invocation:

```text
notebook-launcher mcp <session-id>
```

The bridge resolves private state, validates session/profile, starts/connects the configured MCP backend, injects Jupyter credentials internally, enforces semantic permissions, and exits when the client disconnects or session becomes invalid.

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
- MUST NOT silently convert the notebook into an unrelated Python script/runtime.

### Failure

A kernel exception is an execution result, not automatically an MCP transport failure. The agent can inspect traceback/cell context and retry/edit/restart when its profile permits.

### Timeout/cancellation

Execution has configurable timeout/cancellation. Timeout, cancellation, kernel exception, kernel death, and MCP transport failure are distinguishable outcomes.

### Persistence

Agent edits occur against the persistent working notebook. Outputs are visible in the same Jupyter document/kernel and persist when saved. Agent-generated files written to `/workspace` or `/outputs` persist with the workspace.

### Save a Copy

Save a Copy is a launcher/workspace operation, not an MCP publishing operation. An agent may request it only through an explicitly exposed semantic launcher capability; it never maps to Git commit/push.

## Security invariants

- MCP inherits the runtime sandbox and explicit mounts.
- No Docker socket, host SSH keys, GitHub/cloud credentials, or unrelated host directories.
- No Git commit/push/branch/GitHub mutation capability in this feature.
- Session A cannot resolve Session B private state.
- Readonly cannot escalate to write without explicit new authorization/session policy.
- Stopping the session invalidates MCP private state/bridges.
- Audit logs omit raw credentials and full cell contents by default.

## Writable conformance test

A backend is accepted when it can attach without manual Jupyter token entry, read the notebook, edit a cell, execute it, execute the full notebook in order, surface a deliberate failure, repair/re-execute, restart the kernel, and on GPU sessions observe the same GPU as browser execution.

## Readonly conformance test

A readonly attachment can inspect notebook/cells/outputs and receives explicit permission errors for execution, cell mutation, kernel restart, and other state-changing operations.
