from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FactKind = Literal[
    "clue",
    "npc",
    "location",
    "handout",
    "event",
    "threat",
    "stakes",
    "obstacle",
    "timeline",
    "resource",
]
FactVisibility = Literal["explicit", "hidden", "inferred", "gm_suggestion"]
EvidenceStatus = Literal["source_fact", "inference", "gm_authored", "model_candidate"]
ProfileKind = Literal["runtime", "prep"]
CardEditState = Literal["generated", "edited", "approved"]
ArtifactJobStatus = Literal["queued", "running", "completed", "failed"]
ArtifactJobPhase = Literal[
    "queued",
    "direct_generation",
    "local_digest",
    "global_plan",
    "materializing",
    "validating",
    "completed",
]
BeatMode = Literal["arrival", "investigation", "pressure", "revelation", "confrontation", "aftermath", "transition"]
RuntimeNavigationMode = Literal["location", "beat"]
TriggerState = Literal["unhandled", "active", "resolved"]
SessionLogKind = Literal[
    # Legacy values remain readable so existing saved sessions do not break.
    "move",
    "note",
    "transition",
    # Structured P0.3 runtime review events.
    "run_started",
    "lookup",
    "lookup_missing",
    "source_page_opened",
    "clue_revealed",
    "clock_advanced",
    "clock_rewound",
    "scene_changed",
    "beat_changed",
    "gm_move",
    "manual_note",
    "field_edited",
]
SessionLogSubject = Literal[
    "session",
    "fact",
    "card",
    "scene",
    "beat",
    "clock",
    "clue",
    "source_page",
    "field",
    "gm_move",
]
SessionLogMetadataValue = str | int | float | bool


class FactProvenance(BaseModel):
    """Audit link retained when a reviewed model candidate becomes a fact."""

    model_config = ConfigDict(extra="forbid")

    origin: Literal["shadow_promotion"] = "shadow_promotion"
    candidate_id: str = Field(pattern=r"^shadow_candidate_[a-z0-9_-]+$")
    task_id: str = Field(pattern=r"^shadow_task_[a-z0-9_-]+$")
    run_id: str = Field(pattern=r"^shadow_run_[a-z0-9_-]+$")
    review_id: str = Field(pattern=r"^shadow_review_[a-z0-9_-]+$")
    promoted_at: str = Field(min_length=1)


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    page: int = Field(ge=1)
    locator: str | None = None
    quote: str | None = None
    excerpt: str | None = Field(default=None, max_length=1200)
    region: str | None = None
    source_version: str | None = None

    @field_validator("file", "locator", "quote", "excerpt", "region", "source_version")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class SourceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^fact_[a-z][a-z0-9_]*$")
    campaign_id: int | None = None
    # `source` remains the editable primary reference for existing workbench data.
    # New callers should use `source_refs` to retain all supporting evidence.
    source: SourceRef | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    evidence_status: EvidenceStatus | None = None
    text: str = Field(min_length=1)
    kind: FactKind
    visibility: FactVisibility
    links: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    provenance: FactProvenance | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_source_refs(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        raw_source = data.get("source")
        raw_refs = data.get("source_refs")
        if raw_refs is None:
            refs: list[Any] = []
        elif isinstance(raw_refs, list):
            refs = list(raw_refs)
        else:
            return data

        # A legacy workbench serializes both fields after an edit. Its primary
        # source replaces only the first reference so later references survive.
        if raw_source is not None:
            if refs:
                refs[0] = raw_source
            else:
                refs = [raw_source]
        if raw_refs is None or raw_source is not None:
            data["source_refs"] = refs
        if data.get("source") is None and refs:
            data["source"] = refs[0]
        return data

    @model_validator(mode="after")
    def evidence_is_explicit_and_consistent(self) -> "SourceFact":
        if self.source is not None:
            if self.source_refs:
                self.source_refs[0] = self.source
            else:
                self.source_refs = [self.source]
        elif self.source_refs:
            self.source = self.source_refs[0]

        if self.evidence_status is None:
            if self.visibility == "inferred":
                self.evidence_status = "inference"
            elif self.visibility == "gm_suggestion":
                self.evidence_status = "gm_authored"
            elif self.source_refs:
                self.evidence_status = "source_fact"
            else:
                raise ValueError(
                    "facts without source_refs must explicitly declare evidence_status"
                )

        if self.evidence_status in {"source_fact", "inference"} and not self.source_refs:
            raise ValueError(
                f"{self.evidence_status} facts require at least one source_ref"
            )
        return self

    @field_validator("text", "notes")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("links")
    @classmethod
    def validate_link_shape(cls, value: list[str]) -> list[str]:
        invalid = [item for item in value if not item.startswith("fact_")]
        if invalid:
            raise ValueError(f"fact links must use fact_* ids: {invalid}")
        return value


class CardDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1)
    description: str = ""
    required_fields: list[str] = Field(default_factory=list)
    optional_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def fields_are_unique(self) -> "CardDefinition":
        duplicate = set(self.required_fields) & set(self.optional_fields)
        if duplicate:
            raise ValueError(f"fields are both required and optional: {sorted(duplicate)}")
        return self


class RuleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    profile_kind: ProfileKind = "runtime"
    summary: str = ""
    card_definitions: list[CardDefinition]
    risk_axes: list[str] = Field(default_factory=list)
    failure_moves: list[str] = Field(default_factory=list)
    gm_moves: list[str] = Field(default_factory=list)
    prompts: dict[str, str] = Field(default_factory=dict)
    scene_guidance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def card_types_are_unique(self) -> "RuleProfile":
        types = [definition.type for definition in self.card_definitions]
        duplicates = sorted({item for item in types if types.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate card types: {duplicates}")
        return self

    def definition_for(self, card_type: str) -> CardDefinition:
        for definition in self.card_definitions:
            if definition.type == card_type:
                return definition
        raise ValueError(f"profile {self.id} does not define card type {card_type!r}")


class CardGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)


class DerivedCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^card_[a-z][a-z0-9_]*$")
    campaign_id: int | None = None
    profile_id: str = Field(min_length=1)
    type: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    subtitle: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, list[str]] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)
    generation: CardGeneration | None = None
    edit_state: CardEditState = "generated"

    @field_validator("title", "subtitle")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("open_questions")
    @classmethod
    def normalize_open_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class DisplayMaterialLink(BaseModel):
    """A confirmed runtime association to either a location card or a beat."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(pattern=r"^plan_[a-z][a-z0-9_-]*$")
    card_id: str | None = Field(default=None, pattern=r"^card_[a-z0-9_-]+$")
    beat_id: str | None = Field(default=None, pattern=r"^beat_[a-z][a-z0-9_-]*$")

    @model_validator(mode="after")
    def has_one_runtime_target(self) -> "DisplayMaterialLink":
        if (self.card_id is None) == (self.beat_id is None):
            raise ValueError("display material link needs exactly one card or beat target")
        return self


class DisplayMaterial(BaseModel):
    """A source-page asset that may be shown to players without rewriting it."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^material_[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=160)
    source_fact_ids: list[str] = Field(min_length=1, max_length=50)
    source_refs: list[SourceRef] = Field(min_length=1, max_length=100)
    gm_notes: str = Field(default="", max_length=2000)
    links: list[DisplayMaterialLink] = Field(default_factory=list, max_length=100)

    @field_validator("title", "gm_notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_fact_ids")
    @classmethod
    def unique_source_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("display material source fact ids must be unique")
        return cleaned

    @model_validator(mode="after")
    def links_are_unique(self) -> "DisplayMaterial":
        keys = [(link.plan_id, link.card_id, link.beat_id) for link in self.links]
        if len(keys) != len(set(keys)):
            raise ValueError("display material links must be unique")
        return self


class ArtifactDraftJob(BaseModel):
    """Persisted status for a potentially long-running artifact generation request."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^artifact_job_[a-z0-9_-]+$")
    workspace_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    model_id: str = Field(min_length=1, max_length=160)
    fake_model: bool = False
    status: ArtifactJobStatus = "queued"
    phase: ArtifactJobPhase = "queued"
    card_count: int = Field(default=0, ge=0)
    fact_count: int = Field(default=0, ge=0)
    batch_count: int = Field(default=0, ge=0)
    completed_batches: int = Field(default=0, ge=0)
    unit_count: int = Field(default=0, ge=0)
    planned_card_count: int = Field(default=0, ge=0)
    completed_cards: int = Field(default=0, ge=0)
    input_fingerprint: str | None = Field(default=None, max_length=160)
    budget_method: str = Field(default="conservative-cjk-v1", min_length=1, max_length=80)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    open_question_count: int = Field(default=0, ge=0)
    open_question_overflow_count: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=2000)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def normalize_question_summary(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw_questions = data.get("open_questions")
        preview = (
            list(dict.fromkeys(item.strip() for item in raw_questions if isinstance(item, str) and item.strip()))
            if isinstance(raw_questions, list)
            else []
        )
        count = data.setdefault("open_question_count", len(preview))
        if "open_question_overflow_count" not in data and isinstance(count, int):
            data["open_question_overflow_count"] = max(0, count - len(preview))
        return data

    @field_validator("model_id", "error")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("open_questions")
    @classmethod
    def normalize_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @model_validator(mode="after")
    def batch_progress_is_consistent(self) -> "ArtifactDraftJob":
        if self.completed_batches > self.batch_count:
            raise ValueError("completed_batches cannot exceed batch_count")
        if self.completed_cards > self.planned_card_count:
            raise ValueError("completed_cards cannot exceed planned_card_count")
        if self.open_question_count < len(self.open_questions):
            raise ValueError("open_question_count cannot be below the displayed preview")
        if self.open_question_overflow_count != (
            self.open_question_count - len(self.open_questions)
        ):
            raise ValueError("open question overflow count must match the displayed preview")
        return self


class SceneBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^beat_[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    mode: BeatMode = "investigation"
    source_pages: list[int] = Field(default_factory=list)
    framing: str = Field(min_length=1)
    situation: str = Field(min_length=1)
    rule_focus: str | None = None
    card_ids: list[str] = Field(default_factory=list)
    # Display materials are referenced separately from GM-facing cards. They
    # are populated only after an explicit scene/beat association.
    display_material_ids: list[str] = Field(default_factory=list)
    reveal_fact_ids: list[str] = Field(default_factory=list)
    soft_cues: list[str] = Field(default_factory=list)
    hard_cues: list[str] = Field(default_factory=list)
    question_prompts: list[str] = Field(default_factory=list)
    exit_when: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("title", "framing", "situation", "rule_focus", "notes")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ScenePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^plan_[a-z][a-z0-9_-]*$")
    profile_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    subtitle: str | None = None
    source_file: str = Field(min_length=1)
    source_pages: list[int] = Field(default_factory=list)
    premise: str = Field(min_length=1)
    card_ids: list[str] = Field(default_factory=list)
    navigation_mode: RuntimeNavigationMode = "beat"
    location_card_ids: list[str] = Field(default_factory=list)
    beats: list[SceneBeat] = Field(default_factory=list)
    exit_states: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("title", "subtitle", "premise", "notes")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def navigation_is_consistent(self) -> "ScenePlan":
        beat_ids = [beat.id for beat in self.beats]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError(f"duplicate scene beat id in plan {self.id}")
        if len(self.location_card_ids) != len(set(self.location_card_ids)):
            raise ValueError(f"duplicate location card id in plan {self.id}")
        if any(card_id not in self.card_ids for card_id in self.location_card_ids):
            raise ValueError(f"location cards must belong to plan {self.id}")
        if self.navigation_mode == "location" and self.beats:
            raise ValueError(f"location-led plan {self.id} cannot contain linear beats")
        if self.navigation_mode == "beat" and not self.beats:
            raise ValueError(f"beat-led plan {self.id} needs at least one beat")
        return self


class SessionLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^log_[a-z0-9_-]+$")
    kind: SessionLogKind = "manual_note"
    text: str = Field(min_length=1, max_length=2000)
    created_at: str = Field(min_length=1)
    subject_type: SessionLogSubject | None = None
    subject_id: str | None = None
    plan_id: str | None = None
    card_id: str | None = None
    beat_id: str | None = None
    metadata: dict[str, SessionLogMetadataValue] = Field(default_factory=dict)

    @field_validator("text", "subject_type", "subject_id", "plan_id", "card_id", "beat_id")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(
        cls, value: dict[str, SessionLogMetadataValue]
    ) -> dict[str, SessionLogMetadataValue]:
        if len(value) > 12:
            raise ValueError("session log metadata cannot contain more than 12 keys")
        for key, item in value.items():
            if not key.strip() or len(key) > 80:
                raise ValueError("session log metadata keys must be short non-empty strings")
            if isinstance(item, str) and len(item) > 500:
                raise ValueError("session log metadata string values must be at most 500 characters")
        return value


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    example_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    current_plan_id: str | None = None
    current_beat_id: str | None = None
    current_card_id: str | None = None
    trigger_states: dict[str, TriggerState] = Field(default_factory=dict)
    revealed_clue_keys: list[str] = Field(default_factory=list)
    clock_stages: dict[str, int] = Field(default_factory=dict)
    log: list[SessionLogEntry] = Field(default_factory=list)
    notes: str = ""

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str) -> str:
        return value.strip()


class ExampleBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    description: str = ""
    profile_ids: list[str] = Field(min_length=1)
    facts: list[SourceFact]
    cards: list[DerivedCard]
    display_materials: list[DisplayMaterial] = Field(default_factory=list)
    plans: list[ScenePlan] = Field(default_factory=list)
