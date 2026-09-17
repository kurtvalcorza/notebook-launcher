import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from notebook_launcher.errors import RepositoryIdentityChanged, RepositorySetupFailed
from notebook_launcher.models import ResolvedSource
from notebook_launcher.orchestration import CommandResult
from notebook_launcher.repository import SourceCache


def source(*, clone_url="https://github.com/owner/repo.git"):
    return ResolvedSource(
        repository_id=42,
        repository_node_id="R_42",
        owner="owner",
        repository="repo",
        requested_ref="main",
        commit_sha="a" * 40,
        notebook_path="notebooks/demo.ipynb",
        clone_url=clone_url,
        resolved_at=datetime.now(UTC),
    )


def result(argv, stdout=""):
    return CommandResult(
        argv=tuple(argv),
        returncode=0,
        stdout=stdout,
        stderr="",
        stdout_bytes=len(stdout.encode()),
        stderr_bytes=0,
        truncated=False,
        duration_ms=1,
    )


class FakeGit:
    def __init__(
        self,
        *,
        actual_sha="a" * 40,
        create_notebook=True,
        readonly_git_object=False,
        publish_competing_snapshot=False,
    ):
        self.actual_sha = actual_sha
        self.create_notebook = create_notebook
        self.readonly_git_object = readonly_git_object
        self.publish_competing_snapshot = publish_competing_snapshot
        self.calls = []

    def __call__(self, argv, *, cwd: Path, env, **_kwargs):
        self.calls.append((tuple(argv), cwd, env))
        if argv[1] == "init":
            (cwd / ".git").mkdir()
            if self.readonly_git_object:
                object_file = cwd / ".git" / "object"
                object_file.write_text("object")
                object_file.chmod(stat.S_IREAD)
        if argv[1] == "checkout" and self.create_notebook:
            notebook = cwd / "notebooks" / "demo.ipynb"
            notebook.parent.mkdir()
            notebook.write_text("{}")
        if argv[1] == "rev-parse":
            if self.publish_competing_snapshot:
                competing = cwd.parent / self.actual_sha / "notebooks" / "demo.ipynb"
                competing.parent.mkdir(parents=True)
                competing.write_text('{"winner": "other"}')
            return result(argv, f"{self.actual_sha}\n")
        return result(argv)


def test_acquire_materializes_detached_credential_free_snapshot(tmp_path):
    git = FakeGit()
    snapshot = SourceCache(tmp_path / "sources", runner=git).acquire(source())

    assert snapshot.root == tmp_path / "sources" / "42" / ("a" * 40)
    assert snapshot.notebook.read_text() == "{}"
    assert not (snapshot.root / ".git").exists()
    fetch = next(call for call in git.calls if "fetch" in call[0])
    assert fetch[2]["GIT_TERMINAL_PROMPT"] == "0"
    assert fetch[2]["GCM_INTERACTIVE"] == "Never"
    assert fetch[2]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert all("TOKEN" not in key and "AUTH" not in key for key in fetch[2])
    assert "--depth=1" in fetch[0]
    assert fetch[0][-1] == "a" * 40


def test_acquire_reuses_existing_snapshot_without_git(tmp_path):
    root = tmp_path / "sources" / "42" / ("a" * 40)
    notebook = root / "notebooks" / "demo.ipynb"
    notebook.parent.mkdir(parents=True)
    notebook.write_text("{}")

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("git should not run on a cache hit")

    snapshot = SourceCache(tmp_path / "sources", runner=unexpected_runner).acquire(source())
    assert snapshot.notebook == notebook


def test_acquire_accepts_valid_snapshot_won_by_concurrent_writer(tmp_path):
    snapshot = SourceCache(
        tmp_path / "sources",
        runner=FakeGit(publish_competing_snapshot=True),
    ).acquire(source())

    assert snapshot.notebook.read_text() == '{"winner": "other"}'


@pytest.mark.parametrize(
    "clone_url",
    (
        "file:///tmp/repo",
        "https://token@github.com/owner/repo.git",
        "https://example.com/owner/repo.git",
        "https://github.com/owner/other.git",
        "https://github.com/owner/repo.git?token=secret",
    ),
)
def test_acquire_rejects_non_public_github_clone_urls(tmp_path, clone_url):
    with pytest.raises(ValueError, match="credential-free public GitHub"):
        SourceCache(tmp_path / "sources").acquire(source(clone_url=clone_url))


def test_acquire_rejects_commit_mismatch_and_cleans_staging(tmp_path):
    cache_root = tmp_path / "sources"
    git = FakeGit(actual_sha="b" * 40)

    with pytest.raises(RepositoryIdentityChanged):
        SourceCache(cache_root, runner=git).acquire(source())

    assert not list(cache_root.rglob(".source-*"))


def test_acquire_rejects_missing_notebook(tmp_path):
    with pytest.raises(RepositorySetupFailed, match="missing"):
        SourceCache(
            tmp_path / "sources",
            runner=FakeGit(create_notebook=False, readonly_git_object=True),
        ).acquire(source())
