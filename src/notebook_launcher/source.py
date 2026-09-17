from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class ParsedGitHubSource:
    owner: str
    repository: str
    requested_ref: str
    notebook_path: str

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}.git"


@dataclass(frozen=True, slots=True)
class ParsedBlobURL:
    owner: str
    repository: str
    tail: tuple[str, ...]


def normalize_notebook_path(value: str) -> str:
    decoded = unquote(value)
    path = PurePosixPath(decoded)
    if path.is_absolute():
        raise ValueError("absolute notebook path is not allowed")
    if not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("unsafe notebook path")
    normalized = path.as_posix()
    if not normalized.lower().endswith(".ipynb"):
        raise ValueError("target must be an .ipynb file")
    return normalized


def parse_repo_fields(repo: str, ref: str, path: str) -> ParsedGitHubSource:
    if not _REPO_RE.fullmatch(repo):
        raise ValueError("repo must be owner/repository")
    owner, repository = repo.split("/", 1)
    repository = repository.removesuffix(".git")
    if not ref.strip():
        raise ValueError("ref is required")
    return ParsedGitHubSource(
        owner=owner,
        repository=repository,
        requested_ref=ref,
        notebook_path=normalize_notebook_path(path),
    )


def parse_github_blob_url(url: str) -> ParsedBlobURL:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("only https://github.com notebook URLs are supported")
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 5 or parts[2] != "blob":
        raise ValueError("expected a GitHub blob URL")
    owner, repository = parts[0], parts[1]
    tail = tuple(parts[3:])
    if len(tail) < 2:
        raise ValueError("missing ref or notebook path")
    return ParsedBlobURL(owner=owner, repository=repository, tail=tail)


def resolve_blob_tail(blob: ParsedBlobURL, known_refs: set[str]) -> ParsedGitHubSource:
    """Resolve a blob URL after caller retrieves candidate refs from GitHub.

    Refs may contain slashes, so the longest valid ref prefix wins. This helper
    deliberately does not guess from URL structure alone.
    """
    candidates: list[tuple[int, str, str]] = []
    for split_at in range(1, len(blob.tail)):
        ref = "/".join(blob.tail[:split_at])
        if ref not in known_refs:
            continue
        path = "/".join(blob.tail[split_at:])
        try:
            normalized = normalize_notebook_path(path)
        except ValueError:
            continue
        candidates.append((split_at, ref, normalized))
    if not candidates:
        raise ValueError("could not resolve GitHub ref/path boundary")
    _, ref, notebook_path = max(candidates, key=lambda item: item[0])
    return ParsedGitHubSource(
        owner=blob.owner,
        repository=blob.repository,
        requested_ref=ref,
        notebook_path=notebook_path,
    )
