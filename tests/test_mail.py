import base64
from email import policy
from email.parser import BytesParser
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from PIL import Image

from app import create_app
from db import create_report, get_db, get_report, update_report, upsert_user
from schemas import RoutingOutput
from services import mail
from services.gmail_client import GMAIL_SCOPES, DeliveryUncertain
from services.storage import save_photo


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app({
        "TESTING": True, "SECRET_KEY": "test-only", "DATABASE": str(tmp_path / "db.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "photos"), "GMAIL_TOKEN_KEY": Fernet.generate_key(),
        "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "test-secret",
        "GMAIL_REDIRECT_URI": "http://localhost:5000/gmail/callback", "TEST_RECIPIENT": "test@example.test",
    })
    with app.app_context():
        owner = upsert_user("owner-sub", "owner@example.test")
        stranger = upsert_user("other-sub", "other@example.test")
        photo = BytesIO()
        Image.new("RGB", (16, 16)).save(photo, "PNG")
        photo.seek(0)
        report = create_report(owner, {
            "description": "Fixture leaks", "site_type": "non_residential", "building_or_area": "ENB",
            "room_floor_landmark": "First floor", "photo_id": save_photo(owner, photo),
        })
    monkeypatch.setattr(mail, "_resolve_route", lambda report, issue: RoutingOutput(
        state="ready_email", route_id="facilities", reason="Facilities handles it",
        official_source="https://www.usf.edu/facilities/", checked_date="2026-11-21",
    ))
    return app, owner, stranger, report


def preview(owner, report):
    return mail.prepare_email_review(owner, report, 1, "plumbing", "Leaking fixture", "Fixture leaks.")


def connect(owner):
    mail.connect_mailbox(owner, "owner-sub", "refresh-token-fixture")


def send(owner, report, review):
    return mail.send_approved_report(owner, report, 1, approval_id=review["approval_id"])


def test_connection_separates_gmail_scopes_and_checks_returned_subject(setup, monkeypatch):
    from routes import gmail

    app, owner, _, _ = setup
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = owner
    csrf = client.get("/api/csrf").json["csrf_token"]
    response = client.post("/gmail/connect", headers={"X-CSRFToken": csrf})
    assert response.status_code == 303
    query = parse_qs(urlparse(response.location).query)
    assert set(GMAIL_SCOPES) <= set(query["scope"][0].split())
    assert query["access_type"] == ["offline"]
    monkeypatch.setattr(gmail, "exchange_consent", lambda code, pending: ("other-sub", "token"))
    callback = client.get("/gmail/callback", query_string={"code": "fixture", "state": query["state"][0]})
    assert callback.status_code == 403
    assert client.get("/gmail/status").json["connected"] is False


def test_mailbox_credentials_are_encrypted_and_owner_scoped(setup):
    app, owner, stranger, report = setup
    with app.app_context():
        with pytest.raises(mail.MailError, match="Connect Gmail"):
            preview(owner, report)
        connect(owner)
        ciphertext = get_db().execute("SELECT encrypted_refresh_token FROM gmail_connections").fetchone()[0]
        assert b"refresh-token-fixture" not in bytes(ciphertext)
        assert mail.mailbox_connected(owner) and not mail.mailbox_connected(stranger)
        with pytest.raises(mail.MailError) as denied:
            preview(stranger, report)
        assert denied.value.status == 404


def test_review_only_uses_maintained_routes(setup, monkeypatch):
    app, owner, _, report = setup
    monkeypatch.setattr(mail, "_resolve_route", lambda *args: RoutingOutput(
        state="researched_handoff", reason="Use the official instructions",
    ))
    with app.app_context():
        connect(owner)
        with pytest.raises(mail.MailError, match="verified email route"):
            preview(owner, report)


def test_edited_report_cannot_send_stale_preview(setup):
    app, owner, _, report = setup
    with app.app_context():
        connect(owner)
        draft = preview(owner, report)
        update_report(owner, report, {"building_or_area": "Other building"})
        with pytest.raises(mail.MailError, match="changed"):
            send(owner, report, draft)


def test_unapproved_report_cannot_send(setup):
    app, owner, _, report = setup
    with app.app_context():
        connect(owner)
        with pytest.raises(mail.MailError, match="review"):
            mail.send_approved_report(owner, report, 1, approval_id="invalid")


def test_exact_review_sends_attachment_once_and_locks_out_edits(setup, monkeypatch):
    app, owner, _, report = setup
    calls = []

    def deliver(token, raw):
        calls.append(token)
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
        assert str(message["To"]) == "test@example.test"
        assert str(message["Subject"]).startswith("[DEMO]")
        assert next(message.iter_attachments()).get_content_type() == "image/jpeg"
        with app.app_context():
            assert send(owner, report, draft).state == "sending"
            with pytest.raises(ValueError, match="Preserve outbound evidence"):
                update_report(owner, report, {"description": "Changed"})
        return {"id": "sent-id", "threadId": "thread-id"}

    monkeypatch.setattr(mail.gmail_client, "send_message", deliver)
    with app.app_context():
        connect(owner)
        draft = preview(owner, report)
        assert draft["recipient"] == "test@example.test"
        assert send(owner, report, draft).provider_thread_id == "thread-id"
        assert send(owner, report, draft).state == "sent"
        assert calls == ["refresh-token-fixture"]
        assert get_report(owner, report)["route_id"] == "facilities"


def test_ambiguous_delivery_retains_state_and_does_not_resend(setup, monkeypatch):
    app, owner, _, report = setup
    calls = []

    def deliver(*args):
        calls.append(1)
        raise DeliveryUncertain

    monkeypatch.setattr(mail.gmail_client, "send_message", deliver)
    with app.app_context():
        connect(owner)
        draft = preview(owner, report)
        assert send(owner, report, draft).state == "send_unknown"
        assert send(owner, report, draft).state == "send_unknown"
        assert calls == [1]


def test_reply_check_reads_known_thread_and_records_each_reply_once(setup, monkeypatch):
    app, owner, stranger, report = setup
    monkeypatch.setattr(mail.gmail_client, "send_message", lambda *args: {"id": "out", "threadId": "thread-id"})
    reply = {
        "id": "in", "internalDate": "1795291201000",
        "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": "Desk <desk@example.test>"}],
                    "body": {"data": base64.urlsafe_b64encode(b"We received your report.").decode()}},
    }

    def get_thread(token, thread_id):
        assert thread_id == "thread-id"
        return {"id": thread_id, "messages": [{"id": "out", "internalDate": "1795291200000"}, reply, reply]}

    monkeypatch.setattr(mail.gmail_client, "get_thread", get_thread)
    with app.app_context():
        connect(owner)
        with pytest.raises(mail.MailError, match="sent Gmail report"):
            mail.check_report_replies(owner, report)
        send(owner, report, preview(owner, report))
        with pytest.raises(mail.MailError) as denied:
            mail.check_report_replies(stranger, report)
        assert denied.value.status == 404
        for _ in range(2):
            replies = mail.check_report_replies(owner, report)
            assert [event.safe_text for event in replies] == ["We received your report."]
        assert get_db().execute("SELECT COUNT(*) FROM gmail_reply_refs").fetchone()[0] == 1
        assert get_report(owner, report)["communication_status"] == "reply_received"


def test_http_review_and_send_require_owner_csrf_and_exact_approval(setup, monkeypatch):
    app, owner, stranger, report = setup
    calls = []

    def deliver(*args):
        calls.append(1)
        return {"id": "out", "threadId": "thread-id"}

    monkeypatch.setattr(mail.gmail_client, "send_message", deliver)
    with app.app_context():
        connect(owner)

    client = app.test_client()
    review_url = f"/reports/{report}/email-review"
    send_url = f"/reports/{report}/approve-send"
    draft = {"report_version": 1, "issue_type": "plumbing", "subject": "Fixture", "body": "Fixture leaks."}
    csrf = {"X-CSRFToken": client.get("/api/csrf").json["csrf_token"]}
    assert client.post(review_url, json=draft, headers=csrf).status_code == 401
    with client.session_transaction() as state:
        state["user_id"] = stranger
    assert client.post(review_url, json=draft, headers=csrf).status_code == 404
    with client.session_transaction() as state:
        state["user_id"] = owner
    assert client.post(review_url, json=draft).status_code == 400
    assert client.post(review_url, json={**draft, "recipient": "other@example.test"}, headers=csrf).status_code == 400
    review = client.post(review_url, json=draft, headers=csrf)
    assert review.status_code == 200
    assert review.json["recipient"] == "test@example.test"
    approval = {"approved_version": review.json["report_version"], "approval_id": review.json["approval_id"]}
    assert client.post(send_url, json=approval).status_code == 400
    assert client.post(send_url, json=approval, headers=csrf).json["state"] == "sent"
    assert client.post(send_url, json=approval, headers=csrf).json["state"] == "sent"
    assert calls == [1]


@pytest.mark.parametrize("address", ["", "incomplete", "first@example.test, second@example.test"])
def test_invalid_test_recipient_reports_configuration_error(setup, address):
    app, owner, _, report = setup
    app.config["TEST_RECIPIENT"] = address
    with app.app_context():
        connect(owner)
        with pytest.raises(mail.MailError, match="single controlled") as invalid:
            preview(owner, report)
        assert invalid.value.status == 503


def test_changed_test_recipient_invalidates_review(setup):
    app, owner, _, report = setup
    with app.app_context():
        connect(owner)
        review = preview(owner, report)
        app.config["TEST_RECIPIENT"] = "other@example.test"
        with pytest.raises(mail.MailError, match="recipient.*changed"):
            send(owner, report, review)
