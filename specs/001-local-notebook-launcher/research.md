# Research: Local Notebook Launcher

## Decision 1: Prove the local path without BinderHub first

**Decision**: Use `repo2docker` + local Docker + JupyterLab for the first POC. Keep BinderHub/JupyterHub as a later backend.

**Rationale**: BinderHub orchestrates repository-to-image builds and JupyterHub sessions on Kubernetes. The POC's unresolved risks are lower-level: can a GitHub notebook be resolved automatically, can its repository produce a reusable runtime image, can Jupyter open the exact notebook, and can the resulting container use the local WSL2 GPU? Those can be tested with substantially fewer moving parts using the same repo2docker/container primitives.

**Alternatives considered**:
- **BinderHub + JupyterHub + k3s immediately**: architecturally representative, but adds Kubernetes, Helm, registry, spawning, proxy, and cluster GPU scheduling before the core launch flow is proven.
- **Plain host virtualenv + Jupyter**: simplest, but violates isolation and makes dependency conflicts/host contamination likely.

## Decision 2: Keep a stable launcher URL independent of backend

**Decision**: `/open` is the product contract. Backend-specific URLs are never embedded in repository badges.

Examples:

```text
http://127.0.0.1:8080/open?url=https://github.com/OWNER/REPO/blob/REF/path/notebook.ipynb
```

```text
http://127.0.0.1:8080/open?repo=OWNER/REPO&ref=REF&path=path/notebook.ipynb
```

**Rationale**: This permits later replacement of the local Docker implementation with BinderHub/JupyterHub without changing user-facing links.

## Decision 3: Resolve mutable Git refs to commit SHAs

**Decision**: Every launch records an immutable commit SHA before building/running.

**Rationale**: Branches and tags are user-friendly inputs but insufficient cache/provenance identities. Resolving them first prevents a cache entry from silently representing different source states over time.

For `/blob/...` URLs where the ref/path boundary is ambiguous because branch names can contain `/`, the resolver should compare candidate prefixes against remote refs and choose the longest valid ref match. Explicit `repo`/`ref`/`path` avoids this ambiguity and remains the canonical programmatic form.

## Decision 4: Use repo2docker for environment preparation

**Decision**: Build notebook images through repo2docker instead of defining a new environment-spec format.

**Rationale**: It already understands Binder-style repository configuration and common Python/Conda/package declarations and is the same environment-building component used in the Binder ecosystem.

The launcher owns build orchestration and caching; it should not duplicate repo2docker's environment detection logic.

## Decision 5: GPU policy is explicit but optional

**Decision**: Support `gpu=auto|on|off`, defaulting to `auto`.

**Rationale**: The POC is specifically intended to demonstrate local GPU execution, but CPU-only notebooks should remain usable. `gpu=on` provides a strict acceptance-test mode while `auto` gives the convenient local experience.

The host prerequisite chain is:

```text
Windows NVIDIA driver
  -> WSL2 GPU exposure
  -> Docker/container runtime
  -> NVIDIA Container Toolkit
  -> container GPU device request
  -> CUDA/PyTorch inside notebook image
```

The launcher validates this chain where observable but does not install or manage drivers/toolkit components.

## Decision 6: Long builds use asynchronous launch state

**Decision**: `/open` creates a launch and redirects to a local status page rather than holding one HTTP request open throughout cloning/building.

**Rationale**: First builds may take minutes. A launch state machine keeps the service responsive, exposes useful phases, and gives a natural place for errors and eventual cancellation.

For the single-user POC, in-memory state is acceptable. Durable job queues/databases are deferred.

## Decision 7: Jupyter remains authenticated even on loopback

**Decision**: Generate a per-session Jupyter token and bind published container ports only to `127.0.0.1`.

**Rationale**: Loopback binding reduces exposure, but disabling notebook authentication is unnecessary. The launcher can redirect with the token without requiring the user to manage it manually.

## Deferred questions

- Persistent user workspace semantics versus disposable session files.
- Private GitHub repositories and credential forwarding.
- Git LFS and submodule policy.
- Multi-user identity and quota policy.
- Kubernetes/BinderHub/JupyterHub deployment topology and registry choice.
- GPU sharing, MIG, time-slicing, or queueing.
