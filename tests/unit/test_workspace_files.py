import os
from pathlib import Path

import pytest

from notebook_launcher.errors import HostPathEscape
from notebook_launcher.workspace import (
    atomic_write,
    canonical_grant_root,
    safe_open_under_root,
    save_copy,
)


def test_save_copy_preserves_existing_without_overwrite(tmp_path: Path):
    source = tmp_path / "source.ipynb"
    destination = tmp_path / "copy.ipynb"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        save_copy(source, destination)

    assert destination.read_bytes() == b"old"


def test_atomic_write_replaces_complete_file(tmp_path: Path):
    target = tmp_path / "file.bin"
    target.write_bytes(b"old")
    atomic_write(target, b"new")
    assert target.read_bytes() == b"new"


def test_whole_home_grant_is_rejected(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ValueError, match="whole home"):
        canonical_grant_root(home, home=home)


def test_safe_open_rejects_parent_traversal(tmp_path: Path):
    root = tmp_path / "grant"
    root.mkdir()
    with pytest.raises(HostPathEscape):
        safe_open_under_root(root, "../outside", flags=os.O_RDONLY)


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires O_NOFOLLOW")
def test_safe_open_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "grant"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(HostPathEscape):
        safe_open_under_root(root, "escape/secret.txt", flags=os.O_RDONLY)
