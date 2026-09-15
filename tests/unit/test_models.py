from uuid import uuid4

import pytest
from pydantic import ValidationError

from notebook_launcher.models import LaunchRequest, TrustRecord, TrustScope
from datetime import UTC, datetime


def test_workspace_reopen_cannot_mix_remote_source():
    with pytest.raises(ValidationError):
        LaunchRequest(
            workspace_id=uuid4(),
            repo="owner/repo",
            ref="main",
            path="x.ipynb",
        )


def test_repo_launch_requires_ref_and_path():
    with pytest.raises(ValidationError):
        LaunchRequest(repo="owner/repo")


def test_repository_trust_cannot_include_commit():
    with pytest.raises(ValidationError):
        TrustRecord(
            id=uuid4(),
            repository_id=1,
            grant_owner="owner",
            grant_repository="repo",
            scope=TrustScope.REPOSITORY,
            commit_sha="a" * 40,
            created_at=datetime.now(UTC),
        )
