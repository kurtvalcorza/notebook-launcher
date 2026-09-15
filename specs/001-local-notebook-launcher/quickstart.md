# Quickstart: Local Notebook Launcher POC

## Host prerequisites

Inside WSL2/Linux, confirm these independently before using the launcher:

```bash
nvidia-smi                 # required only for GPU acceptance test
git --version
docker version
repo2docker --version
```

The configured MCP backend/client must also be available for the agent acceptance path. The launcher should provide a diagnostic command/status rather than requiring users to know backend-specific probes.

For GPU containers, a direct Docker smoke test must succeed before blaming the launcher:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

## Development setup

```bash
git clone https://github.com/kurtvalcorza/notebook-launcher.git
cd notebook-launcher
git switch 001-local-notebook-launcher

uv sync --dev
uv run notebook-launcher
```

Expected default listener:

```text
http://127.0.0.1:8080
```

## Launch by full GitHub notebook URL

```text
http://127.0.0.1:8080/open?url=https://github.com/kurtvalcorza/swin-segmentation-pipeline/blob/main/tutorials/swin_segmentation_task_inference.ipynb
```

## Launch by explicit source fields

```text
http://127.0.0.1:8080/open?repo=kurtvalcorza/swin-segmentation-pipeline&ref=main&path=tutorials/swin_segmentation_task_inference.ipynb
```

## Require the local GPU

Append:

```text
&gpu=on
```

Inside the launched notebook:

```python
import torch
assert torch.cuda.is_available()
print(torch.cuda.get_device_name(0))
```

## CPU-only launch

Append:

```text
&gpu=off
```

## Attach an MCP-capable agent

Once launch status is `ready`, note the `session_id` and inspect the non-secret attachment descriptor:

```bash
curl http://127.0.0.1:8080/api/sessions/<SESSION_ID>/mcp
```

Expected shape:

```json
{
  "session_id": "<SESSION_ID>",
  "available": true,
  "status": "available",
  "transport": "stdio",
  "command": "notebook-launcher",
  "args": ["mcp", "<SESSION_ID>"],
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

Configure the MCP client with the returned command and args. A generic stdio configuration is conceptually:

```json
{
  "command": "notebook-launcher",
  "args": ["mcp", "<SESSION_ID>"]
}
```

If running from the repository rather than an installed command, the development equivalent may be:

```json
{
  "command": "uv",
  "args": ["run", "notebook-launcher", "mcp", "<SESSION_ID>"]
}
```

The client configuration MUST NOT require copying the raw Jupyter token.

## Agent acceptance prompts

After attachment, verify the agent can perform the semantic operations in `contracts/mcp-capabilities.md`. A practical acceptance sequence is:

1. Read the active notebook and summarize its code-cell sequence.
2. Execute a harmless diagnostic cell/code request and report output.
3. Execute the notebook from top to bottom.
4. If a cell fails, identify the failing cell and traceback without dropping the MCP session.
5. Restart the kernel and execute a simple expression again.
6. For a GPU session, execute:

```python
import torch
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
```

The result must agree with execution from the browser-visible notebook.

## POC acceptance sequence

1. Launch a small public notebook successfully.
2. Confirm the notebook opens directly, not merely the Jupyter file browser.
3. Confirm a repository-declared dependency imports successfully.
4. Stop the session and relaunch the same commit; confirm image build is skipped.
5. Launch with `gpu=on` and confirm CUDA sees the local NVIDIA GPU.
6. Retrieve the MCP descriptor and attach an MCP-capable agent without manually providing a Jupyter token.
7. Have the agent read and execute the same notebook/kernel visible in JupyterLab.
8. Verify a deliberate cell failure is surfaced with useful context and the agent can continue/restart.
9. Verify an MCP-driven GPU probe agrees with browser execution.
10. Stop the notebook session and confirm its MCP attachment becomes unusable.
11. Submit malformed URLs, traversal inputs, and invalid session IDs and confirm they fail safely.

## Important trust note

Launching a repository means executing code and environment setup supplied by that repository. Giving an agent MCP control intentionally lets it execute code inside that notebook runtime. Use repositories and agent instructions you trust. Containerization reduces host exposure but is not a substitute for repository review or a hardened sandbox.
