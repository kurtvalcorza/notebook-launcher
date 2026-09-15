from notebook_launcher.errors import ConflictError, HostPathEscape, StorageFull


def test_public_errors_are_secret_safe_and_typed():
    assert ConflictError().public_dict()["error"] == "conflict"
    assert HostPathEscape().http_status == 403
    assert StorageFull().http_status == 507
