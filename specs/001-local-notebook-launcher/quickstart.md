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

Readonly must reject execution, kernel restart, notebook mutation, and workspace-file mutation while allowing notebook/cell/output inspection.

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
5. Execute the notebook in order.
6. Encounter a deliberate failing cell and inspect traceback.
7. Repair the cell and continue/re-run.
8. Restart the kernel and execute again.
9. For GPU sessions, confirm MCP and browser GPU probes agree.

## Human/agent conflict acceptance

1. Agent reads a notebook and receives version `V1`.
2. Human edits/saves the same notebook in JupyterLab so authoritative state becomes `V2`.
3. Agent attempts a mutation based on `V1`.
4. Verify the mutation fails as `conflict`, returns/currently exposes the new version, and does not overwrite the human edit.
5. Agent refreshes, receives `V2`, reapplies a valid mutation, and both browser and disk show the result.
6. Repeat in the opposite order and verify subsequent browser edits still persist after MCP edits.

This is a backend-conformance gate; silent bidirectional sync loss is a failure.

## Persistence acceptance

1. Edit and save a notebook.
2. Generate files under `/outputs` and another ordinary path under `/workspace`.
3. Stop the session.
4. Confirm the MCP bridge/lease becomes invalid.
5. Reopen the same workspace ID.
6. Confirm notebook edits, saved outputs, and both generated files survive.
7. Save a copy and confirm both notebooks exist.

## Sandbox/network acceptance

Verify the runtime has only explicit mounts, no Docker socket/SSH/GitHub credentials, and no unrelated host directory access. Outbound package/model/data/API requests may work; Jupyter and launcher services remain loopback-only by default.

## Trust and threat note

Only launch public repositories you trust. Sandboxing is defense-in-depth, not a promise that arbitrary malicious code is safe. Repository-wide trust is intentionally broader than exact-commit trust and should be used only when the user is comfortable trusting future revisions from that repository.
