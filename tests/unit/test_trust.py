from datetime import UTC, datetime

from notebook_launcher.models import ResolvedSource, TrustScope
from notebook_launcher.trust import TrustStore


def _source(repo_id: int, commit: str = "a" * 40, owner: str = "owner", repo: str = "repo"):
    return ResolvedSource(
        repository_id=repo_id,
        repository_node_id=f"R_{repo_id}",
        owner=owner,
        repository=repo,
        requested_ref="main",
        commit_sha=commit,
        notebook_path="demo.ipynb",
        clone_url=f"https://github.com/{owner}/{repo}.git",
        resolved_at=datetime.now(UTC),
    )


def test_exact_commit_trust_does_not_cover_other_commit(state_store):
    trust = TrustStore(state_store)
    source = _source(100)
    trust.grant(source, TrustScope.EXACT_COMMIT)
    assert trust.find(source).trusted
    assert not trust.find(_source(100, "b" * 40)).trusted


def test_repository_trust_follows_stable_repo_id_across_rename(state_store):
    trust = TrustStore(state_store)
    source = _source(100, owner="old", repo="name")
    trust.grant(source, TrustScope.REPOSITORY)
    renamed = _source(100, owner="new", repo="renamed")
    recreated_namespace = _source(200, owner="old", repo="name")
    assert trust.find(renamed).trusted
    assert not trust.find(recreated_namespace).trusted
