"""Cross-page preparation job contracts for the current LLM workflow."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PrepJobStatus = Literal["queued", "running", "completed", "partial", "failed", "cancelled"]
PrepWindowStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
WindowBoundaryBasis = Literal[
    "legacy",
    "scope_end",
    "heading",
    "sentence_end",
    "continuation",
    "page_limit",
    "char_budget",
]
WindowBoundarySignal = Literal["possible_heading", "possible_continuation", "sentence_end"]


class PageSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=1)
    end: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=120)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def end_is_not_before_start(self) -> "PageSpan":
        if self.end < self.start:
            raise ValueError("page span end must be greater than or equal to start")
        return self

    @property
    def page_count(self) -> int:
        return self.end - self.start + 1

    def pages(self) -> list[int]:
        return list(range(self.start, self.end + 1))


def normalize_page_spans(spans: list[PageSpan]) -> list[PageSpan]:
    """Sort and merge overlapping or adjacent unlabeled page spans."""
    ordered = sorted(spans, key=lambda item: (item.start, item.end))
    merged: list[PageSpan] = []
    for span in ordered:
        if not merged:
            merged.append(span)
            continue
        previous = merged[-1]
        if previous.label is None and span.label is None and span.start <= previous.end + 1:
            merged[-1] = PageSpan(start=previous.start, end=max(previous.end, span.end))
        else:
            merged.append(span)
    return merged


def parse_page_spans(value: str) -> list[PageSpan]:
    """Parse ranges such as 159-165, 172, 180-183."""
    text = value.strip().replace("，", ",")
    if not text:
        raise ValueError("page range is required")
    raw_tokens = text.split(",")
    if any(not token.strip() for token in raw_tokens):
        raise ValueError("page range contains an empty item")

    spans: list[PageSpan] = []
    token_pattern = re.compile(r"^(\d+)(?:\s*[-–—]\s*(\d+))?$")
    for raw_token in raw_tokens:
        token = raw_token.strip()
        match = token_pattern.fullmatch(token)
        if match is None:
            raise ValueError(f"invalid page range item: {token!r}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        spans.append(PageSpan(start=start, end=end))
    return normalize_page_spans(spans)


class PrepScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=160)
    page_spans: list[PageSpan] = Field(min_length=1, max_length=24)
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    session_minutes: int = Field(default=120, ge=30, le=480)
    objective: str = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("source_file", "source_version", "profile_id", "objective", "notes")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("page_spans")
    @classmethod
    def normalize_spans(cls, value: list[PageSpan]) -> list[PageSpan]:
        spans = normalize_page_spans(value)
        total_pages = sum(span.page_count for span in spans)
        if total_pages > 240:
            raise ValueError("one prep scope cannot exceed 240 selected pages")
        return spans


class PrepJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(min_length=1, max_length=500)
    page_range: str = Field(min_length=1, max_length=500)
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    session_minutes: int = Field(default=120, ge=30, le=480)

    @field_validator("source_file", "page_range", "profile_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ExtractionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^prep_window_[a-z0-9_-]+$")
    # `page_span` is the transport span sent to the model. `core_span` owns
    # candidate anchors; context pages are repeated only to protect boundaries.
    page_span: PageSpan
    core_span: PageSpan | None = None
    context_pages: list[int] = Field(default_factory=list, max_length=4)
    boundary_pages: list[int] = Field(default_factory=list, max_length=2)
    boundary_basis: WindowBoundaryBasis = "legacy"
    boundary_signals: list[WindowBoundarySignal] = Field(default_factory=list, max_length=3)
    truncated_pages: list[int] = Field(default_factory=list, max_length=8)
    status: PrepWindowStatus = "queued"
    shadow_task_id: str | None = Field(
        default=None, pattern=r"^shadow_task_[a-z0-9_-]+$"
    )
    candidate_count: int = Field(default=0, ge=0)
    input_chars: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=2000)

    @field_validator("error")
    @classmethod
    def strip_error(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @model_validator(mode="after")
    def window_ranges_are_consistent(self) -> "ExtractionWindow":
        if self.core_span is None:
            self.core_span = self.page_span
        if (
            self.core_span.start < self.page_span.start
            or self.core_span.end > self.page_span.end
        ):
            raise ValueError("window core span must be inside its transport span")
        transport_pages = set(self.page_span.pages())
        core_pages = set(self.core_span.pages())
        for field_name, pages in (
            ("context_pages", self.context_pages),
            ("boundary_pages", self.boundary_pages),
            ("truncated_pages", self.truncated_pages),
        ):
            if len(pages) != len(set(pages)):
                raise ValueError(f"{field_name} values must be unique")
            if any(page not in transport_pages for page in pages):
                raise ValueError(f"{field_name} values must stay inside page_span")
        if len(self.boundary_signals) != len(set(self.boundary_signals)):
            raise ValueError("boundary_signals values must be unique")
        if any(page in core_pages for page in self.context_pages):
            raise ValueError("context_pages cannot overlap core_span")
        return self


class PrepJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^prep_job_[a-z0-9_-]+$")
    status: PrepJobStatus = "queued"
    scope: PrepScope
    model_id: str = Field(min_length=1, max_length=160)
    fake_model: bool = False
    prompt_version: str = Field(min_length=1, max_length=80)
    schema_version: str = Field(min_length=1, max_length=80)
    window_strategy: Literal["legacy-overlap-v1", "core-context-v2", "core-context-v3"] = (
        "legacy-overlap-v1"
    )
    workspace_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    analysis_version: int = Field(default=1, ge=1)
    previous_job_id: str | None = Field(
        default=None, pattern=r"^prep_job_[a-z0-9_-]+$"
    )
    windows: list[ExtractionWindow] = Field(min_length=1, max_length=120)
    candidate_count: int = Field(default=0, ge=0)
    promoted_count: int = Field(default=0, ge=0)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
