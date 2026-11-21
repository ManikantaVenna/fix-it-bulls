from html import escape
from io import BytesIO

import pytest
from PIL import Image

from app import create_app
from db import get_report, upsert_user


@pytest.fixture()
def reporting_app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only",
        "DATABASE": str(tmp_path / "reports.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "photos"),
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
