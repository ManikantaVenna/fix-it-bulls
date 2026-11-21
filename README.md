# Fix It Bulls

USF Tampa campus issue reporting with Gemini-assisted interpretation, hybrid routing, and Gmail conversation tracking.

## Current status

The demo branch combines the student report screens, Gemini analysis and source-backed USF routing, and the Gmail test workflow. Use a controlled inbox for each sending demonstration. The tests run with simulated Google and Gmail responses.

## First-time setup

Use Python 3.11 or newer and Git. From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/init_local.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m flask --app app:create_app run
```

Open http://localhost:5000/ and choose **Sign in with Google**. Then save a report and choose **Analyze with Gemini** or select an issue category.

On macOS/Linux use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`.

`tools/init_local.py` creates a private `.env` with a random session secret and preserves an existing `.env`. Never commit that file. The local test suite and health endpoint run without Google credentials.

To test Google sign-in, configure a Google OAuth web client with the exact redirect URI `http://localhost:5000/auth/google/callback`. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `ALLOWED_GOOGLE_EMAILS` in `.env`. The last value is a comma-separated list of test account addresses. Restart the app and visit `http://localhost:5000/auth/google/start` using the same `localhost` origin as the callback. This requests identity and email scopes only. On success, the signed-in user's local ID is available as `session["user_id"]`. Use `POST /logout` with the session's CSRF token to sign out.

Set `GEMINI_API_KEY` in the private `.env` for photo analysis and source research. Use **Choose a category** if Gemini is unavailable and continue with the verified rules. The search request uses the campus, issue category and site type.

## Gmail test setup

1. Enable the Gmail API for the same Google Cloud project. Add the personal Google account that will send the report to the project's OAuth test users and to `ALLOWED_GOOGLE_EMAILS` locally.
2. Add `http://localhost:5000/gmail/callback` as a second redirect URI on the same OAuth web client. Use `localhost` for the whole flow.
3. Generate a private `GMAIL_TOKEN_KEY` with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Save it in your local `.env`. Use the project's virtual environment Python.
4. Set `TEST_RECIPIENT` to a single inbox the team controls. The preview shows the exact address and the app only delivers there in TEST mode.
5. Restart Flask. Sign in, open an email-eligible report, and connect Gmail with the same account. Confirm the **Review test email** details before clicking **Send report**. Reply from the test inbox to the original message, then use **Check for replies** on that report. A reply changes the communication status to **Reply received**.

If Gmail's send result is uncertain, inspect the sender's Sent folder using the reviewed subject. Keep that report locked while verifying delivery to avoid a duplicate. Disconnect removes the stored connection from Fix It Bulls. Google account settings manage the permission grant itself.

## Public reporting

Set `PUBLIC_GOOGLE_SIGN_IN=true` on the shared HTTPS deployment to accept any verified Google account. Set `SEND_MODE=OFFICIAL` to use the active, verified USF contact in the routing directory for approved mail. In this mode users can also prepare a preaddressed email draft, send it from their own email app, and attach the report photo manually. Replies to these messages stay in their email app. The separate Gmail integration still follows the Google OAuth project's test-user and consent settings.

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
