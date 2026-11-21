import json
from types import SimpleNamespace as NS

from schemas import ReportInput
from services.research import build_query_context, research_channel


def report(**changes):
    return ReportInput(**{
        "description": "Bull Runner stop sign fell over, call me at 813-555-0199 or jo@usf.edu",
        "building_or_area": "Collins Blvd stop",
        "room_floor_landmark": "Near bench 22",
        "site_type": "outdoor",
        "asset_tag": "TAG-99812",
        **changes,
    })


FINDING = {
    "suggested_office": "Parking & Transportation Services",
    "suggested_channel": "form",
    "applicability_reason": "The page says Bull Runner issues are reported through its feedback form.",
    "uncertainties": [],
    "sources_conflict": False,
    "found_official_instructions": True,
}


def response(finding=FINDING, chunks=(("usf.edu", "https://vertexaisearch.example/redirect/1"),), urls=()):
    gm = NS(
        grounding_chunks=[NS(web=NS(title=t, uri=u)) for t, u in chunks],
        web_search_queries=["USF Bull Runner report problem"],
        search_entry_point=NS(rendered_content="<div>search</div>"),
    )
    ucm = NS(url_metadata=[NS(retrieved_url=u, url_retrieval_status=s) for u, s in urls])
    text = json.dumps(finding) if isinstance(finding, dict) else finding
    return NS(text=text, candidates=[NS(grounding_metadata=gm, url_context_metadata=ucm)])


class FakeClient:
    def __init__(self, resp=None, error=None):
        self.resp, self.error, self.calls = resp, error, []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.resp


def test_sourced_suggestion_uses_provider_links_only():
    resp = response(urls=[("https://www.usf.edu/parking/bull-runner/", "URL_RETRIEVAL_STATUS_SUCCESS")])
    out = research_channel(report(), issue_type="parking_transport", client=FakeClient(resp))
    assert out.status == "sourced_suggestion"
    assert str(out.supporting_source_links[0]) == "https://www.usf.edu/parking/bull-runner/"
    assert out.suggested_channel == "form"
    assert out.consulted_at is not None
    assert out.grounding_metadata["search_entry_point_html"]


def test_search_context_excludes_private_details():
    context = build_query_context(report(), "parking_transport")
    for private in ["813-555-0199", "jo@usf.edu", "TAG-99812", "bench 22"]:
        assert private not in context


def test_residence_hall_name_is_not_searched():
    context = build_query_context(report(site_type="residence_hall", building_or_area="Holly A"), "other")
    assert "Holly" not in context
    assert "residence hall" in context


def test_no_grounding_is_not_research_success():
    out = research_channel(report(), client=FakeClient(response(chunks=())))
    assert out.status == "no_result"
    assert out.suggested_office is None


def test_only_non_official_sources_is_insufficient():
    out = research_channel(report(), client=FakeClient(response(chunks=(("reddit.com", "https://x.example/r"),))))
    assert out.status == "insufficient_evidence"
    assert out.supporting_source_links == []
    assert out.suggested_office is None


def test_failed_url_retrieval_is_not_cited():
    resp = response(chunks=(("example.com", "https://x.example/1"),),
                    urls=[("https://www.usf.edu/gone", "URL_RETRIEVAL_STATUS_ERROR")])
    out = research_channel(report(), client=FakeClient(resp))
    assert out.status == "insufficient_evidence"


def test_conflicting_sources_are_reported():
    out = research_channel(report(), client=FakeClient(response({**FINDING, "sources_conflict": True})))
    assert out.status == "conflicting_sources"


def test_provider_error_keeps_the_draft():
    out = research_channel(report(), client=FakeClient(error=RuntimeError("boom")))
    assert out.status == "provider_error"
    assert "Bull Runner stop sign fell over" in out.editable_draft


def test_unreadable_answer_is_insufficient_not_success():
    out = research_channel(report(), client=FakeClient(response("no json here")))
    assert out.status == "insufficient_evidence"


class QuotaError(Exception):
    code = 429


class SearchQuotaClient(FakeClient):
    """Google Search grounding is out of quota; URL Context still works."""

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if any(t.google_search is not None for t in kwargs["config"].tools):
            raise QuotaError("RESOURCE_EXHAUSTED")
        return self.resp


def test_search_quota_falls_back_to_reading_official_pages():
    resp = response(chunks=(), urls=[("https://www.usf.edu/parking/bull-runner/", "URL_RETRIEVAL_STATUS_SUCCESS")])
    client = SearchQuotaClient(resp)
    out = research_channel(report(), issue_type="parking_transport", client=client)
    assert out.status == "sourced_suggestion"
    assert str(out.supporting_source_links[0]) == "https://www.usf.edu/parking/bull-runner/"
    assert "Web search was unavailable" in out.uncertainties[0]
    fallback = client.calls[-1]
    assert all(t.google_search is None for t in fallback["config"].tools)
    assert "https://www.usf.edu/parking/" in fallback["contents"]


def test_research_uses_search_and_url_context_tools():
    client = FakeClient(response())
    research_channel(report(), client=client)
    tools = client.calls[0]["config"].tools
    assert any(t.google_search is not None for t in tools)
    assert any(t.url_context is not None for t in tools)


# --- expiry and safe display ---
from datetime import datetime, timedelta, timezone

from schemas import ResearchResult
from services.research import display_sources, is_expired


def test_research_expires():
    fresh = ResearchResult(status="no_result", consulted_at=datetime.now(timezone.utc))
    old = ResearchResult(status="no_result", consulted_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert not is_expired(fresh)
    assert is_expired(old)
    assert is_expired(ResearchResult(status="no_result"))


def test_display_sources_is_plain_data():
    resp = response(urls=[("https://www.usf.edu/parking/bull-runner/", "URL_RETRIEVAL_STATUS_SUCCESS")])
    out = display_sources(research_channel(report(), client=FakeClient(resp)))
    assert out["links"][0] == {"url": "https://www.usf.edu/parking/bull-runner/", "label": "www.usf.edu"}
    assert out["links"][1]["label"] == "Google Search result (usf.edu)"  # redirect link
    assert out["widget_html"] == "<div>search</div>"
    assert out["consulted_at"]


class ReadTimeout(Exception):
    pass


class SearchTimeoutClient(FakeClient):
    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if any(t.google_search is not None for t in kwargs["config"].tools):
            raise ReadTimeout("search took too long")
        return self.resp


def test_search_timeout_falls_back_to_official_pages():
    resp = response(chunks=(), urls=[("https://www.usf.edu/parking/", "URL_RETRIEVAL_STATUS_SUCCESS")])
    out = research_channel(report(), issue_type="parking_transport", client=SearchTimeoutClient(resp))
    assert out.status == "sourced_suggestion"
