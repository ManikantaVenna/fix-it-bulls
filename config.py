import os
from pathlib import Path

from dotenv import dotenv_values


def default_config():
    root = Path(__file__).resolve().parent
    local = dotenv_values(root / ".env")
    allowed_google_emails = os.environ.get("ALLOWED_GOOGLE_EMAILS", local.get("ALLOWED_GOOGLE_EMAILS") or "")
    return {
        "SECRET_KEY": os.environ.get("SECRET_KEY") or local.get("SECRET_KEY"),
        "DATABASE": str(root / "instance" / "reports.sqlite3"),
        "UPLOAD_FOLDER": str(root / "instance" / "photos"),
        "MAX_CONTENT_LENGTH": 6 * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.environ.get("APP_ENV", local.get("APP_ENV")) == "production",
        "SEND_MODE": os.environ.get("SEND_MODE", local.get("SEND_MODE") or "TEST").upper(),
        "PUBLIC_GOOGLE_SIGN_IN": os.environ.get(
            "PUBLIC_GOOGLE_SIGN_IN", local.get("PUBLIC_GOOGLE_SIGN_IN") or ""
        ).lower() == "true",
        "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID") or local.get("GOOGLE_CLIENT_ID"),
        "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET") or local.get("GOOGLE_CLIENT_SECRET"),
        "GMAIL_REDIRECT_URI": (
            os.environ.get("GMAIL_REDIRECT_URI") or local.get("GMAIL_REDIRECT_URI")
            or "http://localhost:5000/gmail/callback"
        ),
        "GMAIL_TOKEN_KEY": os.environ.get("GMAIL_TOKEN_KEY") or local.get("GMAIL_TOKEN_KEY"),
        "TEST_RECIPIENT": os.environ.get("TEST_RECIPIENT") or local.get("TEST_RECIPIENT"),
        "GOOGLE_REDIRECT_URI": (
            os.environ.get("GOOGLE_REDIRECT_URI") or local.get("GOOGLE_REDIRECT_URI")
            or "http://localhost:5000/auth/google/callback"
        ),
        "ALLOWED_GOOGLE_EMAILS": {
            email.strip().casefold() for email in allowed_google_emails.split(",") if email.strip()
        },
    }
