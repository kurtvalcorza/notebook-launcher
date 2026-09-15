# MCP Capability Contract: Local Notebook Launcher

## Purpose

Define implementation-neutral MCP semantics for a ready launcher-created notebook session. Backend-specific tools are private adapter details.

## Same-runtime and fixed-target invariants

MCP uses the same Jupyter server, workspace, sandbox, network policy, GPU scope, active kernel, and launcher-designated `active_notebook_path` visible to the user.

Opening or focusing another notebook in JupyterLab MUST NOT silently change the MCP target. The MVP has no focus-based retarget operation. Supported rename/move of the active notebook updates the target; explicit retargeting to another notebook is future scope.

## Profiles

### `write`

Required semantic capabilities:

| Capability | Semantics |
|---|---|
| `notebook.read` | Read active notebook + `document_version` |
| `cell.read` | Read active-notebook cell + version |
| `cell.insert` | Insert using `expected_document_version` |
| `cell.update` | Edit using version precondition |
| `cell.delete` | Delete using version precondition |
| `notebook.move` | Rename/move active notebook through authoritative Jupyter path |
| `cell.execute` | Queue/execute one active-notebook code cell; return operation ID |
| `notebook.execute_all` | Execute code cells in order through broker |
| `kernel.execute_code` | Queue diagnostic code through broker |
| `execution.cancel` | Cancel an agent-owned execution operation only |
| `output.read` | Return bounded output representation |
| `kernel.restart` | Restart/reconnect kernel when policy permits |
| `workspace.file.read` | Read ordinary workspace file/version |
| `workspace.file.write` | Create/replace ordinary file; active notebook rejected |
| `workspace.file.move` | Move ordinary file; active notebook rejected |
| `workspace.file.delete` | Delete ordinary file; active notebook rejected |
| `workspace.file.list` | Bounded workspace listing |

### `readonly`

Allowed: `notebook.read`, `cell.read`, `output.read`.

Denied: execution, cancellation, kernel restart, notebook mutation/move, workspace file mutation, permission escalation.

Readonly does not consume the writable lease. Concurrent multiple readonly clients are not an MVP guarantee.

## Writable-agent lease

At most one writable MCP attachment may hold a session lease.

- second writer → `writable_agent_busy`;
- normal disconnect → release;
- crash → reclaim only after stale reconciliation;
- session stop → invalidate;
- descriptor retrieval does not acquire lease.

## Authoritative document and conflicts

The active notebook is one Jupyter collaborative document. Normal browser edits participate in it. Reads expose an opaque `document_version`; agent mutations require the version they were based on.

Stale mutation → structured `conflict`, current version, no write.

Any independent full-file save path that could replace the active notebook must carry/derive equivalent version protection. Generic workspace file write/move/delete rejects the active notebook path.

No backend may silently use last-write-wins.

## Execution broker

All agent kernel execution passes through a session-local broker. Browser execution remains possible directly through Jupyter, so the broker MUST observe actual Jupyter kernel/request ownership rather than assume that a dispatched agent request is active.

### Operation states

```text
queued
  | dispatch only when kernel observed idle
  v
dispatched
  | observe busy/status parent request ID
  v
running
  |                    |
  v                    v
completed/failed   cancelling -> cancelled/timeout
```

Additional state `cancel_pending` applies when cancellation is requested after dispatch but before the agent request is confirmed as the active busy-parent request.

### Ownership identifiers

Every dispatched agent execution records:
- launcher operation ID;
- Jupyter execute/request message ID;
- queue/dispatched timestamps;
- current observed kernel busy state;
- observed parent message ID for the active kernel request.

The broker considers an agent operation **active-owned** only when the kernel’s active/busy parent request ID matches the operation’s Jupyter message ID.

### Dispatch

1. While kernel is observed busy with a browser/other non-agent request, keep agent execution queued.
2. Dispatch only after an idle observation.
3. Do not infer ownership from dispatch alone; confirm via Jupyter status/request correlation.
4. Agent operations are serialized by the broker; execute-all uses the same rule for each executed cell/request.

### Cancellation and timeout

- Cancelling/timing out `queued` operation: terminate/remove it, no kernel interrupt.
- Cancelling/timing out `dispatched` but not active-owned: set `cancel_pending`; do not interrupt the kernel.
- If a cancel-pending request later becomes active-owned: interrupt immediately.
- Cancelling/timing out `running` active-owned request: issue Jupyter kernel interrupt.
- A kernel-wide interrupt MUST NOT be sent while the active busy-parent request belongs to browser/other work.
- After interrupt, wait bounded grace. If the agent-owned request/kernel remains unresponsive, restart/replace kernel per policy while preserving workspace files.
- `cancelled`, `timeout`, Python exception, kernel death, permission denial, lease denial, and transport failure remain distinct results.

The browser may enqueue work after an agent request; an interrupt against a confirmed active agent request may affect only current kernel execution semantics, not intentionally target queued browser work.

## Attachment descriptor

Example:

```json
{
  "session_id": "<uuid>",
  "notebook_path": "tutorials/example.ipynb",
  "available": true,
  "status": "available",
  "transport": "stdio",
  "command": "notebook-launcher",
  "args": ["mcp", "<session-id>"],
  "agent_mode": "write",
  "backend": "name/version",
  "write_lease_available": true,
  "capabilities": ["notebook.read", "cell.update", "cell.execute", "execution.cancel"]
}
```

No raw Jupyter/backend credential appears in the descriptor.

## Initial backend

Initial target: Datalayer `jupyter-mcp-server` 2.x against launcher-created Jupyter. The launcher may filter/wrap/supplement backend tools to satisfy this contract.

Exact backend/Jupyter collaboration versions are pinned only after conformance tests pass. Product semantics are not weakened to fit the backend.

## Bounded output

Default maximum serialized MCP output payload: 1 MiB/operation, configurable.

- oversized text: `truncated=true`, size metadata when known;
- binary/multimodal: MIME/type/size + bounded preview/reference;
- full authoritative output remains in notebook/workspace;
- full payload is not copied into audit logs by default.

## Security invariants

- MCP inherits already-verified sandbox and egress policy.
- No host credentials/Docker socket/GitHub write capability/unrelated host directories.
- Session A cannot resolve Session B private state.
- Readonly cannot escalate.
- Generic file operations cannot bypass active-notebook semantics.
- Session stop invalidates bridge/lease/execution handles.
- Browser focus does not retarget MCP.

## Writable conformance tests

A backend/adapter is accepted only when it can:

1. attach without manual token entry;
2. read active notebook/version;
3. edit with valid version and reject stale edit;
4. preserve browser↔agent edits through shared document;
5. execute cell and notebook in order;
6. surface/repair a controlled failure;
7. cancel a running agent-owned non-terminating request and continue;
8. while a browser request is running, queue an agent request then cancel/timeout it without interrupting the browser request;
9. handle an idle→dispatch race without interrupting a non-agent active parent request;
10. create/edit/move/delete ordinary workspace files while rejecting generic active-notebook mutation;
11. move active notebook through `notebook.move` and update metadata;
12. return bounded large/binary output;
13. reject a second writable attachment;
14. preserve fixed MCP target when user opens/focuses another notebook;
15. on GPU session, observe same GPU as browser.

## Readonly conformance tests

Readonly can inspect and receives explicit permission failures for execution, cancellation, kernel restart, notebook move/mutation, workspace mutation, and escalation.
