import base64
from html import escape
from io import BytesIO

import pytest
from cryptography.fernet import Fernet
from PIL import Image

from app import create_app
from db import get_report, upsert_user
from schemas import AnalysisOutput
from services import mail
from services.ai import AIUnavailableError


@pytest.fixture()
def reporting_app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only",
        "DATABASE": str(tmp_path / "reports.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "photos"),
        "GMAIL_TOKEN_KEY": Fernet.generate_key(),
        "TEST_RECIPIENT": "test-inbox@example.test",
    })
    with app.app_context():
        owner = upsert_user("first-test-owner", "first@example.test")
        other = upsert_user("second-test-owner", "second@example.test")
    return app, owner, other


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id


def report_fields(client):
    return {
        "csrf_token": client.get("/api/csrf").json["csrf_token"],
        "description": "A classroom <screen> went dark.",
        "building_or_area": "Engineering II",
        "room_floor_landmark": "Room 100",
        "site_type": "non_residential",
        "ownership": "campus",
    }


def test_capture_review_and_private_photo(reporting_app):
    app, owner, other = reporting_app
    client = app.test_client()
    sign_in(client, owner)
    fields = report_fields(client)
    image = BytesIO()
    Image.new("RGB", (24, 24), "#075640").save(image, format="PNG")
    image.seek(0)
    result = client.post("/reports/new", data={**fields, "photo": (image, "screen.png")})
    assert result.status_code == 303
    review = client.get(result.location)
    assert review.status_code == 200
    assert "Campus issue at Engineering II" in review.text
    assert escape(fields["description"]) in review.text

    photo_url = result.location.rsplit("/", 1)[0] + "/photo"
    photo = client.get(photo_url)
    assert photo.mimetype == "image/jpeg"
    assert photo.data.startswith(b"\xff\xd8")
    assert photo.headers["Cache-Control"] == "no-store"

    sign_in(client, other)
    assert client.get(result.location).status_code == 404
    assert client.get(photo_url).status_code == 404


def test_requires_real_authentication_and_csrf(reporting_app):
    app, owner, _ = reporting_app
    client = app.test_client()
    assert client.post("/reports/new", data=report_fields(client)).status_code == 401
    sign_in(client, owner)
    fields = report_fields(client)
    fields.pop("csrf_token")
    assert client.post("/reports/new", data=fields).status_code == 400


def test_invalid_photo_keeps_form_text_and_allows_text_only(reporting_app):
    app, owner, _ = reporting_app
    client = app.test_client()
    sign_in(client, owner)
    fields = report_fields(client)
    response = client.post(
        "/reports/new", data={**fields, "photo": (BytesIO(b"not a picture"), "screen.jpg")}
    )
    assert response.status_code == 422
    assert "Invalid image" in response.text
    assert escape(fields["description"]) in response.text

    result = client.post("/reports/new", data=fields)
    assert result.status_code == 303
    report_id = result.location.split("/")[-2]
    with app.app_context():
        assert get_report(owner, report_id)["report"]["photo_id"] is None


@pytest.mark.parametrize("issue_type, site_type, urgent, expected", [
    ("computer_av", "non_residential", False, "USF IT"),
    ("plumbing", "residence_hall", False, "Open official form"),
    ("plumbing", "non_residential", True, "Urgent guidance"),
])
def test_analysis_updates_the_review_and_saves_urgency(reporting_app, monkeypatch, issue_type, site_type, urgent, expected):
    app, owner, other = reporting_app
    client = app.test_client()
    sign_in(client, owner)
    fields = report_fields(client)
    result = client.post("/reports/new", data={**fields, "site_type": site_type})
    report_id = result.location.split("/")[-2]
    analysis = AnalysisOutput(
        suggested_issue_type=issue_type, proposed_title="Reviewed issue",
        proposed_body="A maintenance request.", possible_urgent_concern=urgent,
    )
    monkeypatch.setattr("services.ai.analyze_report", lambda *args, **kwargs: analysis)
    action = f"/reports/{report_id}/analyze"
    response = client.post(action, data={"csrf_token": fields["csrf_token"], "report_version": 1}, follow_redirects=True)
    assert response.status_code == 200
    assert expected in response.text
    with app.app_context():
        assert get_report(owner, report_id)["flagged_urgent"] is urgent
    sign_in(client, other)
    assert client.post(action, data={"csrf_token": fields["csrf_token"], "report_version": 1}).status_code == 404


def test_manual_fallback_gets_verified_preview_and_reply_through_report_flow(reporting_app, monkeypatch):
    app, owner, _ = reporting_app
    client = app.test_client()
    sign_in(client, owner)
    fields = report_fields(client)
    created = client.post("/reports/new", data=fields)
    report_path = created.location.rsplit("/", 1)[0]
    form = {"csrf_token": fields["csrf_token"], "report_version": 1}

    def offline(*args, **kwargs):
        raise AIUnavailableError

    monkeypatch.setattr("routes.reports.triage", offline)
    response = client.post(report_path + "/analyze", data=form)
    assert response.status_code == 503
    assert "Choose a category" in response.text
    response = client.post(report_path + "/analyze", data={**form, "issue_type": "computer_av"}, follow_redirects=True)
    assert "USF IT" in response.text
    assert "Connect Gmail" in response.text

    with app.app_context():
        mail.connect_mailbox(owner, "first-test-owner", "fixture-only")
    response = client.get(created.location)
    assert "Review test email" in response.text
    headers = {"X-CSRFToken": fields["csrf_token"]}
    draft = {"report_version": 1, "issue_type": "computer_av", "subject": "Display issue", "body": "Display is dark."}
    wrong_category = client.post(report_path + "/email-review", headers=headers, json={**draft, "issue_type": "plumbing"})
    assert wrong_category.status_code == 409
    preview = client.post(report_path + "/email-review", headers=headers, json=draft)
    assert preview.json["recipient"] == "test-inbox@example.test"
    assert preview.json["route"]["route_id"] == "usf_it_help"

    monkeypatch.setattr(mail.gmail_client, "send_message", lambda *args: {"id": "out", "threadId": "thread"})
    sent = client.post(report_path + "/approve-send", headers=headers, json={
        "approved_version": preview.json["report_version"], "approval_id": preview.json["approval_id"],
    })
    assert sent.json["state"] == "sent"
    assert "Check for replies" in client.get(created.location).text
    assert "Engineering II" in client.get("/reports").text
    messages = [
        {"id": "out", "internalDate": "1795291200000", "labelIds": ["SENT"]},
        {"id": "in", "internalDate": "1795291201000", "payload": {
            "headers": [{"name": "From", "value": "Test desk <test-inbox@example.test>"}],
            "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"We received the report.").decode()},
        }},
    ]
    monkeypatch.setattr(mail.gmail_client, "get_thread", lambda *args: {"id": "thread", "messages": messages})
    replies = client.post(report_path + "/check-replies", headers=headers, json={})
    assert [item["safe_text"] for item in replies.json["replies"]] == ["We received the report."]
    assert "Reply received" in client.get(created.location).text


def test_analyst_gets_one_clarification_round(reporting_app, monkeypatch):
    app, owner, _ = reporting_app
    client = app.test_client()
    sign_in(client, owner)
    fields = report_fields(client)
    created = client.post("/reports/new", data=fields)
    action = created.location.rsplit("/", 1)[0] + "/analyze"
    form = {"csrf_token": fields["csrf_token"], "report_version": 1}
    first = "Is the display powered on?"
    second = "Does the display have a label?"

    def ask(report, photo_bytes, **kwargs):
        question = second if report.clarification_answers else first
        return AnalysisOutput(suggested_issue_type="computer_av", questions=[question])

    monkeypatch.setattr("services.ai.analyze_report", ask)
    started = client.post(action, data=form, follow_redirects=True)
    assert first in started.text and "Continue analysis" in started.text
    answered = client.post(action, data={**form, "answer_0": "Yes"}, follow_redirects=True)
    assert "USF IT" in answered.text
    assert "Continue analysis" not in answered.text
    assert client.post(action, data=form).status_code == 409
