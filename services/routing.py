"""Verified routing: maps a report + issue type to a maintained USF channel.

Gemini never chooses a department. It only suggests an issue type; these
rules and data/routing_directory.json decide where a report goes. Anything the
directory does not clearly cover becomes needs_details or research_available,
never a guessed recipient.
"""

import json
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

from schemas import ReportInput, RoutingOutput

DIRECTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "routing_directory.json"

# Physical building problems: who owns them depends on the kind of site.
PHYSICAL_TYPES = {"plumbing", "lighting", "physical_damage", "cleanliness", "access_barrier"}
# Issue types with no maintained route yet: offer cited research instead.
RESEARCH_ONLY_TYPES = {
    "parking_transport", "recreation_equipment", "physical_id_card",
    "dining_feedback", "library_service", "other",
}


@lru_cache(maxsize=1)
def load_directory(path: str | None = None) -> dict:
    return json.loads(Path(path or DIRECTORY_PATH).read_text(encoding="utf-8"))


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _is_current(route: dict, directory: dict) -> bool:
    """Active and checked recently enough to trust."""
    if route.get("status") != "active":
        return False
    try:
        checked = date.fromisoformat(route["checked_date"])
    except (KeyError, ValueError):
        return False
    return (_today() - checked).days <= directory.get("max_age_days", 180)


def _matches_building(route: dict, building: str) -> bool:
    keywords = route.get("building_keywords") or []
    if not keywords:
        return True
    words = set(re.findall(r"[a-z]+", building.lower()))
    return any(k in words for k in keywords)


def get_route(route_id: str, directory: dict | None = None) -> dict | None:
    directory = directory or load_directory()
    return next((r for r in directory["routes"] if r["id"] == route_id), None)


def get_sendable_route(route_id: str, directory: dict | None = None) -> dict | None:
    """For the Gmail sender: the route only if it is a current, active email route.

    Returns None for forms, stale, inactive or unknown routes, so nothing can be
    emailed to a recipient that is not in the maintained directory.
    """
    directory = directory or load_directory()
    route = get_route(route_id, directory)
    if not route or route.get("channel") != "email" or not route.get("recipient"):
        return None
    return route if _is_current(route, directory) else None


def urgent_guidance(directory: dict | None = None) -> dict:
    return (directory or load_directory())["urgent_guidance"]


def _research(reason: str) -> RoutingOutput:
    return RoutingOutput(state="research_available", reason=reason)


def resolve_route(
    report: ReportInput,
    issue_type: str,
    *,
    urgent: bool = False,
    directory: dict | None = None,
) -> RoutingOutput:
    directory = directory or load_directory()

    # 1. Urgent concerns leave routine reporting entirely.
    if urgent:
        guide = directory["urgent_guidance"]
        return RoutingOutput(
            state="urgent_guidance",
            reason=guide["message"],
            official_source=guide["official_source"],
            checked_date=guide["checked_date"],
        )

    # 2. No maintained route for this kind of issue: offer research.
    if issue_type in RESEARCH_ONLY_TYPES:
        return _research(
            "There's no verified USF route for this kind of issue yet. "
            "Gemini can look up official reporting instructions and cite them."
        )

    # 3. Ask before guessing: these facts decide who is responsible.
    missing = []
    if issue_type in PHYSICAL_TYPES and report.site_type == "unknown":
        missing.append("site_type")
    if not report.room_floor_landmark.strip():
        missing.append("room_floor_landmark")
    if missing:
        return RoutingOutput(
            state="needs_details",
            reason="A few more details are needed before choosing where this report goes.",
            missing_fields=missing,
        )

    # 4. Personal property isn't a campus maintenance issue (campus Wi-Fi on a
    #    personal device is still a campus network problem).
    if report.ownership == "personal" and issue_type != "network_phone":
        return RoutingOutput(
            state="unsupported",
            reason="Campus reporting covers university buildings and equipment, not personal belongings.",
        )

    # 5. Outdoor physical problems: ownership isn't established by location alone.
    if issue_type in PHYSICAL_TYPES and report.site_type == "outdoor":
        return _research(
            "Outdoor areas (roads, lots, grounds) can belong to different offices, "
            "and the verified routes only cover buildings. Gemini can research the right channel."
        )

    # 6. First matching maintained route, most specific first.
    candidates = sorted(
        (
            r for r in directory["routes"]
            if r.get("campus") == report.campus
            and issue_type in r["issue_types"]
            and report.site_type in r["site_types"]
            and _matches_building(r, report.building_or_area)
        ),
        key=lambda r: r.get("priority", 100),
    )
    if not candidates:
        return _research("No verified USF route matches this issue and location.")

    route = candidates[0]
    if not _is_current(route, directory):
        # Stale or disputed routes are never used for sending.
        return _research(
            "The saved instructions for this route need to be re-checked against the official page, "
            "so sending through it is paused. Gemini can look up the current instructions."
        )

    state = "ready_email" if route["channel"] == "email" else "ready_handoff"
    return RoutingOutput(
        state=state,
        route_id=route["id"],
        reason=route["reason"],
        required_fields=route.get("required_fields", []),
        official_source=route["official_source"],
        checked_date=route["checked_date"],
    )
