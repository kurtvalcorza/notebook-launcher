import pytest

from notebook_launcher.source import (
    normalize_notebook_path,
    parse_github_blob_url,
    parse_repo_fields,
    resolve_blob_tail,
)


def test_repo_fields_validate_notebook_path():
    source = parse_repo_fields("owner/repo", "feature/x", "nbs/demo.ipynb")
    assert source.repo_full_name == "owner/repo"
    assert source.requested_ref == "feature/x"
    assert source.notebook_path == "nbs/demo.ipynb"


def test_path_traversal_is_rejected():
    with pytest.raises(ValueError):
        normalize_notebook_path("../secret.ipynb")


def test_non_github_blob_url_is_rejected():
    with pytest.raises(ValueError):
        parse_github_blob_url("https://example.com/owner/repo/blob/main/a.ipynb")


def test_slash_ref_uses_longest_known_prefix():
    blob = parse_github_blob_url(
        "https://github.com/owner/repo/blob/feature/x/notebooks/demo.ipynb"
    )
    source = resolve_blob_tail(blob, {"feature", "feature/x"})
    assert source.requested_ref == "feature/x"
    assert source.notebook_path == "notebooks/demo.ipynb"
