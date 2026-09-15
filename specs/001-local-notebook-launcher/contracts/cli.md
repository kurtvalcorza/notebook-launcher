# CLI Contract: Local Notebook Launcher

## `notebook-launcher serve`

```text
notebook-launcher serve [--host 127.0.0.1] [--port 8080]
```

Default is loopback-only. Non-loopback binding is outside MVP default.

## `notebook-launcher mcp <session-id>`

```text
notebook-launcher mcp <session-id> [--mode write|readonly] [--client-label <label>]
```

Rules:
- session must already be ready;
- default mode is session policy;
- requesting `write` against readonly session is rejected;
- writable mode atomically acquires the one writable-agent lease;
- raw Jupyter credentials stay internal;
- descriptor/bridge remains bound to workspace `active_notebook_path`; browser focus does not retarget it.

## `notebook-launcher trust list`

Lists active grants including stable GitHub repository ID, current/grant-time repository display name, scope, commit SHA when applicable, creation time.

## `notebook-launcher trust revoke <trust-id>`

Revokes future authorization. Does not kill an already-running session.

## `notebook-launcher workspace mount <workspace-id> <host-path>`

```text
notebook-launcher workspace mount <workspace-id> <host-path> --mode ro|rw
```

Rules:
- local management only; remote `/open` parameters cannot supply host path;
- input path must exist as a directory;
- launcher resolves canonical root before forbidden/whole-home validation;
- persisted grant stores display path + canonical root;
- canonical root is revalidated before use;
- host-side operations use symlink/reparse-safe containment rooted at the canonical directory, not string-prefix checks;
- a grant whose root resolves differently, disappears, or becomes forbidden fails clearly;
- active session may require stop/restart before grant change takes effect.

## `notebook-launcher workspace unmount <workspace-id>`

Revokes the optional user-data grant for future runtime starts. Active runtime change may require stop/restart and must be reported explicitly.

## Launch authorization is browser-local, not CLI trust

The stable browser URL uses `GET /open`, which is preview-only. The executable launch requires a one-time local POST authorization from the preview. `trust` commands do not bypass that launch authorization gate.

A future explicit CLI launch command may implement an equivalent local-user authorization path, but is not part of this MVP contract.

## Error categories

Expected stable short identifiers include:

- `invalid_launch_token`
- `launch_token_expired`
- `launch_token_replayed`
- `launch_request_changed`
- `repository_identity_changed`
- `trust_not_found`
- `unknown_session`
- `session_not_ready`
- `writable_agent_busy`
- `permission_denied`
- `execution_timeout`
- `execution_cancelled`
- `unknown_workspace`
- `workspace_active`
- `invalid_host_path`
- `host_path_escape`
- `host_path_changed`
- `network_policy_unavailable`
- `storage_full`

Secrets and full notebook/output contents are not emitted in normal errors.
