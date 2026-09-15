import json

import httpx

from notebook_launcher.github import GitHubResolver
from notebook_launcher.models import LaunchRequest


def response(status: int, payload: dict):
    return httpx.Response(status, content=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})


def test_repo_fields_resolve_stable_repository_id_and_commit():
    def handler(request: httpx.Request):
        if request.url.path == "/repos/owner/repo":
            return response(200, {
                "id": 42,
                "node_id": "R_42",
                "name": "repo",
                "owner": {"login": "owner"},
                "clone_url": "https://github.com/owner/repo.git",
            })
        if request.url.path == "/repos/owner/repo/commits/main":
            return response(200, {"sha": "b" * 40})
        return response(404, {})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = GitHubResolver(client)
    resolved = resolver.resolve(LaunchRequest(repo="owner/repo", ref="main", path="demo.ipynb"))

    assert resolved.repository_id == 42
    assert resolved.commit_sha == "b" * 40
    assert resolved.notebook_path == "demo.ipynb"


def test_blob_url_prefers_longest_valid_slash_ref():
    def handler(request: httpx.Request):
        if request.url.path == "/repos/owner/repo":
            return response(200, {
                "id": 42,
                "node_id": "R_42",
                "name": "repo",
                "owner": {"login": "owner"},
                "clone_url": "https://github.com/owner/repo.git",
            })
        if request.url.path.endswith("/commits/feature%2Fdemo"):
            return response(200, {"sha": "c" * 40})
        if request.url.path.endswith("/commits/feature%2Fdemo%2Fnotebooks"):
            return response(404, {})
        if request.url.path.endswith("/commits/feature"):
            return response(200, {"sha": "d" * 40})
        return response(404, {})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = GitHubResolver(client)
    resolved = resolver.resolve(LaunchRequest(
        url="https://github.com/owner/repo/blob/feature/demo/notebooks/example.ipynb"
    ))

    assert resolved.requested_ref == "feature/demo"
    assert resolved.notebook_path == "notebooks/example.ipynb"
    assert resolved.commit_sha == "c" * 40
