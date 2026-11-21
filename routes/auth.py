"""Google identity login. Mailbox consent is a separate operation."""

import hashlib
import re
import secrets
import time

from flask import Blueprint, current_app, make_response, redirect, render_template, request, session, url_for
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests import RequestException

from db import consume_google_sign_in, store_google_sign_in, upsert_user

bp = Blueprint("auth", __name__)
IDENTITY_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email")
SIGN_IN_TTL = 600


def oauth_problem(title, message, status, *, retry_endpoint="auth.start", retry_label="Try Google sign-in again"):
    return make_response(render_template(
        "oauth_problem.html",
        title=title,
        message=message,
        retry_url=url_for(retry_endpoint),
        retry_label=retry_label,
    ), status)


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
        return oauth_problem(
            "Google sign-in is unavailable",
            "Ask the project owner to check the test site's Google sign-in setup.",
            503,
        )
    nonce = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    browser_key = secrets.token_urlsafe(32)
    now = int(time.time())
    flow = _flow()
    location, _ = flow.authorization_url(state=state, nonce=nonce, access_type="online", prompt="select_account")
    store_google_sign_in(
        state, hashlib.sha256(browser_key.encode()).hexdigest(), nonce, flow.code_verifier,
        now=now, expires_at=now + SIGN_IN_TTL,
    )
    response = redirect(location)
    response.set_cookie(
        f"fib_sign_in_{state}", browser_key, max_age=SIGN_IN_TTL, httponly=True, samesite="Lax",
        secure=config["SESSION_COOKIE_SECURE"], path=url_for("auth.callback"),
    )
    return response


@bp.get("/auth/google/callback")
def callback():
    incoming_state = request.args.get("state", "")
    cookie_name = f"fib_sign_in_{incoming_state}" if re.fullmatch(r"[A-Za-z0-9_-]{43}", incoming_state) else None
    browser_key = request.cookies.get(cookie_name) if cookie_name else None
    pending = consume_google_sign_in(
        incoming_state, hashlib.sha256(browser_key.encode()).hexdigest(), now=int(time.time()),
    ) if browser_key else None
    if pending is None:
        response = oauth_problem(
            "This sign-in link has expired",
            "Start over below and finish Google sign-in in the browser you started in.",
            400,
        )
    else:
        response = _finish_sign_in(pending)
    if browser_key:
        response.delete_cookie(
            cookie_name, path=url_for("auth.callback"), httponly=True, samesite="Lax",
            secure=current_app.config["SESSION_COOKIE_SECURE"],
        )
    return response


def _finish_sign_in(pending):
    if request.args.get("error") or not request.args.get("code"):
        return oauth_problem("Sign-in was interrupted", "Try again when you’re ready to choose your Google account.", 400)
    try:
        claims = _exchange_identity(request.args["code"], pending)
    except (GoogleAuthError, OAuth2Error, RequestException, KeyError, ValueError, Warning):
        return oauth_problem("Google sign-in failed", "We couldn’t verify your sign-in with Google. Try again below.", 400)

    nonce = claims.get("nonce")
    email = claims.get("email")
    subject = claims.get("sub")
    if (
        not isinstance(nonce, str)
        or not secrets.compare_digest(pending["nonce"].encode(), nonce.encode())
        or claims.get("email_verified") is not True
        or not isinstance(email, str)
        or not isinstance(subject, str)
        or not subject
    ):
        return oauth_problem("Google account could not be verified", "Try again with a verified Google account.", 403)
    if email.strip().casefold() not in current_app.config["ALLOWED_GOOGLE_EMAILS"]:
        return oauth_problem(
            "This account needs test access",
            "Choose an invited Google account or ask the project owner to add yours.",
            403,
        )

    user_id = upsert_user(subject, email.strip())
    session.clear()
    session["user_id"] = user_id
    return redirect(url_for("index"), code=303)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"), code=303)
