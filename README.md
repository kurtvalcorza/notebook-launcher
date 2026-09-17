# Notebook Launcher

Notebook Launcher is a local, Linux/WSL2-first service for opening a trusted
public GitHub notebook in a persistent workspace, building its environment with
repo2docker, and running JupyterLab behind a constrained Docker boundary.

The control plane is substantially implemented, but the MVP is **not yet fully
qualified**. The current `jupyter-mcp-server` backend supports live notebook
inspection and some cell-level operations, but it does not provide the complete
semantic surface required by the specification: notebook move, whole-notebook
execution, workspace file operations, atomic shared-document compare-and-swap,
or launcher-owned cancellation. Those remain release blockers rather than being
silently advertised as complete. Until that contract closes, the public MCP
bridge exposes only the conformant read methods, even when write mode was
requested.

## What is implemented

- non-executing `GET /open` preview plus one-time, request-bound local launch
  authorization;
- exact-commit and stable-repository-ID trust, including identity re-resolution
  immediately before a trust grant;
- exact-SHA source caching and persistent editable workspaces;
- deterministic repo2docker image identities and cache reuse;
- non-root, read-only-root Docker execution with dropped capabilities, resource
  limits, loopback-only publication, explicit mounts, and secret filtering;
- launcher-managed IPv4 egress policy that denies non-global destinations and
  permits DNS only through configured resolver IPs;
- optional NVIDIA policy with exact host/container device-identity agreement;
- authenticated collaborative JupyterLab startup and a private MCP credential
  handoff that keeps the Jupyter token out of public descriptors and argv;
- one active session per workspace, one heartbeating writable MCP lease per
  session with stale-owner reclamation, stop invalidation, restart-time
  stale-start reconciliation, trust management, local-data grants, and Save a
  Copy;
- readiness through Jupyter's collaboration-session registry rather than a
  generic contents response;
- bounded outputs, audit metadata, typed public errors, and safe host-path
  operations.

IPv6 networking is intentionally disabled for runtime networks until a complete
deny-policy oracle is implemented. This is a fail-closed MVP choice.

## Prerequisites

Run the launcher inside Linux or WSL2 with a Docker daemon whose bridge and
firewall namespace are local to the launcher. Remote Docker contexts and the
Windows named-pipe/Rancher Desktop topology are rejected because host-side
`iptables` rules there cannot attest the container boundary.

Required tools are Git, Docker Engine, `ip`, `iptables`, and Python 3.12+.
Network-policy installation requires sufficient host privileges. NVIDIA
Container Toolkit is optional and required only for `gpu=on` or effective
`gpu=auto`.

```bash
python -m pip install -e '.[dev,runtime]'
notebook-launcher serve
```

The default management endpoint is `http://127.0.0.1:8080`. Configure explicit
IPv4 DNS resolver exceptions with a JSON environment value, for example:

```bash
export NOTEBOOK_LAUNCHER_APPROVED_DNS_ENDPOINTS='["1.1.1.1"]'
```

## Basic flow

1. Open `/open?repo=OWNER/REPO&ref=REF&path=PATH.ipynb`.
2. Review the resolved source and press **Launch locally**.
3. If prompted, trust the exact commit or stable repository identity.
4. Poll `/api/launches/<launch-id>` until ready.
5. Retrieve the non-secret MCP descriptor from
   `/api/sessions/<session-id>/mcp`.
6. Stop with `DELETE /api/sessions/<session-id>`; the workspace and build cache
   remain available for an explicitly authorized reopen.

Trust never replaces per-launch authorization, and ordinary operation does not
need GitHub write credentials or mutate the upstream repository.

## Verification snapshot

On 2026-09-18 the repaired local suite passed with 251 tests and 6 explicit
environment/platform skips at 81% line coverage. Focused verification also
passed 179 tests, and native WSL probes passed secure directory-FD writes,
atomic no-replace rename, component-swap containment, and symlink-escape
rejection. A real repo2docker cache-miss produced a runnable image with
JupyterLab 4.6.3, Jupyter Collaboration 5.0.3, and Notebook 7.6.0; `pip check`,
the hardened Jupyter readiness smoke, and a live MCP notebook activation/read
also passed.

The complete authorize-to-reopen flow is still blocked in this Windows/Rancher
Desktop environment by the intentionally local firewall attestation. Configured
GPU acceptance is also blocked because this Docker daemon exposes no NVIDIA
runtime, despite the Windows host having a GPU. Remaining implementation and
acceptance work is tracked in `specs/001-local-notebook-launcher/tasks.md`.

## Development

```bash
pytest -q --cov=notebook_launcher --cov-report=term-missing
ruff check src tests
python -m compileall -q src tests
```

The detailed acceptance runbook is in
`specs/001-local-notebook-launcher/quickstart.md`.

## Threat note

Only launch repositories you trust. The sandbox and egress controls reduce blast
radius; they are not a guarantee that arbitrary hostile code is safe.
