# Data Model: Local Notebook Launcher

## LaunchRequest

Represents validated user intent before Git resolution.

| Field | Type | Notes |
|---|---|---|
| `url` | string? | Full GitHub notebook URL; mutually exclusive with explicit source fields |
| `repo` | string? | `owner/repository` |
| `ref` | string? | Branch, tag, or commit supplied by user |
| `path` | string? | Repository-relative `.ipynb` path |
| `gpu` | enum | `auto`, `on`, `off`; default `auto` |

## ResolvedSource

Canonical, immutable source identity used downstream.

| Field | Type | Notes |
|---|---|---|
| `owner` | string | GitHub owner |
| `repository` | string | GitHub repository |
| `requested_ref` | string | Original branch/tag/SHA |
| `commit_sha` | string | Resolved immutable commit |
| `notebook_path` | string | Normalized repository-relative path |
| `clone_url` | string | Validated HTTPS GitHub clone URL |

Invariant: `notebook_path` is relative, normalized, ends in `.ipynb`, and cannot escape the repository root.

## EnvironmentIdentity

Stable cache identity for a built execution environment.

| Field | Type | Notes |
|---|---|---|
| `source` | ResolvedSource ref | Immutable repository revision |
| `builder_version` | string | repo2docker/versioned launcher build strategy |
| `digest` | string | Hash used in local image tag/cache lookup |
| `image_tag` | string | Local Docker image name/tag |

## Launch

Tracks orchestration before and during session creation.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Public local identifier |
| `request` | LaunchRequest | Sanitized request |
| `source` | ResolvedSource? | Populated after resolution |
| `environment` | EnvironmentIdentity? | Populated before build/cache check |
| `phase` | enum | lifecycle phase |
| `message` | string? | User-safe progress/error description |
| `error_code` | string? | Stable machine-readable failure category |
| `session_id` | UUID? | Populated after runtime starts |
| `created_at` | datetime | Local launch creation time |
| `updated_at` | datetime | Last state transition |

### Launch phases

`received`, `resolving`, `acquiring`, `building`, `cache_hit`, `starting`, `ready`, `failed`, `stopping`, `stopped`.

## NotebookSession

Runtime record for a running local notebook server.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Session identity |
| `launch_id` | UUID | Owning launch |
| `container_id` | string | Docker container identifier |
| `host_port` | integer | Random loopback-published port |
| `jupyter_token` | secret string | Never emitted in logs |
| `gpu_enabled` | boolean | Whether GPU devices were requested successfully |
| `notebook_url` | string | Local JupyterLab target URL; API may return redacted form separately |
| `started_at` | datetime | Session start time |

## Cache

The MVP relies on Docker image storage plus repository work directories rather than a separate database. Cache deletion is an explicit maintenance operation and is not coupled to stopping a notebook session.
