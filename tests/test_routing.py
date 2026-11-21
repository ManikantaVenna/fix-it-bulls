import copy
from datetime import date, timedelta

import pytest

from schemas import ReportInput
from services import routing
from services.routing import get_sendable_route, load_directory, resolve_route


def report(**changes):
    return ReportInput(**{
        "description": "Sink is leaking under the counter",
        "building_or_area": "ENB",
        "room_floor_landmark": "2nd floor restroom",
        "site_type": "non_residential",
        "ownership": "campus",
        **changes,
    })


def test_academic_building_maintenance_goes_to_facilities_email():
    out = resolve_route(report(), "plumbing")
    assert out.state == "ready_email"
    assert out.route_id == "usf_facilities_service_center"
    assert "work-requests" in str(out.official_source)
    assert out.checked_date


def test_residence_hall_maintenance_is_a_housing_handoff_not_email():
    out = resolve_route(report(site_type="residence_hall", building_or_area="Juniper-Poplar"), "plumbing")
    assert out.state == "ready_handoff"
    assert out.route_id == "usf_housing_maintenance_portal"


def test_residence_hall_internet_goes_to_it_not_housing():
    out = resolve_route(report(site_type="residence_hall", description="Wi-Fi drops constantly"), "network_phone")
    assert out.state == "ready_email"
    assert out.route_id == "usf_it_help"


@pytest.mark.parametrize("issue", ["access_reader", "computer_av", "network_phone"])
def test_it_issues_go_to_it(issue):
    assert resolve_route(report(), issue).route_id == "usf_it_help"


def test_library_form_overrides_generic_facilities():
    out = resolve_route(report(building_or_area="USF Library"), "lighting")
    assert out.state == "ready_handoff"
    assert out.route_id == "usf_libraries_maintenance_form"


def test_library_keyword_does_not_match_inside_other_words():
    # "Liberty" contains "lib" but isn't a library.
    out = resolve_route(report(building_or_area="Liberty Hall"), "lighting")
    assert out.route_id == "usf_facilities_service_center"


def test_unknown_site_type_asks_instead_of_guessing_facilities_or_housing():
    out = resolve_route(report(site_type="unknown"), "plumbing")
    assert out.state == "needs_details"
    assert "site_type" in out.missing_fields
    assert out.route_id is None


def test_missing_room_or_landmark_needs_details():
    out = resolve_route(report(room_floor_landmark=""), "lighting")
    assert out.state == "needs_details"
    assert "room_floor_landmark" in out.missing_fields


def test_outdoor_physical_issue_is_researched_not_guessed():
    out = resolve_route(report(site_type="outdoor", building_or_area="Lot 18"), "lighting")
    assert out.state == "research_available"
    assert out.route_id is None


@pytest.mark.parametrize("issue", ["parking_transport", "dining_feedback", "other"])
def test_unmapped_issue_types_offer_research(issue):
    out = resolve_route(report(), issue)
    assert out.state == "research_available"
    assert out.route_id is None


def test_urgent_concern_leaves_routine_reporting():
    out = resolve_route(report(), "plumbing", urgent=True)
    assert out.state == "urgent_guidance"
    assert "911" in out.reason
    assert out.route_id is None


def test_personal_property_is_unsupported():
    assert resolve_route(report(ownership="personal"), "computer_av").state == "unsupported"


def _directory_with(route_id, **changes):
    directory = copy.deepcopy(load_directory())
    for r in directory["routes"]:
        if r["id"] == route_id:
            r.update(changes)
    return directory


def test_stale_route_blocks_sending_and_offers_research():
    old = (date.today() - timedelta(days=400)).isoformat()
    directory = _directory_with("usf_facilities_service_center", checked_date=old)
    out = resolve_route(report(), "plumbing", directory=directory)
    assert out.state == "research_available"
    assert get_sendable_route("usf_facilities_service_center", directory) is None


@pytest.mark.parametrize("status", ["needs_verification", "conflicting"])
def test_inactive_route_is_never_ready(status):
    directory = _directory_with("usf_facilities_service_center", status=status)
    assert resolve_route(report(), "plumbing", directory=directory).state == "research_available"


def test_sendable_route_only_for_current_email_routes():
    assert get_sendable_route("usf_facilities_service_center")["recipient"] == "FM-ServiceCenter@usf.edu"
    assert get_sendable_route("usf_it_help")["recipient"] == "help@usf.edu"
    assert get_sendable_route("usf_housing_maintenance_portal") is None  # form route
    assert get_sendable_route("made_up_route") is None


def test_directory_entries_are_complete_and_cite_official_pages():
    directory = load_directory()
    for r in directory["routes"]:
        assert r["official_source"].startswith("https://")
        assert date.fromisoformat(r["checked_date"])
        assert r["channel"] in {"email", "form"}
        assert (r["channel"] == "email") == bool(r["recipient"])
        assert set(r["issue_types"]) <= set(routing.PHYSICAL_TYPES | {"network_phone", "access_reader", "computer_av"})
    assert "911" in directory["urgent_guidance"]["message"]


# --- route_for_analysis / triage ---
from schemas import AnalysisOutput
from services.routing import route_for_analysis


def test_unanswered_questions_hold_routing():
    analysis = AnalysisOutput(suggested_issue_type="computer_av", questions=["Does the power light turn on?"])
    out = route_for_analysis(report(), analysis)
    assert out.state == "needs_details"
    assert out.missing_fields == ["Does the power light turn on?"]


def test_answered_questions_allow_routing():
    q = "Does the power light turn on?"
    analysis = AnalysisOutput(suggested_issue_type="computer_av", questions=[q])
    out = route_for_analysis(report(clarification_answers={q: "No light at all"}), analysis)
    assert out.route_id == "usf_it_help"


def test_urgent_analysis_wins_over_questions():
    analysis = AnalysisOutput(suggested_issue_type="lighting", questions=["Which floor?"], possible_urgent_concern=True)
    assert route_for_analysis(report(), analysis).state == "urgent_guidance"


def test_triage_runs_analysis_then_route():
    from types import SimpleNamespace
    from services.ai import _GeminiAnalysis
    from services.routing import triage

    parsed = _GeminiAnalysis(observations=[], user_reported_facts=[], suggested_issue_type="plumbing",
                             missing_fields=[], questions=[], proposed_title="Leak", proposed_body="Leak.",
                             possible_urgent_concern=False, uncertainty_notes=[])
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **k: SimpleNamespace(parsed=parsed, text="")))
    analysis, route = triage(report(), None, client=client)
    assert analysis.suggested_issue_type == "plumbing"
    assert route.route_id == "usf_facilities_service_center"
