from pathlib import Path

import pytest

from notebook_launcher.errors import HostPathEscape
from notebook_launcher.safe_paths import (
    RootIdentity,
    atomic_write_under_root,
    read_bytes_under_root,
    root_identity,
)


def test_component_safe_operations_reject_traversal(tmp_path: Path):
    root = tmp_path / "grant"
    root.mkdir()

    with pytest.raises(HostPathEscape):
        atomic_write_under_root(root, "../outside.txt", b"escape")

    assert not (tmp_path / "outside.txt").exists()


def test_component_safe_operations_reject_symlink_component(tmp_path: Path):
    root = tmp_path / "grant"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    try:
        (root / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(HostPathEscape):
        read_bytes_under_root(root, "escape/secret.txt")


def test_root_identity_replacement_is_rejected(tmp_path: Path):
    root = tmp_path / "grant"
    root.mkdir()
    identity = root_identity(root)
    root.rename(tmp_path / "original")
    root.mkdir()

    with pytest.raises(HostPathEscape):
        atomic_write_under_root(
            root,
            "data.txt",
            b"data",
            expected_identity=RootIdentity(identity.device, identity.inode),
        )


def test_no_overwrite_create_is_atomic(tmp_path: Path):
    root = tmp_path / "grant"
    root.mkdir()
    (root / "data.txt").write_text("old")

    with pytest.raises(FileExistsError):
        atomic_write_under_root(root, "data.txt", b"new", overwrite=False)

    assert (root / "data.txt").read_text() == "old"
