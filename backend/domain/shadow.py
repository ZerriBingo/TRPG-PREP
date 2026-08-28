"""P1 shadow-mode contracts kept outside approved domain bundles."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import FactKind, SourceRef


ShadowTaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ShadowRunStatus = Literal["running", "succeeded", "failed", "cancelled"]
ShadowReviewState = Literal["needs_review", "accepted", "rejected"]


class ShadowTaskSpec(BaseModel):
    """Reproducible inputs for a model run that cannot affect play directly."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    source_file: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=160)
    source_pages: list[int] = Field(min_length=1, max_length=100)
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    model_id: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=80)
    schema_version: str = Field(min_length=1, max_length=80)
    input_excerpt: str = Field(min_length=1, max_length=16000)

    @field_validator(
        "idempotency_key",
        "source_file",
        "source_version",
        "profile_id",
        "model_id",
        "prompt_version",
        "schema_version",
        "input_excerpt",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_pages")
    @classmethod
    def normalize_source_pages(cls, value: list[int]) -> list[int]:
        if any(page < 1 for page in value):
            raise ValueError("shadow task source pages must be positive")
        pages = sorted(set(value))
        if not pages:
            raise ValueError("shadow task needs at least one source page")
        return pages


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
    """An append-only GM review action that preserves the model's original text."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^shadow_review_[a-z0-9_-]+$")
    review_state: ShadowReviewState
    reviewed_text: str | None = Field(default=None, min_length=1, max_length=2000)
    review_note: str | None = Field(default=None, min_length=1, max_length=2000)
    created_at: str = Field(min_length=1)

    @field_validator("reviewed_text", "review_note")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ShadowCandidate(ShadowCandidateDraft):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^shadow_candidate_[a-z0-9_-]+$")
    task_id: str = Field(pattern=r"^shadow_task_[a-z0-9_-]+$")
    run_id: str = Field(pattern=r"^shadow_run_[a-z0-9_-]+$")
    evidence_status: Literal["model_candidate"] = "model_candidate"
    review_state: ShadowReviewState = "needs_review"
    reviewed_text: str | None = Field(default=None, min_length=1, max_length=2000)
    review_note: str | None = Field(default=None, min_length=1, max_length=2000)
    reviewed_at: str | None = None
    review_history: list[ShadowReviewEvent] = Field(default_factory=list, max_length=100)
    created_at: str = Field(min_length=1)

    @field_validator("reviewed_text", "review_note", "reviewed_at")
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
        if self.reviewed_text != latest.reviewed_text:
            raise ValueError("candidate reviewed_text must match the latest review event")
        if self.review_note != latest.review_note:
            raise ValueError("candidate review_note must match the latest review event")
        if self.reviewed_at != latest.created_at:
            raise ValueError("candidate reviewed_at must match the latest review event")
        return self
