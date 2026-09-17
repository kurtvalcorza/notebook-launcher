from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from .models import LaunchRequest, ResolvedSource
from .source import (
    ParsedBlobURL,
    ParsedGitHubSource,
    normalize_notebook_path,
    parse_github_blob_url,
    parse_repo_fields,
)


class GitHubResolver:
    """Resolve public GitHub notebook requests to stable repo identity + commit."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=10.0,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )

    def resolve(self, request: LaunchRequest) -> ResolvedSource:
        if request.workspace_id is not None:
            raise ValueError("workspace reopen does not require GitHub resolution")

        if request.repo:
            parsed = parse_repo_fields(request.repo, request.ref or "", request.path or "")
            metadata = self._get_json(
                f"https://api.github.com/repos/{parsed.owner}/{parsed.repository}"
            )
            canonical_owner = metadata["owner"]["login"]
            canonical_repo = metadata["name"]
            commit = self._resolve_commit(
                canonical_owner,
                canonical_repo,
                parsed.requested_ref,
            )
        elif request.url:
            parsed, metadata, commit = self._resolve_blob_url(
                parse_github_blob_url(request.url)
            )
            canonical_owner = metadata["owner"]["login"]
            canonical_repo = metadata["name"]
        else:
            raise ValueError("GitHub source is required")

        clone_url = metadata.get("clone_url")
        expected_prefix = "https://github.com/"
        if not isinstance(clone_url, str) or not clone_url.startswith(expected_prefix):
            raise ValueError("GitHub returned an unexpected clone URL")

        return ResolvedSource(
            repository_id=int(metadata["id"]),
            repository_node_id=metadata.get("node_id"),
            owner=canonical_owner,
            repository=canonical_repo,
            requested_ref=parsed.requested_ref,
            commit_sha=str(commit["sha"]),
            notebook_path=parsed.notebook_path,
            clone_url=clone_url,
            resolved_at=datetime.now(UTC),
        )

    def _resolve_blob_url(
        self,
        blob: ParsedBlobURL,
    ) -> tuple[ParsedGitHubSource, dict, dict]:
        metadata = self._get_json(
            f"https://api.github.com/repos/{blob.owner}/{blob.repository}"
        )
        owner = metadata["owner"]["login"]
        repository = metadata["name"]

        for split_at in range(len(blob.tail) - 1, 0, -1):
            ref = "/".join(blob.tail[:split_at])
            path = "/".join(blob.tail[split_at:])
            try:
                normalized = normalize_notebook_path(path)
            except ValueError:
                continue
            commit = self._resolve_commit_if_exists(owner, repository, ref)
            if commit is not None:
                return (
                    ParsedGitHubSource(
                        owner=owner,
                        repository=repository,
                        requested_ref=ref,
                        notebook_path=normalized,
                    ),
                    metadata,
                    commit,
                )
        raise ValueError("could not resolve GitHub ref/path boundary")

    def _commit_url(self, owner: str, repository: str, ref: str) -> str:
        encoded = quote(ref, safe="")
        return f"https://api.github.com/repos/{owner}/{repository}/commits/{encoded}"

    def _resolve_commit_if_exists(
        self,
        owner: str,
        repository: str,
        ref: str,
    ) -> dict | None:
        response = self.client.get(self._commit_url(owner, repository, ref))
        if response.status_code == 404:
            return None
        if response.status_code == 422:
            data = response.json()
            if isinstance(data, dict) and str(data.get("message", "")).startswith(
                "No commit found for SHA:"
            ):
                return None
        response.raise_for_status()
        return self._object_json(response)

    def _resolve_commit(self, owner: str, repository: str, ref: str) -> dict:
        return self._get_json(self._commit_url(owner, repository, ref))

    def _get_json(self, url: str) -> dict:
        response = self.client.get(url)
        response.raise_for_status()
        return self._object_json(response)

    @staticmethod
    def _object_json(response: httpx.Response) -> dict:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected GitHub API response")
        return data
