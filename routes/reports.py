from flask import Blueprint, Response, abort, redirect, render_template, request, session, url_for
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from db import create_report, get_db, get_report
from schemas import ReportInput
from services.storage import read_photo, save_photo


bp = Blueprint("reports", __name__, url_prefix="/reports")

FORM_FIELDS = (
    "description", "site_type", "building_or_area", "room_floor_landmark",
    "asset_tag", "ownership",
)


def signed_in_user():
    # Shared authentication is expected to put the database user ID here.
    user_id = session.get("user_id")
    if not isinstance(user_id, str):
        return None
    user = get_db().execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_id if user else None


def show_form(values=None, errors=None, *, user_id=None, status=200):
    return render_template(
        "reports/new.html", values=values or {}, errors=errors or {}, signed_in=bool(user_id)
    ), status


@bp.route("/new", methods=["GET", "POST"])
def new():
    user_id = signed_in_user()
    if request.method == "GET":
        return show_form(user_id=user_id)

    if user_id is None:
        return show_form(errors={"form": "Sign in before saving a report."}, status=401)

    values = {field: request.form[field] for field in FORM_FIELDS if field in request.form}
    try:
        report = ReportInput.model_validate(values)
    except ValidationError as error:
        messages = {
            item["loc"][0]: "Enter a valid value within the limit shown."
            for item in error.errors()
        }
        return show_form(values, messages, user_id=user_id, status=422)

    photos = [photo for photo in request.files.getlist("photo") if photo.filename]
    if len(photos) > 1:
        return show_form(values, {"photo": "Choose one photo."}, user_id=user_id, status=422)
    if photos:
        try:
            photo_id = save_photo(user_id, photos[0])
        except ValueError as error:
            return show_form(values, {"photo": str(error)}, user_id=user_id, status=422)
        report = ReportInput.model_validate({**report.model_dump(), "photo_id": photo_id})

    report_id = create_report(user_id, report)
    return redirect(url_for("reports.review", report_id=report_id), code=303)


@bp.errorhandler(RequestEntityTooLarge)
def file_too_large(_error):
    return show_form(
        errors={"photo": "The upload is too large. Use an image of 5 MB or less."},
        user_id=signed_in_user(),
        status=413,
    )


def owned_report(report_id):
    user_id = signed_in_user()
    if user_id is None:
        abort(401)
    report = get_report(user_id, report_id)
    if report is None:
        abort(404)
    return user_id, report


@bp.get("/<report_id>/review")
def review(report_id):
    _, saved = owned_report(report_id)
    report = ReportInput.model_validate(saved["report"])
    lines = [
        "Campus: USF Tampa",
        f"Building or area: {report.building_or_area}",
    ]
    if report.room_floor_landmark:
        lines.append(f"Specific location: {report.room_floor_landmark}")
    if report.asset_tag:
        lines.append(f"Asset tag: {report.asset_tag}")
    lines.extend(("", report.description))
    return render_template(
        "reports/review.html", report=report, report_id=report_id,
        subject=f"Campus issue at {report.building_or_area}"[:200],
        body="\n".join(lines),
    )


@bp.get("/<report_id>/photo")
def photo(report_id):
    user_id, saved = owned_report(report_id)
    photo_id = saved["report"].get("photo_id")
    if not photo_id:
        abort(404)
    try:
        image = read_photo(user_id, photo_id)
    except (LookupError, FileNotFoundError):
        abort(404)
    return Response(image, mimetype="image/jpeg")
