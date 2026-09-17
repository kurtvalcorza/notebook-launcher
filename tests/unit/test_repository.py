import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

import notebook_launcher.workspace as workspace_module
from notebook_launcher.errors import RepositoryIdentityChanged, RepositorySetupFailed
from notebook_launcher.models import ResolvedSource
from notebook_launcher.orchestration import CommandResult
from notebook_launcher.repository import (
    SOURCE_MANIFEST_NAME,
    SourceCache,
    source_tree_digest,
)
from notebook_launcher.state import StateStore
from notebook_launcher.workspace import WorkspaceManager


def seal_snapshot(root: Path) -> None:
    (root / SOURCE_MANIFEST_NAME).write_text(f"{source_tree_digest(root)}\n")


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
                seal_snapshot(competing.parents[1])
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
    seal_snapshot(root)

    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("git should not run on a cache hit")

    snapshot = SourceCache(tmp_path / "sources", runner=unexpected_runner).acquire(source())
    assert snapshot.notebook == notebook


def test_verify_unchanged_detects_source_snapshot_mutation(tmp_path):
    root = tmp_path / "sources" / "42" / ("a" * 40)
    notebook = root / "notebooks" / "demo.ipynb"
    notebook.parent.mkdir(parents=True)
    notebook.write_text("{}")
    seal_snapshot(root)
    cache = SourceCache(tmp_path / "sources", runner=lambda *_args, **_kwargs: None)
    snapshot = cache.acquire(source())
    notebook.write_text('{"changed": true}')

    with pytest.raises(RepositoryIdentityChanged):
        cache.verify_unchanged(snapshot)

    with pytest.raises(RepositoryIdentityChanged):
        cache.acquire(source())


def test_manifestless_cache_is_quarantined_and_rebuilt(tmp_path):
    root = tmp_path / "sources" / "42" / ("a" * 40)
    notebook = root / "notebooks" / "demo.ipynb"
    notebook.parent.mkdir(parents=True)
    notebook.write_text('{"untrusted": true}')

    snapshot = SourceCache(tmp_path / "sources", runner=FakeGit()).acquire(source())

    assert snapshot.notebook.read_text() == "{}"
    quarantines = list(root.parent.glob(f".quarantine-{root.name}-*"))
    assert len(quarantines) == 1
    assert (quarantines[0] / "notebooks" / "demo.ipynb").read_text() == (
        '{"untrusted": true}'
    )


def test_source_digest_detects_permission_mutation(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    script = root / "setup.sh"
    script.write_text("echo ok\n")
    before = source_tree_digest(root)

    script.chmod(0o444)

    assert source_tree_digest(root) != before


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


def test_workspace_materialization_rejects_mutate_copy_restore(tmp_path, monkeypatch):
    cache = SourceCache(tmp_path / "sources", runner=FakeGit())
    snapshot = cache.acquire(source())
    original = snapshot.notebook.read_bytes()
    real_copytree = workspace_module.shutil.copytree

    def deceptive_copytree(source_root, work_root, **kwargs):
        snapshot.notebook.write_bytes(b'{"untrusted": true}')
        monkeypatch.setattr(workspace_module.shutil, "copytree", real_copytree)
        try:
            return real_copytree(source_root, work_root, **kwargs)
        finally:
            snapshot.notebook.write_bytes(original)
            monkeypatch.setattr(
                workspace_module.shutil, "copytree", deceptive_copytree
            )

    monkeypatch.setattr(workspace_module.shutil, "copytree", deceptive_copytree)
    state = StateStore(tmp_path / "state.db")
    state.initialize()
    workspaces = tmp_path / "workspaces"
    manager = WorkspaceManager(state, workspaces)

    with pytest.raises(RepositoryIdentityChanged):
        manager.create(
            snapshot,
            source_owner="owner",
            source_repository="repo",
            source_notebook_path="notebooks/demo.ipynb",
        )

    assert snapshot.notebook.read_bytes() == original
    assert not workspaces.exists() or not any(workspaces.iterdir())
