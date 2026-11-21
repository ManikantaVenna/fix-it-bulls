"""Owner-scoped mailbox storage, email approval, and reply tracking."""

import hashlib
import json
from datetime import datetime, timezone
from email.errors import HeaderParseError, InvalidHeaderDefect
from email.headerregistry import Address
from email.utils import make_msgid
from urllib.parse import quote, urlencode
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from pydantic import TypeAdapter

from db import get_db, get_report
from schemas import IssueType, ReportInput, SendOutcome
from services import gmail_client
from services.storage import read_photo
from services.urgency import has_urgent_text

LOCKED_STATES = {"sending", "sent", "send_unknown", "reply_received"}
ISSUE_TYPE = TypeAdapter(IssueType)


class MailError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class UrgentReportError(MailError):
    def __init__(self):
        super().__init__("This report may be urgent. If anyone is in immediate danger, call 911.")


def _require_routine_report(report, subject, body):
    fields = report["report"]
    if report["flagged_urgent"] or has_urgent_text(
        fields["description"], *fields["clarification_answers"].values(), subject, body,
    ):
        raise UrgentReportError()


def _cipher():
    try:
        return Fernet(current_app.config.get("GMAIL_TOKEN_KEY") or b"")
    except (TypeError, ValueError) as error:
        raise MailError("Configure the Gmail token encryption key.", 503) from error


def _owner(user_id):
    owner = get_db().execute("SELECT google_sub,email FROM users WHERE id=?", (user_id,)).fetchone()
    if owner is None:
        raise MailError("Sign in to continue.", 401)
    return owner


def connect_mailbox(user_id, google_sub, refresh_token):
    if _owner(user_id)["google_sub"] != google_sub:
        raise MailError("Use the same Google account you signed in with.", 403)
    if not isinstance(refresh_token, str) or not refresh_token:
        raise MailError("Gmail did not grant ongoing access. Try connecting again.", 400)
    db = get_db()
    with db:
        db.execute(
            "INSERT INTO gmail_connections(user_id,encrypted_refresh_token) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET encrypted_refresh_token=excluded.encrypted_refresh_token",
            (user_id, _cipher().encrypt(refresh_token.encode())),
        )


def mailbox_connected(user_id):
    return get_db().execute("SELECT 1 FROM gmail_connections WHERE user_id=?", (user_id,)).fetchone() is not None


def disconnect_mailbox(user_id):
    db = get_db()
    with db:
        db.execute("DELETE FROM gmail_connections WHERE user_id=?", (user_id,))


def _refresh_token(user_id):
    row = get_db().execute(
        "SELECT encrypted_refresh_token FROM gmail_connections WHERE user_id=?", (user_id,),
    ).fetchone()
    if row is None:
        raise MailError("Connect Gmail first.")
    try:
        return _cipher().decrypt(row[0]).decode()
    except InvalidToken as error:
        raise MailError("Reconnect Gmail to restore access.") from error


def _resolve_route(report, issue_type):
    try:
        from services.routing import resolve_route
    except ModuleNotFoundError as error:
        if error.name != "services.routing":
            raise
        raise MailError("Verified routing is awaiting team integration.", 503) from error
    return resolve_route(ReportInput.model_validate(report), issue_type)


def _verified_route(saved, issue_type):
    analysis = saved["analysis"]
    if analysis is None:
        route = _resolve_route(saved["report"], issue_type)
    else:
        from services.routing import route_for_analysis

        if issue_type != analysis.suggested_issue_type:
            raise MailError("The issue category changed. Review the current report.")
        route = route_for_analysis(ReportInput.model_validate(saved["report"]), analysis)
    if route.state == "urgent_guidance":
        raise UrgentReportError()
    if route.state != "ready_email" or not route.route_id or not route.official_source:
        raise MailError("This report needs a verified email route before sending.")
    return route.model_dump(mode="json")


def _email_address(value, *, error_message="A single valid email recipient is required."):
    try:
        address = Address(addr_spec=value)
        if not address.username or not address.domain:
            raise ValueError
    except (ValueError, TypeError, HeaderParseError, InvalidHeaderDefect) as error:
        raise MailError(error_message, 503) from error
    return address


def _delivery_recipient(route):
    mode = current_app.config["SEND_MODE"]
    if mode == "TEST":
        return _email_address(
            current_app.config.get("TEST_RECIPIENT") or "",
            error_message="Configure a single controlled TEST_RECIPIENT address.",
        ).addr_spec
    if mode != "OFFICIAL":
        raise MailError("Email delivery is not configured.", 503)
    from services.routing import get_sendable_route

    destination = get_sendable_route(route["route_id"])
    if not destination:
        raise MailError("The verified email contact is no longer available.")
    address = _email_address(destination["recipient"])
    if address.domain.casefold() != "usf.edu":
        raise MailError("The verified email contact must use an official USF address.")
    return address.addr_spec


def _report(user_id, report_id):
    report = get_report(user_id, report_id)
    if report is None:
        raise MailError("Report not found.", 404)
    return report


def _photo(user_id, fields):
    photo_id = fields.get("photo_id")
    if not photo_id:
        return None
    try:
        return read_photo(user_id, photo_id)
    except (LookupError, OSError) as error:
        raise MailError("The report photo is unavailable. Check the attachment and review again.") from error


def _photo_hash(photo):
    return hashlib.sha256(photo).hexdigest() if photo is not None else None


def _email_draft_details(user_id, report_id, report_version, issue_type, subject, body):
    report = _report(user_id, report_id)
    if report["version"] != report_version or report["communication_status"] in LOCKED_STATES:
        raise MailError("The report changed or already has an outbound email.")
    _require_routine_report(report, subject, body)
    try:
        ISSUE_TYPE.validate_python(issue_type)
    except ValueError as error:
        raise MailError("Select a supported issue category.", 400) from error
    if not subject.strip() or len(subject) > 200 or "\n" in subject or "\r" in subject:
        raise MailError("Provide a single-line subject of at most 200 characters.", 400)
    if not body.strip() or len(body) > 4000:
        raise MailError("Provide a message of at most 4,000 characters.", 400)
    route = _verified_route(report, issue_type)
    return report, route


def prepare_external_draft(user_id, report_id, report_version, issue_type, subject, body):
    if current_app.config["SEND_MODE"] != "OFFICIAL":
        raise MailError("Opening email drafts requires official delivery mode.")
    _, route = _email_draft_details(user_id, report_id, report_version, issue_type, subject, body)
    recipient = _delivery_recipient(route)
    query = urlencode({"subject": subject.strip(), "body": body.strip()}, quote_via=quote)
    return {"recipient": recipient, "draft_url": f"mailto:{quote(recipient, safe='@')}?{query}"}


def prepare_email_review(user_id, report_id, report_version, issue_type, subject, body):
    report, route = _email_draft_details(user_id, report_id, report_version, issue_type, subject, body)
    _refresh_token(user_id)
    fields = report["report"]
    mode = current_app.config["SEND_MODE"]
    outgoing_subject = f"[DEMO] [{report_id[:8]}] {subject.strip()}" if mode == "TEST" else subject.strip()
    approval_id = uuid4().hex
    snapshot = {
        "route": route, "issue_type": issue_type, "sender": _owner(user_id)["email"],
        "recipient": _delivery_recipient(route), "subject": outgoing_subject, "mode": mode,
        "body": body.strip(), "photo_id": fields.get("photo_id"), "photo_hash": _photo_hash(_photo(user_id, fields)),
    }
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        latest = _report(user_id, report_id)
        if latest["version"] != report_version or latest["communication_status"] in LOCKED_STATES:
            raise MailError("The report changed while preparing the email. Review it again.")
        if latest["flagged_urgent"]:
            raise UrgentReportError()
        if latest["analysis"] != report["analysis"]:
            raise MailError("The analysis changed. Review the current report.")
        db.execute(
            "INSERT INTO gmail_reviews(report_id,id,version,snapshot_json) VALUES(?,?,?,?) "
            "ON CONFLICT(report_id) DO UPDATE SET id=excluded.id,version=excluded.version,snapshot_json=excluded.snapshot_json",
            (report_id, approval_id, report_version, json.dumps(snapshot)),
        )
    return {
        **{key: value for key, value in snapshot.items() if key != "photo_hash"},
        "approval_id": approval_id, "report_version": report_version,
    }


def _outcome(row):
    return SendOutcome(
        state=row["state"], report_id=row["report_id"], attempt_id=row["id"],
        provider_message_id=row["message_id"], provider_thread_id=row["thread_id"],
        sent_at=row["sent_at"], safe_error_code=row["error_code"],
    )


def send_approved_report(user_id, report_id, approved_version, *, approval_id):
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        report = _report(user_id, report_id)
        past = db.execute("SELECT * FROM gmail_attempts WHERE id=? AND report_id=?", (approval_id, report_id)).fetchone()
        if past is not None:
            return _outcome(past)
        review = db.execute("SELECT * FROM gmail_reviews WHERE report_id=? AND id=?", (report_id, approval_id)).fetchone()
        if review is None:
            raise MailError("Open the current email review before sending.")
        if review["version"] != approved_version or report["version"] != approved_version:
            raise MailError("The report changed. Review the current version before sending.")
        if report["communication_status"] in LOCKED_STATES:
            raise MailError("This report already has an outbound email attempt.")
        snapshot = json.loads(review["snapshot_json"])
        _require_routine_report(report, snapshot["subject"], snapshot["body"])
        photo = _photo(user_id, report["report"])
        route = _verified_route(report, snapshot["issue_type"])
        if (
            snapshot.get("mode", "TEST") != current_app.config["SEND_MODE"]
            or snapshot["route"] != route
            or snapshot["recipient"] != _delivery_recipient(route)
            or snapshot["sender"] != _owner(user_id)["email"]
            or snapshot["photo_hash"] != _photo_hash(photo)
        ):
            raise MailError("The route, sender, recipient, or photo changed. Review the email again.")
        token = _refresh_token(user_id)
        rfc_id = make_msgid(domain="fix-it-bulls.local")
        raw = gmail_client.build_message(
            snapshot["sender"], snapshot["recipient"], snapshot["subject"], snapshot["body"], photo, rfc_id,
        )
        db.execute(
            "INSERT INTO gmail_attempts(id,report_id,snapshot_json,state,rfc_message_id) VALUES(?,?,?,'sending',?)",
            (approval_id, report_id, review["snapshot_json"], rfc_id),
        )
        db.execute(
            "UPDATE reports SET communication_status='sending',route_id=?,updated_at=? WHERE id=?",
            (snapshot["route"]["route_id"], datetime.now(timezone.utc).isoformat(), report_id),
        )

    sent = {}
    try:
        sent = gmail_client.send_message(token, raw)
        state, error = "sent", None
    except gmail_client.DeliveryUncertain:
        state, error = "send_unknown", "provider_outcome_unknown"
    except gmail_client.ProviderError:
        state, error = "send_failed", "provider_rejected"
    updated_at = datetime.now(timezone.utc).isoformat()
    sent_at = updated_at if state == "sent" else None
    with db:
        db.execute(
            "UPDATE gmail_attempts SET state=?,message_id=?,thread_id=?,sent_at=?,error_code=? WHERE id=?",
            (state, sent.get("id"), sent.get("threadId"), sent_at, error, approval_id),
        )
        db.execute(
            "UPDATE reports SET communication_status=?,updated_at=? WHERE id=?", (state, updated_at, report_id),
        )
    return _outcome(db.execute("SELECT * FROM gmail_attempts WHERE id=?", (approval_id,)).fetchone())


def check_report_replies(user_id, report_id):
    report = _report(user_id, report_id)
    if report["communication_status"] not in {"sent", "reply_received"}:
        raise MailError("Reply checks require a sent Gmail report.")
    db = get_db()
    attempt = db.execute(
        "SELECT id,message_id,thread_id FROM gmail_attempts WHERE report_id=? AND state='sent'", (report_id,),
    ).fetchone()
    if attempt is None:
        raise MailError("No Gmail conversation is recorded for this report.")
    try:
        thread = gmail_client.get_thread(_refresh_token(user_id), attempt["thread_id"])
        events = gmail_client.incoming_events(thread, attempt["message_id"])
    except gmail_client.ProviderError as error:
        raise MailError("Gmail could not check this conversation. Try again or reconnect Gmail.", 502) from error
    if events:
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO gmail_reply_refs(attempt_id,message_id,received_at) VALUES(?,?,?)",
                [(attempt["id"], event.provider_message_id, event.received_at.isoformat()) for event in events],
            )
            db.execute(
                "UPDATE reports SET communication_status='reply_received',updated_at=? "
                "WHERE id=? AND communication_status='sent'",
                (datetime.now(timezone.utc).isoformat(), report_id),
            )
    return events
