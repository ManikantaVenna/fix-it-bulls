# Working together

Use one repository, the shared Python contracts, small commits and early integration. `main` is the working baseline. The three feature branches start at the same foundation commit.

## Start your assigned branch

After accepting the repository invitation:

```bash
git clone https://github.com/ManikantaVenna/fix-it-bulls.git
cd fix-it-bulls
git fetch origin
```

Sasi:

```bash
git switch --track origin/feature/report-ui
```

Hanif:

```bash
git switch --track origin/feature/routing-research
```

Manikanta:

```bash
git switch --track origin/feature/gmail-tracking
```

If the branch already exists locally, use `git switch <branch>` without `--track`. Follow README setup and run the tests before implementing your first slice.

## Sasi: student interface

Work in `templates/`, `static/`, `routes/reports.py`, and `tests/test_reports.py`. Start with description/location/photo capture and editable review. Next add clarification, cited research handoff and the report timeline. Own `base.html` so all screens share one layout. Keep current backend helpers and schemas; do not create another app/database or redesign the AI service response.

Use the persistence/photo helpers below. Request the signed-in user ID from the shared authenticated session once Manikanta connects auth; do not create a production auth bypass. Clearly labeled test fixtures can be used in tests while services are pending.

## Hanif: Gemini and routing

Work in `services/ai.py`, `services/routing.py`, `services/research.py`, `data/routing_directory.json`, and matching test files. Import models from `schemas.py` and implement:

```python
analyze_report(report: ReportInput, photo_bytes: bytes | None) -> AnalysisOutput
resolve_route(report: ReportInput, issue_type: str) -> RoutingOutput
research_channel(report: ReportInput) -> ResearchResult
```

Prioritize a real image response and core IT/Facilities/Housing/Library rules, then bounded cited research. Keep issue type distinct from department. Research uses a fresh minimal context and returns uncertainty when appropriate. It cannot create a send-enabled route. `grounding_metadata` is transient genuine provider attribution, not trusted HTML and not a database field; coordinate a reviewed renderer with Sasi.

## Manikanta: foundation and Gmail

Own shared schema/config/database changes, `routes/auth.py`, `routes/gmail.py`, `services/gmail_client.py`, and `services/mail.py`. Google sign-in and Gmail consent are separate. The backend binds the mailbox to the signed-in account and checks Hanif's `resolve_route` again before sending. Only `ready_email` with a maintained route ID and official source can reach the email review screen.

### Gmail endpoints for the report interface

All endpoints require a signed-in session. For POST requests, include a form CSRF token or use the `X-CSRFToken` header from `GET /api/csrf` for JavaScript requests.

| Endpoint | Request and response |
|---|---|
| `GET /gmail/status` | Returns `{"connected": true}` or `{"connected": false}`. |
| `POST /gmail/connect` | Submit as a browser form so the redirect opens Google consent. |
| `POST /gmail/disconnect` | Removes the saved connection and returns `{"connected": false}`. |
| `POST /reports/<id>/email-review` | JSON with `report_version`, `issue_type`, `subject`, and `body`. Returns `approval_id`, `report_version`, `mode`, `route`, `sender`, `recipient`, `subject`, `body`, `photo_id` and `issue_type`. |
| `POST /reports/<id>/approve-send` | JSON with `approved_version` and `approval_id` from the review. Returns the `SendOutcome` fields in `schemas.py`. |
| `POST /reports/<id>/check-replies` | Returns `{"replies": [...]}` with the `MailEvent` fields in `schemas.py`. |

Show the exact sender, test inbox, subject, message and attachment returned by the server. Open a new review after edits, including draft text edits. Call approve-send only when the user presses **Send report**. For `send_failed`, open a new review before another attempt. If delivery is uncertain, direct the user to verify in Gmail and keep the report locked. Render `safe_text` and sender names as text. Reply bodies stay in Gmail.

Preview and send both return HTTP 409 with `code: "urgent_guidance"`, `error` and `guidance_url` when the report may be urgent. Show the guidance and hide the send controls for that report. When the server finishes Gemini analysis, call `record_urgent_analysis(user_id, report_id, analyzed_version, analysis)` from `db.py` before returning the result to the UI. Use the version that was actually analyzed and pass the saved urgency flag to routing. Supply the trusted server result to this helper.

## Existing shared interfaces

Calls below require a Flask application context. The app's request handlers already have one; standalone scripts use `with app.app_context():`.

```python
from db import create_report, get_report, update_report
from services.storage import save_photo, read_photo

create_report(user_id: str, report: ReportInput) -> str
get_report(user_id: str, report_id: str) -> dict | None
update_report(user_id: str, report_id: str, changes: dict) -> dict
save_photo(user_id: str, upload) -> str
read_photo(user_id: str, photo_id: str) -> bytes
```

`get_report` returns `id`, `user_id`, `version`, nested `report` input fields, `communication_status`, `route_id`, `created_at` and `updated_at`. Another owner's ID returns `None`. Updates validate ReportInput fields, increment version and clear the route/ready state. Outbound/sent/unknown evidence is locked against silent rewriting. An explicit correction/follow-up workflow is future work.

`save_photo` accepts a file-like object such as Flask's FileStorage. It validates and re-encodes JPEG/PNG/WebP into a private JPEG; `read_photo` enforces ownership. Do not expose the upload directory through `static/`. Both report and photo ownership must be checked in newly added routes.

`upsert_user` is an internal persistence helper; it does not validate identity tokens. Only the completed auth adapter should call it for real users after Google token validation. The starter has no public endpoint that creates authenticated users.

## Commit and integrate

Commit only your working slice. Example for Sasi; stage only files that exist:

```bash
git status
git add templates static routes/reports.py tests/test_reports.py
git diff --cached
git commit -m "Add report capture and review flow"
git push -u origin feature/report-ui
```

Open a PR into `main` with what changed, tests run, and incomplete parts. Manikanta coordinates merges; Hanif or Sasi reviews his work. Use a normal merge to preserve commits. No force pushes or shared-history rewriting.

Bring current `main` into an ongoing branch only after your working tree is clean/committed:

```bash
git fetch origin
git merge origin/main
```

Resolve conflicts with the file owner and test again. After your PR is merged, start the next task from updated `main` on a new short branch. Integrate a working slice every 30-45 minutes where possible, rather than waiting until the deadline.

Do not commit `.env`, OAuth JSON/tokens, keys, databases, photos, logs, `.venv` or private coordination history. Ask before changing another owner's interface/files. Runtime credentials are shared only through a secure channel if necessary, never through Git.

## First integration target

One real report: Sasi's form -> Hanif's analysis and verified route -> Manikanta's approved test email/photo -> actual test reply -> private timeline. Then add a real cited research/handoff case. Use labeled test inboxes, never practice complaints to USF. Freeze feature additions 40-60 minutes before the real submission cutoff.
