# Workspace Lifecycle Contract

## Purpose

Define Colab-like local persistence, trust, concurrency, and runtime ownership without making GitHub a write target.

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
                    Jupyter + kernel + MCP
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
11. Start a disposable runtime against the workspace.

A fresh source launch creates a fresh workspace by default; it does not silently reuse a prior edited workspace.

## Reopen

Reopening by `workspace_id` MUST NOT reset files from GitHub.

- If no active session exists, claim the workspace session slot and start a new runtime against the existing working copy.
- If an active session already exists, return/direct the user to that session rather than creating another runtime.
- If recorded active ownership is stale after a crash, reconcile against the actual runtime before clearing the ownership record.
- If the cached environment image is missing, rebuild from recorded provenance without replacing workspace contents.

## Stop

Stopping a session:

- stops/removes the runtime container/process;
- invalidates Jupyter/MCP credentials;
- invalidates/releases writable-agent lease;
- releases the workspace active-session slot;
- leaves workspace files, notebook edits, outputs, trust records, and reusable environment cache intact.

Workspace deletion is a separate explicit user action/future management surface.

## Writable workspace semantics

The default writable agent may create, read, modify, rename, and delete files anywhere inside the persistent workspace. `/outputs` is a conventional artifact location, not a mandatory write-only enclave.

Paths outside the workspace remain inaccessible unless the user explicitly granted local data. An `ro` user-data grant remains read-only even for a writable agent.

## Conflict semantics

Browser and MCP share the same authoritative notebook document. Notebook/cell reads expose an opaque `document_version`; agent mutations require the version they were based on. A stale version rejects the mutation with `conflict` and no write occurs.

Non-notebook files use analogous `file_version` preconditions for overwrite/move/delete. The implementation MUST NOT silently use last-write-wins for stale agent mutations.

The initial MCP/Jupyter collaboration stack must pass an acceptance test proving browser edits remain persistent after agent edits and agent edits remain persistent after browser edits.

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

A ready writable session supports at most one active writable MCP lease. A second writable attachment is rejected until the lease is released or reconciled stale. Read-only attachments do not acquire the writable lease.

Session stop invalidates all launcher-owned MCP runtime state.

## Outputs

`outputs/` is persistent workspace-owned artifact storage intended for generated models, CSV/JSON files, images, reports, checkpoints, or other notebook results. Runtime cleanup never deletes it.

## Git semantics

GitHub is immutable source input. The launcher exposes no Git publishing workflow. The editable working tree is not given GitHub write credentials and should not depend on a writable Git remote relationship.
