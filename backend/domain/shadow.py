"""P1 shadow-mode contracts kept outside approved domain bundles."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import FactKind, SourceRef


ShadowTaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ShadowRunStatus = Literal["running", "succeeded", "failed", "cancelled"]
ShadowErrorKind = Literal[
    "model_format",
    "upstream_unavailable",
    "account_access",
    "input_config",
    "worker",
    "cancelled",
]
ShadowReviewState = Literal["needs_review", "accepted", "rejected"]
ShadowReviewAction = Literal["review", "edit", "split", "merge"]
CandidateContentBasis = Literal["model_candidate", "source_fact", "inference", "gm_authored"]
ShadowTaskKind = Literal["standalone", "prep_window", "semantic_consolidation"]
QueueVisibility = Literal["review", "internal"]
CandidateRole = Literal["standalone", "window_observation", "segment_result"]


class ShadowTaskSpec(BaseModel):
    """Reproducible inputs for a model run that cannot affect play directly."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    source_file: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=160)
    source_pages: list[int] = Field(min_length=1, max_length=240)
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    model_id: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=80)
    schema_version: str = Field(min_length=1, max_length=80)
    input_excerpt: str = Field(min_length=1, max_length=16000)
    task_kind: ShadowTaskKind = "standalone"
    queue_visibility: QueueVisibility = "review"
    semantic_segment_id: str | None = Field(
        default=None, pattern=r"^semantic_segment_[a-z0-9_-]+$"
    )
    segment_window_index: int | None = Field(default=None, ge=1)
    segment_window_count: int | None = Field(default=None, ge=1)
    parent_task_ids: list[str] = Field(default_factory=list, max_length=120)

    @field_validator(
        "idempotency_key",
        "source_file",
        "source_version",
        "profile_id",
        "model_id",
        "prompt_version",
        "schema_version",
        "input_excerpt",
        "semantic_segment_id",
    )
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("source_pages")
    @classmethod
    def normalize_source_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("shadow task source pages must be positive")
        pages = sorted(set(value))
        if not pages:
            raise ValueError("shadow task needs at least one source page")
        return pages

    @field_validator("parent_task_ids")
    @classmethod
    def normalize_parent_task_ids(cls, value: list[str]) -> list[str]:
        values = [item.strip() for item in value if item.strip()]
        if len(values) != len(set(values)):
            raise ValueError("shadow task parent ids must be unique")
        return values

    @model_validator(mode="after")
    def semantic_task_metadata_is_consistent(self) -> "ShadowTaskSpec":
        indexed = self.segment_window_index is not None
        counted = self.segment_window_count is not None
        window_index = self.segment_window_index
        window_count = self.segment_window_count
        if indexed != counted:
            raise ValueError(
                "segment window index and count must be provided together"
            )
        if indexed and self.semantic_segment_id is None:
            raise ValueError("segment window numbering requires a semantic segment id")
        if window_index is not None and window_count is not None and window_index > window_count:
            raise ValueError("segment window index cannot exceed its count")
        if self.task_kind == "semantic_consolidation" and self.semantic_segment_id is None:
            raise ValueError("semantic consolidation tasks require a semantic segment id")
        if self.task_kind == "prep_window" and self.queue_visibility != "internal":
            raise ValueError("prep window tasks must remain internal to the review queue")
        return self


class ShadowTask(ShadowTaskSpec):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^shadow_task_[a-z0-9_-]+$")
    status: ShadowTaskStatus = "queued"
    attempt_count: int = Field(default=0, ge=0)
    latest_run_id: str | None = None
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class ShadowRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^shadow_run_[a-z0-9_-]+$")
    task_id: str = Field(pattern=r"^shadow_task_[a-z0-9_-]+$")
    attempt: int = Field(ge=1)
    status: ShadowRunStatus = "running"
    model_id: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=80)
    schema_version: str = Field(min_length=1, max_length=80)
    started_at: str = Field(min_length=1)
    finished_at: str | None = None
    raw_response_summary: str | None = Field(default=None, max_length=2000)
    parse_error: str | None = Field(default=None, max_length=2000)
    transport_error: str | None = Field(default=None, max_length=2000)
    error_kind: ShadowErrorKind | None = None
    candidate_count: int = Field(default=0, ge=0)

    @field_validator(
        "model_id",
        "prompt_version",
        "schema_version",
        "finished_at",
        "raw_response_summary",
        "parse_error",
        "transport_error",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ShadowCandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    kind: FactKind
    source_refs: list[SourceRef] = Field(min_length=1, max_length=12)
    confidence: float | None = Field(default=None, ge=0, le=1)
    possible_links: list[str] = Field(default_factory=list, max_length=24)
    open_questions: list[str] = Field(default_factory=list, max_length=24)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("possible_links", "open_questions")
    @classmethod
    def strip_list_items(cls, value: list[str]) -> list[str]:
        values = [item.strip() for item in value if item.strip()]
        if len(values) != len(set(values)):
            raise ValueError("shadow candidate list values must be unique")
        return values


class ShadowResponse(BaseModel):
    """Strict JSON shape accepted from a shadow-model worker."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[ShadowCandidateDraft] = Field(default_factory=list, max_length=50)


class ShadowReviewEvent(BaseModel):
    """Metadata for one candidate operation; content lives only on the candidate."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^shadow_review_[a-z0-9_-]+$")
    action: ShadowReviewAction = "review"
    review_state: ShadowReviewState
    note: str | None = Field(default=None, min_length=1, max_length=2000)
    source_changes: list[str] = Field(default_factory=list, max_length=24)
    field_paths: list[str] = Field(default_factory=list, max_length=24)
    related_candidate_ids: list[str] = Field(default_factory=list, max_length=50)
    created_at: str = Field(min_length=1)

    @field_validator("note")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("source_changes", "field_paths", "related_candidate_ids")
    @classmethod
    def normalize_metadata_lists(cls, value: list[str]) -> list[str]:
        values = [item.strip() for item in value if item.strip()]
        if len(values) != len(set(values)):
            raise ValueError("review metadata values must be unique")
        return values

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_content_fields(cls, value: Any) -> Any:
        """Read old rows without carrying historical content into new writes."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "action" not in data:
            data["action"] = "review"
        if data.get("note") is None and data.get("review_note"):
            data["note"] = data["review_note"]
        # Older rows stored a second text field in every event. It is
        # intentionally ignored; only operation metadata is reconstructed.
        data.pop("reviewed_text", None)
        data.pop("review_note", None)
        return data


class ShadowCandidate(ShadowCandidateDraft):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^shadow_candidate_[a-z0-9_-]+$")
    task_id: str = Field(pattern=r"^shadow_task_[a-z0-9_-]+$")
    run_id: str = Field(pattern=r"^shadow_run_[a-z0-9_-]+$")
    evidence_status: Literal["model_candidate"] = "model_candidate"
    queue_visibility: QueueVisibility = "review"
    candidate_role: CandidateRole = "standalone"
    semantic_segment_id: str | None = Field(
        default=None, pattern=r"^semantic_segment_[a-z0-9_-]+$"
    )
    # The candidate remains unpromoted, while this field records the basis of
    # the current editable claim for promotion validation.
    content_basis: CandidateContentBasis = "model_candidate"
    review_state: ShadowReviewState = "needs_review"
    review_note: str | None = Field(default=None, min_length=1, max_length=2000)
    reviewed_at: str | None = None
    review_history: list[ShadowReviewEvent] = Field(default_factory=list, max_length=100)
    created_at: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_review_content(cls, value: Any) -> Any:
        """Load legacy rows while writing only the current-record shape."""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        # The previous schema kept a second reviewed text field. The current
        # record is authoritative; discard the stale duplicate instead of
        # allowing old model output to overwrite the current text.
        data.pop("reviewed_text", None)
        if "content_basis" not in data:
            data["content_basis"] = "model_candidate"
        return data

    @field_validator("review_note", "reviewed_at")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def review_summary_matches_history(self) -> "ShadowCandidate":
        if not self.review_history:
            return self
        latest = self.review_history[-1]
        if self.review_state != latest.review_state:
            raise ValueError("candidate review_state must match the latest review event")
        if self.reviewed_at != latest.created_at:
            raise ValueError("candidate reviewed_at must match the latest review event")
        if self.review_note != latest.note:
            raise ValueError("candidate review_note must match the latest review event")
        return self


class ShadowCandidateEdit(BaseModel):
    """Current-record candidate edit; omitted fields remain unchanged."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: FactKind | None = None
    source_refs: list[SourceRef] | None = Field(default=None, min_length=1, max_length=12)
    possible_links: list[str] | None = Field(default=None, max_length=24)
    open_questions: list[str] | None = Field(default=None, max_length=24)
    content_basis: Literal["source_fact", "inference", "gm_authored"] | None = None
    review_note: str | None = Field(default=None, max_length=2000)

    @field_validator("text", "review_note")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("possible_links", "open_questions")
    @classmethod
    def normalize_optional_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        values = [item.strip() for item in value if item.strip()]
        if len(values) != len(set(values)):
            raise ValueError("candidate list values must be unique")
        return values

    @model_validator(mode="after")
    def has_change(self) -> "ShadowCandidateEdit":
        if not any(
            value is not None
            for value in (
                self.text,
                self.kind,
                self.source_refs,
                self.possible_links,
                self.open_questions,
                self.content_basis,
                self.review_note,
            )
        ) and "review_note" not in self.model_fields_set:
            raise ValueError("candidate edit must change at least one field")
        if self.text is not None and self.content_basis is None:
            # A rewritten claim is not allowed to silently retain source-fact
            # semantics; the caller must opt into a stronger basis explicitly.
            self.content_basis = "inference"
        return self


class ShadowCandidateSplitIn(BaseModel):
    """Replacement children for one candidate split operation."""

    model_config = ConfigDict(extra="forbid")

    parts: list[ShadowCandidateDraft] = Field(min_length=2, max_length=12)
    content_basis: Literal["source_fact", "inference", "gm_authored"] = "inference"
    review_note: str | None = Field(default=None, max_length=2000)

    @field_validator("review_note")
    @classmethod
    def strip_optional_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ShadowCandidateMergeIn(BaseModel):
    """Replacement candidate for a merge operation."""

    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(min_length=2, max_length=50)
    text: str = Field(min_length=1, max_length=2000)
    kind: FactKind | None = None
    source_refs: list[SourceRef] | None = Field(default=None, min_length=1, max_length=12)
    possible_links: list[str] | None = Field(default=None, max_length=24)
    open_questions: list[str] | None = Field(default=None, max_length=24)
    content_basis: Literal["source_fact", "inference", "gm_authored"] = "inference"
    review_note: str | None = Field(default=None, max_length=2000)

    @field_validator("candidate_ids")
    @classmethod
    def normalize_candidate_ids(cls, value: list[str]) -> list[str]:
        values = [item.strip() for item in value if item.strip()]
        if len(values) < 2:
            raise ValueError("merge needs at least two candidate ids")
        if len(values) != len(set(values)):
            raise ValueError("merge candidate ids must be unique")
        return values

    @field_validator("review_note")
    @classmethod
    def strip_optional_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("possible_links", "open_questions")
    @classmethod
    def normalize_optional_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        values = [item.strip() for item in value if item.strip()]
        if len(values) != len(set(values)):
            raise ValueError("candidate list values must be unique")
        return values
