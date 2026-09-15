from notebook_launcher.audit import AuditEvent, AuditStore, MAX_TARGET_LENGTH


def test_audit_target_is_bounded(state_store):
    audit = AuditStore(state_store)
    audit.record(AuditEvent(operation="execute", target="x" * (MAX_TARGET_LENGTH + 100)))
    with state_store.connect() as conn:
        row = conn.execute("SELECT target FROM audit_events").fetchone()
    assert len(row["target"]) == MAX_TARGET_LENGTH
