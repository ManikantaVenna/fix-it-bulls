"""Shared wire contracts. Coordinate field changes before changing callers."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
IssueType = Literal[
    "plumbing", "lighting", "physical_damage", "cleanliness", "computer_av",
    "network_phone", "access_reader", "parking_transport", "recreation_equipment",
    "physical_id_card", "access_barrier", "dining_feedback", "library_service", "other",
]
RoutingState = Literal[
    "ready_email", "ready_handoff", "needs_details", "research_available",
    "researching", "researched_handoff", "unsupported", "urgent_guidance",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReportInput(Contract):
    description: str = Field(min_length=1, max_length=4000)
    campus: Literal["usf_tampa"] = "usf_tampa"
    site_type: Literal["non_residential", "residence_hall", "outdoor", "unknown"] = "unknown"
    building_or_area: str = Field(min_length=1, max_length=200)
    room_floor_landmark: str = Field(default="", max_length=300)
    asset_tag: str | None = Field(default=None, max_length=100)
    ownership: Literal["campus", "personal", "unknown"] = "unknown"
    photo_id: str | None = Field(default=None, max_length=64)
    clarification_answers: dict[str, ShortText] = Field(default_factory=dict, max_length=2)


class AnalysisOutput(Contract):
    observations: list[ShortText] = Field(default_factory=list, max_length=10)
    user_reported_facts: list[ShortText] = Field(default_factory=list, max_length=10)
    suggested_issue_type: IssueType = "other"
    missing_fields: list[ShortText] = Field(default_factory=list, max_length=10)
    questions: list[ShortText] = Field(default_factory=list, max_length=2)
    proposed_title: str = Field(default="", max_length=200)
    proposed_body: str = Field(default="", max_length=4000)
    possible_urgent_concern: bool = False
    uncertainty_notes: list[ShortText] = Field(default_factory=list, max_length=10)


class RoutingOutput(Contract):
    state: RoutingState
    route_id: str | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=1, max_length=2000)
    required_fields: list[ShortText] = Field(default_factory=list, max_length=10)
    missing_fields: list[ShortText] = Field(default_factory=list, max_length=10)
    official_source: HttpUrl | None = None
    checked_date: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def require_email_route(self):
        if self.state == "ready_email" and not self.route_id:
            raise ValueError("ready_email requires a maintained route_id")
        return self


class ResearchResult(Contract):
    status: Literal[
        "sourced_suggestion", "conflicting_sources", "insufficient_evidence",
        "no_result", "source_unavailable", "provider_error",
    ]
    suggested_office: str | None = Field(default=None, max_length=200)
    suggested_channel: Literal["email", "form", "instructions", "unknown"] = "unknown"
    supporting_source_links: list[HttpUrl] = Field(default_factory=list, max_length=6)
    applicability_reason: str = Field(default="", max_length=2000)
    uncertainties: list[ShortText] = Field(default_factory=list, max_length=10)
    consulted_at: datetime | None = None
    editable_draft: str = Field(default="", max_length=4000)
    # Preserve genuine grounding attribution separately; render only through
    # the reviewed attribution path. Never persist this raw provider payload.
    grounding_metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_sources(self):
        if self.status == "sourced_suggestion" and not self.supporting_source_links:
            raise ValueError("A sourced suggestion requires actual supporting links")
        return self


class SendOutcome(Contract):
    state: Literal["sending", "sent", "send_failed", "send_unknown"]
    report_id: str
    attempt_id: str
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    sent_at: datetime | None = None
    safe_error_code: str | None = None

    @model_validator(mode="after")
    def require_send_evidence(self):
        if self.state == "sent" and not (self.provider_message_id and self.provider_thread_id):
            raise ValueError("sent requires Gmail message and conversation identifiers")
        return self


class MailEvent(Contract):
    provider_message_id: str
    direction: Literal["incoming", "outgoing"]
    sender: str = Field(max_length=320)
    received_at: datetime
    safe_text: str = Field(max_length=20000)
