"""Mailbox consent and user-approved report mail endpoints."""

import secrets
import time
from uuid import UUID

from flask import Blueprint, current_app, jsonify, redirect, request, session, url_for
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from oauthlib.oauth2 import OAuth2Error
from pydantic import Field, ValidationError
from requests import RequestException

from routes.auth import IDENTITY_SCOPES, _flow
from schemas import Contract, IssueType
from services import mail
from services.gmail_client import GMAIL_SCOPES

bp = Blueprint("gmail", __name__)


class EmailReviewInput(Contract):
    report_version: int = Field(ge=1, strict=True)
    issue_type: IssueType
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class EmailApproval(Contract):
    approved_version: int = Field(ge=1, strict=True)
    approval_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def _user_id():
    user_id = session.get("user_id")
    if not user_id:
        raise mail.MailError("Sign in to continue.", 401)
    return user_id


def _consent_flow(**kwargs):
    return _flow(scopes=IDENTITY_SCOPES + GMAIL_SCOPES, redirect_key="GMAIL_REDIRECT_URI", **kwargs)


def exchange_consent(code, pending):
    flow = _consent_flow(state=pending["state"], code_verifier=pending["code_verifier"])
    response = flow.fetch_token(code=code, timeout=15)
    granted = response.get("scope", " ".join(IDENTITY_SCOPES + GMAIL_SCOPES))
    if isinstance(granted, str):
        granted = granted.split()
    if not set(GMAIL_SCOPES) <= set(granted):
        raise mail.MailError("Grant both Gmail permissions to connect the mailbox.", 403)
    claims = id_token.verify_oauth2_token(response["id_token"], Request(), audience=current_app.config["GOOGLE_CLIENT_ID"])
    nonce = claims.get("nonce")
    if (
        not isinstance(nonce, str) or not secrets.compare_digest(pending["nonce"].encode(), nonce.encode())
        or claims.get("email_verified") is not True
    ):
        raise mail.MailError("The Gmail account could not be verified.", 403)
    return claims["sub"], response.get("refresh_token")


@bp.errorhandler(mail.MailError)
def _mail_error(error):
    return jsonify(error=str(error)), error.status


@bp.errorhandler(mail.UrgentReportError)
def _urgent_report(error):
    return jsonify(
        error=str(error),
        code="urgent_guidance",
        guidance_url="https://www.usf.edu/facilities/service-center/work-requests.aspx",
    ), error.status


@bp.errorhandler(ValidationError)
def _invalid_data(_error):
    return jsonify(error="Check the requested report version, category, and email fields."), 400


@bp.post("/gmail/connect")
def connect():
    user_id = _user_id()
    if not all(current_app.config.get(key) for key in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GMAIL_REDIRECT_URI", "GMAIL_TOKEN_KEY",
    )):
        raise mail.MailError("Configure Gmail OAuth and token encryption before connecting.", 503)
    nonce = secrets.token_urlsafe(32)
    flow = _consent_flow()
    location, state = flow.authorization_url(nonce=nonce, access_type="offline", prompt="consent select_account")
    session["gmail_consent"] = {
        "user_id": user_id, "state": state, "nonce": nonce, "code_verifier": flow.code_verifier,
        "started_at": int(time.time()),
    }
    report_id = request.form.get("report_id")
    if report_id and "reports.review" in current_app.view_functions:
        try:
            session["gmail_consent"]["report_id"] = UUID(report_id).hex
        except ValueError:
            pass
    elif request.form.get("return_to") == "account" and "reports.account" in current_app.view_functions:
        session["gmail_consent"]["return_to_account"] = True
    return redirect(location, code=303)


@bp.get("/gmail/callback")
def callback():
    user_id = _user_id()
    pending = session.get("gmail_consent")
    state = request.args.get("state", "")
    if not pending or pending["user_id"] != user_id or not secrets.compare_digest(pending["state"].encode(), state.encode()):
        raise mail.MailError("Gmail consent does not match your sign-in session. Connect again.", 400)
    session.pop("gmail_consent")
    if time.time() - pending["started_at"] > 600 or request.args.get("error") or not request.args.get("code"):
        raise mail.MailError("Gmail connection did not finish. Connect again.", 400)
    try:
        subject, token = exchange_consent(request.args["code"], pending)
    except mail.MailError:
        raise
    except (GoogleAuthError, OAuth2Error, RequestException, KeyError, ValueError, Warning) as error:
        raise mail.MailError("Gmail could not be connected with both permissions. Connect again.", 400) from error
    mail.connect_mailbox(user_id, subject, token)
    if pending.get("report_id") and "reports.review" in current_app.view_functions:
        return redirect(url_for("reports.review", report_id=pending["report_id"]), code=303)
    if pending.get("return_to_account") and "reports.account" in current_app.view_functions:
        return redirect(url_for("reports.account"), code=303)
    return redirect(url_for("index"), code=303)


@bp.get("/gmail/status")
def status():
    return jsonify(connected=mail.mailbox_connected(_user_id()))


@bp.post("/gmail/disconnect")
def disconnect():
    mail.disconnect_mailbox(_user_id())
    session.pop("gmail_consent", None)
    if request.form.get("return_to") == "account" and "reports.account" in current_app.view_functions:
        return redirect(url_for("reports.account"), code=303)
    return jsonify(connected=False)


@bp.post("/reports/<report_id>/email-review")
def email_review(report_id):
    user_id = _user_id()
    data = EmailReviewInput.model_validate(request.get_json(silent=True))
    return jsonify(mail.prepare_email_review(user_id, report_id, **data.model_dump()))


@bp.post("/reports/<report_id>/approve-send")
def approve_send(report_id):
    user_id = _user_id()
    data = EmailApproval.model_validate(request.get_json(silent=True))
    return jsonify(mail.send_approved_report(user_id, report_id, **data.model_dump()).model_dump(mode="json"))


@bp.post("/reports/<report_id>/check-replies")
def check_replies(report_id):
    events = mail.check_report_replies(_user_id(), report_id)
    return jsonify(replies=[event.model_dump(mode="json") for event in events])
