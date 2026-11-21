from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from app import create_app
from db import (
    ReportLockedError,
    create_report,
    get_db,
    get_report,
    update_report,
    upsert_user,
)
from schemas import AnalysisOutput, ReportInput, RoutingOutput, SendOutcome
from services.storage import read_photo, save_photo


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-session-key",
        "DATABASE": str(tmp_path / "reports.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "private-photos"),
    })


@pytest.fixture
def owners(app):
    with app.app_context():
        first = upsert_user("google-a", "first@example.test")
        second = upsert_user("google-b", "second@example.test")
    return first, second


def example_report(**changes):
    return ReportInput(**{
        "description": "Water fountain is leaking",
        "building_or_area": "ENB",
        "room_floor_landmark": "First floor by the elevators",
        "site_type": "non_residential",
        **changes,
    })


def test_health_identifies_foundation_without_claiming_live_features(app):
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.json["project"] == "Fix It Bulls"
    assert response.json["stage"] == "foundation"
    assert response.json["gmail_connected"] is False


def test_startup_rejects_missing_session_secret(tmp_path):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"SECRET_KEY": None, "DATABASE": str(tmp_path / "unused.db")})


def test_private_responses_are_not_cacheable_and_cookie_is_protected(app):
    response = app.test_client().get("/api/csrf")
    assert response.status_code == 200
    assert response.json["csrf_token"]
    assert "no-store" in response.headers["Cache-Control"]
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_post_routes_require_csrf(app):
    @app.post("/test-write")
    def test_write():
        return {"saved": True}

    client = app.test_client()
    assert client.post("/test-write").status_code == 400
    token = client.get("/api/csrf").json["csrf_token"]
    assert client.post("/test-write", headers={"X-CSRFToken": token}).status_code == 200


def test_report_is_private_and_survives_another_app_context(app, owners):
    with app.app_context():
        report_id = create_report(owners[0], example_report())
    with app.app_context():
        report = get_report(owners[0], report_id)
        assert report["report"]["description"] == "Water fountain is leaking"
        assert report["version"] == 1
        assert get_report(owners[1], report_id) is None


def test_google_subject_is_stable_even_if_email_changes(app):
    with app.app_context():
        first = upsert_user("same-google-sub", "old@example.test")
        second = upsert_user("same-google-sub", "new@example.test")
        assert first == second
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_edit_invalidates_previous_route_and_increments_version(app, owners):
    with app.app_context():
        report_id = create_report(owners[0], example_report())
        get_db().execute(
            "UPDATE reports SET route_id='usf_facilities', communication_status='ready_email' WHERE id=?",
            (report_id,),
        )
        get_db().commit()
        updated = update_report(owners[0], report_id, {"building_or_area": "CMC"})
        assert updated["version"] == 2
        assert updated["report"]["building_or_area"] == "CMC"
        assert updated["route_id"] is None
        assert updated["communication_status"] == "prepared"


def test_other_owner_cannot_edit_report(app, owners):
    with app.app_context():
        report_id = create_report(owners[0], example_report())
        with pytest.raises(LookupError):
            update_report(owners[1], report_id, {"description": "Changed"})
        assert get_report(owners[0], report_id)["version"] == 1


def test_edit_cannot_change_owner_or_privileged_send_fields(app, owners):
    with app.app_context():
        report_id = create_report(owners[0], example_report())
        with pytest.raises(ValueError):
            update_report(owners[0], report_id, {"user_id": owners[1]})
        with pytest.raises(ValueError):
            update_report(owners[0], report_id, {"communication_status": "sent"})


@pytest.mark.parametrize("state", ["sending", "sent", "send_unknown", "reply_received"])
def test_edit_does_not_rewrite_sent_or_uncertain_evidence(app, owners, state):
    with app.app_context():
        report_id = create_report(owners[0], example_report())
        get_db().execute("UPDATE reports SET communication_status=? WHERE id=?", (state, report_id))
        get_db().commit()
        with pytest.raises(ReportLockedError):
            update_report(owners[0], report_id, {"description": "Replacement"})
        assert get_report(owners[0], report_id)["version"] == 1


def test_empty_description_and_unknown_input_fields_are_rejected():
    with pytest.raises(ValidationError):
        example_report(description="  ")
    with pytest.raises(ValidationError):
        example_report(recipient="unapproved@example.test")


def test_ai_question_count_is_bounded():
    with pytest.raises(ValidationError):
        AnalysisOutput(questions=["One?", "Two?", "Three?"])


def test_ready_email_requires_an_existing_route_id():
    with pytest.raises(ValidationError):
        RoutingOutput(state="ready_email", route_id=None, reason="Needs a route")


def test_sent_result_requires_provider_evidence():
    with pytest.raises(ValidationError):
        SendOutcome(state="sent", report_id="report-a", attempt_id="attempt-a")


def test_photo_is_reencoded_and_private(app, owners):
    source = BytesIO()
    Image.new("RGB", (16, 16), color="green").save(source, "PNG")
    source.seek(0)
    with app.app_context():
        photo_id = save_photo(owners[0], source)
        stored = read_photo(owners[0], photo_id)
        assert Image.open(BytesIO(stored)).format == "JPEG"
        with pytest.raises(LookupError):
            read_photo(owners[1], photo_id)


def test_report_cannot_attach_someone_elses_photo(app, owners):
    source = BytesIO()
    Image.new("RGB", (8, 8)).save(source, "PNG")
    source.seek(0)
    with app.app_context():
        photo_id = save_photo(owners[0], source)
        with pytest.raises(ValueError, match="photo"):
            create_report(owners[1], example_report(photo_id=photo_id))


def test_corrupt_and_oversized_images_are_rejected(app, owners):
    with app.app_context():
        with pytest.raises(ValueError, match="image"):
            save_photo(owners[0], BytesIO(b"not an image"))
        with pytest.raises(ValueError, match="size"):
            save_photo(owners[0], BytesIO(b"x" * (5 * 1024 * 1024 + 1)))
