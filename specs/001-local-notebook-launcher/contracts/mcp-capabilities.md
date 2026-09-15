# MCP Capability Contract: Local Notebook Launcher

## Purpose

This contract defines what an MCP-capable agent must be able to do with a ready `NotebookSession`. It is intentionally independent of any specific MCP server package or implementation-specific tool names.

The launcher MAY adapt an existing Jupyter MCP server, an adapted Colab-style MCP server, or another compatible backend. The selected backend is conformant only when it satisfies the required semantic capabilities and trust-boundary rules below.

## Same-runtime invariant

All MCP operations MUST target the same Jupyter server/workspace/kernel lifecycle created for the browser-visible notebook session.

```text
Browser -> JupyterLab -----+
                          |
                          +--> same Jupyter server/kernel
                          |
Agent -> MCP adapter ------+
```

The adapter MUST NOT silently launch an independent notebook container or kernel for agent execution.

## Required P1 capability profile

| Capability ID | Semantics | Minimum observable result |
|---|---|---|
| `notebook.read` | Read the active notebook structure and cell ordering | Cell IDs/indices, cell types, sources, and bounded output metadata are available |
| `cell.read` | Read a specific cell and its current outputs | Requested cell source and current execution/output state are returned |
| `cell.execute` | Execute one code cell in the active kernel | Execution result identifies the cell and includes success/failure plus output/traceback |
| `notebook.execute_all` | Execute code cells in notebook order | Results identify progress/failing cell and preserve notebook/kernel state |
| `kernel.execute_code` | Execute diagnostic code without requiring permanent notebook mutation | Return value/stdout/stderr/error is surfaced |
| `output.read` | Retrieve current output for a cell/execution | Text and structured error output are available; multimodal output is passed when supported |
| `kernel.restart` | Restart the session kernel and reconnect | Subsequent MCP/browser execution uses the restarted kernel |

A backend that cannot provide all required capabilities MUST set `McpAttachment.available=false` for the P1 profile and report missing capabilities.

## Optional remediation profile

These capabilities are desirable for agents that diagnose and repair notebooks but are not required for the first execution-focused acceptance gate:

| Capability ID | Semantics |
|---|---|
| `cell.insert` | Insert a code/markdown cell at an explicit location |
| `cell.update` | Replace or patch cell source |
| `cell.delete` | Delete an explicitly identified cell |
| `cell.move` | Reorder a cell |
| `output.clear` | Clear one or more cell outputs |

The adapter MUST advertise optional capabilities honestly. It MUST NOT emulate unsupported mutation by rewriting arbitrary notebook files outside the Jupyter document/session semantics.

## Attachment descriptor

A ready session exposes a non-secret descriptor conceptually equivalent to:

```json
{
  "session_id": "4f0951dc-8c3f-4d8c-a8bb-8afdf9d056a8",
  "available": true,
  "status": "available",
  "transport": "stdio",
  "command": "notebook-launcher",
  "args": ["mcp", "4f0951dc-8c3f-4d8c-a8bb-8afdf9d056a8"],
  "backend": "configured-backend-name/version",
  "capabilities": [
    "notebook.read",
    "cell.read",
    "cell.execute",
    "notebook.execute_all",
    "kernel.execute_code",
    "output.read",
    "kernel.restart"
  ]
}
```

The descriptor MUST NOT contain the Jupyter token or backend secrets.

## Local stdio bridge

The preferred MVP invocation is:

```text
notebook-launcher mcp <session-id>
```

The bridge:

1. resolves private state for the session locally;
2. verifies that the owning Jupyter session is still running;
3. starts/connects the configured MCP backend;
4. injects the Jupyter URL/token/document path internally;
5. proxies MCP over stdio between the client and backend where required;
6. exits cleanly when the MCP client disconnects or session becomes invalid.

A later Streamable HTTP implementation MAY be offered, but it must preserve this contract and default to loopback/authenticated operation.

## Execution semantics

### Cell exception

A Python/kernel exception is an execution result, not automatically an MCP transport failure. The agent should receive:

- target cell identifier/index;
- execution success=false;
- exception type/message where available;
- traceback/output subject to backend/client support;
- continued ability to inspect/retry/restart unless the kernel itself died.

### Whole-notebook execution

`notebook.execute_all` MUST preserve cell order. On failure, the result MUST identify the failed cell. Whether execution stops on first failure or continues MUST be explicit in adapter/backend configuration; MVP defaults to stop-on-error for deterministic diagnosis.

### Timeout/cancellation

Execution calls MUST have a configurable upper bound or cancellation path. Timeout MUST be distinguishable from Python exception and MCP transport failure.

### GPU consistency

For a GPU-enabled session, code executed through MCP uses the same container/kernel device scope. An MCP-driven GPU probe and browser-driven probe must agree about GPU availability.

## Security and isolation invariants

- MCP attachment MUST NOT receive the host Docker socket.
- MCP attachment MUST NOT mount or expose host SSH keys, cloud credentials, arbitrary home directories, or unrelated session state.
- Raw Jupyter tokens MUST NOT be emitted in normal HTTP responses, browser status pages, or logs.
- Session A's MCP bridge MUST NOT attach to Session B without explicitly using Session B's session identifier/private state.
- Stopping a notebook session invalidates its MCP attachment and launcher-owned bridge processes.
- User-controlled repository/ref/path strings MUST never be interpolated into shell commands by MCP setup code.
- Agent code execution is intentionally powerful inside the notebook runtime; container isolation and repository trust warnings remain applicable.

## Audit events

The launcher records bounded execution metadata for MCP activity:

- session ID;
- semantic operation;
- target cell/index when applicable;
- start timestamp;
- duration;
- success/failure/timeout/cancelled;
- sanitized error category;
- optional client label if known.

Full notebook source, cell source, binary outputs, Jupyter tokens, and other secrets are not copied into audit logs by default.

## Conformance acceptance tests

A concrete MCP backend is accepted for the MVP when all of the following pass against a launcher-created session:

1. attach using the launcher descriptor without manually entering a Jupyter token;
2. read the requested notebook and identify its cells;
3. execute a deterministic cell and observe its output;
4. execute the complete notebook in order;
5. receive structured context from a deliberately failing cell, then successfully execute a later diagnostic action;
6. restart the kernel and execute code afterward;
7. on a GPU-enabled session, execute a GPU probe that agrees with browser execution;
8. stop the notebook session and confirm further MCP attachment/execution fails cleanly;
9. confirm ordinary API responses/logs contain no Jupyter token;
10. confirm the backend reports missing optional remediation capabilities instead of claiming unsupported operations.
