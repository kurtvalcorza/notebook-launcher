# Workspace Lifecycle Contract

## Purpose

Define Colab-like local persistence, trust, concurrency, active-notebook safety, and runtime ownership without making GitHub a write target.

## Separation of concerns

```text
GitHub source @ SHA  -> immutable source snapshot
                         |
                         v
                    persistent workspace
                    work/ + outputs/
                         |
                         v
                    disposable runtime
             full standard sandbox + Jupyter
             shared notebook document + MCP
```

Control metadata is transactional local state; notebook/workspace data remains ordinary files.

## Fresh source launch

1. Validate the GitHub notebook request and resolve source to immutable SHA.
2. Check active exact-commit/repository trust records.
3. If uncovered, render local trust challenge with one-time nonce and offer `trust exact commit`, `trust repository`, or deny/cancel.
4. Execute no repository-supplied setup/code until trust succeeds.
5. Acquire immutable source material.
6. Create a new workspace ID.
7. Materialize an editable working copy separately from immutable source provenance.
8. Create persistent `outputs/`.
9. Prepare/reuse the environment.
10. Claim the workspace's single active-session slot transactionally.
11. Construct and validate the complete standard sandbox policy.
12. Start JupyterLab with collaboration support and launcher save/version integration inside that sandbox.
13. Mark the session ready only after sandbox, collaboration, and notebook readiness checks succeed.

A fresh source launch creates a fresh workspace by default; it does not silently reuse a prior edited workspace.

## Reopen

Reopening by `workspace_id` MUST NOT reset files from GitHub.

- If no active session exists, claim the workspace session slot and start a new runtime against the existing working copy.
- If an active session already exists, return/direct the user to that session rather than creating another runtime.
- If recorded active ownership is stale after a crash, reconcile against the actual runtime before clearing the ownership record.
- If the cached environment image is missing, rebuild from recorded provenance without replacing workspace contents.
- If the recorded active notebook path no longer exists because of an unsupported out-of-band move/delete, fail clearly rather than selecting another notebook automatically.

## Stop

Stopping a session:

- stops/removes the runtime container/process;
- invalidates Jupyter/MCP credentials;
- invalidates/releases writable-agent lease and execution-control handles;
- releases the workspace active-session slot;
- leaves workspace files, notebook edits, outputs, trust records, and reusable environment cache intact.

The launcher does not automatically delete persistent workspaces in this MVP. User-facing workspace deletion is a separately specified future management capability; until then, a user may remove workspace data manually outside the launcher.

## Writable workspace semantics

The default writable agent may create, read, modify, rename, and delete files anywhere inside the persistent workspace. `/outputs` is a conventional artifact location, not a mandatory write-only enclave.

The active notebook is still writable, but it is governed by notebook-aware shared-document operations. Generic workspace file write/move/delete MUST reject the active notebook path so they cannot bypass document conflict/version semantics. Active-notebook rename/move uses a supported notebook/Jupyter operation and updates `active_notebook_path`.

Paths outside the workspace remain inaccessible unless the user explicitly granted local data. An `ro` user-data grant remains read-only even for a writable agent.

## Conflict semantics

Browser and MCP share the same authoritative Jupyter collaborative notebook document.

- Normal browser edits participate directly in that shared document and advance its opaque `document_version`.
- Agent notebook mutations require the version they were based on.
- A stale agent version rejects the mutation with `conflict` and no write occurs.
- Any independent full-document/file save path that could replace the active notebook outside the shared document is guarded by launcher/Jupyter save-version integration; stale saves are rejected and require refresh/retry.
- Non-notebook files use analogous `file_version` preconditions for overwrite/move/delete.
- The implementation MUST NOT silently use last-write-wins for stale writes.

The initial MCP/Jupyter collaboration stack must pass acceptance tests proving browser edits remain persistent after agent edits, agent edits remain persistent after browser edits, and a deliberately stale independent save cannot overwrite the authoritative notebook.

## Active notebook rename/move

A supported active-notebook rename/move:

1. validates the destination remains within the workspace and ends in `.ipynb`;
2. operates through the authoritative Jupyter/notebook path rather than generic file mutation;
3. preserves/advances document version semantics;
4. updates `Workspace.active_notebook_path` as part of the successful operation;
5. causes future MCP attachment/status/reopen to target the new path.

A failed rename leaves the prior path authoritative. An unsupported external rename/delete is reported as missing state; the launcher does not guess a replacement notebook.

## Persistent-write failure semantics

Launcher-managed replacement writes use safe/atomic replacement behavior appropriate to the filesystem. Jupyter notebook saves retain atomic-save behavior or equivalent protection.

If the filesystem reports `ENOSPC` or another replacement failure:

- the operation fails with an actionable storage error;
- the previously saved file remains authoritative and intact;
- a partial temporary file MUST NOT replace it;
- workspace metadata is not advanced as if the write succeeded.

## Save a Copy

Save a Copy duplicates a notebook to:

- a validated workspace-relative path; or
- a validated relative path under an explicitly configured writable user-data grant.

Rules:

- destination remains within selected scope;
- destination ends in `.ipynb`;
- overwrite defaults false;
- current saved outputs may be included;
- operation snapshots the current saved notebook state;
- replacement follows persistent-write failure semantics;
- operation never changes immutable source provenance;
- operation never commits, pushes, creates branches, or mutates GitHub.

## User-data grant

No host directory is mounted by default. The local user explicitly selects one directory and `ro`/`rw` mode through local management (MVP CLI contract). Remote launch URLs cannot specify the host path. The runtime sees the grant at a stable path such as `/mnt/user-data`.

The launcher MUST NOT automatically mount the whole home directory. Live mount-policy changes may require the active workspace session to stop/restart; the launcher reports this explicitly.

## Trust lifecycle

- exact-commit trust authorizes only that repository + immutable SHA;
- repository trust authorizes future revisions from that same repository;
- trust is stored locally and may be revoked for future launches;
- revocation does not silently terminate an already-running session;
- deny/cancel creates no positive trust grant and executes no repository code/setup.

## Agent attachment lifecycle

A ready writable session supports at most one active writable MCP lease. A second writable attachment is rejected until the lease is released or reconciled stale. Read-only attachments do not acquire the writable lease, but concurrent multiple read-only attachments are not guaranteed by the MVP.

Session stop invalidates all launcher-owned MCP runtime state and active execution-control handles.

## Execution cancellation lifecycle

Agent execution receives an operation ID and local deadline. Cancellation/deadline expiry interrupts the active kernel operation. If interrupt does not restore responsiveness within the configured grace interval, runtime policy may restart/replace the kernel while preserving the workspace. The result remains distinguishable as `cancelled` or `timeout`.

Cancellation ends the execution request, not the persistent workspace.

## Outputs

`outputs/` is persistent workspace-owned artifact storage intended for generated models, CSV/JSON files, images, reports, checkpoints, or other notebook results. Runtime cleanup never deletes it.

Agent-facing output retrieval is separately bounded (default 1 MiB serialized response per operation). Oversized/binary/multimodal output is represented with truncation/type/size/reference metadata while the complete authoritative output remains in the notebook/workspace. Audit metadata does not copy full output payloads by default.

## Git semantics

GitHub is immutable source input. The launcher exposes no Git publishing workflow. The editable working tree is not given GitHub write credentials and should not depend on a writable Git remote relationship.
