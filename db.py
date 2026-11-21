import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from flask import current_app, g

from schemas import ReportInput


class ReportLockedError(ValueError):
    """An outbound/sent version cannot be silently rewritten."""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=5)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    get_db().executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            google_sub TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS photos (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            storage_name TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            version INTEGER NOT NULL DEFAULT 1,
            report_json TEXT NOT NULL,
            communication_status TEXT NOT NULL DEFAULT 'prepared',
            route_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reports_owner ON reports(user_id);
        CREATE TABLE IF NOT EXISTS gmail_connections (
            user_id TEXT PRIMARY KEY REFERENCES users(id),
            encrypted_refresh_token BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gmail_reviews (
            report_id TEXT PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
            id TEXT NOT NULL UNIQUE,
            version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gmail_attempts (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            snapshot_json TEXT NOT NULL,
            state TEXT NOT NULL,
            message_id TEXT,
            thread_id TEXT,
            sent_at TEXT,
            error_code TEXT,
            rfc_message_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gmail_reply_refs (
            attempt_id TEXT NOT NULL REFERENCES gmail_attempts(id) ON DELETE CASCADE,
            message_id TEXT NOT NULL,
            received_at TEXT NOT NULL,
            PRIMARY KEY (attempt_id, message_id)
        );
    """)


def upsert_user(google_sub, email):
    # Auth owner must validate the Google ID token before calling this helper.
    if not google_sub or not email:
        raise ValueError("Validated Google subject and email are required")
    db = get_db()
    with db:
        row = db.execute(
            "INSERT INTO users(id,google_sub,email) VALUES(?,?,?) "
            "ON CONFLICT(google_sub) DO UPDATE SET email=excluded.email RETURNING id",
            (uuid4().hex, google_sub, email),
        ).fetchone()
    return row["id"]


def _check_photo_owner(user_id, report):
    if report.photo_id and get_db().execute(
        "SELECT id FROM photos WHERE id=? AND user_id=?", (report.photo_id, user_id)
    ).fetchone() is None:
        raise ValueError("The photo is unavailable to this report owner")


def create_report(user_id, report):
    report = ReportInput.model_validate(report)
    _check_photo_owner(user_id, report)
    report_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO reports(id,user_id,report_json,created_at,updated_at) VALUES(?,?,?,?,?)",
        (report_id, user_id, report.model_dump_json(), now, now),
    )
    db.commit()
    return report_id


def get_report(user_id, report_id):
    row = get_db().execute("SELECT * FROM reports WHERE id=? AND user_id=?", (report_id, user_id)).fetchone()
    if row is None:
        return None
    report = dict(row)
    report["report"] = json.loads(report.pop("report_json"))
    return report


def update_report(user_id, report_id, changes):
    if not isinstance(changes, dict) or set(changes) - set(ReportInput.model_fields):
        raise ValueError("Only report input fields can be edited")
    db = get_db()
    # Reserve the edit before reading to avoid racing with another editor.
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = get_report(user_id, report_id)
        if existing is None:
            raise LookupError("Report not found")
        if existing["communication_status"] in {"sending", "sent", "send_unknown", "reply_received"}:
            raise ReportLockedError("Preserve outbound evidence; create an explicit correction instead")
        report = ReportInput.model_validate({**existing["report"], **changes})
        _check_photo_owner(user_id, report)
        db.execute(
            "UPDATE reports SET report_json=?,version=version+1,route_id=NULL,"
            "communication_status='prepared',updated_at=? WHERE id=? AND user_id=?",
            (report.model_dump_json(), datetime.now(timezone.utc).isoformat(), report_id, user_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_report(user_id, report_id)
