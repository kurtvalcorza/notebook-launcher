# Quickstart: Local Notebook Launcher POC

## Host prerequisites

Inside WSL2/Linux, confirm these independently before using the launcher:

```bash
nvidia-smi                 # required only for GPU acceptance test
git --version
docker version
repo2docker --version
```

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

## POC acceptance sequence

1. Launch a small public notebook successfully.
2. Confirm the notebook opens directly, not merely the Jupyter file browser.
3. Confirm a repository-declared dependency imports successfully.
4. Stop the session and relaunch the same commit; confirm image build is skipped.
5. Launch with `gpu=on` and confirm CUDA sees the local NVIDIA GPU.
6. Submit malformed URLs and path traversal inputs and confirm they fail before execution.

## Important trust note

Launching a repository means executing code and environment setup supplied by that repository. Use repositories you trust. The POC isolates them in containers, but containerization is not a substitute for repository review or a hardened sandbox.
