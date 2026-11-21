"""Cited research for issues the verified directory doesn't cover.

Runs a fresh Gemini request with Google Search grounding and URL Context,
using only minimal service context (no photo, identity, exact room or asset
tag). Supporting links come from the provider's grounding metadata, never
from URLs the model writes itself. A result is only a suggestion with an
official-channel handoff: it can't create a send-enabled route.
"""

import json
import re
from datetime import datetime, timezone
from typing import get_args
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from schemas import IssueType, ReportInput, ResearchResult
from services.ai import AIUnavailableError, generate, get_client, http_options, is_retryable, model_name

OFFICIAL_DOMAIN = "usf.edu"
SUCCESS = "URL_RETRIEVAL_STATUS_SUCCESS"

# When Google Search grounding isn't available (e.g. no quota on a free key),
# Gemini reads these official starting pages directly with URL Context instead.
# Every URL here was checked to load on 2026-11-21. URL Context does not follow
# links, so a page that doesn't cover the issue simply yields weaker evidence.
_FACILITIES = "https://www.usf.edu/facilities/"
_IT = "https://www.usf.edu/it/get-help/"
_STUDENT_AFFAIRS = "https://www.usf.edu/student-affairs/"
SEED_PAGES = {
    "parking_transport": ["https://www.usf.edu/parking/", "https://www.usf.edu/parking/bull-runner/"],
    "library_service": ["https://lib.usf.edu/forms/"],
    "recreation_equipment": [_STUDENT_AFFAIRS],
    "physical_id_card": [_STUDENT_AFFAIRS],
    "dining_feedback": [_STUDENT_AFFAIRS],
    "physical": [_FACILITIES, "https://www.usf.edu/parking/", "https://www.usf.edu/housing/"],
    "other": [_FACILITIES, _IT, _STUDENT_AFFAIRS],
}
PHYSICAL = {"plumbing", "lighting", "physical_damage", "cleanliness", "access_barrier"}


def seed_pages(issue_type: str) -> list[str]:
    if issue_type in PHYSICAL:
        return SEED_PAGES["physical"]
    return SEED_PAGES.get(issue_type, SEED_PAGES["other"])


class _Finding(BaseModel):
    suggested_office: str | None = None
    suggested_channel: str = "unknown"
    applicability_reason: str = ""
    uncertainties: list[str] = []
    sources_conflict: bool = False
    found_official_instructions: bool = False


def build_query_context(report: ReportInput, issue_type: str = "other") -> str:
    category = issue_type if issue_type in get_args(IssueType) else "other"
    return (
        f"Campus: University of South Florida, Tampa campus.\n"
        f"Issue type: {category.replace('_', ' ')}.\n"
        f"Site type: {report.site_type.replace('_', ' ')}."
    )


def _prompt(context: str, pages: list[str] | None = None) -> str:
    where = (
        "Read these official USF pages and use only what they say:\n" + "\n".join(pages)
        if pages else
        "Search the web and read the most relevant official usf.edu pages (at most 3)."
    )
    return f"""Find how a USF Tampa student should officially report this non-urgent campus problem.

{context}

{where}
Prefer an official departmental page that explains how to report or request service.
A general contact email or a staff profile is NOT proof that email is the correct way to submit this request.
Check: that it applies to the Tampa campus, that the office handles this kind of issue, and whether
it says to use email, a web form/portal, or other instructions.

Reply with ONLY a JSON object:
{{"suggested_office": string or null,
  "suggested_channel": "email" | "form" | "instructions" | "unknown",
  "applicability_reason": "why this office applies, based on what the page says",
  "uncertainties": ["anything unconfirmed or missing"],
  "sources_conflict": true/false,
  "found_official_instructions": true/false}}
Do not include email addresses or URLs in your answer."""


def _parse_finding(text: str) -> _Finding:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("no JSON object in research response")
    return _Finding.model_validate(json.loads(match.group(0)))


def _is_official(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == OFFICIAL_DOMAIN or host.endswith("." + OFFICIAL_DOMAIN)


def _evidence(candidate) -> tuple[list[str], dict]:
    """Official links actually retrieved/cited by the provider, plus transient attribution."""
    links: list[str] = []
    chunks = []
    gm = getattr(candidate, "grounding_metadata", None)
    for chunk in (getattr(gm, "grounding_chunks", None) or []):
        web = getattr(chunk, "web", None)
        if not web or not getattr(web, "uri", None):
            continue
        title = (getattr(web, "title", "") or "").lower()
        chunks.append({"title": getattr(web, "title", ""), "uri": web.uri})
        # Grounding URIs are Google redirect links; the title carries the site's domain.
        if _is_official(web.uri) or title == OFFICIAL_DOMAIN or title.endswith("." + OFFICIAL_DOMAIN):
            links.append(web.uri)
    ucm = getattr(candidate, "url_context_metadata", None)
    for meta in (getattr(ucm, "url_metadata", None) or []):
        url = getattr(meta, "retrieved_url", None)
        status = str(getattr(meta, "url_retrieval_status", ""))
        if url and SUCCESS in status and _is_official(url):
            links.insert(0, url)  # real page URLs first
    entry = getattr(gm, "search_entry_point", None)
    attribution = {
        "web_search_queries": list(getattr(gm, "web_search_queries", None) or []),
        "chunks": chunks,
        # Google's required Search suggestion widget. Render only through the reviewed path.
        "search_entry_point_html": getattr(entry, "rendered_content", None),
    }
    return list(dict.fromkeys(links))[:6], attribution


def _draft(report: ReportInput) -> str:
    """The student's own facts, ready to paste into whatever channel they choose."""
    where = ", ".join(p for p in [report.building_or_area, report.room_floor_landmark] if p)
    return f"Location: {where}\n\nProblem: {report.description}"


def research_channel(report: ReportInput, *, issue_type: str = "other", client=None) -> ResearchResult:
    now = datetime.now(timezone.utc)
    draft = _draft(report)

    def result(status: str, **kw) -> ResearchResult:
        return ResearchResult(status=status, consulted_at=now, editable_draft=draft[:4000], **kw)

    context = build_query_context(report, issue_type)
    notes: list[str] = []
    try:
        from google.genai import types

        client = client or get_client()

        def ask(prompt, tools, models=None, quick_timeout_ms=15_000):
            config = types.GenerateContentConfig(tools=tools, temperature=0.2, http_options=http_options(60_000))
            return generate(client, prompt, config, models=models, quick_timeout_ms=quick_timeout_ms)

        try:
            # Quota errors apply to the whole key, so web search tries one model only.
            response = ask(_prompt(context), [
                types.Tool(google_search=types.GoogleSearch()),
                types.Tool(url_context=types.UrlContext()),
            ], models=[model_name()])
        except Exception as exc:
            if not is_retryable(exc):
                raise
            # Web search has no quota, is busy or timed out: read known official pages instead.
            pages = seed_pages(issue_type)
            # Reading pages takes longer than a plain answer (about 10-60s live), so
            # each model gets 45s and only the two fastest models are tried.
            response = ask(
                _prompt(context, pages), [types.Tool(url_context=types.UrlContext())],
                models=list(dict.fromkeys([model_name(), "gemini-3.6-flash"])), quick_timeout_ms=45_000,
            )
            notes.append(f"Web search was unavailable, so only {len(pages)} official USF page(s) were checked.")
    except AIUnavailableError:
        return result("provider_error", uncertainties=["Gemini research isn't configured."])
    except Exception:
        return result("provider_error", uncertainties=["Research is unavailable right now. Your draft is saved."])

    candidate = (getattr(response, "candidates", None) or [None])[0]
    if candidate is None:
        return result("no_result", uncertainties=["The search returned nothing."])

    links, attribution = _evidence(candidate)
    try:
        finding = _parse_finding(getattr(response, "text", "") or "")
    except (ValueError, ValidationError):
        return result(
            "insufficient_evidence",
            supporting_source_links=links,
            uncertainties=["The research answer couldn't be read. Check the official sources directly."],
            grounding_metadata=attribution,
        )

    channel = finding.suggested_channel if finding.suggested_channel in {"email", "form", "instructions"} else "unknown"
    uncertainties = (notes + [u.strip()[:1000] for u in finding.uncertainties if u and u.strip()])[:8]
    common = dict(
        suggested_office=(finding.suggested_office or None) and finding.suggested_office[:200],
        suggested_channel=channel,
        supporting_source_links=links,
        applicability_reason=finding.applicability_reason[:2000],
        grounding_metadata=attribution,
    )

    if not attribution["chunks"] and not links:
        # No grounding at all: whatever the model said isn't research.
        return result("no_result", uncertainties=uncertainties + ["No sources were found."],
                      **{**common, "suggested_office": None, "suggested_channel": "unknown"})
    if finding.sources_conflict:
        return result("conflicting_sources", uncertainties=uncertainties or ["Official sources disagree."], **common)
    if not links:
        return result(
            "insufficient_evidence",
            uncertainties=uncertainties + ["No official usf.edu page supported this answer."],
            **{**common, "suggested_office": None, "suggested_channel": "unknown"},
        )
    if not finding.found_official_instructions:
        return result("insufficient_evidence", uncertainties=uncertainties or ["No clear reporting instructions found."], **common)
    return result("sourced_suggestion", uncertainties=uncertainties, **common)


RESEARCH_MAX_AGE_MINUTES = 30


def is_expired(result: ResearchResult, now: datetime | None = None) -> bool:
    """Research is transient. Past this age (or after a reload that lost it),
    show "Research result expired - check again" and keep the student's draft."""
    if result.consulted_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - result.consulted_at).total_seconds() > RESEARCH_MAX_AGE_MINUTES * 60


def display_sources(result: ResearchResult) -> dict:
    """Safe data for the research card. Plain strings only: escape them in the template.

    Google's Search suggestion widget (search_entry_point_html) is
    provider-supplied HTML. Render it only inside a sandboxed iframe, e.g.
    <iframe sandbox srcdoc="{{ widget_html }}"> with Jinja autoescaping on,
    never with |safe. It is None when search wasn't used.
    """
    links = []
    for url in result.supporting_source_links:
        url = str(url)
        host = urlparse(url).hostname or ""
        links.append({"url": url, "label": host if _is_official(url) else "Google Search result (usf.edu)"})
    meta = result.grounding_metadata or {}
    return {
        "links": links,
        "search_queries": [str(q)[:200] for q in meta.get("web_search_queries", [])][:5],
        "widget_html": meta.get("search_entry_point_html"),
        "consulted_at": result.consulted_at.isoformat() if result.consulted_at else None,
    }
