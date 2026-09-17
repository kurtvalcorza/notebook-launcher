from notebook_launcher.audit import (
    MAX_TARGET_LENGTH,
    AuditEvent,
    AuditStore,
    sanitize_audit_target,
)


def test_audit_target_is_bounded(state_store):
    audit = AuditStore(state_store)
    audit.record(AuditEvent(operation="execute", target="x" * (MAX_TARGET_LENGTH + 100)))
    with state_store.connect() as conn:
        row = conn.execute("SELECT target FROM audit_events").fetchone()
    assert len(row["target"]) == MAX_TARGET_LENGTH


def test_audit_target_redacts_credentials_and_flattens_payloads():
    safe = sanitize_audit_target(
        "cell.ipynb\nAuthorization: Bearer raw-token token=other password=hunter2"
    )

    assert safe is not None
    assert "raw-token" not in safe
    assert "other" not in safe
    assert "hunter2" not in safe
    assert "\n" not in safe
    assert safe.count("[REDACTED]") == 3
