from typing import get_args

from flask import Blueprint, Response, abort, flash, get_flashed_messages, redirect, render_template, request, session, url_for
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from db import create_report, get_db, get_report, record_urgent_analysis, save_report_analysis, update_report
from schemas import AnalysisOutput, IssueType, ReportInput
from services.ai import AIUnavailableError
from services.mail import LOCKED_STATES, mailbox_connected
from services.research import display_sources, research_channel
from services.routing import get_route, resolve_route, route_for_analysis, triage
from services.storage import read_photo, save_photo
from services.urgency import has_urgent_text


bp = Blueprint("reports", __name__, url_prefix="/reports")

FORM_FIELDS = (
    "description", "site_type", "building_or_area", "room_floor_landmark",
    "asset_tag", "ownership",
)
ISSUE_LABELS = {
    "plumbing": "Plumbing", "lighting": "Lighting", "physical_damage": "Building or fixture damage",
    "cleanliness": "Cleanliness", "computer_av": "Computer or classroom technology",
    "network_phone": "Network or phone", "access_reader": "Card reader", "parking_transport": "Parking or transit",
    "recreation_equipment": "Recreation equipment", "physical_id_card": "USF ID card",
    "access_barrier": "Physical access barrier", "dining_feedback": "Dining", "library_service": "Library service",
    "other": "Other",
}
COMMUNICATION_LABELS = {
    "prepared": "Draft", "sending": "Sending", "sent": "Email sent",
    "reply_received": "Reply received", "send_failed": "Send failed", "send_unknown": "Delivery unconfirmed",
}


def signed_in_user():
    # Shared authentication is expected to put the database user ID here.
    user_id = session.get("user_id")
    if not isinstance(user_id, str):
        return None
    user = get_db().execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_id if user else None


def report_rows(user_id, limit=-1):
    return get_db().execute(
        """SELECT r.id, COALESCE(NULLIF(json_extract(a.analysis_json, '$.proposed_title'), ''),
                   substr(json_extract(r.report_json, '$.description'), 1, 95)) AS title,
                   json_extract(r.report_json, '$.building_or_area') AS location,
                   r.communication_status, r.created_at
           FROM reports AS r
           LEFT JOIN report_analysis AS a ON a.report_id=r.id AND a.version=r.version
           WHERE r.user_id=? ORDER BY r.created_at DESC, r.id DESC LIMIT ?""", (user_id, limit),
    ).fetchall()


@bp.get("/home")
def dashboard():
    user_id = signed_in_user()
    return render_template(
        "reports/dashboard.html", signed_in=bool(user_id),
        reports=report_rows(user_id, 3) if user_id else [],
        connected=mailbox_connected(user_id) if user_id else False, statuses=COMMUNICATION_LABELS,
    )


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
    if request.form.get("analyze_now") and not has_urgent_text(report.description):
        saved = get_report(user_id, report_id)
        try:
            save_analysis(user_id, saved, run_analysis(user_id, report))
        except (AIUnavailableError, LookupError, OSError):
            flash("Analysis is unavailable. Your report is saved. Choose a category to continue.", "analysis")
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
    user_id, saved = owned_report(report_id)
    if saved["communication_status"] in {"sent", "reply_received"}:
        return tracking_page(user_id, saved)
    messages = get_flashed_messages(category_filter=["analysis"])
    return review_page(user_id, saved, error=messages[0] if messages else None)


def tracking_page(user_id, saved):
    report = ReportInput.model_validate(saved["report"])
    attempt = get_db().execute(
        "SELECT id, sent_at FROM gmail_attempts WHERE report_id=? AND state='sent' ORDER BY sent_at DESC LIMIT 1",
        (saved["id"],),
    ).fetchone()
    replies = get_db().execute(
        "SELECT received_at FROM gmail_reply_refs WHERE attempt_id=? ORDER BY received_at, message_id",
        (attempt["id"],),
    ).fetchall() if attempt else []
    analysis = saved["analysis"]
    return render_template(
        "reports/tracking.html", report=report, report_id=saved["id"],
        title=analysis.proposed_title if analysis and analysis.proposed_title else f"Campus issue at {report.building_or_area}",
        status=COMMUNICATION_LABELS[saved["communication_status"]],
        sent_at=attempt["sent_at"] if attempt else None, replies=replies, connected=mailbox_connected(user_id),
    )


def report_route(saved, report):
    analysis = saved["analysis"]
    if saved["flagged_urgent"] or has_urgent_text(report.description, *report.clarification_answers.values()):
        return resolve_route(report, "other", urgent=True)
    return route_for_analysis(report, analysis) if analysis else None


def review_page(user_id, saved, *, error=None, research=None, status=200):
    report = ReportInput.model_validate(saved["report"])
    analysis = saved["analysis"]
    route = report_route(saved, report)
    destination = get_route(route.route_id) if route and route.route_id else None
    lines = [
        "Campus: USF Tampa",
        f"Building or area: {report.building_or_area}",
    ]
    if report.room_floor_landmark:
        lines.append(f"Specific location: {report.room_floor_landmark}")
    if report.asset_tag:
        lines.append(f"Asset tag: {report.asset_tag}")
    lines.extend(("", analysis.proposed_body if analysis and analysis.proposed_body else report.description))
    return render_template(
        "reports/review.html", report=report, saved=saved, report_id=saved["id"],
        subject=(analysis.proposed_title if analysis and analysis.proposed_title else
                 f"Campus issue at {report.building_or_area}")[:200],
        body="\n".join(lines), analysis=analysis, route=route, destination=destination,
        connected=mailbox_connected(user_id), locked=saved["communication_status"] in LOCKED_STATES,
        issue_labels=ISSUE_LABELS, error=error, research=research,
        research_sources=display_sources(research) if research else None,
    ), status


def editable_report(report_id):
    user_id, saved = owned_report(report_id)
    if request.form.get("report_version", type=int) != saved["version"]:
        abort(409, "The report changed. Open the latest review.")
    if saved["communication_status"] in LOCKED_STATES:
        abort(409, "This report has an outbound email attempt.")
    return user_id, saved


def run_analysis(user_id, report):
    photo_bytes = read_photo(user_id, report.photo_id) if report.photo_id else None
    analysis, _ = triage(report, photo_bytes)
    return analysis


def save_analysis(user_id, saved, analysis):
    report_id, version = saved["id"], saved["version"]
    record_urgent_analysis(user_id, report_id, version, analysis)
    if not save_report_analysis(user_id, report_id, version, analysis):
        abort(409, "The report changed while it was being analyzed.")


@bp.post("/<report_id>/analyze")
def analyze(report_id):
    user_id, saved = editable_report(report_id)
    report = ReportInput.model_validate(saved["report"])
    classified = request.form.get("issue_type")
    previous = saved["analysis"]
    prompted = previous.questions if previous and not report.clarification_answers else []
    if prompted and request.form.get("answer_0") is not None:
        answers = {question: request.form.get(f"answer_{index}", "").strip()
                   for index, question in enumerate(prompted)}
        if any(not answer or len(answer) > 1000 for answer in answers.values()):
            return review_page(user_id, saved, error="Answer each clarification question.", status=422)
        try:
            saved = update_report(user_id, report_id, {"clarification_answers": answers}, expected_version=saved["version"])
        except ValueError:
            abort(409, "The report changed. Open the latest review.")
        report = ReportInput.model_validate(saved["report"])

    if saved["flagged_urgent"] or has_urgent_text(report.description, *report.clarification_answers.values()):
        return redirect(url_for("reports.review", report_id=report_id), code=303)
    if classified:
        if classified not in get_args(IssueType):
            abort(400)
        analysis = AnalysisOutput(suggested_issue_type=classified)
    else:
        try:
            analysis = run_analysis(user_id, report)
        except (AIUnavailableError, LookupError, OSError):
            return review_page(user_id, saved, error="Analysis is unavailable. You can choose a category below.", status=503)
    save_analysis(user_id, saved, analysis)
    return redirect(url_for("reports.review", report_id=report_id), code=303)


@bp.post("/<report_id>/edit-location")
def edit_location(report_id):
    user_id, saved = editable_report(report_id)
    changes = {key: request.form.get(key, "").strip() for key in ("site_type", "room_floor_landmark")}
    try:
        update_report(user_id, report_id, changes, expected_version=saved["version"])
    except ValidationError:
        return review_page(user_id, saved, error="Check the location details.", status=422)
    except ValueError:
        abort(409, "The report changed. Open the latest review.")
    return redirect(url_for("reports.review", report_id=report_id), code=303)


@bp.post("/<report_id>/research")
def research(report_id):
    user_id, saved = editable_report(report_id)
    report = ReportInput.model_validate(saved["report"])
    route = report_route(saved, report)
    if route is None or route.state != "research_available":
        abort(409, "Research is available for reports without a verified route.")
    result = research_channel(report, issue_type=saved["analysis"].suggested_issue_type)
    latest = get_report(user_id, report_id)
    if (latest["version"] != saved["version"] or latest["flagged_urgent"] or
            latest["analysis"] != saved["analysis"] or latest["communication_status"] in LOCKED_STATES):
        abort(409, "The report changed during research.")
    return review_page(user_id, latest, research=result)


@bp.get("")
def history():
    user_id = signed_in_user()
    if user_id is None:
        return redirect(url_for("auth.start"))
    return render_template("reports/history.html", reports=report_rows(user_id), statuses=COMMUNICATION_LABELS)


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
