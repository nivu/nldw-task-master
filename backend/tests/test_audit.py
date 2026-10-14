from app.domain.audit import SYSTEM, actor_name


def test_no_actor_reads_as_system():
    assert actor_name(None, {"u1": "Deepika"}) == SYSTEM == "System"


def test_a_person_reads_as_their_name():
    assert actor_name("u1", {"u1": "Deepika"}) == "Deepika"


def test_an_unknown_actor_falls_back_to_the_id():
    assert actor_name("u9", {"u1": "Deepika"}) == "u9"


# ---------------------------------------------------------------------------
# Spec 007 FR-CRM-CHG-09, FR-CRM-TVA-03 — the widened actor
# ---------------------------------------------------------------------------


def test_a_system_row_with_a_person_names_them_via_the_crm():
    assert actor_name("u1", {"u1": "Deepika"}, "system") == "Deepika (via CRM deal)"


def test_a_user_row_reads_as_the_person():
    assert actor_name("u1", {"u1": "Deepika"}, "user") == "Deepika"


def _recorded(monkeypatch, **kwargs) -> dict:
    from app.services import audit
    from app.services import supabase as db

    written: list[dict] = []
    monkeypatch.setattr(db, "insert_audit", written.append)
    audit.record(action="user.created", target_table="profiles", target_id="u1", **kwargs)
    return written[0]


def test_record_writes_workspace_label_and_channel(monkeypatch):
    entry = _recorded(
        monkeypatch, actor_id="u-admin", workspace_id="ws-1", actor_label="system", channel="mcp"
    )
    assert (entry["workspace_id"], entry["actor_label"], entry["channel"]) == (
        "ws-1",
        "system",
        "mcp",
    )


def test_record_without_them_is_a_tva_row_as_before(monkeypatch):
    entry = _recorded(monkeypatch, actor_id="u-admin")
    assert entry["actor_label"] == "user"
    assert "workspace_id" not in entry and "channel" not in entry


def test_record_with_no_actor_is_still_system(monkeypatch):
    assert _recorded(monkeypatch, actor_id=None)["actor_label"] == "system"
