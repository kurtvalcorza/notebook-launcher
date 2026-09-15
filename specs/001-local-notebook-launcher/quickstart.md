# Quickstart: Local Notebook Launcher POC

This guide is an acceptance/runbook for the planned MVP, not implementation code.

## Host prerequisites

Inside WSL2/Linux:

```bash
git --version
docker version
repo2docker --version
nvidia-smi  # GPU acceptance only
```

For GPU containers, first prove host/container GPU access independently:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

The launcher-managed runtime must also provision JupyterLab collaboration support. A session is not `ready` until collaboration and standard-sandbox readiness checks succeed.

## Start the launcher

```bash
git clone https://github.com/kurtvalcorza/notebook-launcher.git
cd notebook-launcher
git switch 001-local-notebook-launcher
uv sync --dev
uv run notebook-launcher serve
```

Expected default listener:

```text
http://127.0.0.1:8080
```

## Launch from GitHub

```text
http://127.0.0.1:8080/open?url=https://github.com/kurtvalcorza/swin-segmentation-pipeline/blob/main/tutorials/swin_segmentation_task_inference.ipynb
```

or:

```text
http://127.0.0.1:8080/open?repo=kurtvalcorza/swin-segmentation-pipeline&ref=main&path=tutorials/swin_segmentation_task_inference.ipynb
```

For a source not covered by local trust, verify that execution pauses at a local confirmation page showing repository, immutable SHA, and notebook path. The page must offer:

```text
Trust this exact commit
Trust this repository for future revisions
Cancel / deny
```

Cancel/deny must produce zero repository-supplied notebook/setup execution.

## Trust management acceptance

After granting trust:

```bash
uv run notebook-launcher trust list
uv run notebook-launcher trust revoke <TRUST_ID>
```

Verify exact-commit trust prompts again for a different SHA, while repository-wide trust covers later SHAs from the same repository until revoked.

## Environment isolation acceptance

Use a fixture repository whose notebook imports a dependency that is intentionally absent from the unrelated host Python environment.

1. Confirm the dependency import fails from the launcher host environment/fixture interpreter used for the negative check.
2. Launch the notebook through the normal trust/environment flow.
3. Confirm the dependency import succeeds inside the notebook kernel.
4. Confirm the host environment was not modified to make the test pass.

This validates source-declared notebook dependencies independently from unrelated host Python state.

## Workspace behavior

The remote source is not edited:

```text
source snapshot @ SHA   immutable provenance
workspace/work/         editable + persistent
workspace/outputs/      generated artifacts + persistent
runtime                 disposable
```

Edit/run normally in JupyterLab. Stop the runtime, then reopen:

```text
http://127.0.0.1:8080/open?workspace_id=<WORKSPACE_ID>
```

Verify the existing working copy is reused. While that workspace is already active, a second reopen request must return/direct to the existing session rather than create another runtime.

## Active notebook rename/move acceptance

Rename or move the active notebook through the supported Jupyter/notebook operation.

1. Confirm the move remains inside the workspace and the destination ends in `.ipynb`.
2. Confirm workspace/status metadata reports the new `active_notebook_path`.
3. Stop the session and reopen the workspace.
4. Confirm the new path opens, not the old path.
5. Move/delete the file outside the supported path as a negative test and confirm the launcher reports the active notebook missing instead of selecting a different notebook automatically.

Generic MCP workspace-file write/move/delete must reject the active notebook path; active-notebook changes use notebook-aware operations.

## Save a Copy

```bash
curl -X POST http://127.0.0.1:8080/api/workspaces/<WORKSPACE_ID>/notebooks/copy \
  -H 'content-type: application/json' \
  -d '{
    "source_path": "tutorials/example.ipynb",
    "destination_scope": "workspace",
    "destination_path": "copies/example-copy.ipynb",
    "include_outputs": true,
    "overwrite": false
  }'
```

Verify both notebooks exist and GitHub remains unchanged.

## Storage-failure acceptance

Automated tests should simulate `ENOSPC`/replacement failure for launcher-managed writes and notebook-save integration.

Expected result:

- the operation fails with an actionable storage error;
- the previous saved file contents remain intact;
- no partial temporary file becomes authoritative;
- workspace metadata does not advance as though the failed write succeeded.

## Optional local data grant

No host directory is mounted by default. Grant one locally:

```bash
uv run notebook-launcher workspace mount <WORKSPACE_ID> /path/to/data --mode ro
# or explicit writable access
uv run notebook-launcher workspace mount <WORKSPACE_ID> /path/to/data --mode rw
```

The runtime sees the selected directory at the configured user-data mount (planned default `/mnt/user-data`). The remote launch URL cannot choose that path. Whole-home mounts should be rejected by default.

Remove the grant with:

```bash
uv run notebook-launcher workspace unmount <WORKSPACE_ID>
```

## GPU modes

Append one of:

```text
&gpu=auto
&gpu=on
&gpu=off
```

Inside the notebook:

```python
import torch
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
```

## Agent profiles

Writable (default):

```text
&agent_mode=write
```

Readonly:

```text
&agent_mode=readonly
```

Readonly must reject execution, execution cancellation, kernel restart, notebook mutation/move, and workspace-file mutation while allowing notebook/cell/output inspection.

The MVP guarantees at most one writable MCP attachment. It does not guarantee simultaneous multiple readonly clients.

## Attach an MCP agent

Inspect the non-secret descriptor:

```bash
curl http://127.0.0.1:8080/api/sessions/<SESSION_ID>/mcp
```

Generic stdio configuration:

```json
{
  "command": "notebook-launcher",
  "args": ["mcp", "<SESSION_ID>"]
}
```

The bridge resolves Jupyter credentials privately. In writable mode it atomically acquires the session's writable-agent lease.

Start a second writable MCP bridge for the same session and verify it fails with a clear `writable_agent_busy` outcome. Disconnect the first bridge and verify a new writable bridge can then attach.

## Writable-agent acceptance

1. Read the active notebook and record its opaque `document_version`.
2. Edit/insert a controlled cell using that version.
3. Execute the cell and observe output in the same Jupyter kernel/browser notebook.
4. Create, edit, rename, and delete representative non-notebook files inside `/workspace`.
5. Confirm generic workspace-file write/move/delete rejects the active notebook path.
6. Rename/move the active notebook through the notebook-aware operation and verify `active_notebook_path` updates.
7. Execute the notebook in order.
8. Encounter a deliberate failing cell and inspect traceback.
9. Repair the cell and continue/re-run.
10. Restart the kernel and execute again.
11. For GPU sessions, confirm MCP and browser GPU probes agree.

## Human/agent conflict acceptance

The active notebook uses one authoritative Jupyter collaborative document.

1. Agent reads the notebook and receives version `V1`.
2. Human edits the same notebook normally in JupyterLab; the shared document advances to `V2`.
3. Agent attempts a mutation based on `V1`.
4. Verify the mutation fails as `conflict`, exposes/returns the current version, and does not overwrite the human edit.
5. Agent refreshes, receives `V2`, reapplies a valid mutation, and both browser and disk show the result.
6. Repeat in the opposite order and verify subsequent browser edits still persist after MCP edits.
7. Exercise the deliberately stale independent save/file-replacement path and verify it is rejected rather than overwriting the shared document.

This is a backend-conformance gate; silent bidirectional sync loss or stale overwrite is a failure.

## Execution cancellation acceptance

Use a deliberately non-terminating cell, for example:

```python
while True:
    pass
```

1. Start the cell through the writable agent and record its execution operation ID.
2. Invoke `execution.cancel` before the configured deadline; separately test deadline expiry.
3. Verify the kernel execution is actually interrupted, not merely relabelled.
4. Verify the result is distinctly `cancelled` or `timeout`.
5. If interrupt cannot restore the kernel, verify the bounded recovery path restarts/replaces the kernel without deleting workspace files.
6. Run a normal cell afterward and confirm the session/workspace remains usable.

## Large/binary/multimodal output acceptance

The default MCP serialized output bound is planned at 1 MiB per operation and is locally configurable.

1. Produce text larger than the configured bound and verify the agent response reports `truncated=true` and size metadata when available.
2. Produce representative binary/multimodal notebook output and verify MCP returns MIME/type/size metadata plus only a bounded preview/reference representation.
3. Confirm the full authoritative output remains persisted in the notebook/workspace.
4. Confirm audit records do not copy the full output payload.

## Persistence acceptance

1. Edit and save a notebook.
2. Generate files under `/outputs` and another ordinary path under `/workspace`.
3. Stop the session.
4. Confirm the MCP bridge/lease becomes invalid.
5. Reopen the same workspace ID.
6. Confirm notebook edits, saved outputs, and both generated files survive.
7. Save a copy and confirm both notebooks exist.

## Sandbox/network acceptance

Before allowing the first notebook cell to execute, verify the standard runtime has:

- non-root execution where compatible;
- no privileged mode;
- `no-new-privileges`;
- unnecessary capabilities dropped;
- seccomp/equivalent filtering active;
- configured CPU/memory/PID limits;
- only explicit source/workspace/output/user-data mounts;
- no Docker socket, SSH keys, GitHub/cloud credentials, whole-home mount, or unrelated host path;
- a scrubbed environment without unrelated host secrets;
- Jupyter and launcher-controlled inbound services bound only to loopback.

Then verify outbound package/model/data/API access works by default from the notebook runtime.

## Trust and threat note

Only launch public repositories you trust. Sandboxing is defense-in-depth, not a promise that arbitrary malicious code is safe. Repository-wide trust is intentionally broader than exact-commit trust and should be used only when the user is comfortable trusting future revisions from that repository.
