# Fix It Bulls

USF Tampa campus issue reporting with Gemini-assisted interpretation, hybrid routing, and Gmail conversation tracking.

## Current status

This repository currently contains the **shared foundation**, not the finished application:

- Flask app factory, health endpoint and CSRF support.
- Shared typed contracts in `schemas.py`.
- SQLite helpers with report ownership, edit versioning and outbound-evidence protection.
- Private validated photo storage.
- Foundation tests.

Google login, Gmail sending/replies, Gemini analysis/research, department routing and the student interface are **not connected yet**. `/health` explicitly reports the foundation stage. No test complaints should be sent to USF offices.

## First-time setup

Use Python 3.11 or newer and Git. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/init_local.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m flask --app app:create_app run
```

Open http://localhost:5000/health. The root URL currently returns foundation information; Sasi's interface will be integrated separately.

On macOS/Linux use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.

`tools/init_local.py` creates a private `.env` with a random session secret and preserves an existing `.env`. Never commit that file. API credentials are not required for the foundation tests or health endpoint. Integration owners will configure them privately later.

SQLite and photos are created under ignored `instance/`. Each developer uses separate runtime data. Git transfers source code, not the team's live database. The integrated demo will run on one primary instance.

## Work ownership

| Person | Initial branch | Owns |
|---|---|---|
| Sasi (`Sasi534211`) | `feature/report-ui` | `templates/`, `static/`, `routes/reports.py`, report-flow tests |
| Hanif (`hanifaslam123`) | `feature/routing-research` | `services/ai.py`, `services/routing.py`, `services/research.py`, routing directory and their tests |
| Manikanta (`ManikantaVenna`) | `feature/gmail-tracking` | Foundation, shared schemas, database, Google/Gmail authorization, send/reply services and integration |

Read [CONTRIBUTING.md](CONTRIBUTING.md) before editing. Agree changes to `schemas.py`, `db.py`, `app.py` and `requirements.txt` with Manikanta so callers do not diverge.

## Product rules

- Known issues use active source-backed USF routes. Unfamiliar issues may produce cited research suggestions and official-channel handoff.
- Research cannot silently add a Gmail recipient or restore a stale route.
- A student reviews each exact email and attachment before sending.
- Sent, replied and repaired are different states. Never claim a repair from a courtesy response.
- Gmail access and report/photo records are private to their owner.
- Keep research separate from private Gmail/image-analysis context.

The shared [project blueprint](docs/project-blueprint.pdf) describes the intended completed workflow. Some optional features are explicitly deferred.

## Planned feature-module convention

Feature web modules export `bp`, a Flask `Blueprint`:

```python
from flask import Blueprint

bp = Blueprint("reports", __name__)
```

The factory automatically registers `routes.auth`, `routes.reports`, and `routes.gmail` when their modules exist. Use unique endpoint names. Coordinate root URL changes during integration; `/health` stays available.

`GET /api/csrf` returns a CSRF token. Fetch-based writes send it as the `X-CSRFToken` header with the same browser session. Jinja forms can use `csrf_token()`. All write routes need CSRF and authenticated ownership checks when added. Do not add a fake public login to unblock UI development.

Run `python -m pytest -q` through your environment before opening a PR. If an external API is unavailable, report that honestly rather than substituting a fake live result.
