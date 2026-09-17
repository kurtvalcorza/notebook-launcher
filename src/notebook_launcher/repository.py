from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .errors import RepositoryIdentityChanged, RepositorySetupFailed
from .models import ResolvedSource
from .orchestration import CommandResult, run_argv

Runner = Callable[..., CommandResult]
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_GITHUB_PATH_RE = re.compile(r"^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$")
SOURCE_MANIFEST_NAME = ".notebook-launcher-source.sha256"


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    root: Path
    notebook: Path
    repository_id: int
    commit_sha: str
    tree_digest: str


def source_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if relative == SOURCE_MANIFEST_NAME.encode():
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            kind = b"L"
            payload = os.readlink(path).encode()
        elif stat.S_ISDIR(info.st_mode):
            kind = b"D"
            payload = b""
        elif stat.S_ISREG(info.st_mode):
            kind = b"F"
            payload = path.read_bytes()
        else:
            kind = b"O"
            payload = str(info.st_mode).encode()
        digest.update(kind)
        digest.update(stat.S_IMODE(info.st_mode).to_bytes(4, "big"))
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _write_manifest(target: Path, digest: str) -> None:
    manifest = target / SOURCE_MANIFEST_NAME
    try:
        descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RepositoryIdentityChanged() from None
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(f"{digest}\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        manifest.unlink(missing_ok=True)
        raise


def _validated_cached_digest(target: Path) -> str:
    manifest = target / SOURCE_MANIFEST_NAME
    try:
        expected = manifest.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise RepositoryIdentityChanged() from exc
    digest = source_tree_digest(target)
    if expected != digest:
        raise RepositoryIdentityChanged()
    return digest


def _quarantine_manifestless_snapshot(target: Path) -> Path | None:
    if not target.exists():
        return None
    quarantine = target.parent / f".quarantine-{target.name}-{uuid4().hex}"
    try:
        target.replace(quarantine)
    except FileNotFoundError:
        return None
    return quarantine


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
            if not (target / SOURCE_MANIFEST_NAME).is_file():
                _quarantine_manifestless_snapshot(target)
            else:
                digest = _validated_cached_digest(target)
                notebook = _validated_notebook(target, source.notebook_path)
                return SourceSnapshot(
                    target,
                    notebook,
                    source.repository_id,
                    commit_sha,
                    digest,
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
            digest = source_tree_digest(staging)
            _write_manifest(staging, digest)
            try:
                staging.replace(target)
            except OSError:
                if not target.is_dir():
                    raise
            notebook = _validated_notebook(target, source.notebook_path)
            digest = _validated_cached_digest(target)
            return SourceSnapshot(
                target,
                notebook,
                source.repository_id,
                commit_sha,
                digest,
            )
        except (RepositoryIdentityChanged, RepositorySetupFailed, ValueError):
            raise
        except OSError as exc:
            raise RepositorySetupFailed() from exc
        finally:
            if staging.exists():
                _remove_tree(staging)

    @staticmethod
    def verify_unchanged(snapshot: SourceSnapshot) -> None:
        manifest = snapshot.root / SOURCE_MANIFEST_NAME
        current = (
            _validated_cached_digest(snapshot.root)
            if manifest.is_file()
            else source_tree_digest(snapshot.root)
        )
        if current != snapshot.tree_digest:
            raise RepositoryIdentityChanged()

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
