import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from flask import current_app, g

from schemas import AnalysisOutput, ReportInput


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
        CREATE TABLE IF NOT EXISTS google_sign_in_attempts (
            state TEXT PRIMARY KEY,
            browser_digest TEXT NOT NULL,
            nonce TEXT NOT NULL,
            code_verifier TEXT NOT NULL,
            expires_at INTEGER NOT NULL
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
        CREATE TABLE IF NOT EXISTS urgent_report_versions (
            report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            PRIMARY KEY (report_id, version)
        );
        CREATE TABLE IF NOT EXISTS report_analysis (
            report_id TEXT PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            analysis_json TEXT NOT NULL
        );
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


def store_google_sign_in(state, browser_digest, nonce, code_verifier, *, now, expires_at):
    db = get_db()
    with db:
        db.execute("DELETE FROM google_sign_in_attempts WHERE expires_at <= ?", (now,))
        db.execute(
            "INSERT INTO google_sign_in_attempts(state,browser_digest,nonce,code_verifier,expires_at) "
            "VALUES(?,?,?,?,?)",
            (state, browser_digest, nonce, code_verifier, expires_at),
        )


def consume_google_sign_in(state, browser_digest, *, now):
    db = get_db()
    with db:
        return db.execute(
            "DELETE FROM google_sign_in_attempts WHERE state=? AND browser_digest=? AND expires_at>? "
            "RETURNING state, nonce, code_verifier",
            (state, browser_digest, now),
        ).fetchone()


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
    row = get_db().execute(
        "SELECT reports.*, report_analysis.analysis_json, EXISTS (SELECT 1 FROM urgent_report_versions "
        "WHERE urgent_report_versions.report_id=reports.id AND urgent_report_versions.version=reports.version) AS flagged_urgent "
        "FROM reports LEFT JOIN report_analysis ON report_analysis.report_id=reports.id "
        "AND report_analysis.version=reports.version WHERE reports.id=? AND reports.user_id=?",
        (report_id, user_id),
    ).fetchone()
    if row is None:
        return None
    report = dict(row)
    report["report"] = json.loads(report.pop("report_json"))
    analysis_json = report.pop("analysis_json")
    report["analysis"] = AnalysisOutput.model_validate_json(analysis_json) if analysis_json else None
    report["flagged_urgent"] = bool(report["flagged_urgent"])
    return report


def record_urgent_analysis(user_id: str, report_id: str, version: int, analysis: AnalysisOutput) -> bool:
    """Record a positive finding from the server's analysis of this report version."""
    if not analysis.possible_urgent_concern:
        return False
    db = get_db()
    with db:
        result = db.execute(
            "INSERT OR IGNORE INTO urgent_report_versions(report_id,version) "
            "SELECT id,version FROM reports WHERE id=? AND user_id=? AND version=?",
            (report_id, user_id, version),
        )
    return result.rowcount > 0


def save_report_analysis(user_id: str, report_id: str, version: int, analysis: AnalysisOutput) -> bool:
    db = get_db()
    with db:
        result = db.execute(
            "INSERT INTO report_analysis(report_id,version,analysis_json) "
            "SELECT id,version,? FROM reports WHERE id=? AND user_id=? AND version=? "
            "AND communication_status IN ('prepared','send_failed') "
            "ON CONFLICT(report_id) DO UPDATE SET version=excluded.version,analysis_json=excluded.analysis_json",
            (analysis.model_dump_json(), report_id, user_id, version),
        )
    return result.rowcount > 0


def update_report(user_id, report_id, changes, *, expected_version=None):
    if not isinstance(changes, dict) or set(changes) - set(ReportInput.model_fields):
        raise ValueError("Only report input fields can be edited")
    db = get_db()
    # Reserve the edit before reading to avoid racing with another editor.
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = get_report(user_id, report_id)
        if existing is None:
            raise LookupError("Report not found")
        if expected_version is not None and existing["version"] != expected_version:
            raise ReportLockedError("The report changed. Reload the latest version.")
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
