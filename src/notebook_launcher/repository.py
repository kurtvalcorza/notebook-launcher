from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .errors import RepositoryIdentityChanged, RepositorySetupFailed
from .models import ResolvedSource
from .orchestration import CommandResult, run_argv


Runner = Callable[..., CommandResult]
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_GITHUB_PATH_RE = re.compile(r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$")


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    root: Path
    notebook: Path
    repository_id: int
    commit_sha: str


def _git_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    return environment


def _remove_tree(path: Path) -> None:
    def make_writable_and_retry(
        function: Callable[[str], object],
        filename: str,
        _error: BaseException,
    ) -> None:
        os.chmod(filename, stat.S_IWRITE)
        function(filename)

    shutil.rmtree(path, onexc=make_writable_and_retry)


def _validate_public_clone_url(
    clone_url: str,
    *,
    owner: str,
    repository: str,
) -> None:
    parsed = urlsplit(clone_url)
    expected_path = f"/{owner}/{repository}.git"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not _GITHUB_PATH_RE.fullmatch(parsed.path)
        or parsed.path.casefold() != expected_path.casefold()
    ):
        raise ValueError("clone URL must be a credential-free public GitHub HTTPS URL")


def _validated_notebook(root: Path, notebook_path: str) -> Path:
    candidate = root.joinpath(*notebook_path.split("/"))
    if candidate.is_symlink() or not candidate.is_file():
        raise RepositorySetupFailed("The requested notebook is missing from the revision.")
    resolved_root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise RepositorySetupFailed("The requested notebook escapes the source snapshot.")
    return candidate


class SourceCache:
    def __init__(self, root: Path, *, runner: Runner = run_argv) -> None:
        self.root = root
        self.runner = runner

    def acquire(self, source: ResolvedSource) -> SourceSnapshot:
        if not _SHA_RE.fullmatch(source.commit_sha):
            raise ValueError("commit SHA must be 40 hexadecimal characters")
        _validate_public_clone_url(
            source.clone_url,
            owner=source.owner,
            repository=source.repository,
        )
        commit_sha = source.commit_sha.lower()
        target = self.root / str(source.repository_id) / commit_sha
        if target.exists():
            notebook = _validated_notebook(target, source.notebook_path)
            return SourceSnapshot(
                target,
                notebook,
                source.repository_id,
                commit_sha,
            )

        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".source-", dir=target.parent))
        try:
            self._git(staging, "init", "--quiet")
            self._git(staging, "remote", "add", "origin", source.clone_url)
            self._git(
                staging,
                "-c",
                "credential.helper=",
                "fetch",
                "--quiet",
                "--depth=1",
                "origin",
                commit_sha,
            )
            self._git(staging, "checkout", "--quiet", "--detach", "FETCH_HEAD")
            actual = self._git(staging, "rev-parse", "HEAD").stdout.strip().lower()
            if actual != commit_sha:
                raise RepositoryIdentityChanged()
            notebook = _validated_notebook(staging, source.notebook_path)
            _remove_tree(staging / ".git")
            try:
                staging.replace(target)
            except OSError:
                if not target.is_dir():
                    raise
            notebook = _validated_notebook(target, source.notebook_path)
            return SourceSnapshot(
                target,
                notebook,
                source.repository_id,
                commit_sha,
            )
        except (RepositoryIdentityChanged, RepositorySetupFailed, ValueError):
            raise
        except OSError as exc:
            raise RepositorySetupFailed() from exc
        finally:
            if staging.exists():
                _remove_tree(staging)

    def _git(self, cwd: Path, *args: str) -> CommandResult:
        result = self.runner(
            ("git", *args),
            cwd=cwd,
            env=_git_environment(),
            timeout_seconds=120,
            max_output_bytes=64 * 1024,
        )
        if result.returncode != 0:
            raise RepositorySetupFailed()
        return result
