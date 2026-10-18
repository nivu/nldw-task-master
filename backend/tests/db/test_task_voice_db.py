"""Voice capture in the real database — spec 008 FR-TASK-VOICE-01, -02, -05;
data-model §2.9; migration 054.

What only Postgres and Storage can prove: the `task-voice` bucket is
private, takes at most 4 MB of the listed audio types, and has no
`storage.objects` policy, so neither the browser's anon key nor a signed-in
person can read or write it; only the service role can. The `voice_uploads`
row is unreadable from the browser, holds at most 20,000 characters of
transcript, goes with its profile, and `services/tasks/voice.py`'s own
queries (the sweep's included) work against it.
"""

from __future__ import annotations

import uuid

import pytest
from supabase import create_client

from app.services import supabase as db
from app.services.tasks import voice

pytestmark = pytest.mark.db

BUCKET = "task-voice"


@pytest.fixture
def service(stack, monkeypatch):
    """`services/tasks/voice.py` pointed at the local stack."""
    monkeypatch.setattr(db, "supabase", create_client(stack.url, stack.service_key))


def _object_url(stack, path: str) -> str:
    return f"{stack.url}/storage/v1/object/{BUCKET}/{path}"


class TestTheBucket:
    def test_it_is_private_4_mb_and_audio_only(self, stack):
        [bucket] = stack.sql(
            "SELECT public, file_size_limit, allowed_mime_types FROM storage.buckets "
            f"WHERE id = {stack.q(BUCKET)}"
        )
        assert bucket["public"] is False
        assert bucket["file_size_limit"] == 4 * 1024 * 1024
        assert sorted(bucket["allowed_mime_types"]) == sorted(voice.AUDIO_TYPES)

    def test_no_storage_objects_policy_reaches_it(self, stack):
        policies = stack.sql(
            "SELECT policyname, coalesce(qual, '') || ' ' || coalesce(with_check, '') AS rule "
            "FROM pg_policies WHERE schemaname = 'storage' AND tablename = 'objects'"
        )
        # Every policy there is about another, named bucket.
        for policy in policies:
            assert BUCKET not in policy["rule"], policy["policyname"]
            assert "bucket_id" in policy["rule"], policy["policyname"]

    def test_only_the_service_role_reads_and_writes_it(self, stack, service, make_profile):
        user = make_profile()
        path = f"{user}/{uuid.uuid4()}"
        voice.put_audio(path, b"OggS a voice note", "audio/ogg")
        try:
            for bearer in (stack.anon_key, stack.jwt_for(user)):
                headers = {"apikey": stack.anon_key, "Authorization": f"Bearer {bearer}"}
                r = stack.http.get(_object_url(stack, path), headers=headers)
                assert r.status_code in (400, 401, 403, 404), r.text
                r = stack.http.post(
                    _object_url(stack, f"{user}/{uuid.uuid4()}"),
                    headers={**headers, "content-type": "audio/ogg"},
                    content=b"OggS",
                )
                assert r.status_code in (400, 401, 403, 404), r.text
            assert voice.get_audio(path) == b"OggS a voice note"
        finally:
            voice.delete_audio(path)
        rows = stack.sql(
            f"SELECT 1 FROM storage.objects WHERE bucket_id = {stack.q(BUCKET)} "
            f"AND name = {stack.q(path)}"
        )
        assert rows == []

    def test_it_refuses_a_file_that_is_not_audio(self, stack, service, make_profile):
        user = make_profile()
        with pytest.raises(Exception):  # noqa: B017 — Storage's own refusal
            voice.put_audio(f"{user}/{uuid.uuid4()}", b"%PDF", "application/pdf")


class TestTheRow:
    def test_the_browser_cannot_read_it(self, stack, make_profile):
        user = make_profile()
        r = stack.rest("voice_uploads", bearer=stack.jwt_for(user))
        assert r.status_code in (401, 403), r.text

    def test_the_service_writes_reads_and_sweeps_it(self, stack, service, make_profile):
        user = make_profile()
        upload_id = str(uuid.uuid4())
        path = f"{user}/{upload_id}"
        voice.insert_upload(
            {"id": upload_id, "user_id": user, "status": "uploaded", "object_path": path}
        )
        voice.update_upload(
            upload_id,
            {
                "status": "transcribed",
                "transcript": "call Kiran",
                "candidates": [
                    {"index": 0, "kind": "task", "text": "call Kiran", "handled": False}
                ],
            },
        )
        row = voice.get_upload(upload_id)
        assert (row["status"], row["transcript"], row["candidates"][0]["text"]) == (
            "transcribed",
            "call Kiran",
            "call Kiran",
        )
        stack.sql(
            "UPDATE voice_uploads SET created_at = now() - interval '25 hours' "
            f"WHERE id = {stack.q(upload_id)}"
        )
        later = stack.sql("SELECT (now() - interval '23 hours 30 minutes')::text AS t")[0]["t"]
        assert upload_id in {r["id"] for r in voice.expired_uploads(later)}
        voice.update_upload(upload_id, {"transcript": None, "object_path": None})
        assert upload_id not in {r["id"] for r in voice.expired_uploads(later)}

    def test_a_candidate_is_marked_once_and_the_last_clears_the_transcript(
        self, stack, service, make_profile
    ):
        user = make_profile()
        upload_id = str(uuid.uuid4())
        voice.insert_upload({"id": upload_id, "user_id": user, "status": "transcribed"})
        voice.update_upload(
            upload_id,
            {
                "transcript": "call Kiran and email Deepa",
                "candidates": [
                    {"index": 0, "kind": "task", "text": "call Kiran", "handled": False},
                    {"index": 1, "kind": "task", "text": "email Deepa", "handled": False},
                ],
            },
        )
        assert voice.mark_candidate(upload_id, 0, True) is True
        assert voice.mark_candidate(upload_id, 0, True) is False  # a second confirm
        assert voice.finish_upload(upload_id) is False  # #1 still open
        assert voice.mark_candidate(upload_id, 1, True) is True
        row = voice.get_upload(upload_id)
        assert [c["handled"] for c in row["candidates"]] == [True, True]
        assert voice.mark_candidate(upload_id, 1, False) is True  # a release
        assert voice.mark_candidate(upload_id, 1, True) is True
        assert voice.finish_upload(upload_id) is True
        row = voice.get_upload(upload_id)
        assert (row["status"], row["transcript"]) == ("done", None)
        assert [c["text"] for c in row["candidates"]] == [None, None]
        assert [c["kind"] for c in row["candidates"]] == ["task", "task"]

    def test_a_swept_note_is_never_marked(self, stack, service, make_profile):
        user = make_profile()
        upload_id = str(uuid.uuid4())
        voice.insert_upload({"id": upload_id, "user_id": user, "status": "transcribed"})
        voice.update_upload(
            upload_id,
            {
                "transcript": None,
                "status": "done",
                "candidates": [{"index": 0, "kind": "task", "text": None, "handled": False}],
            },
        )
        assert voice.mark_candidate(upload_id, 0, True) is False
        assert voice.get_upload(upload_id)["candidates"][0]["handled"] is False

    def test_the_browser_cannot_call_the_functions(self, stack, make_profile):
        user = make_profile()
        for fn, body in (
            (
                "voice_candidate_mark",
                {"p_upload": str(uuid.uuid4()), "p_index": 0, "p_handled": True},
            ),
            ("voice_upload_finish", {"p_upload": str(uuid.uuid4())}),
        ):
            r = stack.http.post(
                f"{stack.url}/rest/v1/rpc/{fn}",
                headers={
                    "apikey": stack.anon_key,
                    "Authorization": f"Bearer {stack.jwt_for(user)}",
                },
                json=body,
            )
            assert r.status_code in (401, 403, 404), r.text

    def test_a_transcript_over_20000_characters_is_refused(self, stack, make_profile):
        user = make_profile()
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                "INSERT INTO voice_uploads (user_id, transcript) VALUES "
                f"({stack.q(user)}, repeat('a', 20001))"
            )
        assert raised.value.code == "23514"

    def test_only_the_five_statuses(self, stack, make_profile):
        user = make_profile()
        with pytest.raises(stack.SqlError) as raised:
            stack.sql(
                f"INSERT INTO voice_uploads (user_id, status) VALUES ({stack.q(user)}, 'sent')"
            )
        assert raised.value.code == "23514"

    def test_it_goes_with_its_profile(self, stack, make_profile):
        user = make_profile(keep=True)
        stack.sql(f"INSERT INTO voice_uploads (user_id) VALUES ({stack.q(user)})")
        stack.sql(f"DELETE FROM auth.users WHERE id = {stack.q(user)}")
        assert stack.sql(f"SELECT 1 FROM voice_uploads WHERE user_id = {stack.q(user)}") == []

    def test_it_is_not_audited(self, stack, make_profile):
        user = make_profile()
        [row] = stack.sql(
            f"INSERT INTO voice_uploads (user_id, transcript) VALUES ({stack.q(user)}, "
            "'zqxvoice') RETURNING id"
        )
        audited = stack.sql(
            f"SELECT 1 FROM audit_log WHERE target_id = {stack.q(row['id'])} "
            "OR target_table = 'voice_uploads'"
        )
        assert audited == []
