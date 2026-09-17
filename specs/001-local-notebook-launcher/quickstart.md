# Quickstart: Local Notebook Launcher POC

This is an acceptance/runbook for the planned MVP, not implementation code.

## Host prerequisites

Inside WSL2/Linux:

```bash
git --version
docker version
repo2docker --version
nvidia-smi  # GPU acceptance only
```

For GPU containers, independently prove host/container GPU access first.

## Start launcher

```bash
uv sync --dev
uv run notebook-launcher serve
```

Default management listener:

```text
http://127.0.0.1:8080
```

## 1. Preview a GitHub notebook — GET must not execute

Navigate to:

```text
http://127.0.0.1:8080/open?url=https://github.com/kurtvalcorza/swin-segmentation-pipeline/blob/main/tutorials/swin_segmentation_colab.ipynb
```

Expected preview shows:
- stable GitHub repository identity/display name;
- resolved immutable commit SHA;
- notebook path;
- GPU/agent mode;
- trust coverage state;
- local **Launch** button.

Before pressing Launch, verify:
- no repo2docker build;
- no repository setup command;
- no Jupyter runtime/kernel;
- no notebook code;
- no persistent executable launch state.

Repeat after repository trust exists. GET alone must still execute nothing.

The preview page must not be frameable by another site.

## 2. Local launch authorization

Press the local Launch button. It submits a short-lived one-time request-bound token via POST.

Acceptance:
- expired token fails;
- replayed token fails;
- changing source/workspace/GPU/agent mode after token issue fails;
- cross-site submission without the local preview token fails;
- existing trust does not bypass this launch-authorization step.

If source trust is missing, the authorized launch then prompts:

```text
Trust this exact commit
Trust this repository for future revisions
Deny
```

Trust and launch authorization are separate.

## 3. Trust identity behavior

After granting repository-wide trust:

```bash
uv run notebook-launcher trust list
```

Verify the record shows stable GitHub repository ID.

Acceptance:
- repo rename with same GitHub ID → trust still applies;
- repo transfer with same GitHub ID → trust still applies;
- delete/recreate under same `owner/name` with a new ID → trust does not apply;
- exact-commit trust applies only to same repo ID + SHA.

## 4. Workspace behavior

```text
source snapshot @ repo-id+SHA  immutable
workspace/work/                editable + persistent
workspace/outputs/             persistent
runtime                        disposable
```

Stop runtime, navigate to:

```text
http://127.0.0.1:8080/open?workspace_id=<WORKSPACE_ID>
```

GET must only preview the reopen. Press Launch to authorize compute restart.

While workspace is already active, launch POST should return/direct to existing session, not create another runtime.

## 5. Save a Copy

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

Verify original source/GitHub remain unchanged.

## 6. Local data grant and symlink escape tests

```bash
uv run notebook-launcher workspace mount <WORKSPACE_ID> /path/to/data --mode ro
uv run notebook-launcher workspace unmount <WORKSPACE_ID>
```

Then test `rw` separately.

Acceptance:
- canonical root is reported/recorded;
- whole-home canonical root is rejected by default;
- `..` destination escape rejected;
- symlink inside grant pointing outside is rejected for host-side Save a Copy/write;
- symlink/junction/reparse/root-swap between validation/open does not escape;
- missing/replaced canonical root fails clearly;
- unrelated host directories remain inaccessible.

## 7. Default network policy

Inside notebook, verify public Internet retrieval works, for example a public package/model/API endpoint.

Also verify attempts to reach these classes fail by default:
- `127.0.0.0/8` / `::1`;
- Docker/WSL host gateway;
- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`;
- IPv6 ULA `fc00::/7`;
- link-local IPv4/IPv6;
- metadata/link-local service endpoints;
- multicast/non-global local targets.

Configured DNS resolver may be an explicit exception. A hostname that resolves to a denied private address must still fail.

No `host.docker.internal`/host-gateway alias should be added by default.

## 8. GPU modes

Use `gpu=auto|on|off` in the preview request. Browser and MCP probes must agree on effective device scope.

## 9. MCP attachment

```bash
curl http://127.0.0.1:8080/api/sessions/<SESSION_ID>/mcp
```

Generic stdio config:

```json
{
  "command": "notebook-launcher",
  "args": ["mcp", "<SESSION_ID>"]
}
```

Verify descriptor names the launcher-designated `notebook_path` and contains no Jupyter token.

## 10. Fixed active-notebook target

With MCP attached to notebook A:
1. open notebook B in another JupyterLab tab;
2. focus/edit notebook B;
3. call MCP `notebook.read`/cell operation;
4. verify it still targets notebook A;
5. supported `notebook.move` of A updates the target path;
6. ordinary browser focus never retargets MCP.

## 11. Execution ownership and cancellation

### Running-agent cancel

1. Ensure kernel idle.
2. Start non-terminating agent execution.
3. Confirm Jupyter busy-parent/request ID matches the agent operation ID mapping.
4. Cancel.
5. Verify agent execution is interrupted and result is `cancelled`/`timeout`.

### Browser-running + queued-agent cancel

1. Start a controlled long-running cell from browser.
2. Submit an agent execution while browser owns busy kernel.
3. Confirm agent operation remains queued/not active-owned.
4. Cancel or let agent deadline expire.
5. Verify browser cell continues uninterrupted to its expected result.
6. Verify agent operation terminates without kernel interrupt.

### Dispatched race

Force/test a race where agent was dispatched but browser/other request is observed as active parent. Cancellation must become pending and MUST NOT interrupt until/if the agent request itself becomes active.

## 12. Writable/readonly profiles

Writable can edit/execute active notebook and ordinary workspace files. Generic file tools cannot overwrite/move/delete the active notebook.

Readonly permits inspection only; execution/cancel/restart/mutation/escalation all fail.

Second writable MCP bridge must fail with `writable_agent_busy` until first releases lease.

## 13. Persistence/storage/output

- edits/output/artifacts survive stop/reopen;
- active notebook move survives reopen;
- simulated `ENOSPC` preserves prior file;
- oversized text returns bounded/truncated MCP response;
- binary/multimodal output returns type/size/preview/reference metadata;
- full authoritative output remains in notebook/workspace and is absent from audit payload.

## Threat note

Only launch repositories you trust. Sandboxing and local-network egress restrictions reduce blast radius but are not a promise that arbitrary malicious code is safe.
