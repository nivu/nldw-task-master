from app.domain.audit import SYSTEM, actor_name


def test_no_actor_reads_as_system():
    assert actor_name(None, {"u1": "Deepika"}) == SYSTEM == "System"


def test_a_person_reads_as_their_name():
    assert actor_name("u1", {"u1": "Deepika"}) == "Deepika"


def test_an_unknown_actor_falls_back_to_the_id():
    assert actor_name("u9", {"u1": "Deepika"}) == "u9"
