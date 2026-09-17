import pytest

from notebook_launcher.errors import ConflictError
from notebook_launcher.versions import (
    assert_not_active_notebook,
    content_hash,
    new_version,
    require_version,
)


def test_versions_are_opaque_and_distinct():
    assert new_version() != new_version()


def test_stale_version_is_rejected():
    with pytest.raises(ConflictError):
        require_version("old", "new")


def test_active_notebook_generic_file_operation_is_rejected():
    with pytest.raises(ConflictError):
        assert_not_active_notebook(
            target_path="notebooks/main.ipynb",
            active_notebook_path="notebooks/main.ipynb",
        )


def test_active_notebook_guard_normalizes_separators():
    with pytest.raises(ConflictError):
        assert_not_active_notebook(
            target_path="notebooks\\main.ipynb",
            active_notebook_path="notebooks/main.ipynb",
        )


def test_active_notebook_guard_rejects_traversal_alias():
    with pytest.raises(ConflictError, match="normalized"):
        assert_not_active_notebook(
            target_path="notebooks/../main.ipynb",
            active_notebook_path="main.ipynb",
        )


def test_content_hash_is_stable():
    assert content_hash(b"abc") == content_hash(b"abc")
