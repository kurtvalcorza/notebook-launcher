# Research: Local Notebook Launcher

## Decision 1: Local Docker + repo2docker + JupyterLab first

Use the smallest local runtime stack that proves source → persistent workspace → sandboxed notebook → same-runtime MCP. BinderHub/JupyterHub/Kubernetes remain future backends.

## Decision 2: Public contracts remain backend-neutral

`/open`, workspace lifecycle, permission profiles, trust, and semantic MCP capabilities are launcher contracts. Docker/Jupyter/MCP backend details stay replaceable.

## Decision 3: GET launch URLs are non-executing

Stable links are useful, but browser GET requests can be triggered cross-site. Therefore GET `/open` resolves/previews only. It does not build or run repository code, Jupyter, kernels, or notebook code.

## Decision 4: Separate local launch authorization from source trust

Every fresh launch or stopped-workspace compute restart requires a short-lived one-time local authorization gesture/token bound to the normalized request. Existing source trust skips only the trust choice.

Rationale: trust answers “may this repository code execute?”; launch authorization answers “does the local user want to start compute now?” Combining them creates drive-by localhost execution once persistent trust exists.

## Decision 5: Launch authorization uses same-origin anti-drive-by controls

Use a signed request-bound token, expiry, one-time JTI/replay record on POST, SameSite local browser context, loopback/same-origin request checks, and anti-framing headers. The token is rendered by local preview and never accepted from the public GET URL.

## Decision 6: Resolve mutable Git refs to immutable SHAs

Branches/tags are inputs; recorded provenance/environment identity uses immutable commit SHA.

## Decision 7: Resolve stable GitHub repository identity before trust

Store GitHub numeric repository ID as trust key; node ID may be retained as diagnostic cross-check. Owner/name is mutable display/clone metadata.

- rename/transfer: same ID, trust continues;
- delete/recreate same namespace: new ID, trust does not continue.

## Decision 8: repo2docker prepares repository environments

Use repository-declared Binder/repo2docker conventions instead of inventing a launcher environment DSL.

## Decision 9: Filesystem data + SQLite control state

Files remain transparent ordinary files. SQLite provides transactions/uniqueness for trust, launch-token replay, session ownership, writable leases, versions, and bounded audit metadata.

## Decision 10: Separate source, workspace, runtime

Immutable source provenance, persistent mutable workspace, disposable runtime/kernel/MCP.

## Decision 11: GitHub is never a write target

No commit/push/branch creation or ordinary write credentials.

## Decision 12: Exact-commit and repository-wide trust

Exact commit key: stable repo ID + SHA. Repository-wide key: stable repo ID. Trust is locally revocable.

## Decision 13: Save a Copy is local persistence

Save to validated workspace path or explicit writable user-data grant; no Git publishing.

## Decision 14: Local data grant is explicit and canonical

No host folder by default. Local CLI selects one directory + mode. Resolve canonical root and validate forbidden/home policy after canonicalization.

## Decision 15: Host-side data operations are symlink-safe

String-prefix/`realpath` prechecks alone are race-prone. Host-side writes/copies should be anchored to a canonical root with Linux `openat2`-style beneath/no-magiclink/no-follow semantics where available, or a component-by-component dirfd `O_NOFOLLOW` equivalent. Reject symlink/junction/reparse/path-swap escapes.

## Decision 16: Public Internet egress, non-global local egress denied

Notebook Internet access is useful for packages/models/data/APIs, but unrestricted outbound access can reach host/LAN services. Default policy permits globally routable Internet destinations and required explicitly approved infrastructure (notably configured DNS resolver endpoints), while blocking host gateway, loopback, private/RFC1918, IPv6 ULA, link-local, metadata, multicast, and other non-global local ranges.

DNS answers do not bypass the IP-layer deny policy. No host-gateway alias is added by default.

## Decision 17: Standard sandbox is defense-in-depth

Non-root where compatible, no-new-privileges, dropped capabilities, syscall filtering, resource limits, minimal mounts, secret scrubbing, no Docker socket, loopback-only inbound publication, egress policy. Not hostile-code-grade containment.

## Decision 18: GPU policy is `auto|on|off`

Default auto; required mode fails clearly; browser/MCP share device scope.

## Decision 19: Jupyter collaboration is authoritative notebook state

Browser and MCP use the same active notebook document/kernel. Independent stale replacement is forbidden.

## Decision 20: Active notebook target is launcher-designated

`active_notebook_path` determines MCP target. Opening/focusing another notebook tab does not retarget the agent. Explicit retargeting is future scope; supported move/rename updates the designated path.

## Decision 21: Datalayer Jupyter MCP Server 2.x is initial adapter target

Use behind launcher semantic contract; pin exact versions only after conformance.

## Decision 22: Reject stale notebook/file mutations

Opaque document/file versions; no last-write-wins.

## Decision 23: One active runtime per workspace

Transactional local ownership. Reopen live workspace returns existing session.

## Decision 24: One writable MCP lease per session

Serialized writable controllers; readonly does not consume lease.

## Decision 25: Writable mode covers workspace, with active-notebook guard

Agent can manipulate ordinary workspace files, but generic file tools cannot replace/move/delete active notebook outside notebook-aware semantics.

## Decision 26: Readonly is inspection-only

No code execution, cancel, restart, mutation, or escalation.

## Decision 27: stdio is default local MCP transport

Keeps raw Jupyter credentials internal and avoids another listener.

## Decision 28: Agent execution uses an ownership-aware broker

Kernel interrupts are global, so a cancel API cannot blindly interrupt a shared browser/agent kernel. Agent executions are queued/serialized; broker observes Jupyter request/message IDs and busy-parent ownership.

Queued cancellation never interrupts. Dispatched-but-not-active cancellation becomes pending. Interrupt only when active parent message ID belongs to the agent operation.

## Decision 29: Notebook execution remains notebook execution

Use active Jupyter kernel, cell order, stop-on-error; no unrelated script runtime.

## Decision 30: Agent output is bounded; notebook output is not destroyed

Default 1 MiB MCP response bound; preserve full authoritative notebook output.

## Decision 31: Persistent writes fail safely

Atomic/safe replacement; `ENOSPC` does not replace last good file.

## Decision 32: Audit metadata, not notebook payloads

Bounded identity/operation/timing/outcome only; omit credentials and full cell/output payloads.

## Deferred non-MVP work

- Private repositories/auth forwarding.
- Explicit local/LAN egress grants.
- Focus-based or explicit MCP notebook retargeting.
- Workspace deletion/quotas/retention/archive UX.
- Stronger strict sandbox (gVisor/Kata/etc.).
- Multi-user/distributed scheduling.
- Remote/network MCP transport.
- BinderHub/JupyterHub runtime backend.
