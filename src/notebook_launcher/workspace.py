from __future__ import annotations

import errno
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from .errors import HostPathEscape, StorageFull


def canonical_grant_root(path: Path, *, home: Path | None = None) -> Path:
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("granted path must be a directory")
    if home is not None and root == home.expanduser().resolve(strict=False):
        raise ValueError("whole home directory cannot be granted")
    return root


def _safe_relative_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts:
        raise HostPathEscape()
    if any(part in ("", ".", "..") for part in path.parts):
        raise HostPathEscape()
    return tuple(path.parts)


def safe_open_under_root(
    root: Path,
    relative: str,
    *,
    flags: int,
    mode: int = 0o600,
) -> int:
    """Open a path beneath root without following symlinks in any component."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RuntimeError("safe no-follow path opening is unsupported on this platform")

    parts = _safe_relative_parts(relative)
    canonical = root.resolve(strict=True)
    root_fd = os.open(canonical, os.O_RDONLY | os.O_DIRECTORY)
    current_fd = root_fd
    owned: list[int] = []
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            owned.append(next_fd)
            current_fd = next_fd
        return os.open(parts[-1], flags | os.O_NOFOLLOW, mode, dir_fd=current_fd)
    except (OSError, ValueError) as exc:
        if isinstance(exc, OSError) and exc.errno in {
            errno.ELOOP,
            errno.ENOTDIR,
            errno.ENOENT,
        }:
            raise HostPathEscape() from exc
        raise
    finally:
        for fd in reversed(owned):
            os.close(fd)
        os.close(root_fd)


def atomic_write(path: Path, data: bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            if exc.errno == errno.ENOSPC:
                raise StorageFull() from exc
        raise


def save_copy(source: Path, destination: Path, *, overwrite: bool = False) -> None:
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    atomic_write(destination, source.read_bytes(), overwrite=overwrite)


def materialize_workspace(source_root: Path, work_root: Path) -> None:
    if work_root.exists():
        raise FileExistsError(work_root)
    work_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, work_root, symlinks=True)
