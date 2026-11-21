"""Google identity login. Mailbox consent is a separate operation."""

import secrets
import time

from flask import Blueprint, current_app, jsonify, redirect, request, session, url_for
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests import RequestException

from db import upsert_user

bp = Blueprint("auth", __name__)
IDENTITY_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email")


def _flow(*, state=None, code_verifier=None, scopes=IDENTITY_SCOPES, redirect_key="GOOGLE_REDIRECT_URI"):
    config = current_app.config
    return Flow.from_client_config(
        {"web": {
            "client_id": config["GOOGLE_CLIENT_ID"],
            "client_secret": config["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }},
        scopes=scopes,
        redirect_uri=config[redirect_key],
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=code_verifier is None,
    )


def _exchange_identity(code, pending):
    flow = _flow(state=pending["state"], code_verifier=pending["code_verifier"])
    response = flow.fetch_token(code=code, timeout=15)
    return id_token.verify_oauth2_token(
        response["id_token"], Request(), audience=current_app.config["GOOGLE_CLIENT_ID"],
    )


@bp.get("/auth/google/start")
def start():
    config = current_app.config
    if not all(config.get(key) for key in (
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "ALLOWED_GOOGLE_EMAILS",
    )):
        return jsonify(error="Google sign-in needs a client and authorized test accounts."), 503
    nonce = secrets.token_urlsafe(32)
    flow = _flow()
    location, state = flow.authorization_url(nonce=nonce, access_type="online", prompt="select_account")
    session["oauth_login"] = {
        "state": state, "nonce": nonce, "code_verifier": flow.code_verifier, "started_at": int(time.time()),
    }
    return redirect(location)


@bp.get("/auth/google/callback")
def callback():
    pending = session.get("oauth_login")
    incoming_state = request.args.get("state", "")
    if not pending or not secrets.compare_digest(pending["state"].encode(), incoming_state.encode()):
        return jsonify(error="Sign-in response does not match your session. Start sign-in again."), 400
    session.pop("oauth_login")
    if time.time() - pending["started_at"] > 600 or request.args.get("error") or not request.args.get("code"):
        return jsonify(error="Sign-in did not finish. Start sign-in again."), 400
    try:
        claims = _exchange_identity(request.args["code"], pending)
    except (GoogleAuthError, OAuth2Error, RequestException, KeyError, ValueError, Warning):
        return jsonify(error="Google sign-in could not be verified. Start sign-in again."), 400

    nonce = claims.get("nonce")
    email = claims.get("email")
    subject = claims.get("sub")
    if (
        not isinstance(nonce, str)
        or not secrets.compare_digest(pending["nonce"].encode(), nonce.encode())
        or claims.get("email_verified") is not True
        or not isinstance(email, str)
        or email.strip().casefold() not in current_app.config["ALLOWED_GOOGLE_EMAILS"]
        or not isinstance(subject, str)
        or not subject
    ):
        return jsonify(error="This Google account could not be verified for the prototype."), 403

    user_id = upsert_user(subject, email.strip())
    session.clear()
    session["user_id"] = user_id
    return redirect(url_for("index"), code=303)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"), code=303)
