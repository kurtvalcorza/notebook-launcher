# CLI Contract: Local Notebook Launcher

## Purpose

Define local management commands that should not be exposed as remote-source launch parameters, especially host-path grants and trust administration.

## `notebook-launcher serve`

Starts the loopback-only launcher service using configured local state/workspace roots.

```text
notebook-launcher serve [--host 127.0.0.1] [--port 8080]
```

Non-loopback binding is outside the MVP default and requires explicit configuration.

## `notebook-launcher mcp <session-id>`

Starts a local stdio MCP bridge for an existing ready notebook session.

```text
notebook-launcher mcp <session-id> [--mode write|readonly] [--client-label <label>]
```

Rules:

- default mode is the session's configured agent mode;
- requesting `write` against a readonly session is rejected;
- requesting `readonly` against a writable session is allowed as a downgrade;
- writable mode must atomically acquire the session's writable-agent lease before backend attachment;
- if a writable lease already exists, command exits with a clear `writable_agent_busy` error;
- disconnect/session-stop releases or invalidates the writable lease;
- raw Jupyter credentials are resolved internally and never printed to stdout/stderr in normal operation.

## `notebook-launcher trust list`

Lists active local trust grants without showing secrets.

```text
notebook-launcher trust list
```

Expected fields: trust ID, repository, scope (`exact_commit` or `repository`), commit SHA when applicable, creation time.

## `notebook-launcher trust revoke <trust-id>`

Revokes a trust grant for future launches.

```text
notebook-launcher trust revoke <trust-id>
```

Revocation does not silently terminate an already-running session that was authorized earlier.

## `notebook-launcher workspace mount <workspace-id> <host-path>`

Explicitly grants one local host directory to a workspace.

```text
notebook-launcher workspace mount <workspace-id> <host-path> --mode ro|rw
```

Rules:

- command is local management only; `/open` and GitHub URLs cannot provide `host-path`;
- path must resolve to an existing directory and pass configured forbidden-path checks;
- whole-home mounting is rejected by default;
- mount metadata is persisted with the workspace;
- `ro` is the recommended default if mode is omitted by any future interactive wrapper.

## `notebook-launcher workspace unmount <workspace-id>`

Removes the optional user-data grant from future/runtime restarts.

```text
notebook-launcher workspace unmount <workspace-id>
```

If the workspace session is active, implementation may require stop/restart before mount changes take effect; it must report that requirement rather than silently changing the live sandbox.

## Exit/error categories

CLI commands use non-zero exit status and a stable short error identifier for expected failures such as:

- `unknown_session`
- `session_not_ready`
- `writable_agent_busy`
- `permission_denied`
- `unknown_workspace`
- `workspace_active`
- `invalid_host_path`
- `trust_not_found`

Secrets and full notebook contents are not printed as part of these errors.
