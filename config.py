import os
from pathlib import Path

from dotenv import dotenv_values


def default_config():
    root = Path(__file__).resolve().parent
    local = dotenv_values(root / ".env")
    return {
        "SECRET_KEY": os.environ.get("SECRET_KEY") or local.get("SECRET_KEY"),
        "DATABASE": str(root / "instance" / "reports.sqlite3"),
        "UPLOAD_FOLDER": str(root / "instance" / "photos"),
        "MAX_CONTENT_LENGTH": 6 * 1024 * 1024,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": os.environ.get("APP_ENV", local.get("APP_ENV")) == "production",
        "SEND_MODE": "TEST",
    }
