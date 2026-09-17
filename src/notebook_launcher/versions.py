from __future__ import annotations

import hashlib
import secrets
from pathlib import PurePosixPath

from .errors import ConflictError


def new_version() -> str:
    return secrets.token_urlsafe(24)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_version(expected: str | None, current: str) -> None:
    if expected is None or not secrets.compare_digest(expected, current):
        raise ConflictError()


def assert_not_active_notebook(*, target_path: str, active_notebook_path: str) -> None:
    if _normalized_path(target_path) == _normalized_path(active_notebook_path):
        raise ConflictError(
            "The active notebook must be changed through notebook-aware operations."
        )


def _normalized_path(path: str) -> str:
    candidate = PurePosixPath(path.replace("\\", "/"))
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ConflictError("Workspace paths must be normalized relative paths.")
    return candidate.as_posix()
