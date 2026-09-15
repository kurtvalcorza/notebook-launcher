# Quickstart: Local Notebook Launcher POC

## Host prerequisites

Inside WSL2/Linux:

```bash
git --version
docker version
repo2docker --version
nvidia-smi  # GPU acceptance only
```

For GPU containers, first prove the host/runtime independently:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

## Start launcher

```bash
git clone https://github.com/kurtvalcorza/notebook-launcher.git
cd notebook-launcher
git switch 001-local-notebook-launcher
uv sync --dev
uv run notebook-launcher
```

Default listener:

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

The first launch of an untrusted source may show a local confirmation page identifying repository, immutable SHA, and notebook before any repository-supplied code/environment setup executes.

## Workspace behavior

The remote source is not edited. The launcher creates persistent local work:

```text
source snapshot @ SHA   immutable
workspace/work/         editable + persistent
workspace/outputs/      generated artifacts + persistent
runtime/container       disposable
```

Edit/run the notebook normally in JupyterLab. Stop the runtime and reopen with:

```text
http://127.0.0.1:8080/open?workspace_id=<WORKSPACE_ID>
```

The existing working copy is reused.

## Save a Copy

Conceptual API:

```bash
curl -X POST http://127.0.0.1:8080/api/workspaces/<WORKSPACE_ID>/notebooks/copy \
  -H 'content-type: application/json' \
  -d '{
    "source_path": "tutorials/example.ipynb",
    "destination_scope": "workspace",
    "destination_path": "copies/example-copy.ipynb",
    "include_outputs": true
  }'
```

This is local copying only. It never commits, pushes, creates a Git branch, or modifies GitHub.

## Optional local data mount

No host folder is mounted by default. A user may explicitly configure one local directory as `ro` or `rw`; the runtime sees it at a stable path such as:

```text
/mnt/user-data
```

The repository launch URL cannot choose that host path. Do not mount your whole home directory merely for convenience.

## GPU modes

Append one of:

```text
&gpu=auto
&gpu=on
&gpu=off
```

GPU check:

```python
import torch
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
```

## Agent modes

Default writable agent:

```text
&agent_mode=write
```

Inspection-only agent:

```text
&agent_mode=readonly
```

`readonly` cannot execute code, edit cells, restart the kernel, or mutate workspace state.

## Attach MCP agent

```bash
curl http://127.0.0.1:8080/api/sessions/<SESSION_ID>/mcp
```

Generic stdio client configuration:

```json
{
  "command": "notebook-launcher",
  "args": ["mcp", "<SESSION_ID>"]
}
```

Raw Jupyter tokens are not copied into client configuration.

## Writable agent acceptance

1. Read the active notebook.
2. Edit/insert a controlled cell.
3. Execute the cell and observe output in the same Jupyter kernel.
4. Execute the notebook in order.
5. Encounter a deliberate failing cell and inspect traceback.
6. Repair the cell and continue/re-run.
7. Restart the kernel and execute again.
8. For GPU sessions, confirm MCP and browser GPU probes agree.

## Persistence acceptance

1. Edit a notebook and save it.
2. Generate a file under `/outputs`.
3. Stop the session.
4. Reopen the same workspace ID.
5. Confirm edit, notebook outputs, and generated file survive.
6. Save a copy and confirm both notebooks exist.

## Sandbox/network expectations

The standard runtime is restricted and receives only explicit mounts. It has outbound network access for packages/models/data/APIs, but Jupyter is published only to loopback and no general inbound sandbox ports are exposed.

## Trust note

Only launch public repositories you trust. Sandboxing is defense-in-depth, not a promise that arbitrary malicious code is safe. The runtime receives no GitHub credentials by default and this feature never writes back to GitHub.
