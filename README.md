# Notebook Launcher

Local, WSL2/Linux-first launcher for user-trusted public GitHub notebooks.

This implementation is being built against the approved specification in `specs/001-local-notebook-launcher/`.

## Current implementation tranche

This branch implements the control-plane foundation plus bounded host/source
primitives:

- package/configuration bootstrap;
- typed public errors;
- domain models;
- one-time launch authorization token primitives;
- SQLite control-state schema and replay protection;
- stable repository trust storage;
- writable-agent leases;
- notebook/file version guards;
- bounded audit records;
- strict GitHub source/path parsing helpers;
- structured argv-only subprocess execution with timeout/cancellation, process-tree
  cleanup, redaction, and bounded output capture;
- host capability diagnostics for Git, Docker, repo2docker, Jupyter collaboration,
  MCP, GPU, state storage, and network-policy support;
- credential-free exact-SHA source acquisition with immutable cache snapshots;
- deterministic environment identities and Docker image-cache lookup;
- FastAPI/CLI skeletons;
- unit tests for these components.

It intentionally does **not** claim completion of repo2docker image builds, Docker
runtime sandboxing, network enforcement, Jupyter collaboration, MCP backend
integration, kernel execution ownership, GPU runtime acceptance, or end-to-end
launch behavior. Those require the remaining implementation and local runtime
validation.

## Security model

The authoritative requirements are in the merged spec. In particular:

- `GET /open` must remain non-executing;
- execution requires a separate one-time local launch authorization;
- persistent source trust and per-launch authorization are separate concepts;
- repository-wide trust is keyed to stable GitHub repository identity;
- one active session is allowed per workspace;
- one writable MCP agent lease is allowed per active session;
- the standard sandbox is defense-in-depth for user-trusted repositories, not arbitrary hostile-code containment;
- no GitHub write credentials are required for ordinary use.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
```

Runtime/integration tests will additionally require the local WSL2/Linux, Docker, Jupyter, MCP, and optional NVIDIA environment described in the feature plan.
