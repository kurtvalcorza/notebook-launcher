# MCP Capability Contract: Local Notebook Launcher

## Purpose

Define implementation-neutral agent capabilities for a ready `NotebookSession`. Backend tool names are private adapter details.

## Same-runtime invariant

All MCP operations target the same Jupyter server, persistent workspace, authoritative active notebook document, kernel lifecycle, sandbox, GPU policy, and resource limits visible to the browser. No hidden second execution environment is permitted.

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
| `notebook.move` | Rename/move the active notebook through the authoritative notebook/Jupyter path and update active-notebook metadata |
| `cell.execute` | Execute one code cell in active kernel and return an execution operation ID |
| `notebook.execute_all` | Execute code cells in notebook order and return an execution operation ID |
| `execution.cancel` | Cancel/interrupt an active agent execution operation |
| `kernel.execute_code` | Execute diagnostic code in active kernel and return an execution operation ID |
| `output.read` | Retrieve a bounded representation of execution/cell output |
| `kernel.restart` | Restart/reconnect session kernel |
| `workspace.file.read` | Read a non-active-notebook file inside the persistent workspace and return `file_version` |
| `workspace.file.write` | Create/replace a workspace file; existing files require `expected_file_version`; active notebook path is rejected |
| `workspace.file.move` | Rename/move a workspace file with version/precondition checks; active notebook path is rejected in favor of `notebook.move` |
| `workspace.file.delete` | Delete a workspace file using `expected_file_version`; active notebook path is rejected |
| `workspace.file.list` | List bounded workspace paths/metadata |

A backend/adapter missing any required writable capability is not conformant for the default MVP profile. The launcher MAY implement or supplement capabilities outside the underlying backend when required to preserve these semantics.

### `readonly`

Required capabilities are inspection-only:

- `notebook.read`
- `cell.read`
- `output.read`

The launcher/MCP boundary MUST reject cell execution, whole-notebook execution, arbitrary kernel code, execution cancellation of another controller's operation, kernel restart, notebook mutation/move, and workspace-file mutation for readonly attachments. Readonly does not consume the writable-agent lease.

The MVP does not guarantee multiple simultaneous readonly clients. A backend/bridge MAY serialize readonly attachments; it MUST still support the readonly profile when admitted.

## Authoritative notebook and optimistic concurrency contract

The active notebook is represented by one authoritative Jupyter collaborative document. Normal JupyterLab browser edits participate directly in that shared document. Every authoritative document change advances an opaque launcher-visible `document_version`.

Every MCP notebook/cell mutation MUST provide `expected_document_version` from the state the mutation was based on. If it does not match the authoritative current document, the operation returns a structured `conflict` result containing the new current version and performs no mutation.

Any independent full-document/file save path capable of replacing the active notebook outside the shared collaboration document MUST be guarded by launcher/Jupyter save-version integration. A stale save is rejected and must refresh/retry rather than replacing newer state.

Generic `workspace.file.write|move|delete` MUST reject the active notebook path. Active-notebook rename/move uses `notebook.move`, preserving document/version semantics and atomically updating `active_notebook_path` as part of the supported operation.

Workspace non-notebook file reads return an opaque `file_version`. Mutations of existing files require the expected version. Create operations may use a `must_not_exist` precondition. The version format is intentionally opaque so the implementation can use collaboration revisions, hashes, or another safe mechanism without changing the semantic contract.

No adapter may silently resolve stale writes with last-write-wins.

## Writable-agent lease

A session may have at most one active writable MCP attachment. `notebook-launcher mcp <session-id>` in writable mode MUST acquire a transactional write lease before exposing mutation/execution capabilities.

- second writable attachment → `writable_agent_busy`;
- normal disconnect → release lease;
- bridge crash → lease becomes reclaimable only after liveness/reconciliation rules confirm it is stale;
- session stop → invalidate lease;
- read-only attachment → no write lease.

The lease serializes writable agents; it does not block normal interactive browser use. Browser/agent conflict safety comes from the shared document plus version/save guards, not from blocking the browser.

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
  "capabilities": ["notebook.read", "cell.update", "cell.execute", "execution.cancel", "workspace.file.write"]
}
```

The descriptor MUST NOT contain raw Jupyter/backend credentials. Retrieving it does not acquire the writable-agent lease.

## Initial backend adapter

The first implementation targets Datalayer `jupyter-mcp-server` 2.x connected to the launcher-created Jupyter server. The adapter MAY filter, wrap, or supplement backend tools to satisfy this contract, including workspace file operations, permission checks, collaboration/version preconditions, active-notebook file guards, execution cancellation, output bounding, and lease enforcement.

The exact backend/Jupyter collaboration patch versions are pinned only after conformance tests pass. Backend-specific tool names and schemas are never the public launcher contract.

## Local stdio bridge

Preferred MVP invocation:

```text
notebook-launcher mcp <session-id>
```

The bridge resolves private state, validates the session/profile, acquires a writable lease when required, starts/connects the selected backend, injects Jupyter credentials internally, enforces semantic permissions/version/output/cancellation rules, proxies MCP over stdio, and exits/reconciles when the client disconnects or session becomes invalid.

## Execution semantics

### Single cell

Execution occurs through the active Jupyter kernel. The request receives an execution operation ID and returns success/failure plus stdout/stderr/display/traceback through the bounded output policy.

### Whole notebook

`notebook.execute_all`:

- executes code cells in notebook order;
- uses the active Jupyter kernel;
- defaults to stop-on-error;
- identifies the failing cell;
- preserves notebook/kernel state;
- exposes an execution operation ID that can be cancelled;
- MUST NOT silently convert the notebook into an unrelated Python script/runtime.

### Timeout and cancellation

Every agent execution uses a configurable deadline unless explicitly configured otherwise within local policy. `execution.cancel(operation_id)` or deadline expiry MUST terminate the requested execution path rather than merely relabel a still-running operation.

Cancellation behavior:

1. request a Jupyter kernel interrupt for the active execution;
2. wait a bounded grace interval for the kernel to become responsive;
3. if the kernel remains unresponsive, restart/replace the kernel according to runtime policy while preserving persistent workspace files;
4. return a distinct `cancelled` or `timeout` result;
5. leave the notebook session usable when the surrounding runtime remains healthy.

A kernel exception is an execution result, not automatically an MCP transport failure. Conflict, Python exception, timeout, cancellation, kernel death, permission denial, lease denial, and MCP transport failure are distinct outcomes.

## Bounded output contract

The default maximum serialized MCP output payload is **1 MiB per operation**, configurable locally.

- Oversized text MUST set `truncated=true` and include original-size metadata when known.
- Binary/multimodal output MUST return MIME/type/size metadata and only a bounded preview/reference representation through MCP.
- The full authoritative output remains in the notebook/workspace according to normal Jupyter persistence.
- Full output payloads MUST NOT be copied into bounded audit records by default.

The output limit constrains the control channel; it does not erase or truncate the notebook's persisted authoritative output.

## Persistence and storage errors

Agent edits target the persistent working notebook/shared Jupyter document. Agent-generated non-notebook files anywhere inside `/workspace` persist with the workspace. `/outputs` is the recommended conventional artifact location, not the only writable path.

Launcher-managed replacement file operations MUST fail safely on disk-full/comparable errors. An unsuccessful replacement MUST NOT leave a partial file as the authoritative state.

## Save a Copy

Save a Copy remains a launcher/workspace operation. If exposed to agents later, it maps to an explicit semantic launcher capability and never to Git commit/push.

## Security invariants

- MCP inherits the already-verified standard runtime sandbox and explicit mounts.
- The session is not advertised ready for agent execution until mandatory sandbox and collaboration readiness gates pass.
- No Docker socket, host SSH keys, GitHub/cloud credentials, or unrelated host directories.
- No Git commit/push/branch/GitHub mutation capability in this feature.
- Session A cannot resolve Session B private state.
- Readonly cannot escalate to write without explicit authorization/session policy.
- A second writable agent cannot bypass the active write lease.
- Generic file operations cannot overwrite/delete/move the active notebook outside notebook-aware semantics.
- Stopping the session invalidates MCP private state/bridges/leases/execution handles.
- Audit logs omit raw credentials, full cell contents, and full large/binary output payloads by default.

## Writable conformance test

A backend/adapter is accepted only when it can:

1. attach without manual Jupyter token entry;
2. read the active notebook and version;
3. edit a cell with a valid version precondition;
4. reject a deliberately stale agent edit without overwriting the newer browser edit;
5. preserve normal browser edits through the authoritative collaboration document and reject a deliberately stale independent save path;
6. execute a cell and the full notebook in order;
7. cancel a deliberately non-terminating execution or terminate it at its configured timeout and continue using the persistent workspace;
8. surface a deliberate failure, repair it, and re-execute;
9. create/edit/rename/delete representative non-notebook workspace files;
10. reject generic file overwrite/move/delete against the active notebook and successfully rename/move it through `notebook.move`, updating active-notebook metadata;
11. restart the kernel;
12. preserve bidirectional browser↔agent notebook edits on disk;
13. return bounded/truncation/type metadata for representative oversized text and binary/multimodal output while preserving full notebook output;
14. reject a second simultaneous writable agent attachment;
15. on GPU sessions, observe the same GPU as browser execution.

## Readonly conformance test

A readonly attachment can inspect notebook/cells/outputs and receives explicit permission errors for execution, execution cancellation, notebook move/mutation, kernel restart, workspace-file mutation, and write escalation.
