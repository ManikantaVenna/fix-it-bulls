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

Own shared schema/config/database changes and `routes/auth.py`, `routes/gmail.py`, `services/gmail_client.py`. Google login and Gmail consent are separate; bind tokens to the validated owner. Use approved immutable report versions and the directory when sending. Add actual photo email, message/thread IDs and manual same-thread reply retrieval before background features.

Planned service interface:

```python
send_approved_report(user_id: str, report_id: str, approved_version: int) -> SendOutcome
check_report_replies(user_id: str, report_id: str) -> list[MailEvent]
```

These functions are pending, not implemented by the starter. Keep imported-mail AI summaries disabled until compatible processing is established.

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
