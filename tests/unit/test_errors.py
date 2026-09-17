from notebook_launcher.errors import (
    ConflictError,
    ExecutionCancelled,
    ExecutionTimeout,
    HostPathChanged,
    HostPathEscape,
    InvalidLaunchToken,
    LaunchTokenExpired,
    NetworkPolicyUnavailable,
    RepositoryIdentityChanged,
    RepositorySetupFailed,
    SessionNotReady,
    StorageFull,
    TrustNotFound,
    UnknownSession,
    UnknownWorkspace,
)


def test_public_errors_are_secret_safe_and_typed():
    assert ConflictError().public_dict()["error"] == "conflict"
    assert HostPathEscape().http_status == 403
    assert StorageFull().http_status == 507
    assert InvalidLaunchToken().public_dict()["error"] == "invalid_launch_token"
    assert LaunchTokenExpired().public_dict()["error"] == "launch_token_expired"
    assert ExecutionTimeout().public_dict()["error"] == "execution_timeout"
    assert ExecutionCancelled().public_dict()["error"] == "execution_cancelled"
    assert RepositorySetupFailed().public_dict()["error"] == "repository_setup_failed"
    assert RepositoryIdentityChanged().public_dict()["error"] == "repository_identity_changed"
    assert HostPathChanged().public_dict()["error"] == "host_path_changed"
    assert NetworkPolicyUnavailable().http_status == 503
    assert SessionNotReady().http_status == 409
    assert UnknownSession().http_status == 404
    assert UnknownWorkspace().http_status == 404
    assert TrustNotFound().public_dict()["error"] == "trust_not_found"
