# Workspace Lifecycle Contract

## Purpose

Define Colab-like local persistence without making GitHub a write target.

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

## Fresh source launch

1. Resolve source to immutable SHA.
2. Require trust confirmation when source is not already trusted under local policy.
3. Acquire immutable source material.
4. Create a new workspace ID.
5. Materialize an editable working copy separately from immutable source provenance.
6. Create persistent `outputs/`.
7. Start a disposable runtime against the workspace.

A fresh source launch creates a fresh workspace by default; it does not silently reuse a prior edited workspace.

## Reopen

Reopening by `workspace_id` starts a new runtime against the existing working copy. It MUST NOT reset files from GitHub. If the cached runtime image is missing, the launcher may rebuild the environment from recorded provenance while preserving workspace contents.

## Stop

Stopping a session:

- stops/removes the runtime container/process;
- invalidates Jupyter/MCP credentials and attachment state;
- leaves workspace files, notebook edits, outputs, and environment cache intact.

Workspace deletion is a separate explicit future/user action.

## Save a Copy

Save a Copy duplicates a notebook to:

- a validated workspace-relative path; or
- a validated relative path under an explicitly configured user-data mount.

Rules:

- destination MUST remain within its selected scope;
- destination MUST end in `.ipynb`;
- overwrite defaults false;
- current saved outputs may be included;
- operation never changes immutable source provenance;
- operation never commits, pushes, creates branches, or mutates GitHub.

## User-data mount

No host directory is mounted by default. A local user may explicitly configure one directory with `ro` or `rw` mode. Remote launch URLs cannot specify the host path. The container path is stable (recommended `/mnt/user-data`).

The launcher MUST NOT mount the entire home directory merely for convenience.

## Outputs

`outputs/` is persistent, workspace-owned artifact storage intended for generated models, CSV/JSON files, images, reports, checkpoints, or other notebook results. Runtime/session cleanup MUST NOT delete it.

## Git semantics

The feature treats GitHub as immutable input. The launcher exposes no Git publishing workflow. The editable work tree SHOULD be materialized without a writable `.git` remote relationship, and no GitHub credentials are injected into the runtime.
