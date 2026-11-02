"""Generate source-bound preparation artifact drafts from promoted shelf facts."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import prep, storage
from .fact_input import estimate_tokens as _estimate_tokens
from .fact_input import fact_batches as shared_fact_batches
from .fact_input import fact_payload as _fact_payload_from_facts
from .persistent_step import run_json_step as _shared_run_json_step
from .persistent_step import stable_hash as _stable_hash
from .llm import RequestBudget, make_client
from .planning_transport import PlanningTransport
from ..domain import PrepUnit, RuntimeBrief, load_profiles, validate_bundle
from ..domain.models import (
    ArtifactDraftJob,
    CardGeneration,
    DerivedCard,
    ExampleBundle,
    RuleProfile,
)


PROMPT_VERSION = "prep-artifact-draft-v15"
GLOBAL_PLAN_PROMPT_VERSION = "prep-artifact-global-plan-v22"
LOCAL_DIGEST_PROMPT_VERSION = "prep-artifact-local-digest-v1"
# Both versions participate in durable step hashes, so retries regenerate steps
# produced under older semantics instead of reusing their successful outputs.

# A board says how to organise preparation material; the published game it borrows
# methods from is design reference, not the rules the GM plays with. Generation
# messages therefore carry the card contract plus this one boundary, never the
# board's rule-flavoured metadata (summary, risk axes, failure/GM moves, prompts).
BOARD_REFERENCE_BOUNDARY = (
    "BOARD_IS_ORGANISATION_NOT_RULES: BOARD_PROFILE_JSON describes how to organise "
    "preparation material for a GM. It is a design reference, not the rules used at the "
    "table. Never add or convert checks, statistics, difficulty, levels, armour, hit "
    "points, or adversary types that the supplied facts do not state. Preserve supplied "
    "rule statistics and mechanics as cited source material in supported source fields; "
    "when a card has no such field, keep the original rule text accessible through its "
    "cited facts rather than inventing a field. Preserve source-backed non-mechanical "
    "quantities, dates, times, and distances in appropriate narrative fields.\n"
)


def _board_prompt_payload(
    profile: RuleProfile,
    *,
    definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The part of a board a generation message may see: structure, never rule mechanics."""
    payload: dict[str, Any] = {
        "id": profile.id,
        "profile_kind": profile.profile_kind,
        "runtime_navigation_mode": profile.runtime_navigation_mode,
        "runtime_anchor_type": profile.runtime_anchor_type,
    }
    if definitions is not None:
        payload["card_definitions"] = definitions
    return payload
SOURCE_SEMANTIC_GUIDANCE = (
    "Distinguish source play reports and examples from default current state. Preserve whether "
    "a passage describes established setup, historical background, one group's past play, or a "
    "conditional example. Do not promote reported player choices, discoveries, victories, deaths, "
    "or other outcomes into opening conditions, current situation, or inevitable future events. "
    "Keep example outcomes conditional and background historical; when the source does not "
    "establish a starting state, state what the material assumes in assumed_state rather than "
    "asking whether it happened. Which optional events have occurred, and where a table stands in "
    "the timeline, depend on player action and differ at every table, so never turn them into "
    "review questions. Reserve open_questions for contradictions, terminology gaps, or missing "
    "framing that exist inside the source itself. "
    "Preserve reusable actionable capabilities from included facts, including what an NPC, "
    "creature, object, or environment can do, its triggers, limits, costs, and counterplay when "
    "supplied. An illustrative use may support a reusable capability only when the source "
    "establishes that capability; it does not establish that the example happened in this game. "
    "A fact ID alone is not coverage of an actionable capability: carry it into the local summary, "
    "select it for the relevant card and its purpose/focus, and reflect it in appropriate declared "
    "card fields during materialization. Do not bury it solely in reference facts or background. "
    "Preserve original source statistics and their fact citations where supported and allowed by "
    "the card definition; do not invent or convert statistics to the target rules. "
)
CLOCK_SEMANTIC_GUIDANCE = (
    "Each clock must track a single evolving pressure with a coherent starting state, "
    "advancement triggers, observable escalation stages, and an endpoint consequence. Stages "
    "must be changes in that same pressure, not a room itinerary, clue sequence, historical "
    "chronology, fixed player route, attack counts, or numbered actions. Keep rooms in location "
    "or environment material, clues in discoveries, and history in background. Do not create "
    "a clock merely to package a sequence; without a supported evolving pressure, use the "
    "appropriate non-clock material or record an open question. Clock structure may be a "
    "GM-facing transformation, but factual triggers and consequences require supplied fact "
    "citations. Do not set current_stage from a play report's completed outcome or assume "
    "players will advance the clock along a predetermined route. "
)
MATERIALIZATION_FIDELITY_GUIDANCE = (
    "Required and source-backed are different things. When the source does not "
    "establish a required field, the field may still be present and non-empty by "
    "saying plainly what the source leaves open; that sentence is a note about the "
    "source and needs no field_sources. A working assumption covers only the state "
    "this material starts in. It must never add a motive, a reason to cooperate, a "
    "condition for giving information, an access rule, an opening procedure, staff "
    "behaviour, or any other decision the source does not state. When the source "
    "hedges (看起来, 据报道, 可能, 据说), keep the hedge in the field; never restate it "
    "as a flat assertion, and never supply the detail the hedge leaves open, such as "
    "which ship, which door or which clerk. A person's card carries that one person's "
    "stated goal; do not invent what they want in exchange for knowledge. A place's "
    "card carries only what the source says about arriving, being inside, and using "
    "it. One NPC card is one playable person: another person may appear in its "
    "fields as a relationship, never as a second subject of the card. "
)
PERSON_SEMANTIC_GUIDANCE = (
    "One NPC card carries exactly one playable person. Two people the source "
    "introduces separately never share a card, and a card about a pair or a group is "
    "not an NPC card: emit one card per person and leave the relationship to the "
    "overview or to each card's own people fields. Two cards about one person under "
    "different titles are a duplicate of one card, not two people. "
)
MAX_FACT_INPUT_CHARS = 120_000
# Keep individual model requests comfortably below the hard input ceiling.  A
# single artifact job may contain several of these batches; the final cards are
# merged and validated before they are written to the shelf.
# This is a technical guard against an accidentally unbounded model response,
# not a product rule that may force useful entities into one card.
# Bound individual model responses, not the combined output of all chapters.
MAX_DRAFT_CARDS = 120
MAX_LOCAL_UNITS = 120
MAX_LOCAL_OPEN_QUESTIONS = 120
MAX_JOB_OPEN_QUESTION_PREVIEW = 50
MAX_CARD_FACT_INPUT_CHARS = 72_000
MAX_CARD_FACT_INPUT_TOKENS = 22_000
# An upstream outage used to be retried once per planned card; stop the job
# instead of issuing hundreds of doomed requests, and let the user retry.
MAX_CONSECUTIVE_MATERIALIZE_FAILURES = 5
# For the blocked reality-horror batch, three sampled 16-fact windows produced complete,
# validated JSON at 12k with reasoning_effort=low. The same first window failed twice
# when no effort was sent, and 31 facts were truncated at 12k in an earlier probe. Treat
# these as conservative runtime transport ceilings, not narrative boundaries or guarantees
# for every model/source batch. A single long fact may exceed the target token size but
# still respects the shared hard input limit.
LOCAL_DIGEST_MAX_FACTS_PER_BATCH = 16
LOCAL_DIGEST_MAX_FACT_TOKENS = 3200
LOCAL_DIGEST_MAX_TOKENS = 7000
LOCAL_DIGEST_MAX_TOKENS_CEILING = 12000
RUNTIME_LOCAL_DIGEST_MAX_TOKENS = 12000
GLOBAL_PLAN_MAX_TOKENS = 12000
# The immediately previous prompt version can have a durable, known length failure.
# Its input hash differs only by this version marker after a prompt-only upgrade.
GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION = "prep-artifact-global-plan-v18"
# A failed full-scope planning request may be recovered through several durable
# windows.  The cap is shared by the parent and all children, so a provider that
# keeps exhausting the response budget cannot turn one retry into an unbounded run.
GLOBAL_PLAN_MAX_REQUESTS = 16
GLOBAL_PLAN_WINDOW_MAX_CHARS = 36_000
# A window that can no longer be split gets ONE larger budget instead of failing, but
# only when the upstream itself reported finish_reason=length.  The value follows the
# 2026-11-01 leaf evidence: the same production input came back truncated at 12,000 and
# produced a valid window plan at 16,000.  Ordinary requests keep their budgets; this
# ceiling applies to one retry of an unsplittable window through the shared
# RequestBudget, so it counts against the same request cap and happens at most once per
# step.  A reply that is still truncated stops the step instead of raising again.
GLOBAL_PLAN_WINDOW_MAX_TOKENS = 16_000
ARTIFACT_REQUEST_TIMEOUT = 300
ARTIFACT_MAX_TOKENS = 9000
# Cross-chapter identity reconciliation runs once, after every chapter plan has
# been collected.  A single request sees all candidates, so it cannot disagree
# with itself the way per-chapter planning requests inevitably would.
IDENTITY_RECONCILE_PROMPT_VERSION = "prep-artifact-identity-reconcile-v2"
IDENTITY_RECONCILE_MAX_TOKENS = 8000
IDENTITY_GROUP_CHAR_BUDGET = 40_000
# Keep a chunk's plan count well below IdentityReconciliationPayload.clusters
# max_length, because validation requires every candidate to be covered once.
IDENTITY_MAX_PLANS_PER_CHUNK = 120
# Same-scope candidates: cards of one type that share at least one fact inside one
# prep scope can be one entity split under two titles.  Sharing a fact is only a
# candidate signal; the reconcile prompt makes the decision from the fact text, so
# this rule may over-group without ever merging on its own.
IDENTITY_SAME_SCOPE_TYPES = frozenset({"location", "npc", "threat"})
# The reconcile step decides from the fact text, so each candidate carries a bounded
# slice of its fact closure instead of the whole thing.
IDENTITY_CANDIDATE_MAX_FACTS = 8
IDENTITY_CANDIDATE_FACT_CHARS = 240
# A component bigger than one chunk cannot be judged as a whole, so it is
# reported instead of split.  Real components on the 8-chapter material are 16,
# 27, 59 and 64 plans: the earlier cap of 40 silently dropped the two largest,
# including the environment group that carries the mixed-location problem.
MAX_IDENTITY_CANDIDATE_PLANS = IDENTITY_MAX_PLANS_PER_CHUNK
# A merged card must stay materializable, and the binding constraint is the
# character/token budget on its fact closure, not a fact count: short facts
# legitimately allow many more citations than long ones.  The merge guard now
# measures the closure against MAX_CARD_FACT_INPUT_CHARS and
# MAX_CARD_FACT_INPUT_TOKENS directly, so this value is only a structural ceiling
# on the card contracts: the guard prefers the budget and falls back to this count
# only when it is given no fact table.  Measured, the leanest serialized fact
# payload is about 300 characters and 80 tokens, so the character budget binds at
# roughly 240 facts; 600 sits well above that.
MAX_MERGED_FACT_IDS = 600


class ArtifactDraftRequest(BaseModel):
    """The completed preparation task already owns the artifact source scope."""

    model_config = ConfigDict(extra="forbid")
LIST_FIELDS = {
    "immediate_actions",
    "table_questions",
    "direct_clues",
    "hidden_clues",
    "gm_moves",
    "exit_conditions",
    "npc_hooks",
    "clock_links",
    "noncombat_exits",
    "stages",
    "usable_features",
    "hazards",
    "positioning",
    "sensory_details",
    "environment_changes",
    "signature_actions",
    "resolutions",
    "entry_points",
    "discoveries",
    "relevant_characters",
    "major_threads",
    "endings",
    "key_people",
    "cross_location_clues",
    "first_triggers",
    "consequences",
    "display_materials",
    "return_changes",
    "major_conflicts",
    "opening_conditions",
    "possible_endings",
    "factions",
    "cross_environment_consequences",
    "impulses",
    "gm_responses",
    "phase_changes",
    "exit_outcomes",
    "triggers",
    "potential_adversaries",
    "motives",
    "attitude_changes",
    "choices",
    "complications",
}
RUNTIME_ANCHOR_TYPES = {"location", "environment"}
GM_AUTHORED_FIELDS = {"gm_moves", "gm_responses"}

_active_jobs: set[str] = set()
_active_jobs_lock = threading.Lock()


class ArtifactGenerationError(ValueError):
    """Raised when facts or model output cannot produce a valid artifact set."""


class ArtifactJobConflictError(RuntimeError):
    """Raised when another incompatible artifact job is already active."""


class _OpenQuestionSummary:
    """Keep a bounded job preview while retaining an exact run-local total."""

    def __init__(self) -> None:
        self._questions: dict[str, None] = {}

    def add(self, values: list[str]) -> None:
        for value in values:
            if not isinstance(value, str):
                continue
            question = value.strip()
            if question:
                self._questions.setdefault(question, None)

    @property
    def preview(self) -> list[str]:
        return list(self._questions)[:MAX_JOB_OPEN_QUESTION_PREVIEW]

    @property
    def count(self) -> int:
        return len(self._questions)

    @property
    def overflow_count(self) -> int:
        return self.count - len(self.preview)


def _question_summary_fields(summary: _OpenQuestionSummary) -> dict[str, Any]:
    return {
        "open_questions": summary.preview,
        "open_question_count": summary.count,
        "open_question_overflow_count": summary.overflow_count,
    }


def _artifact_resume_saved_response(stage: str, validator: Callable[[Any], dict[str, Any]]):
    """A saved global plan is reusable only when a coverage gap is its whole problem."""
    if stage != "global_plan":
        return None

    def hook(raw: Any) -> Any:
        try:
            validator(raw)
        except PlanCoverageError:
            return raw
        except Exception:  # noqa: BLE001 - unusable saved response means a fresh call
            return None
        return None

    return hook


def _run_json_step(
    job: ArtifactDraftJob,
    *,
    stage: str,
    step_index: int,
    input_payload: dict[str, Any],
    input_refs: dict[str, Any],
    messages: list[dict[str, str]],
    client: Any,
    max_tokens: int,
    validator: Callable[[Any], dict[str, Any]],
    chat_kwargs: dict[str, Any] | None = None,
    step_budget: RequestBudget | None = None,
    budget_identity: str | None = None,
    max_budget: int | None = None,
    allow_correction: bool = True,
    recover_error: Callable[[Any, Exception], Any] | None = None,
    recover_existing_failure: Callable[[Any, Exception], bool] | None = None,
    startup_recovery: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Artifact binding of the shared durable step.

    The mechanism lives in `persistent_step`. What stays here is what belongs to
    artifacts: reusing a saved plan whose only defect is missing coverage, repairing
    that coverage on the original plan instead of rewriting it, and treating the
    coverage error as fatal rather than taking the bounded correction retry.
    """

    step_budget = step_budget if step_budget is not None else RequestBudget()

    def recover(raw: Any, error: Exception) -> Any:
        if not isinstance(error, PlanCoverageError):
            raise error
        return _complete_plan_coverage(
            job, stage, step_index, raw, error, client, validator,
            step_budget=step_budget,
        )

    return _shared_run_json_step(
        job,
        stage=stage,
        step_index=step_index,
        input_payload=input_payload,
        input_refs=input_refs,
        messages=messages,
        client=client,
        max_tokens=max_tokens,
        validator=validator,
        request_timeout=ARTIFACT_REQUEST_TIMEOUT,
        resume_saved_response=_artifact_resume_saved_response(stage, validator),
        recover_validation=recover,
        fatal_error_types=(PlanCoverageError,),
        error_factory=ArtifactGenerationError,
        chat_kwargs=chat_kwargs,
        step_budget=step_budget,
        budget_identity=budget_identity,
        max_budget=max_budget,
        allow_correction=allow_correction,
        recover_error=recover_error,
        recover_existing_failure=recover_existing_failure,
        startup_recovery=startup_recovery,
    )


def recover_interrupted_artifact_jobs() -> int:
    """Mark jobs left queued/running by a previous process as retryable failures."""
    recovered = 0
    for raw in storage.list_artifact_jobs():
        try:
            job = ArtifactDraftJob.model_validate(raw)
        except ValidationError:
            continue
        if job.status not in {"queued", "running"}:
            continue
        with _active_jobs_lock:
            if job.id in _active_jobs:
                continue
        _save_job(
            job,
            status="failed",
            error="服务重启，之前的产物生成任务已停止；请重新尝试。",
        )
        recovered += 1
    return recovered


def _job_from_store(job_id: str) -> ArtifactDraftJob:
    raw = storage.load_artifact_job(job_id)
    if raw is None:
        raise ArtifactGenerationError(f"unknown artifact generation job: {job_id}")
    try:
        job = ArtifactDraftJob.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(
            f"stored artifact generation job {job_id} is invalid: {error}"
        ) from error
    return job


def latest_artifact_job(
    workspace_id: str,
    *,
    profile_id: str | None = None,
    model_id: str | None = None,
    fake_model: bool | None = None,
    status: str | None = None,
) -> ArtifactDraftJob | None:
    """Return the newest valid job matching the supplied lifecycle filters."""
    for job in list_artifact_jobs(
        workspace_id,
        profile_id=profile_id,
        model_id=model_id,
        fake_model=fake_model,
        status=status,
    ):
        return job
    return None


def list_artifact_jobs(
    workspace_id: str,
    *,
    profile_id: str | None = None,
    model_id: str | None = None,
    fake_model: bool | None = None,
    status: str | None = None,
) -> list[ArtifactDraftJob]:
    """Return all valid durable full-board jobs matching lifecycle filters."""
    jobs: list[ArtifactDraftJob] = []
    for raw in storage.list_artifact_jobs(workspace_id):
        try:
            job = ArtifactDraftJob.model_validate(raw)
        except ValidationError:
            continue
        if profile_id is not None and job.profile_id != profile_id:
            continue
        if model_id is not None and job.model_id != model_id:
            continue
        if fake_model is not None and job.fake_model != fake_model:
            continue
        if status is not None and job.status != status:
            continue
        jobs.append(job)
    return jobs


def select_artifact_job(
    jobs: list[ArtifactDraftJob],
    *,
    profile_id: str | None = None,
) -> ArtifactDraftJob | None:
    """Choose the job the workbench should foreground without losing history.

    Active work wins, then failed work, then the newest completed record. A
    preferred profile breaks ties so a stale job from another board does not
    take over a current shelf while the complete list remains available to the
    UI.
    """
    if not jobs:
        return None
    status_rank = {"running": 0, "queued": 1, "failed": 2, "completed": 3}

    def profile_rank(job: ArtifactDraftJob) -> int:
        return 0 if profile_id is None or job.profile_id == profile_id else 1

    return min(
        enumerate(jobs),
        key=lambda pair: (
            status_rank.get(pair[1].status, 9),
            profile_rank(pair[1]),
            pair[0],
        ),
    )[1]


def create_artifact_job(
    workspace_id: str,
    profile_id: str,
    *,
    model_id: str,
    fake_model: bool,
    prep_unit_id: str | None = None,
) -> tuple[ArtifactDraftJob, bool]:
    """Create one durable, full-scope artifact request."""
    with _active_jobs_lock:
        # A failed build is the current queue's retryable state, not a reason
        # to create another parallel queue. Explicit retry reuses its job id.
        failed = [
            job
            for job in list_artifact_jobs(workspace_id)
            if job.profile_id == profile_id and job.status == "failed"
        ]
        if failed:
            return max(failed, key=lambda item: item.updated_at), False
        active = next(
            (
                job
                for job in list_artifact_jobs(workspace_id)
                if job.status in {"queued", "running"}
            ),
            None,
        )
        if active is not None:
            same_request = (
                active.profile_id == profile_id
                and active.prep_unit_id == prep_unit_id
                and active.model_id == model_id
                and active.fake_model == fake_model
            )
            if same_request:
                return active, False
            raise ArtifactJobConflictError("当前已有整板生成任务在运行，请等待该任务完成后再试")
        timestamp = storage.now()
        job = ArtifactDraftJob(
            id=f"artifact_job_{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            profile_id=profile_id,
            prep_unit_id=prep_unit_id,
            model_id=model_id,
            fake_model=fake_model,
            status="queued",
            created_at=timestamp,
            updated_at=timestamp,
        )
        storage.create_artifact_job(job.model_dump(mode="json"))
        return job, True


def retry_artifact_job(
    job_id: str,
    *,
    model_id: str | None = None,
    fake_model: bool | None = None,
) -> tuple[ArtifactDraftJob, bool]:
    """Requeue one failed job with the currently selected model.

    The request scope and durable job ID remain stable, while the model mode is
    refreshed at the retry boundary.  Callers may pass an explicit config
    snapshot; when omitted, the persisted application config is used.
    """
    with _active_jobs_lock:
        job = _job_from_store(job_id)
        if job.status != "failed":
            return job, False
        active = next(
            (
                candidate
                for candidate in list_artifact_jobs(job.workspace_id)
                if candidate.status in {"queued", "running"}
            ),
            None,
        )
        if active is not None:
            raise ArtifactJobConflictError(
                "当前已有整板生成任务在运行，暂不能重试；请等待该任务完成后再试"
            )
        config = storage.get_config()
        retry_model_id = model_id if model_id is not None else config["model"]
        retry_fake_model = fake_model if fake_model is not None else config["fake"]
        return _save_job(
            job,
            status="queued",
            phase="queued",
            error=None,
            model_id=retry_model_id,
            fake_model=retry_fake_model,
        ), True


def has_profile_artifacts(
    bundle: ExampleBundle, profile_id: str, prep_unit_id: str | None = None
) -> bool:
    """Return whether a bookshelf already contains cards for one profile."""
    return any(
        card.profile_id == profile_id
        and (prep_unit_id is None or card.prep_unit_id == prep_unit_id)
        for card in bundle.cards
    )


def get_artifact_job(job_id: str) -> ArtifactDraftJob:
    return _job_from_store(job_id)


def _save_job(job: ArtifactDraftJob, **updates: Any) -> ArtifactDraftJob:
    data = job.model_dump(mode="json")
    data.update(updates)
    data["updated_at"] = storage.now()
    updated = ArtifactDraftJob.model_validate(data)
    storage.save_artifact_job(updated.model_dump(mode="json"))
    return updated


def execute_artifact_job(
    job_id: str,
    *,
    workspace_id: str,
    profile_id: str,
) -> None:
    """Run a queued request outside the HTTP request and persist its final state."""
    with _active_jobs_lock:
        if job_id in _active_jobs:
            return
        _active_jobs.add(job_id)
    try:
        job = _job_from_store(job_id)
        if job.status not in {"queued", "running"}:
            return
        job = _save_job(job, status="running", error=None)
        saved = storage.load_domain_bundle(workspace_id)
        if not saved:
            raise ArtifactGenerationError("书架工作区不存在")
        bundle = ExampleBundle.model_validate(saved[0])
        profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
        validate_bundle(bundle, profiles)
        profile = profiles.get(profile_id)
        if profile is None:
            raise ArtifactGenerationError("当前书架没有可用的备团板块")
        if has_profile_artifacts(bundle, profile_id):
            raise ArtifactGenerationError(
                "当前板块已经有备团产物；请先完成现有草案的复核，不重复生成一套平行卡片"
            )
        client = make_client(model_id=job.model_id, force_fake=job.fake_model)
        scoped_facts = _facts_for_workspace(bundle, workspace_id)
        if profile_id == "daggerheart" and bundle.fact_dispositions:
            artifact_fact_ids = {
                item.fact_id
                for item in bundle.fact_dispositions
                if item.kind == "chapter"
            }
            scoped_facts = [
                fact for fact in scoped_facts if fact.id in artifact_fact_ids
            ]
        if not scoped_facts:
            raise ArtifactGenerationError("书架没有可用于生成产物的已提升事实")
        batches = _artifact_batches(bundle, scoped_facts, profile=profile)
        input_fingerprint = _stable_hash(
            {
                "prompt_version": PROMPT_VERSION,
                "profile_id": profile_id,
                "model_id": job.model_id,
                "facts": _fact_payload_from_facts(scoped_facts),
            }
        )
        job = _save_job(
            job,
            fact_count=len(scoped_facts),
            batch_count=len(batches),
            completed_batches=0,
            card_count=0,
            unit_count=0,
            planning_scope_count=0,
            completed_planning_scopes=0,
            planned_card_count=0,
            completed_cards=0,
            input_fingerprint=input_fingerprint,
            budget_method="conservative-cjk-v1",
            open_questions=[],
            open_question_count=0,
            open_question_overflow_count=0,
        )

        runtime_brief: dict[str, Any] | None = None
        chapter_briefs: list[dict[str, Any]] = []
        # Runtime boards always pass through the planner so location coverage
        # and chapter-overview requirements are checked before materializing
        # cards. The compact direct path is reserved for non-runtime boards.
        if len(batches) == 1 and profile.profile_kind != "runtime":
            job = _save_job(job, phase="direct_generation")
            generated_cards, questions = _generate_direct_step(
                job,
                bundle,
                profile,
                client,
                scoped_facts,
                prep_unit_id=None,
            )
            question_summary = _OpenQuestionSummary()
            question_summary.add(questions)
            job = _save_job(
                _job_from_store(job_id),
                completed_batches=1,
                planned_card_count=len(generated_cards),
                completed_cards=len(generated_cards),
                card_count=len(generated_cards),
                **_question_summary_fields(question_summary),
            )
        else:
            (
                generated_cards,
                question_summary,
                job,
                runtime_brief,
                chapter_briefs,
            ) = _generate_hierarchical_artifacts(
                job,
                bundle,
                profile,
                client,
                scoped_facts,
                batches,
                prep_unit_id=None,
            )

        if not generated_cards:
            raise ArtifactGenerationError("模型没有返回可用的备团产物")
        if profile.profile_kind == "runtime" and not any(
            card.type in RUNTIME_ANCHOR_TYPES for card in generated_cards
        ):
            raise ArtifactGenerationError("运行板块的产物缺少场景或环境卡")
        job = _save_job(_job_from_store(job_id), phase="validating")
        # Reload after the model call so a concurrent manual edit cannot be overwritten.
        latest = storage.load_domain_bundle(workspace_id)
        if not latest:
            raise ArtifactGenerationError("书架工作区在生成期间被删除")
        bundle = ExampleBundle.model_validate(latest[0])
        validate_bundle(bundle, profiles)
        if has_profile_artifacts(bundle, profile_id):
            raise ArtifactGenerationError("生成期间已有同一板块的备团产物，请刷新书架后继续复核")
        if runtime_brief is not None:
            bundle.runtime_brief = RuntimeBrief.model_validate(runtime_brief)
        if profile_id == "daggerheart" and chapter_briefs:
            briefs_by_id = {
                item["chapter_id"]: RuntimeBrief.model_validate(item["brief"])
                for item in chapter_briefs
            }
            bundle.adventure_chapters = [
                chapter.model_copy(update={
                    "overview": briefs_by_id.get(chapter.id),
                    "reference_fact_ids": next(
                        item["reference_fact_ids"] for item in chapter_briefs if item["chapter_id"] == chapter.id
                    ),
                })
                for chapter in bundle.adventure_chapters
            ]
            bundle.runtime_brief = None
        bundle.cards.extend(generated_cards)
        validate_bundle(bundle, profiles)
        storage.save_domain_bundle(workspace_id, bundle.model_dump(mode="json", by_alias=True))
        _save_job(
            job,
            status="completed",
            phase="completed",
            card_count=len(generated_cards),
            fact_count=len(scoped_facts),
            batch_count=len(batches),
            completed_batches=len(batches),
            planned_card_count=len(generated_cards),
            completed_cards=len(generated_cards),
            **_question_summary_fields(question_summary),
            error=None,
        )
    except Exception as error:  # noqa: BLE001
        try:
            job = _job_from_store(job_id)
            _save_job(job, status="failed", error=str(error)[:2000])
        except Exception:
            pass
    finally:
        with _active_jobs_lock:
            _active_jobs.discard(job_id)


class DraftCardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=300)
    # Mirrors MAX_MERGED_FACT_IDS: a cross-chapter merge may legitimately hold
    # more facts than one chapter's original card, so the draft contract has to
    # accept what the merge produces or the card cannot be materialized.
    fact_ids: list[str] = Field(min_length=1, max_length=MAX_MERGED_FACT_IDS)
    fields: dict[str, Any]
    field_sources: dict[str, list[str]] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    runtime_group_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "subtitle")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("fact_ids")
    @classmethod
    def unique_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("fact_ids must be unique")
        return cleaned

    @field_validator("field_sources")
    @classmethod
    def normalize_field_sources(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {
            key: list(dict.fromkeys(item.strip() for item in refs if item.strip()))
            for key, refs in value.items()
        }

    @field_validator("open_questions")
    @classmethod
    def normalize_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("runtime_group_ids")
    @classmethod
    def normalize_runtime_group_ids(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        invalid = [item for item in cleaned if not re.fullmatch(r"encounter_[a-z0-9_-]+", item)]
        if invalid:
            raise ValueError(f"invalid runtime group ids: {invalid}")
        return cleaned


class DraftResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[DraftCardPayload] = Field(min_length=1, max_length=MAX_DRAFT_CARDS)
    open_questions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("open_questions")
    @classmethod
    def normalize_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


LOCAL_UNIT_KINDS = {
    "background",
    "scene",
    "location",
    "npc",
    "faction",
    "clue_cluster",
    "threat",
    "timeline",
    "outcome",
    "resource",
}


def _local_runtime_guidance(profile: RuleProfile) -> str:
    if profile.runtime_navigation_mode == "environment":
        return (
            "For environment-led fantasy material, organize around each playable situation, not "
            "every named place. A situation may be exploration, social interaction, traversal, "
            "combat, or an event. Preserve its narrative function, active motives, pressures, "
            "phase changes, nonviolent outcomes, and meaningful travel choices. Ordinary transit "
            "and background-only place names stay supporting context. Do not invent or convert "
            "game statistics. "
        )
    if profile.runtime_navigation_mode == "location":
        return (
            "For location material, create one location unit per distinct place that players can "
            "enter, search, revisit, seek help at, or trigger an event in. Put that place's "
            "canonical name in entity_keys; do not combine a dock, house, generator shed, workshop, "
            "searchable room, or other actionable sublocation merely because they share an island "
            "or building. "
        )
    return ""


class LocalUnitPayload(BaseModel):
    """A compact planning index that never replaces its cited source facts."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    fact_ids: list[str] = Field(min_length=1, max_length=200)
    entity_keys: list[str] = Field(default_factory=list, max_length=30)
    relationship_hints: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned not in LOCAL_UNIT_KINDS:
            raise ValueError(f"unknown local unit kind: {cleaned}")
        return cleaned

    @field_validator("title", "summary")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("fact_ids", "entity_keys", "relationship_hints", "open_questions")
    @classmethod
    def unique_text_items(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class LocalDigestResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[LocalUnitPayload] = Field(min_length=1, max_length=MAX_LOCAL_UNITS)
    open_questions: list[str] = Field(default_factory=list, max_length=MAX_LOCAL_OPEN_QUESTIONS)

    @field_validator("open_questions")
    @classmethod
    def normalize_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class PlannedCardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=800)
    fact_ids: list[str] = Field(min_length=1, max_length=MAX_MERGED_FACT_IDS)
    focus: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)
    runtime_groups: list[str] = Field(default_factory=list, max_length=20)
    chapter_ids: list[str] = Field(default_factory=list, max_length=24)
    story_beat_ids: list[str] = Field(default_factory=list, max_length=120)
    # The single playable situation this card covers.  An environment card must
    # never bundle unrelated situations: one card owns exactly one runtime state.
    situation: str = Field(default="", max_length=160)
    # Where this material sits relative to the situation the table starts in.
    case_scope: Literal["current", "related", "conditional_example"] = "current"

    @field_validator("title", "purpose", "situation")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("fact_ids", "focus", "open_questions", "runtime_groups", "chapter_ids", "story_beat_ids")
    @classmethod
    def unique_text_items(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class ChapterBriefPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(pattern=r"^chapter_[a-z0-9_-]+$")
    brief: RuntimeBrief
    reference_fact_ids: list[str] = Field(default_factory=list, max_length=2000)

    @field_validator("reference_fact_ids")
    @classmethod
    def unique_fact_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class GlobalPlanResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[PlannedCardPayload] = Field(min_length=1, max_length=MAX_DRAFT_CARDS)
    brief: RuntimeBrief | None = None
    chapter_briefs: list[ChapterBriefPayload] = Field(default_factory=list, max_length=24)
    global_reference_fact_ids: list[str] = Field(default_factory=list, max_length=2000)
    open_questions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("open_questions", "global_reference_fact_ids")
    @classmethod
    def normalize_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _flatten_one_level(value: Any) -> Any:
    """Flatten one JSON list wrapper while leaving deeper invalid shapes strict."""
    if not isinstance(value, list) or not any(isinstance(item, list) for item in value):
        return value
    flattened: list[Any] = []
    for item in value:
        flattened.extend(item if isinstance(item, list) else [item])
    return flattened


def _normalize_field_source_refs(value: Any) -> Any:
    """Collapse an item-to-sources map into the card's field-level provenance."""
    if not isinstance(value, dict):
        return _flatten_one_level(value)
    flattened: list[str] = []
    for refs in value.values():
        if isinstance(refs, str):
            flattened.append(refs)
            continue
        if not isinstance(refs, list):
            return value
        one_level = _flatten_one_level(refs)
        if not isinstance(one_level, list) or any(
            not isinstance(item, str) for item in one_level
        ):
            return value
        flattened.extend(one_level)
    return list(dict.fromkeys(flattened))


def _normalize_nested_field_provenance(card: dict[str, Any]) -> dict[str, Any]:
    """Flatten one common gateway shape without accepting unknown structures."""
    fields = card.get("fields")
    if not isinstance(fields, dict):
        return card
    normalized = dict(card)
    normalized_fields = dict(fields)
    if not isinstance(card.get("field_sources", {}), dict):
        return card
    normalized_sources = dict(card.get("field_sources") or {})
    changed = False
    for field_name, value in fields.items():
        if not isinstance(value, dict) or "value" not in value:
            continue
        if set(value) - {"value", "field_sources"}:
            continue
        normalized_fields[field_name] = value["value"]
        nested_sources = value.get("field_sources")
        if nested_sources is not None:
            existing_sources = normalized_sources.get(field_name)
            if existing_sources is None:
                normalized_sources[field_name] = nested_sources
            elif isinstance(existing_sources, list) and isinstance(nested_sources, list):
                normalized_sources[field_name] = [*existing_sources, *nested_sources]
            else:
                # Leave malformed provenance for the strict Pydantic validator.
                normalized_sources[field_name] = nested_sources
        changed = True
    if not changed:
        return card
    normalized["fields"] = normalized_fields
    normalized["field_sources"] = normalized_sources
    return normalized


def _normalize_list_item_provenance(card: dict[str, Any]) -> dict[str, Any]:
    """Unwrap one common object shape emitted for list-valued card fields."""
    fields = card.get("fields")
    if not isinstance(fields, dict):
        return card
    normalized = dict(card)
    normalized_fields = dict(fields)
    if not isinstance(card.get("field_sources", {}), dict):
        return card
    normalized_sources = dict(card.get("field_sources") or {})
    changed = False
    for field_name, value in fields.items():
        if not isinstance(value, list):
            continue
        unwrapped: list[Any] = []
        item_sources: list[Any] = []
        field_changed = False
        for item in value:
            if not isinstance(item, dict):
                unwrapped.append(item)
                continue
            keys = set(item)
            is_text_item = "text" in item and not keys - {"text", "field_sources"}
            is_named_item = {"name", "description"} <= keys and not keys - {
                "name",
                "description",
                "field_sources",
            }
            if not is_text_item and not is_named_item:
                unwrapped.append(item)
                continue
            if is_named_item:
                if not all(
                    isinstance(item[key], str) and item[key].strip()
                    for key in ("name", "description")
                ):
                    unwrapped.append(item)
                    continue
                unwrapped.append(f"{item['name'].strip()}：{item['description'].strip()}")
            else:
                unwrapped.append(item["text"])
            if "field_sources" in item:
                item_sources.append(item["field_sources"])
            field_changed = True
        if not field_changed:
            continue
        normalized_fields[field_name] = unwrapped
        existing_sources = normalized_sources.get(field_name)
        flattened_existing = _flatten_one_level(existing_sources)
        flattened_items = _flatten_one_level(item_sources)
        if existing_sources is None:
            normalized_sources[field_name] = flattened_items
        elif isinstance(flattened_existing, list) and isinstance(flattened_items, list):
            normalized_sources[field_name] = [*flattened_existing, *flattened_items]
        else:
            normalized_sources[field_name] = existing_sources
        changed = True
    if not changed:
        return card
    normalized["fields"] = normalized_fields
    normalized["field_sources"] = normalized_sources
    return normalized


def _normalize_global_plan_shape(raw: Any) -> Any:
    """Repair scalar list fields without weakening the plan schema."""
    if not isinstance(raw, dict) or not isinstance(raw.get("cards"), list):
        return raw
    normalized = dict(raw)
    cards: list[Any] = []
    for item in raw["cards"]:
        if not isinstance(item, dict):
            cards.append(item)
            continue
        card = dict(item)
        focus = card.get("focus")
        if isinstance(focus, str):
            card["focus"] = [focus.strip()] if focus.strip() else []
        chapter_ids = card.get("chapter_ids")
        if isinstance(chapter_ids, str):
            card["chapter_ids"] = [chapter_ids.strip()] if chapter_ids.strip() else []
        cards.append(card)
    normalized["cards"] = cards
    return normalized


def _unique_questions(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def display_material_fact_ids(bundle: ExampleBundle) -> set[str]:
    """Return facts explicitly reviewed as display-only.

    Showing a source and preparing from it are different roles.  A display material
    records that the GM may hand this source to the players; it says nothing about
    whether the source still contains leads, motives or places, so registering one must
    never remove the fact from planning.  Only an explicit ``display_material``
    disposition — an actual use decision from the structure review — makes a fact
    display-only, and a narrative disposition still wins, because the same letter can
    be shown to players and used to prepare a chapter.
    """
    narrative_ids = {
        item.fact_id
        for item in bundle.fact_dispositions
        if item.kind in {"chapter", "global_reference"}
    }
    display_only = {
        item.fact_id
        for item in bundle.fact_dispositions
        if item.kind == "display_material"
    }
    return display_only - narrative_ids


def _facts_for_workspace(
    bundle: ExampleBundle, workspace_id: str, unit: PrepUnit | None = None
) -> list[Any]:
    """Limit task-owned shelves to the source file and selected page scope.

    Facts participate in planning by default; only an explicit display-only
    disposition removes one (see ``display_material_fact_ids``).  Nothing here guesses
    from a fact's text, so a pure map and a letter with a clue are separated by an
    explicit decision rather than by a keyword, and a display material record does not
    change either of them.
    """
    task = prep.find_prep_job_by_workspace(workspace_id)
    display_only = display_material_fact_ids(bundle)
    usable = [
        fact
        for fact in bundle.facts
        if fact.evidence_status != "model_candidate" and fact.id not in display_only
    ]
    if task is None:
        return sorted(usable, key=lambda fact: _fact_source_sort_key(fact, None))
    scope = unit or task.scope
    allowed_pages = {page for span in scope.page_spans for page in span.pages()}
    selected: list[Any] = []
    for fact in usable:
        refs = list(fact.source_refs)
        if not refs or any(
            ref.file == scope.source_file and ref.page in allowed_pages for ref in refs
        ):
            selected.append(fact)
    return sorted(selected, key=lambda fact: _fact_source_sort_key(fact, scope.source_file))


def _fact_source_sort_key(fact: Any, preferred_file: str | None) -> tuple:
    """Order facts by their earliest useful source page before batching."""
    refs = list(getattr(fact, "source_refs", []) or [])
    if preferred_file:
        preferred = [ref for ref in refs if ref.file == preferred_file]
        if preferred:
            ref = min(preferred, key=lambda item: (item.page, item.file.casefold()))
            return (0, ref.page, ref.file.casefold(), fact.id)
    if refs:
        ref = min(refs, key=lambda item: (item.file.casefold(), item.page))
        return (1, ref.file.casefold(), ref.page, fact.id)
    return (2, "", 0, fact.id)


def _local_digest_messages(
    profile: RuleProfile,
    facts: list[dict[str, Any]],
    *,
    chapter: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    response_schema = LocalDigestResponsePayload.model_json_schema()
    response_schema["$defs"]["LocalUnitPayload"]["properties"]["kind"]["enum"] = sorted(LOCAL_UNIT_KINDS)
    system = (
        "You build a compact planning index from reviewed TRPG source facts. Return only one "
        "JSON object with keys units and open_questions. Every unit must contain kind, title, "
        "summary, fact_ids, entity_keys, relationship_hints, and open_questions. Use only supplied "
        "facts. Cover every supplied fact_id in at least one unit. Group facts into useful local "
        "material such as background, scenes, locations, NPCs, factions, traversal situations, clue clusters, "
        "threats, timelines, outcomes, or resources. Preserve actionable details and uncertainty, "
        "but do not write final cards. "
        + SOURCE_SEMANTIC_GUIDANCE
        + CLOCK_SEMANTIC_GUIDANCE
        + _local_runtime_guidance(profile)
        + BOARD_REFERENCE_BOUNDARY
        + "Do not invent cross-batch relationships. kind must be one of ALLOWED_UNIT_KINDS_JSON, "
        "not the source fact kind or a final card type. Event material belongs to scene, timeline, "
        "or outcome according to its narrative function; event is not a local unit kind. "
        "Follow RESPONSE_SCHEMA_JSON. All user-facing text must be Chinese."
    )
    user = (
        "[TASK:prep:artifact_local_digest]\n"
        f"PROMPT_VERSION_JSON={json.dumps(LOCAL_DIGEST_PROMPT_VERSION)}\n"
        f"CHAPTER_CONTEXT_JSON={json.dumps(chapter, ensure_ascii=False)}\n"
        f"BOARD_PROFILE_JSON={json.dumps(_board_prompt_payload(profile), ensure_ascii=False)}\n"
        f"ALLOWED_UNIT_KINDS_JSON={json.dumps(sorted(LOCAL_UNIT_KINDS), ensure_ascii=False)}\n"
        f"RESPONSE_SCHEMA_JSON={json.dumps(response_schema)}\n"
        f"LOCAL_FACTS_JSON={json.dumps(facts, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_local_digest(
    raw: Any,
    batch_facts: list[Any],
    *,
    batch_index: int | str,
) -> dict[str, Any]:
    # A local digest only owns the facts in its transport batch. Some gateways
    # still copy an id seen in nearby context; discard that reference while
    # preserving the unit as an explicit unresolved question.
    if isinstance(raw, dict) and isinstance(raw.get("units"), list):
        normalized = dict(raw)
        normalized_units: list[Any] = []
        for item in raw["units"]:
            if not isinstance(item, dict):
                normalized_units.append(item)
                continue
            unit = dict(item)
            for field_name in ("fact_ids", "entity_keys", "relationship_hints", "open_questions"):
                value = unit.get(field_name)
                if isinstance(value, str):
                    unit[field_name] = [value] if value.strip() else []
            normalized_units.append(unit)
        normalized["units"] = normalized_units
        raw = normalized
    try:
        payload = LocalDigestResponsePayload.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(f"局部整理 JSON 不符合约定: {error}") from error

    facts_by_id = {fact.id: fact for fact in batch_facts}
    covered: set[str] = set()
    units: list[dict[str, Any]] = []
    normalization_questions: list[str] = []
    for unit_index, unit in enumerate(payload.units, start=1):
        unknown = [fact_id for fact_id in unit.fact_ids if fact_id not in facts_by_id]
        valid_fact_ids = [fact_id for fact_id in unit.fact_ids if fact_id in facts_by_id]
        if unknown:
            normalization_questions.append(
                f"局部整理单元“{unit.title}”引用了 {len(unknown)} 条当前批次外事实；已忽略越界引用。"
            )
        if not valid_fact_ids:
            normalization_questions.append(
                f"局部整理单元“{unit.title}”没有可用的当前批次事实；已跳过该单元。"
            )
            continue
        covered.update(valid_fact_ids)
        source_refs: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, int]] = set()
        for fact_id in valid_fact_ids:
            for ref in facts_by_id[fact_id].source_refs:
                key = (ref.file, ref.page)
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                source_refs.append({"file": ref.file, "page": ref.page})
        unit_data = unit.model_dump(mode="json")
        unit_data["fact_ids"] = valid_fact_ids
        unit_data["open_questions"] = _unique_questions(
            [*unit_data.get("open_questions", []), *normalization_questions]
        )
        digest = _stable_hash(
            {"batch": batch_index, "index": unit_index, "unit": unit_data}
        ).removeprefix("sha256:")[:16]
        units.append(
            {
                "id": f"local_unit_{batch_index}_{unit_index}_{digest}",
                **unit_data,
                "source_refs": sorted(
                    source_refs, key=lambda item: (item["file"].casefold(), item["page"])
                ),
            }
        )

    missing = sorted(set(facts_by_id) - covered)
    if missing:
        # Models occasionally omit an otherwise valid fact while grouping a large
        # batch. Preserve coverage deterministically instead of discarding the
        # whole batch; the global planner can decide whether the fallback belongs
        # on a final card, while its source text remains fully traceable.
        for fact_id in missing:
            fact = facts_by_id[fact_id]
            fallback = {
                "title": f"待整理事实 {fact_id[-8:]}",
                "summary": fact.text,
                "fact_ids": [fact_id],
                "entity_keys": [],
                "relationship_hints": [],
                "open_questions": ["模型未在局部整理中归类；保留原事实供全局规划。"],
            }
            digest = _stable_hash(
                {"batch": batch_index, "index": len(units) + 1, "unit": fallback}
            ).removeprefix("sha256:")[:16]
            units.append(
                {
                    "id": f"local_unit_{batch_index}_fallback_{digest}",
                    **fallback,
                    "source_refs": [
                        {"file": ref.file, "page": ref.page} for ref in fact.source_refs
                    ],
                }
            )
    return {
        "units": units,
        "open_questions": _unique_questions(
            [*payload.open_questions, *normalization_questions]
        ),
    }


def _local_digest_split_candidate(error: Exception, facts: list[Any]) -> bool:
    """Return whether a failed local window can be safely split for transport.

    A split is a recovery for an upstream response boundary, not for an invalid
    semantic result.  The client includes ``finish_reason=length`` only when the
    upstream reported that it stopped at its output limit; requiring that marker
    prevents ordinary validation, authentication, and network errors from being
    hidden by repeated smaller requests.
    """
    return len(facts) > 1 and "finish_reason=length" in str(error)


def _local_digest_raw_from_output(output: dict[str, Any]) -> dict[str, Any]:
    """Project a validated child result back to the wire schema for its parent."""
    units: list[dict[str, Any]] = []
    for item in output.get("units", []):
        if not isinstance(item, dict):
            continue
        units.append({
            key: item[key]
            for key in (
                "kind", "title", "summary", "fact_ids", "entity_keys",
                "relationship_hints", "open_questions",
            )
            if key in item
        })
    return {
        "units": units,
        "open_questions": list(output.get("open_questions") or []),
    }


def _merge_local_digest_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge disjoint transport children without inventing a semantic boundary."""
    units: list[dict[str, Any]] = []
    questions: list[str] = []
    for output in outputs:
        raw = _local_digest_raw_from_output(output)
        units.extend(raw["units"])
        questions.extend(raw["open_questions"])
    return {
        "units": units,
        "open_questions": _unique_questions(questions),
    }


def _run_local_digest_window(
    job: ArtifactDraftJob,
    *,
    profile: RuleProfile,
    facts: list[Any],
    chapter: dict[str, Any] | None,
    batch_index: int,
    batch_count: int,
    path: str,
    max_tokens: int,
    chat_kwargs: dict[str, Any],
    client: Any,
    step_budget: RequestBudget,
) -> dict[str, Any]:
    """Run one local window and recursively split only an output-truncated one."""
    fact_payload = _fact_payload_from_facts(facts)
    validation_index: int | str = f"{batch_index}{path}" if path else batch_index
    stage = "local_digest" if not path else f"local_digest_window_{batch_index}_{path}"
    step_index = batch_index if not path else 1

    def recover_error(_raw: Any, error: Exception) -> Any:
        if not _local_digest_split_candidate(error, facts):
            step_budget.recoveries.append(
                f"局部整理窗口 {path or batch_index} 已无法拆分"
            )
            if "finish_reason=length" not in str(error):
                raise error
            raise _unsplittable_window_error(
                f"局部整理窗口 {path or batch_index}",
                f"{len(facts)} 条事实",
                step_budget,
                cause=error,
            )
        midpoint = len(facts) // 2
        step_budget.recoveries.append(
            f"拆分局部整理窗口 {path or batch_index} 为 2 个窗口"
        )
        left = _run_local_digest_window(
            job,
            profile=profile,
            facts=facts[:midpoint],
            chapter=chapter,
            batch_index=batch_index,
            batch_count=batch_count,
            path=f"{path}L",
            max_tokens=max_tokens,
            chat_kwargs=chat_kwargs,
            client=client,
            step_budget=step_budget,
        )
        right = _run_local_digest_window(
            job,
            profile=profile,
            facts=facts[midpoint:],
            chapter=chapter,
            batch_index=batch_index,
            batch_count=batch_count,
            path=f"{path}R",
            max_tokens=max_tokens,
            chat_kwargs=chat_kwargs,
            client=client,
            step_budget=step_budget,
        )
        return _merge_local_digest_outputs([left, right])

    input_payload = {
        "prompt_version": LOCAL_DIGEST_PROMPT_VERSION,
        "profile_id": profile.id,
        "batch_index": batch_index,
        "batch_count": batch_count,
        "facts": fact_payload,
        "chapter_id": chapter.get("id") if chapter else None,
    }
    input_refs = {"fact_ids": [fact.id for fact in facts]}
    if path:
        input_payload["split_path"] = path
        input_refs["split_path"] = path

    return _run_json_step(
        job,
        stage=stage,
        step_index=step_index,
        input_payload=input_payload,
        input_refs=input_refs,
        messages=_local_digest_messages(profile, fact_payload, chapter=chapter),
        client=client,
        max_tokens=max_tokens,
        validator=lambda raw: _validate_local_digest(
            raw, facts, batch_index=validation_index
        ),
        chat_kwargs=chat_kwargs,
        step_budget=step_budget,
        recover_error=recover_error,
        # A leaf that cannot shrink is asked again on the next explicit retry instead of
        # replaying its stored length failure.
        recover_existing_failure=lambda raw, error: _local_digest_split_candidate(
            error, facts
        ),
    )


def _leaf_plan_messages(
    profile: RuleProfile,
    planning_units: list[dict[str, Any]],
    fact_sizes: dict[str, int],
    fact_tokens: dict[str, int],
    structure_context: dict[str, Any] | None,
    *,
    lead_window: bool = True,
    fact_payload: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """The planning contract for one unsplittable window.

    A leaf owns one planning index and a handful of facts, so it does not need the
    whole-scope narrative, the beat essay or the full response schema.  It still owes
    the same *local contributions* every window owes: its plan cards with the fields the
    later stages consume (situation, case_scope, runtime groups, beat links), a partial
    overview card when the board has one, a package brief when the workspace has no
    chapters, and chapter briefs for the chapters in its own scope.  Dropping those
    producers would leave the merged plan unable to satisfy its own validation, and no
    deterministic text may stand in for a model's partial contribution.
    """
    overview_type = next(
        (definition.type for definition in profile.card_definitions
         if definition.type == "chapter_overview"),
        None,
    )
    environment_led = profile.runtime_navigation_mode == "environment"
    chapter_ids = sorted(
        {
            chapter["id"]
            for chapter in (structure_context or {}).get("chapters", [])
            if isinstance(chapter, dict) and chapter.get("id")
        }
    )
    card_fields: dict[str, Any] = {
        "type": "<one of ALLOWED_CARD_TYPES_JSON>",
        "title": "<short card title>",
        "purpose": "<what this card is for at the table>",
        "fact_ids": ["<fact IDs from GLOBAL_UNITS_JSON only>"],
        "chapter_ids": chapter_ids,
        "focus": [],
        "open_questions": [],
        "runtime_groups": [],
        "story_beat_ids": [],
        "situation": "",
        "case_scope": "current",
    }
    schema: dict[str, Any] = {"cards": [card_fields], "open_questions": []}
    if overview_type:
        schema["cards"].append(
            {
                "type": overview_type,
                "title": "<chapter or scope overview title>",
                "purpose": "<how this window's facts fit the wider situation>",
                "fact_ids": ["<fact IDs this part of the overview rests on>"],
                "chapter_ids": chapter_ids,
            }
        )
    if chapter_ids:
        schema["chapter_briefs"] = [
            {
                "chapter_id": chapter_ids[0],
                "brief": {
                    "premise": "<short>",
                    "current_situation": "<short>",
                },
                "reference_fact_ids": ["<fact IDs from this window>"],
            }
        ]
    elif environment_led:
        schema["brief"] = {
            "premise": "<short>",
            "current_situation": "<short>",
        }

    card_types = sorted({definition.type for definition in profile.card_definitions})
    board_rule = ""
    if environment_led:
        board_rule = (
            "Set situation to the one playable situation this card covers and case_scope "
            "to current, related, or conditional_example according to the source; do not "
            "turn a related or example situation into the opening state. Use story_beat_ids "
            "only for the supplied beats this environment actually supports, and "
            "runtime_groups only for source-backed groups. "
        )
    elif profile.runtime_navigation_mode == "location":
        board_rule = (
            "A location card needs source-supported runnable content — something to "
            "investigate, a person or obstacle, a usable resource, or a change that happens "
            "there. A police station, publisher, hotel, residence, office, or a minor scene "
            "holding a lead keeps its own card, and several rooms of one building may be "
            "separate when each is usable. A name alone, pure geography (river, boundary, "
            "district outline), a historical note, a route, a destination, or a map reference "
            "is not a location card: leave it to the overview, global_reference_fact_ids or "
            "open_questions. Each card's fact_ids must specifically support its title, "
            "purpose and situation, and several cards must not reuse one fact set under "
            "different titles unless their playable situations are independent. "
        )
    source_rules = (
        "Use only fact IDs from GLOBAL_UNITS_JSON and never invent facts. A later stage "
        "rereads the original facts to write each card's playable fields, so plan only. "
        "You see one window: report unresolved cross-window questions in open_questions "
        "and do not claim facts outside it. case_scope must be exactly one of current, "
        "related, or conditional_example: current is the situation the table starts in, "
        "related is a connected but separate situation, and conditional_example is source "
        "play or examples that are not established opening conditions. "
    )
    chapter_rule = (
        f"Reviewed chapters are supplied: chapter_ids may only use {chapter_ids}, and "
        "return chapter_briefs for the chapters in this window. "
        if chapter_ids
        else "No chapter scope exists for this window, so chapter_ids must be [] for every "
        "card and no chapter_briefs are returned; do not invent chapter identifiers. "
    )
    overview_rule = (
        f"Return one {overview_type} card for this window's part of the wider picture; "
        "the application merges these partial overviews. "
        if overview_type
        else ""
    )
    brief_rule = (
        "Return the package brief fields this window can support; the application "
        "combines them. "
        if environment_led and not chapter_ids
        else ""
    )
    system = (
        "You plan one bounded window of a TRPG preparation workspace. Return one JSON "
        "object following LEAF_RESPONSE_SCHEMA_JSON. "
        + source_rules
        + chapter_rule
        + overview_rule
        + brief_rule
        + board_rule
        + PERSON_SEMANTIC_GUIDANCE
        + SOURCE_SEMANTIC_GUIDANCE
        + "All user-facing text must be Chinese."
    )
    facts_block = ""
    if fact_payload is not None:
        facts_block = (
            f"ORIGINAL_FACTS_JSON={json.dumps(fact_payload, ensure_ascii=False)}\n"
        )
    user = (
        "[TASK:prep:artifact_global_plan_leaf]\n"
        f"ALLOWED_CARD_TYPES_JSON={json.dumps(card_types, ensure_ascii=False)}\n"
        f"LEAF_RESPONSE_SCHEMA_JSON={json.dumps(schema, ensure_ascii=False)}\n"
        f"REVIEWED_STRUCTURE_JSON={json.dumps(structure_context, ensure_ascii=False) if structure_context else 'null'}\n"
        f"FACT_SIZES_JSON={json.dumps(fact_sizes, ensure_ascii=False)}\n"
        f"FACT_TOKENS_JSON={json.dumps(fact_tokens, ensure_ascii=False)}\n"
        f"GLOBAL_UNITS_JSON={json.dumps(planning_units, ensure_ascii=False)}\n"
        + facts_block.rstrip("\n")
    )
    # The input boundary is judged on the encoded request at the call site, so a leaf
    # that only fits after transport encoding is not rejected here.
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _gestalt_fact_digest(facts: list[Any]) -> str:
    """Identity of the fact *content* a plan step may rely on."""
    payload = _fact_payload_from_facts(facts)
    return _stable_hash(payload)


def _global_plan_messages(
    profile: RuleProfile,
    units: list[dict[str, Any]],
    fact_sizes: dict[str, int],
    fact_tokens: dict[str, int],
    structure_context: dict[str, Any] | None = None,
    *,
    partial_window: bool = False,
    lead_window: bool = True,
    leaf_window: bool = False,
    fact_payload: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    planning_units = _compact_global_units(units)
    if leaf_window:
        return _leaf_plan_messages(
            profile,
            planning_units,
            fact_sizes,
            fact_tokens,
            structure_context,
            lead_window=lead_window,
            fact_payload=fact_payload,
        )
    if profile.runtime_navigation_mode == "environment":
        runtime_guidance = (
            "For this environment-led profile, create one environment card per playable situation, "
            "not every named place. A playable situation may be exploration, social, traversal, "
            "combat, or event material and can span several nearby places when they share one active "
            "conflict and phase structure. Do not duplicate one situation merely because its mode "
            "changes. Preserve motives, meaningful choices, phase changes, nonviolent outcomes, and "
            "cross-environment consequences. Represent meaningful travel as an environment plan "
            "whose purpose and focus describe traversal; do not create a separate travel card. Do not generate or "
            "convert game statistics. "
            "An environment card must cover exactly one playable situation: put its short name in "
            "situation and never list more than one runtime_groups entry. When the source holds two "
            "independently runnable situations, emit two environment cards instead of one combined "
            "card. Set case_scope on every card: current for the situation the table starts in, "
            "related for a connected but separate situation, and conditional_example for source play "
            "reports or examples that are not established opening conditions. "
        )
    elif profile.runtime_navigation_mode == "location":
        runtime_guidance = (
            "A location card needs source-supported runnable content: something the players can "
            "investigate, a person or obstacle they meet there, a resource they can use, or a change "
            "that happens there. A police station, publisher, hotel, residence, office, or a minor "
            "scene that holds a lead must keep its own location card, and several rooms of one "
            "building may be separate cards when each can be entered, searched, revisited, used for "
            "help, or holds a clue. A place that is only a name, pure geography (a river, a park "
            "boundary, a district outline), a historical note, a route, a destination, or a map "
            "reference is NOT a location card: keep it in the overview, in "
            "global_reference_fact_ids, or in open_questions, and do not dress it up as a visitable "
            "scene. Every card's fact_ids must specifically support its title, purpose and "
            "situation; if the source does not establish a use, say so in open_questions instead of "
            "asserting one. Do not emit several cards from the same facts under different titles: "
            "split one entity into several cards only when their duties or playable situations are "
            "genuinely independent. "
        )
    else:
        runtime_guidance = ""
    window_guidance = ""
    if partial_window:
        window_guidance = (
            "This is one bounded planning window, not the complete selected scope. Use only the "
            "supplied GLOBAL_UNITS_JSON and its fact IDs; do not claim that facts outside this "
            "window are covered. Keep each card independently useful and leave unresolved "
            "cross-window identity or relationship questions in open_questions. The application "
            "will merge this window with other validated windows before the final coverage check. "
        )
        if any(item.type == "chapter_overview" for item in profile.card_definitions):
            window_guidance += (
                "Include a chapter_overview card as a partial view of this window's source "
                "facts. Every window should return its own partial overview; the application "
                "will merge them into one card and combine their cited facts. "
            )
        if profile.runtime_navigation_mode == "environment" and not structure_context:
            window_guidance += (
                "Return a concise partial package brief based only on this window. Every window "
                "should return one; the application will combine their supported lists and "
                "citations. "
            )
        if structure_context:
            window_guidance += (
                "Return chapter_briefs for only the chapter or chapters in the supplied reviewed "
                "structure context. Each brief and reference list is partial to this window; "
                "the application will combine them before whole-chapter coverage validation. "
            )
    facts_block = ""
    if fact_payload:
        facts_block = (
            "\nORIGINAL_FACTS_JSON=" + json.dumps(fact_payload, ensure_ascii=False)
        )
    system = (
        "You are the global planning stage for a TRPG preparation workspace. Return only one JSON "
        "object with keys cards, brief, chapter_briefs, global_reference_fact_ids, and "
        "open_questions. For an environment-led runtime profile, "
        "brief is package metadata for an unstructured workspace; when reviewed chapters are supplied "
        "set brief=null and place summaries in chapter_briefs only. A brief must contain premise, current_situation, "
        "major_conflicts, opening_conditions, possible_endings, fact_ids, field_sources, and open_questions. "
        "Each planned card must contain type, title, "
        "purpose, fact_ids, focus, open_questions, runtime_groups, chapter_ids, story_beat_ids, "
        "situation, and case_scope. "
        "For environment cards, story_beat_ids explicitly identifies supplied chapter-local beats "
        "whose immediate situation this environment supports, and nothing else. NPC, enemy, and "
        "clock/support cards MUST use story_beat_ids=[]; their relevance to a beat does not make "
        "them environment navigation anchors. Shared storyline membership alone is "
        "not sufficient. Do not attach every environment to every beat to satisfy coverage. "
        "One environment can support several beats and one beat several environments. "
        "First identify independently runnable situations from the local source indexes, then associate "
        "them with beat thread_developments and interplay. Beats organize narrative changes across "
        "a possible game; they are not a card inventory. Do not copy beat titles/summaries as "
        "environment plans or merge distinct playable places merely because they serve the same beat. "
        "A one-to-one link is allowed when the source supports it, but is not a target. "
        "Narrative suggestions do not authorize invented facts. Use an empty "
        "list for supporting cards or when no beat is supported by the source. Report unsupported "
        "beats in open_questions instead of inventing environments. Reconcile aliases and relationships across all "
        "local units, then plan a coherent working set for the whole selected scope. The compact units "
        "are planning indexes only: do not write final card fields and do not treat their prose as a "
        "new source. Each card will later reread the original fact_ids you select. Use only listed "
        "fact_ids, avoid duplicate cards, preserve conflicts as open questions, and keep each card's "
        "estimated original-fact input within the supplied character budget. "
        + SOURCE_SEMANTIC_GUIDANCE
        + CLOCK_SEMANTIC_GUIDANCE
        + PERSON_SEMANTIC_GUIDANCE
        + runtime_guidance
        + window_guidance
        + BOARD_REFERENCE_BOUNDARY
        + "Player-facing source assets "
        "such as maps, letters, photos, newspapers, logs, and records are tracked separately and must "
        "not become artifact cards merely as display assets. When a supplied handout fact is "
        "assigned to a chapter, use its actionable leads or evidence in relevant card plans; "
        "its handout kind does not make that content display-only. "
        "All user-facing text must be Chinese. For an environment-led "
        "profile, runtime_groups are optional retrieval hints. Use a shared short semantic key only "
        "when source-backed material makes an environment and support card clearly relevant together. "
        "Do not invent an encounter, require an enemy or clock, or preselect what must appear in play. "
        "Reusable support may list multiple keys. A group denotes one situation or one recurring "
        "entity, never a campaign-wide theme, and the same situation must reuse one canonical name "
        "across chapters, with no chapter number, scope marker, or planning request in it. Write each "
        "entry as a short lowercase ASCII key joined by underscores, such as trollskull_tavern, "
        "because the key becomes the identifier encounter_<key>_<hash>; a Chinese name cannot be "
        "slugged and collapses to a meaningless placeholder. "
        "Runtime brief metadata is not a card and does not need a runtime group. "
        "Follow RESPONSE_SCHEMA_JSON exactly. Do not add environment_type or other final card fields "
        "to a planned card; express operating modes in purpose/focus. opening_conditions is an array. "
        "field_sources maps brief field names to arrays of supplied fact IDs, never book names, "
        "rulebooks, URLs, or invented source labels. Each cited ID must also be in that brief's fact_ids. "
        "chapter_briefs is an array of objects with chapter_id, brief (a nested RuntimeBrief), and "
        "reference_fact_ids; it is not a chapter-keyed dictionary or a list of title/summary/cards. "
        + (
            " The complete job covers all approved chapters through separate planning requests. "
            "THIS request generates ONLY REVIEWED_STRUCTURE_JSON.chapters. adventure_index and "
            "cross-chapter thread roles are context for continuity, not additional generation targets. "
            "chapter_fact_ids lists the facts this request must account for, and "
            "fact_threads lists, per thread, which of them carry it; "
            "fact_shared_with_chapters does the same for each other chapter. Read all three "
            "as roles of the facts already listed in GLOBAL_UNITS_JSON. "
            "Every card must list only applicable chapter IDs from chapters in THIS request. "
            "Shared support will be reconciled across chapter plans by the application. Return one "
            "chapter_briefs item for each chapter in THIS request, and none for adventure_index-only "
            "chapters. Local unit prose may summarize wider context; only its listed fact_ids "
            "authorize content for this request. For each chapter, account for every applicable fact in a card, that "
            "chapter brief, or reference_fact_ids. Account for every global_reference fact in "
            "global_reference_fact_ids or the package brief. Do not omit facts merely to shorten "
            "the card set; reference facts remain directly retrievable without becoming cards."
            " Actionable leads, evidence connecting locations, NPC motives, and changes to an environment "
            "belong in relevant card fact_ids or the chapter brief; do not park them in reference_fact_ids "
            "to reduce generation effort. Reference facts are supporting context. Avoid duplicating "
            "facts already covered by cards/briefs in reference_fact_ids."
            if structure_context
            else ""
        )
    )
    allowed_ids = {fact_id for unit in units for fact_id in unit.get("fact_ids", [])}
    fact_sizes = {key: value for key, value in fact_sizes.items() if key in allowed_ids}
    fact_tokens = {key: value for key, value in fact_tokens.items() if key in allowed_ids}
    schema = GlobalPlanResponsePayload.model_json_schema()
    schema["$defs"]["PlannedCardPayload"]["allOf"] = [{
        "if": {"properties": {"type": {"const": "environment"}}, "required": ["type"]},
        "else": {"properties": {"story_beat_ids": {"maxItems": 0}}},
    }]
    schema["$defs"]["PlannedCardPayload"]["properties"]["type"]["enum"] = [
        definition.type for definition in profile.card_definitions
    ]
    if structure_context:
        chapter_ids = [item["id"] for item in structure_context["chapters"]]
        schema["$defs"]["PlannedCardPayload"]["properties"]["chapter_ids"]["items"]["enum"] = chapter_ids
        beat_ids = [item["id"] for item in structure_context.get("beats", [])]
        beat_field = schema["$defs"]["PlannedCardPayload"]["properties"]["story_beat_ids"]
        if beat_ids:
            beat_field["items"]["enum"] = beat_ids
        else:
            beat_field["maxItems"] = 0
        schema["$defs"]["ChapterBriefPayload"]["properties"]["chapter_id"]["enum"] = chapter_ids
    example_fact = next(iter(sorted(allowed_ids)), "fact_example")
    brief_example = {
        "premise": "来源支持的背景。",
        "current_situation": "当前章节的局势。",
        "opening_conditions": ["来源支持的开局条件。"],
        "fact_ids": [example_fact],
        "field_sources": {"premise": [example_fact], "current_situation": [example_fact]},
    }
    chapter_example = ([{
        "chapter_id": structure_context["chapters"][0]["id"],
        "brief": brief_example,
        "reference_fact_ids": [],
    }] if structure_context else [])
    # The planner needs card categories, not the final card field contract.
    planning_profile = _board_prompt_payload(
        profile,
        definitions=[
            {
                key: value
                for key, value in definition.model_dump(mode="json").items()
                if key in {"type", "label", "description"}
            }
            for definition in profile.card_definitions
        ],
    )
    user = (
        "[TASK:prep:artifact_global_plan]\n"
        f"PROMPT_VERSION_JSON={json.dumps(GLOBAL_PLAN_PROMPT_VERSION)}\n"
        f"BOARD_PROFILE_JSON={json.dumps(planning_profile, ensure_ascii=False)}\n"
        f"RESPONSE_SCHEMA_JSON={json.dumps(schema, ensure_ascii=False)}\n"
        "EXAMPLE_IS_SHAPE_ONLY_JSON=true\n"
        f"CHAPTER_BRIEFS_EXAMPLE_JSON={json.dumps(chapter_example, ensure_ascii=False)}\n"
        f"MAX_CARD_FACT_INPUT_CHARS_JSON={MAX_CARD_FACT_INPUT_CHARS}\n"
        f"MAX_CARD_FACT_INPUT_TOKENS_JSON={MAX_CARD_FACT_INPUT_TOKENS}\n"
        f"FACT_INPUT_CHARS_JSON={json.dumps(fact_sizes, ensure_ascii=False)}\n"
        f"FACT_INPUT_TOKENS_JSON={json.dumps(fact_tokens, ensure_ascii=False)}\n"
        f"REVIEWED_STRUCTURE_JSON={json.dumps(structure_context, ensure_ascii=False)}\n"
        f"GLOBAL_UNITS_JSON={json.dumps(planning_units, ensure_ascii=False)}"
        + facts_block
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _normalize_runtime_group_anchors(
    plan: dict[str, Any], profile: RuleProfile
) -> dict[str, Any]:
    """Drop support-only group ids without inventing an environment relation."""
    if profile.runtime_navigation_mode != "environment":
        return plan
    cards = list(plan.get("cards") or [])
    anchor_ids = {
        group_id
        for card in cards
        if card.get("type") == profile.runtime_anchor_type
        for group_id in card.get("runtime_group_ids") or []
    }
    normalized_cards: list[Any] = []
    questions = list(plan.get("open_questions") or [])
    changed = False
    for card in cards:
        if not isinstance(card, dict) or card.get("type") == profile.runtime_anchor_type:
            normalized_cards.append(card)
            continue
        groups = list(card.get("runtime_group_ids") or [])
        orphaned = [group_id for group_id in groups if group_id not in anchor_ids]
        if not orphaned:
            normalized_cards.append(card)
            continue
        normalized = dict(card)
        normalized["runtime_group_ids"] = [
            group_id for group_id in groups if group_id in anchor_ids
        ]
        normalized_cards.append(normalized)
        questions.append(
            f"全局计划“{card.get('title') or '未命名支持卡'}”的 {len(orphaned)} 个运行组"
            "缺少环境锚点；已保留卡片并移除孤立关联。"
        )
        changed = True
    if not changed:
        return plan
    normalized_plan = dict(plan)
    normalized_plan["cards"] = normalized_cards
    normalized_plan["open_questions"] = _unique_questions(questions)
    return normalized_plan


class PlanCoverageError(ArtifactGenerationError):
    def __init__(self, chapters, global_ids, facts):
        self.chapters = chapters
        self.global_ids = global_ids
        missing = set(global_ids).union(*(set(ids) for ids in chapters.values()))
        self.facts = _fact_payload_from_facts([fact for fact in facts if fact.id in missing])
        super().__init__(f"全局计划没有闭合全部事实去向: chapters={chapters}, global={global_ids}")


class CoverageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    chapter_id: str | None
    destination: Literal["card", "chapter_reference", "global_reference"]
    card_index: int | None = Field(default=None, ge=0)
    reason: str = Field(min_length=1, max_length=800)


class CoverageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decisions: list[CoverageDecision]


def _validate_coverage_decisions(response, expected):
    # Recover only ownership already determined by the request, never model intent.
    if isinstance(response, dict) and isinstance(response.get("decisions"), list):
        decisions = []
        for item in response["decisions"]:
            if isinstance(item, dict):
                item = dict(item)
                owners = {chapter for fact_id, chapter in expected if fact_id == item.get("fact_id")}
                if not owners:
                    raise ArtifactGenerationError("事实去向修正引用未知事实")
                if item.get("chapter_id") is None:
                    if len(owners) != 1:
                        raise ArtifactGenerationError("事实去向修正缺少章节，归属存在歧义")
                    item["chapter_id"] = next(iter(owners))
                elif item["chapter_id"] not in owners:
                    raise ArtifactGenerationError("事实去向修正的章节归属错误")
            decisions.append(item)
        response = {**response, "decisions": decisions}
    payload = CoverageResponse.model_validate(response)
    pairs = [(item.fact_id, item.chapter_id) for item in payload.decisions]
    if len(pairs) != len(set(pairs)):
        raise ArtifactGenerationError("事实去向修正包含重复事实与章节组合")
    missing = expected - set(pairs)
    if missing:
        raise ArtifactGenerationError(f"事实去向修正遗漏 {len(missing)} 个事实与章节组合")
    return payload


def _complete_plan_coverage(
    job, stage, index, raw, error, client, validator,
    *, step_budget: RequestBudget | None = None,
):
    expected = {(fid, chapter) for chapter, ids in error.chapters.items() for fid in ids}
    expected.update((fid, None) for fid in error.global_ids)

    def merge(response):
        payload = _validate_coverage_decisions(response, expected)
        merged = GlobalPlanResponsePayload.model_validate(_normalize_global_plan_shape(raw)).model_dump(mode="json")
        for item in payload.decisions:
            if item.chapter_id is None:
                if item.destination != "global_reference" or item.card_index is not None:
                    raise ArtifactGenerationError("全局参考事实必须保留为全局参考")
                merged["global_reference_fact_ids"].append(item.fact_id)
            elif item.destination == "card":
                if item.card_index is None or item.card_index >= len(merged["cards"]):
                    raise ArtifactGenerationError("事实去向修正引用未知卡片")
                card = merged["cards"][item.card_index]
                if card["chapter_ids"] and item.chapter_id not in card["chapter_ids"]:
                    raise ArtifactGenerationError("事实去向修正引用其他章节的卡片")
                card["fact_ids"] = list(dict.fromkeys([*card["fact_ids"], item.fact_id]))
            elif item.destination == "chapter_reference" and item.card_index is None:
                brief = next(b for b in merged["chapter_briefs"] if b["chapter_id"] == item.chapter_id)
                brief["reference_fact_ids"].append(item.fact_id)
            else:
                raise ArtifactGenerationError("事实去向修正的目标不符合约定")
        validator(merged)
        return merged

    messages = [{"role": "system", "content": (
        "Complete only the missing fact destinations in this TRPG card plan. Never rewrite the plan. "
        "Return decisions following the schema. For chapter facts choose an existing card when its "
        "purpose fits; otherwise retain the fact as chapter_reference with a reason. Do not dump "
        "actionable facts into reference merely to pass validation. Global facts remain global_reference. "
        "Every decision must include chapter_id: copy the exact chapter key from missing_chapters; "
        "use null only for missing_global facts. Return each requested fact/chapter pair exactly once. "
        "Use zero-based card_index only for card destinations; otherwise null. Descriptions in Chinese."
    )}, {"role": "user", "content": json.dumps({
        "schema": CoverageResponse.model_json_schema(), "missing_chapters": error.chapters,
        "missing_global": error.global_ids, "facts": error.facts,
        "cards": [{"index": i, **{k: c.get(k) for k in ("type", "title", "purpose", "focus", "chapter_ids")}}
                  for i, c in enumerate(raw["cards"])],
    }, ensure_ascii=False)}]
    try:
        return _run_json_step(
            job, stage=stage + "_coverage", step_index=index,
            input_payload={"version": "coverage-v2", "plan": raw, "facts": error.facts,
                           "chapters": error.chapters, "global": error.global_ids},
            input_refs={"fact_ids": [fact["id"] for fact in error.facts]},
            messages=messages, client=client, max_tokens=8000, validator=merge,
            step_budget=step_budget,
        )
    except Exception as failure:
        # Keep the original plan intact instead of triggering a whole-plan rewrite.
        error.args = (f"{error}；事实去向修正失败：{failure}",)
        raise error from failure


def _validate_global_plan(
    raw: Any,
    units: list[dict[str, Any]],
    facts: list[Any],
    profile: RuleProfile,
    bundle: ExampleBundle | None = None,
    chapter_scope_ids: set[str] | None = None,
    *,
    require_overview: bool = True,
    require_runtime_anchor: bool = True,
    require_coverage: bool = True,
) -> dict[str, Any]:
    try:
        payload = GlobalPlanResponsePayload.model_validate(_normalize_global_plan_shape(raw))
    except ValidationError as error:
        raise ArtifactGenerationError(f"全局卡片计划 JSON 不符合约定: {error}") from error

    allowed_fact_ids = {fact_id for unit in units for fact_id in unit.get("fact_ids", [])}
    facts_by_id = {fact.id: fact for fact in facts}
    definitions = {definition.type: definition for definition in profile.card_definitions}
    seen: set[tuple[str, str]] = set()
    planned: list[dict[str, Any]] = []
    used_fact_ids: set[str] = set()
    runtime_group_ids: dict[str, str] = {}
    all_chapter_ids = {
        chapter.id for chapter in (bundle.adventure_chapters if bundle is not None else [])
    }
    known_chapter_ids = chapter_scope_ids or all_chapter_ids

    def group_id(group: str) -> str:
        key = " ".join(group.split()).casefold()
        if key not in runtime_group_ids:
            slug = re.sub(r"[^a-z0-9]+", "_", key).strip("_")[:40] or "situation"
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
            runtime_group_ids[key] = f"encounter_{slug}_{digest}"
        return runtime_group_ids[key]

    for index, card in enumerate(payload.cards, start=1):
        if card.type not in definitions:
            raise ArtifactGenerationError(f"全局计划包含未定义卡型: {card.type}")
        key = (card.type, card.title.casefold())
        if key in seen:
            raise ArtifactGenerationError(f"全局计划包含重复产物: {card.type} / {card.title}")
        seen.add(key)
        unknown = [fact_id for fact_id in card.fact_ids if fact_id not in allowed_fact_ids]
        if unknown:
            raise ArtifactGenerationError(f"全局计划 {card.title} 引用了局部索引外事实: {unknown}")
        unknown_chapters = sorted(set(card.chapter_ids) - known_chapter_ids)
        if unknown_chapters:
            raise ArtifactGenerationError(
                f"全局计划 {card.title} 引用了未知章节: {unknown_chapters}"
            )
        if known_chapter_ids and not card.chapter_ids:
            if chapter_scope_ids and len(chapter_scope_ids) == 1:
                card.chapter_ids = sorted(chapter_scope_ids)
            else:
                raise ArtifactGenerationError(f"全局计划 {card.title} 缺少适用章节")
        selected_facts = [facts_by_id[fact_id] for fact_id in card.fact_ids]
        beats_by_id = {beat.id: beat for beat in (bundle.story_beats if bundle else [])}
        for beat_id in card.story_beat_ids:
            beat = beats_by_id.get(beat_id)
            if beat is None:
                raise ArtifactGenerationError(f"全局计划 {card.title} 引用了未知拍: {beat_id}")
            if beat.chapter_id not in card.chapter_ids:
                raise ArtifactGenerationError(f"全局计划 {card.title} 的拍关联跨章节: {beat_id}")
        if card.type != "environment" and card.story_beat_ids:
            payload.open_questions.append(
                f"支持卡“{card.title}”的拍筛选关联已移除；卡片内容与事实保留。"
            )
            card.story_beat_ids = []
        input_chars = len(json.dumps(_fact_payload_from_facts(selected_facts), ensure_ascii=False))
        input_tokens = _estimate_tokens(
            json.dumps(_fact_payload_from_facts(selected_facts), ensure_ascii=False)
        )
        if input_chars > MAX_CARD_FACT_INPUT_CHARS or input_tokens > MAX_CARD_FACT_INPUT_TOKENS:
            raise ArtifactGenerationError(
                f"全局计划 {card.title} 的原始事实输入约 {input_chars} 字符 / "
                f"{input_tokens} 估算 token，超过单卡保护上限；请拆成多张用途明确的卡。"
            )
        data = card.model_dump(mode="json")
        groups = data.pop("runtime_groups", [])
        situation = data.get("situation", "")
        if card.type == "environment":
            # One environment card owns exactly one runtime state.  More than one
            # declared group means the model bundled independent situations; the
            # step runner feeds this message back as a correction request.
            if len(groups) > 1:
                raise ArtifactGenerationError(
                    f"环境卡“{card.title}”声明了 {len(groups)} 个独立情境"
                    f"（{groups}）；每张环境卡只能覆盖一个可运行情境，"
                    "请拆分为多张环境卡，并为每张填写 situation。"
                )
            situation = situation or (groups[0] if groups else "")
            if not situation:
                situation = card.title
                payload.open_questions.append(
                    f"环境卡“{card.title}”未声明可运行情境；已按标题暂定，请复核是否需要拆分。"
                )
        data["situation"] = situation
        data["runtime_group_ids"] = [group_id(group) for group in groups]
        digest = _stable_hash({"index": index, "card": data}).removeprefix("sha256:")[:16]
        planned.append(
            {
                "id": f"planned_card_{index}_{digest}",
                **data,
                "input_chars": input_chars,
                "input_tokens": input_tokens,
            }
        )
        used_fact_ids.update(card.fact_ids)

    if bundle:
        linked_beats = {beat_id for card in planned for beat_id in card.get("story_beat_ids", [])}
        for beat in bundle.story_beats:
            if beat.chapter_id in known_chapter_ids and beat.id not in linked_beats:
                payload.open_questions.append(f"拍“{beat.title}”暂无明确关联环境，需要复核其可运行情境。")
    overview_types = {"chapter_overview"}
    requires_overview = any(
        definition.type in overview_types for definition in profile.card_definitions
    )
    if (
        require_overview
        and requires_overview
        and not any(card["type"] in overview_types for card in planned)
    ):
        raise ArtifactGenerationError("全局计划缺少总览卡")
    if (
        require_runtime_anchor
        and profile.profile_kind == "runtime"
        and not any(card["type"] in RUNTIME_ANCHOR_TYPES for card in planned)
    ):
        raise ArtifactGenerationError("全局计划缺少场景或环境类运行锚点")
    brief = payload.brief
    if (
        require_runtime_anchor
        and profile.runtime_navigation_mode == "environment"
        and brief is None
        and not known_chapter_ids
    ):
        raise ArtifactGenerationError("环境运行板块缺少运行包摘要")
    if brief is not None:
        unknown_brief_facts = [fact_id for fact_id in brief.fact_ids if fact_id not in facts_by_id]
        if unknown_brief_facts:
            raise ArtifactGenerationError(
                f"运行包摘要引用了不存在的事实: {unknown_brief_facts}"
            )
    chapter_briefs = [item.model_dump(mode="json") for item in payload.chapter_briefs]
    global_reference_fact_ids = list(payload.global_reference_fact_ids)
    for item in chapter_briefs:
        chapter = next((chapter for chapter in (bundle.adventure_chapters if bundle else [])
                        if chapter.id == item["chapter_id"]), None)
        if chapter and set(item["reference_fact_ids"]) - set(chapter.fact_ids):
            raise ArtifactGenerationError(f"章节摘要 {chapter.id} 的参考事实不属于该章节")
        unknown = [
            fact_id
            for fact_id in [*item["brief"]["fact_ids"], *item["reference_fact_ids"]]
            if fact_id not in allowed_fact_ids
        ]
        if unknown:
            raise ArtifactGenerationError(
                f"章节摘要 {item['chapter_id']} 引用了局部索引外事实: {unknown}"
            )
    if known_chapter_ids and require_coverage:
        brief_ids = [item["chapter_id"] for item in chapter_briefs]
        if len(brief_ids) != len(set(brief_ids)) or set(brief_ids) != known_chapter_ids:
            raise ArtifactGenerationError(
                "章节摘要必须完整覆盖全部已确认章节"
            )
        dispositions = {
            disposition.fact_id: disposition
            for disposition in bundle.fact_dispositions
            if disposition.fact_id in allowed_fact_ids
        }
        cards_by_chapter = {
            chapter_id: {
                fact_id
                for card in planned
                if chapter_id in card["chapter_ids"]
                for fact_id in card["fact_ids"]
            }
            for chapter_id in known_chapter_ids
        }
        briefs_by_chapter = {
            item["chapter_id"]: set(
                [*item["brief"]["fact_ids"], *item["reference_fact_ids"]]
            )
            for item in chapter_briefs
        }
        missing_by_chapter: dict[str, list[str]] = {}
        for chapter_id in known_chapter_ids:
            expected = {
                fact_id
                for fact_id, disposition in dispositions.items()
                if chapter_id in disposition.chapter_ids
            }
            missing = sorted(
                expected - cards_by_chapter[chapter_id] - briefs_by_chapter[chapter_id]
            )
            if missing:
                missing_by_chapter[chapter_id] = missing
        expected_global = {
            fact_id
            for fact_id, disposition in dispositions.items()
            if disposition.kind == "global_reference"
        }
        package_facts = set(brief.fact_ids) if brief is not None else set()
        missing_global = sorted(
            expected_global - set(global_reference_fact_ids) - package_facts
        )
        if missing_by_chapter or missing_global:
            raise PlanCoverageError(missing_by_chapter, missing_global, facts)
    # Runtime groups are optional retrieval hints. An environment remains
    # usable without a preselected enemy or clock.
    used_fact_ids.update(global_reference_fact_ids)
    if brief is not None:
        used_fact_ids.update(brief.fact_ids)
    for item in chapter_briefs:
        used_fact_ids.update(item["brief"]["fact_ids"])
        used_fact_ids.update(item["reference_fact_ids"])
    uncovered = sorted(allowed_fact_ids - used_fact_ids)
    return _normalize_runtime_group_anchors({
        "cards": planned,
        "brief": brief.model_dump(mode="json") if brief is not None else None,
        "chapter_briefs": chapter_briefs,
        "global_reference_fact_ids": global_reference_fact_ids,
        "uncovered_fact_ids": uncovered,
        "open_questions": payload.open_questions,
    }, profile)


def _validate_global_plan_window(
    raw: Any,
    units: list[dict[str, Any]],
    facts: list[Any],
    profile: RuleProfile,
    bundle: ExampleBundle | None,
    chapter_scope_ids: set[str] | None,
) -> dict[str, Any]:
    """Validate a planning window without requiring whole-scope invariants.

    A window is still checked for card shape, source ownership, chapter ownership,
    card closure size, and runtime-group shape.  The complete anchor, overview,
    and fact-coverage checks remain on the merged parent response.
    """
    normalized = _normalize_global_plan_shape(raw)
    payload = GlobalPlanResponsePayload.model_validate(normalized)
    _validate_global_plan(
        normalized,
        units,
        facts,
        profile,
        bundle,
        chapter_scope_ids,
        require_overview=False,
        require_runtime_anchor=False,
        require_coverage=False,
    )
    return payload.model_dump(mode="json")


def _reply_state(error: Exception) -> str:
    """Say whether the boundary reply carried any text.

    `LLMClient` reports this directly; a stored failure or another client only leaves
    its message, so the same distinction is recovered from the two known wordings.
    Returns "empty", "truncated" or "unknown" — never a guess about the model's
    reasoning.
    """
    state = getattr(error, "reply_state", None)
    if state in {"empty", "truncated"}:
        return state
    text = str(error)
    if "空正文" in text:
        return "empty"
    if "被截断" in text:
        return "truncated"
    return "unknown"


def _unsplittable_window_error(
    label: str,
    scope: str,
    step_budget: RequestBudget,
    *,
    cause: Exception | None = None,
) -> ArtifactGenerationError:
    """Describe a stop that is not the request cap.

    The live failure was reported as a bare "finish_reason=length" reply, which says
    nothing about how far the recovery got.  A reader needs the failing window, that it
    can no longer be reduced, whether the upstream sent any text at all, how many
    requests this step already sent and which recoveries ran — and must not be told the
    request budget was exhausted when it was not, nor that no text arrived when it did.
    """
    recoveries = "、".join(step_budget.recoveries) or "无"
    state = _reply_state(cause) if cause is not None else "unknown"
    if state == "empty":
        boundary = "上游在输出正文前用尽预算（返回空正文）"
    elif state == "truncated":
        boundary = "上游返回了正文但在预算处被截断（正文不完整，未采用修补结果）"
    else:
        boundary = "上游以 finish_reason=length 停止（无法确认正文是否为空）"
    error = ArtifactGenerationError(
        f"{label}（{scope}）已无法继续拆分；{boundary}。"
        f"本步骤已发出 {step_budget.requests} 次请求"
        f"（上限 {step_budget.max_requests}，未用尽则不是请求次数问题）；"
        f"已尝试的恢复：{recoveries}。"
        f"已成功的窗口和步骤仍然保留，可从同一入口重试。"
    )
    if cause is not None:
        error.__cause__ = cause
    return error


def _global_plan_split_candidate(error: Exception, units: list[dict[str, Any]]) -> bool:
    """Only split when the provider explicitly exhausted the response budget."""
    return len(units) > 1 and "finish_reason=length" in str(error)


def _merge_runtime_brief_dicts(
    current: dict[str, Any] | None, incoming: dict[str, Any] | None
) -> dict[str, Any] | None:
    if current is None:
        return dict(incoming) if incoming is not None else None
    if incoming is None:
        return current
    merged = dict(current)
    for key in (
        "major_conflicts",
        "opening_conditions",
        "possible_endings",
        "key_people",
        "factions",
        "cross_environment_consequences",
        "fact_ids",
        "open_questions",
    ):
        limit = 100 if key == "fact_ids" else 50 if key == "open_questions" else 30
        merged[key] = list(dict.fromkeys([
            *(merged.get(key) or []),
            *(incoming.get(key) or []),
        ]))[:limit]
    sources = dict(merged.get("field_sources") or {})
    for field, refs in (incoming.get("field_sources") or {}).items():
        sources[field] = list(dict.fromkeys([
            *(sources.get(field) or []),
            *(refs or []),
        ]))
    merged["field_sources"] = sources
    return merged


def _merge_global_plan_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine independently validated window payloads into one raw plan."""
    cards: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for output in outputs:
        for raw_card in output.get("cards") or []:
            card = dict(raw_card)
            card_type = str(card.get("type", ""))
            card_title = "" if card_type == "chapter_overview" else str(
                card.get("title", "")
            ).casefold()
            key = (card_type, card_title)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = card
                cards.append(card)
                continue
            # A lead overview or a repeated source alias can occur at a window
            # boundary.  Preserve the first wording while unioning its explicit
            # ownership and retrieval fields; the parent validator still checks
            # the combined closure against the materialization budget.
            for field in (
                "fact_ids",
                "focus",
                "open_questions",
                "runtime_groups",
                "chapter_ids",
                "story_beat_ids",
            ):
                existing[field] = list(dict.fromkeys([
                    *(existing.get(field) or []),
                    *(card.get(field) or []),
                ]))

    brief: dict[str, Any] | None = None
    chapter_briefs: dict[str, dict[str, Any]] = {}
    global_reference: list[str] = []
    open_questions: list[str] = []
    for output in outputs:
        brief = _merge_runtime_brief_dicts(brief, output.get("brief"))
        global_reference = list(dict.fromkeys([
            *global_reference,
            *(output.get("global_reference_fact_ids") or []),
        ]))
        open_questions = list(dict.fromkeys([
            *open_questions,
            *(output.get("open_questions") or []),
        ]))[:50]
        for item in output.get("chapter_briefs") or []:
            chapter_id = item["chapter_id"]
            existing = chapter_briefs.get(chapter_id)
            if existing is None:
                chapter_briefs[chapter_id] = {
                    "chapter_id": chapter_id,
                    "brief": dict(item["brief"]),
                    "reference_fact_ids": list(item.get("reference_fact_ids") or []),
                }
                continue
            existing["brief"] = _merge_runtime_brief_dicts(
                existing["brief"], item.get("brief")
            )
            existing["reference_fact_ids"] = list(dict.fromkeys([
                *existing.get("reference_fact_ids", []),
                *(item.get("reference_fact_ids") or []),
            ]))
    return {
        "cards": cards,
        "brief": brief,
        "chapter_briefs": list(chapter_briefs.values()),
        "global_reference_fact_ids": global_reference,
        "open_questions": open_questions,
    }


def _global_plan_window_chunks(
    units: list[dict[str, Any]],
    profile: RuleProfile,
    fact_sizes: dict[str, int],
    fact_tokens: dict[str, int],
    structure_context: dict[str, Any] | None,
    plan_client: Any,
) -> list[list[dict[str, Any]]]:
    """Pack planning units by the encoded request size, preserving unit ownership."""
    encode_messages = getattr(plan_client, "messages", None)

    def encoded(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        return encode_messages(messages) if callable(encode_messages) else messages

    def facts_chars(units: list[dict[str, Any]]) -> int:
        ids = {fid for unit in units for fid in unit.get("fact_ids", [])}
        return sum(fact_sizes.get(fid, 0) for fid in ids)

    if len(units) <= 1:
        return [list(units)]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for unit in units:
        candidate = [*current, unit]
        messages = _global_plan_messages(
            profile,
            candidate,
            fact_sizes,
            fact_tokens,
            structure_context,
            partial_window=True,
            lead_window=not chunks,
        )
        encoded_chars = len(json.dumps(
            encoded(messages), ensure_ascii=False
        )) + facts_chars(candidate)
        if current and encoded_chars > GLOBAL_PLAN_WINDOW_MAX_CHARS:
            chunks.append(current)
            current = [unit]
        else:
            current = candidate
    if current:
        chunks.append(current)
    # A large fixed schema can exceed the target even for a single unit.  Force
    # progress by bisecting the unit list; the leaf still gets the same validation
    # and the shared request cap prevents endless provider retries.
    if len(chunks) == 1 and len(units) > 1:
        midpoint = max(1, len(units) // 2)
        return [units[:midpoint], units[midpoint:]]
    return chunks


def _recover_global_plan_windows(
    job: ArtifactDraftJob,
    *,
    plan_index: int,
    profile: RuleProfile,
    bundle: ExampleBundle | None,
    plan_units: list[dict[str, Any]],
    plan_facts: list[Any],
    plan_fact_sizes: dict[str, int],
    plan_fact_tokens: dict[str, int],
    plan_context: dict[str, Any] | None,
    chapter_scope: set[str] | None,
    plan_client: Any,
    step_budget: RequestBudget,
    path: str = "",
    lead_window: bool = True,
) -> dict[str, Any]:
    """Recover a length failure by splitting the scope into durable windows.

    A window that owns one planning unit is the leaf: it cannot split, so its single
    budget raise is spent *inside its own persistent step* (see the `chat_kwargs`
    below) and both requests land in that step's own attempt diagnostics.  Every other
    window splits first, because that is the cheaper recovery.  There is exactly one
    raise per window identity and no path that re-runs a step with a larger budget from
    the outside, so a request is never sent twice at the same budget.
    """
    chunks = _global_plan_window_chunks(
        plan_units,
        profile,
        plan_fact_sizes,
        plan_fact_tokens,
        plan_context,
        plan_client,
    )
    if len(chunks) == 1:
        step_budget.recoveries.append(f"窗口 {path or plan_index} 已无法拆分")
        raise _unsplittable_window_error(
            f"全局规划窗口 {path or plan_index}",
            f"{len(plan_units)} 个规划单元、{len(plan_facts)} 条事实",
            step_budget,
        )
    step_budget.recoveries.append(
        f"拆分全局规划窗口 {path or f'{plan_index}（整范围）'} 为 {len(chunks)} 个窗口"
    )
    outputs: list[dict[str, Any]] = []
    for offset, window_units in enumerate(chunks, start=1):
        window_path = f"{path}.{offset}" if path else str(offset)
        window_stage = (
            f"global_plan_window_{plan_index}_"
            f"{window_path.replace('.', '_')}"
        )
        window_ids = {
            fact_id for unit in window_units for fact_id in unit.get("fact_ids", [])
        }
        window_facts = [fact for fact in plan_facts if fact.id in window_ids]
        window_sizes = {
            fact_id: value for fact_id, value in plan_fact_sizes.items()
            if fact_id in window_ids
        }
        window_tokens = {
            fact_id: value for fact_id, value in plan_fact_tokens.items()
            if fact_id in window_ids
        }
        window_lead = lead_window and offset == 1
        leaf_window = len(window_units) == 1
        window_fact_payload = _fact_payload_from_facts(window_facts)
        window_messages = _global_plan_messages(
            profile,
            window_units,
            window_sizes,
            window_tokens,
            plan_context,
            partial_window=True,
            lead_window=window_lead,
            # Every window, leaf or not, carries the facts it must cite; the index
            # summary is navigation only.
            leaf_window=leaf_window,
            fact_payload=window_fact_payload,
        )
        encode = getattr(plan_client, "messages", None)
        encoded_size = len(json.dumps(
            encode(window_messages) if callable(encode) else window_messages,
            ensure_ascii=False,
        ))
        if encoded_size > MAX_FACT_INPUT_CHARS:
            if len(window_units) == 1:
                raise ArtifactGenerationError(
                    f"窗口 {window_path} 连同事实依据约 {encoded_size:,} 字符，超过 "
                    f"{MAX_FACT_INPUT_CHARS:,} 的输入边界；没有发送请求，也没有截断事实正文。"
                )
            outputs.extend([])
            outputs.append(_recover_global_plan_windows(
                job,
                plan_index=plan_index,
                profile=profile,
                bundle=bundle,
                plan_units=window_units,
                plan_facts=window_facts,
                plan_fact_sizes=window_sizes,
                plan_fact_tokens=window_tokens,
                plan_context=plan_context,
                chapter_scope=chapter_scope,
                plan_client=plan_client,
                step_budget=step_budget,
                path=window_path,
                lead_window=window_lead,
            ))
            continue

        def recover_child(
            raw: Any,
            error: Exception,
            *,
            child_units=window_units,
            child_path=window_path,
            child_lead=window_lead,
        ) -> Any:
            if not _global_plan_split_candidate(error, child_units):
                if "finish_reason=length" not in str(error):
                    raise error
                step_budget.recoveries.append(f"窗口 {child_path} 已无法拆分")
                raise _unsplittable_window_error(
                    f"全局规划窗口 {child_path}",
                    f"{len(child_units)} 个规划单元、{len(window_facts)} 条事实",
                    step_budget,
                    cause=error,
                )
            return _recover_global_plan_windows(
                job,
                plan_index=plan_index,
                profile=profile,
                bundle=bundle,
                plan_units=child_units,
                plan_facts=window_facts,
                plan_fact_sizes=window_sizes,
                plan_fact_tokens=window_tokens,
                plan_context=plan_context,
                chapter_scope=chapter_scope,
                plan_client=plan_client,
                step_budget=step_budget,
                path=child_path,
                lead_window=child_lead,
            )

        outputs.append(_run_json_step(
            job,
            stage=window_stage,
            step_index=1,
            input_payload={
                "prompt_version": GLOBAL_PLAN_PROMPT_VERSION,
                "window_path": window_path,
                # The leaf contract is part of the step identity, so a window produced by
                # the older, larger contract is never reused for it.
                "leaf_contract": "compact-v1" if leaf_window else "window",
                "profile": profile.model_dump(mode="json"),
                "units": _compact_global_units(window_units),
                "fact_sizes": window_sizes,
                "fact_tokens": window_tokens,
                "fact_digest": _gestalt_fact_digest(window_facts),
                "facts_in_prompt": True,
                "structure": plan_context,
            },
            input_refs={
                "unit_ids": [unit["id"] for unit in window_units],
                "fact_ids": sorted(window_ids),
                "window_path": window_path,
            },
            messages=window_messages,
            client=plan_client,
            max_tokens=GLOBAL_PLAN_MAX_TOKENS,
            max_budget=GLOBAL_PLAN_WINDOW_MAX_TOKENS,
            validator=lambda raw, owned_units=window_units, owned_facts=window_facts: (
                _validate_global_plan_window(
                    raw,
                    owned_units,
                    owned_facts,
                    profile,
                    bundle,
                    chapter_scope,
                )
            ),
            recover_error=recover_child,
            # Only a window that can still be reduced re-enters recovery without a new
            # request; an unsplittable leaf asks the provider again on retry.
            recover_existing_failure=(
                lambda raw, error, owned=window_units: _global_plan_split_candidate(
                    error, owned
                )
            ),
            # The leaf spends this window's single raise inside its own step, so both
            # requests are recorded in this step's own attempt diagnostics.
            chat_kwargs=(
                {"escalate_max_tokens": GLOBAL_PLAN_WINDOW_MAX_TOKENS}
                if leaf_window
                else None
            ),
            step_budget=step_budget,
            budget_identity=f"global_plan:{plan_index}:{window_path}",
        ))
    return _merge_global_plan_outputs(outputs)


def _run_global_plan_scope(
    job: ArtifactDraftJob,
    *,
    plan_index: int,
    profile: RuleProfile,
    bundle: ExampleBundle | None,
    plan_units: list[dict[str, Any]],
    plan_facts: list[Any],
    plan_fact_sizes: dict[str, int],
    plan_fact_tokens: dict[str, int],
    plan_context: dict[str, Any] | None,
    chapter_scope: set[str] | None,
    plan_client: Any,
) -> dict[str, Any]:
    """Run one full global plan, recovering length exhaustion through windows."""
    step_budget = RequestBudget(max_requests=GLOBAL_PLAN_MAX_REQUESTS)
    scope_is_leaf = len(plan_units) == 1
    scope_facts = _fact_payload_from_facts(plan_facts)
    messages = _global_plan_messages(
        profile,
        plan_units,
        plan_fact_sizes,
        plan_fact_tokens,
        plan_context,
        # A single-unit scope is already a leaf and gets the compact contract; every
        # scope, however, now carries the original facts it must cite, so a multi-unit
        # request that succeeds directly cannot plan without per-ID evidence.
        leaf_window=scope_is_leaf,
        fact_payload=scope_facts,
    )
    oversize_scope = False
    encoded_messages = getattr(plan_client, "messages", None)
    encoded_size = len(json.dumps(
        encoded_messages(messages) if callable(encoded_messages) else messages,
        ensure_ascii=False,
    ))
    if encoded_size > MAX_FACT_INPUT_CHARS:
        if scope_is_leaf:
            # Nothing smaller to try: refuse before sending, without truncating facts.
            raise ArtifactGenerationError(
                f"全局规划范围连同事实依据约 {encoded_size:,} 字符，超过 "
                f"{MAX_FACT_INPUT_CHARS:,} 的输入边界；没有发送请求，也没有截断事实正文。"
            )
        # Too large with the facts included: start from the windows instead of sending an
        # oversize request.  This must not bypass the root step, so the recovery runs
        # through the root's own attempt and its result still faces the full parent
        # validation.
        oversize_scope = True
    input_payload = {
        "prompt_version": GLOBAL_PLAN_PROMPT_VERSION,
        "leaf_contract": "compact-v1" if scope_is_leaf else "scope",
        "profile": profile.model_dump(mode="json"),
        "units": _compact_global_units(plan_units),
        "fact_sizes": plan_fact_sizes,
        "fact_tokens": plan_fact_tokens,
        "fact_digest": _gestalt_fact_digest(plan_facts),
        "facts_in_prompt": True,
        "structure": plan_context,
    }
    input_refs = {
        "unit_ids": [unit["id"] for unit in plan_units],
        "fact_ids": [fact.id for fact in plan_facts],
    }

    def recover(raw: Any, error: Exception) -> Any:
        if not _global_plan_split_candidate(error, plan_units):
            if "finish_reason=length" not in str(error):
                raise error
            step_budget.recoveries.append(f"全局规划范围 {plan_index} 已无法拆分")
            raise _unsplittable_window_error(
                f"全局规划范围 {plan_index}",
                f"{len(plan_units)} 个规划单元、{len(plan_facts)} 条事实",
                step_budget,
                cause=error,
            )
        return _recover_global_plan_windows(
            job,
            plan_index=plan_index,
            profile=profile,
            bundle=bundle,
            plan_units=plan_units,
            plan_facts=plan_facts,
            plan_fact_sizes=plan_fact_sizes,
            plan_fact_tokens=plan_fact_tokens,
            plan_context=plan_context,
            chapter_scope=chapter_scope,
            plan_client=plan_client,
            step_budget=step_budget,
        )

    current_hash = _stable_hash({
        **input_payload,
        "_model_id": job.model_id,
        "_fake_model": job.fake_model,
    })
    current_step = storage.load_artifact_job_step(
        job.id, "global_plan", plan_index, current_hash
    )

    def _stored_length_failure(step_record: Any) -> tuple[Any, RuntimeError] | None:
        """The newest attempt the provider stopped for length, if one exists.

        A recovery can fail after the length reply was already stored, so the step's
        last attempt is then the recovery's error rather than the budget stop.  That
        earlier attempt is still the reason to resume the durable windows instead of
        paying for the same full-scope request again.
        """
        if not step_record or step_record.get("status") != "failed":
            return None
        for attempt in reversed(step_record.get("attempts") or []):
            error_text = str(attempt.get("error") or "")
            if "finish_reason=length" in error_text:
                return attempt.get("response"), RuntimeError(error_text)
        return None

    seed = _stored_length_failure(current_step)
    if seed is None and (current_step is None or current_step.get("status") != "succeeded"):
        # An upgrade recovery (or a plain recovery) that failed leaves the current step
        # failed with the child window's error, not with a length reply.  The stored
        # failure of the prior version can still seed the same recovery, so a retry
        # resumes the durable windows instead of paying for the full-scope request that
        # could not finish.
        previous_payload = {
            **input_payload,
            "prompt_version": GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            "_model_id": job.model_id,
            "_fake_model": job.fake_model,
        }
        previous_hash = _stable_hash(previous_payload)
        previous_step = storage.load_artifact_job_step(
            job.id, "global_plan", plan_index, previous_hash
        )
        seed = _stored_length_failure(previous_step)

    startup_recovery = None
    if oversize_scope:
        def startup_recovery() -> Any:  # noqa: F811 - the oversize path takes precedence
            return _recover_global_plan_windows(
                job,
                plan_index=plan_index,
                profile=profile,
                bundle=bundle,
                plan_units=plan_units,
                plan_facts=plan_facts,
                plan_fact_sizes=plan_fact_sizes,
                plan_fact_tokens=plan_fact_tokens,
                plan_context=plan_context,
                chapter_scope=chapter_scope,
                plan_client=plan_client,
                step_budget=step_budget,
            )
    # Resuming recovery without a request is only right while the scope can still be
    # reduced; a single-unit scope must ask the provider again, and the caller's
    # recovery checks the same predicate.
    if not oversize_scope and seed is not None and _global_plan_split_candidate(
        seed[1], plan_units
    ):
        # A prompt-only version change (or an earlier failed recovery) must not make the
        # same known-failing full-scope request run again.  The callable runs inside the
        # current step's attempt, so the recovery's start, failure and success are all
        # recorded on this parent step.
        seed_response, seed_error = seed

        def startup_recovery() -> Any:
            return recover(seed_response, seed_error)

    return _run_json_step(
        job,
        stage="global_plan",
        step_index=plan_index,
        input_payload=input_payload,
        input_refs=input_refs,
        messages=messages,
        client=plan_client,
        max_tokens=GLOBAL_PLAN_MAX_TOKENS,
        validator=lambda raw: _validate_global_plan(
            raw,
            plan_units,
            plan_facts,
            profile,
            bundle,
            chapter_scope,
        ),
        recover_error=recover,
        # Re-entering the window recovery without a request only makes sense while the
        # scope can still be reduced; a single-unit scope must ask the provider again.
        recover_existing_failure=(
            lambda raw, error: _global_plan_split_candidate(error, plan_units)
        ),
        # A single-unit scope is already a leaf: its one raise belongs in this step.
        chat_kwargs=(
            {"escalate_max_tokens": GLOBAL_PLAN_WINDOW_MAX_TOKENS}
            if len(plan_units) == 1
            else None
        ),
        max_budget=GLOBAL_PLAN_WINDOW_MAX_TOKENS,
        # A root that had to start from windows never sent its own request, so its
        # correction must not become that oversize request.
        allow_correction=not oversize_scope,
        step_budget=step_budget,
        budget_identity=f"global_plan:{plan_index}:root",
        startup_recovery=startup_recovery,
    )


def _compact_global_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove provenance already recoverable from fact IDs before global planning."""
    keys = (
        "id",
        "kind",
        "title",
        "summary",
        "fact_ids",
        "entity_keys",
        "relationship_hints",
        "open_questions",
    )
    return [{key: unit.get(key) for key in keys} for unit in units]


def _prompt_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Serialise a plan for a prompt without planning-only keys."""
    # Planning-only keys are dropped so a plan that carries none of them still
    # serialises byte-identically and keeps its cached materialization step.
    return {
        key: value
        for key, value in plan.items()
        if key not in {"identity_key", "situation", "case_scope"}
    }


def _materialization_messages(
    profile: RuleProfile,
    plan: dict[str, Any],
    facts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    definition = next(item for item in profile.card_definitions if item.type == plan["type"])
    list_fields = sorted(
        LIST_FIELDS & set([*definition.required_fields, *definition.optional_fields])
    )
    required_shapes = {
        field: ("array of concise strings" if field in LIST_FIELDS else "concise string")
        for field in definition.required_fields
    }
    gm_authored_fields = sorted(
        GM_AUTHORED_FIELDS & set([*definition.required_fields, *definition.optional_fields])
    )
    system = (
        "You materialize exactly one planned TRPG preparation card by rereading its original reviewed "
        "facts. Return only one JSON object with keys cards and open_questions; cards must contain "
        "exactly one card. Use the planned type and title exactly. The card must contain type, title, "
        "subtitle, fact_ids, fields, field_sources, and open_questions. Runtime grouping is owned by "
        "the supplied plan and must not be returned or changed. Use only supplied original "
        "facts. Every factual field must cite one or more fact_ids from that card, and every populated "
        "field must have field_sources. Do not fill gaps from the planning summary; record an open "
        "question instead. Every required field listed below must be present and non-empty. When the "
        "source does not establish a required field, name that gap inside the field instead of "
        "inventing detail, and keep the rest of the field to what the source supports"
        + (
            "; the only place for a working assumption is assumed_state. "
            if "assumed_state" in set([*definition.required_fields, *definition.optional_fields])
            else ", leaving the field's uncertainty in open_questions. "
        )
        + MATERIALIZATION_FIDELITY_GUIDANCE
        + "Before returning, check the fields object key by key against REQUIRED_FIELD_SHAPES_JSON; "
        "array-shaped fields must be JSON arrays, never scalar strings. "
        "source_statistics, when supported, is a concise string quoting only supplied source information, "
        "never a stat-block object or generated rules. current_stage must be a string if included; "
        "omit unsupported optional fields instead of null. field_sources must be an object mapping "
        "field names to arrays of fact IDs, never a flat array. "
        "For every optional field, actively inspect the supplied facts and include it whenever the "
        "facts support useful table-side content; omit it only when no useful content exists. In "
        "particular, check relevant_characters, hidden_clues, and return_changes separately. "
        + (
            "assumed_state is one concise declarative sentence naming the situation this material "
            "assumes, never a question about what happened. "
            if "assumed_state" in set([*definition.required_fields, *definition.optional_fields])
            else ""
        )
        + (
            "table_questions holds two to four short prompts that invite the table to answer from "
            "their own game, grounded in this card's fiction. "
            if "table_questions" in set([*definition.required_fields, *definition.optional_fields])
            else ""
        )
        + "A preparation card is read at the table, not studied, so every array field stays "
        "scannable mid-scene: usable_features holds two to five named features, each one line "
        "pairing a trigger with its effect, and never an inventory, fee schedule, calendar, "
        "roster, or gazetteer; supplied reference detail of that kind belongs in "
        "source_statistics, and unsupported detail is left out because its facts stay "
        "retrievable. impulses holds three to six short phrases, and hazards, triggers, and "
        "phase_changes stay equally short. Begin environment_type with one scene rhythm — "
        "探索, 社交, 穿越, or 事件 — followed by a short qualifier, so a GM reads the scene's "
        "shape at a glance. "
        "Fields listed in GM_AUTHORED_FIELDS_JSON may be concise GM-authored operating prompts "
        "grounded in the card's source-backed fiction; mark those prompts with no field_sources unless "
        "the source explicitly supports them. Never use that exception for factual claims. "
        f"REQUIRED_FIELD_SHAPES_JSON={json.dumps(required_shapes, ensure_ascii=False)} "
        f"GM_AUTHORED_FIELDS_JSON={json.dumps(gm_authored_fields, ensure_ascii=False)} "
        + SOURCE_SEMANTIC_GUIDANCE
        + CLOCK_SEMANTIC_GUIDANCE
        + BOARD_REFERENCE_BOUNDARY
        + "A consequence or reaction the source does not state must not be given a fact ID; "
        "say what the source does establish and keep the rest as an open question. Preserve the "
        "source's own uncertainty: something that appears, is claimed, or is reported is not an "
        "established fact. A chapter or page reference (for example an interview the source "
        "points to) is not a display material unless the source shows a document the players may "
        "read; route such references to the card's people or clue fields. "
        + "All user-facing text must be Chinese."
    )
    user = (
        "[TASK:prep:artifact_materialize]\n"
        f"PROMPT_VERSION_JSON={json.dumps(PROMPT_VERSION)}\n"
        f"BOARD_PROFILE_JSON={json.dumps(_board_prompt_payload(profile), ensure_ascii=False)}\n"
        f"CARD_DEFINITION_JSON={json.dumps(definition.model_dump(mode='json'), ensure_ascii=False)}\n"
        f"LIST_FIELDS_JSON={json.dumps(list_fields, ensure_ascii=False)}\n"
        f"CARD_PLAN_JSON={json.dumps(_prompt_plan(plan), ensure_ascii=False)}\n"
        f"ORIGINAL_FACTS_JSON={json.dumps(facts, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_materialized_card(
    raw: Any,
    bundle: ExampleBundle,
    profile: RuleProfile,
    plan: dict[str, Any],
    *,
    model_id: str,
    prep_unit_id: str | None = None,
) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        # The global plan owns the card's fact closure. Models may omit a
        # supporting fact while rereading the card, but that omission must not
        # shrink the canonical set selected by the planner.
        normalized = dict(raw)
        normalized_cards: list[Any] = []
        for item in raw["cards"]:
            if not isinstance(item, dict):
                normalized_cards.append(item)
                continue
            card = dict(item)
            declared_ids = card.get("fact_ids")
            if isinstance(declared_ids, list):
                card["fact_ids"] = list(dict.fromkeys([*declared_ids, *plan["fact_ids"]]))
            elif declared_ids is None:
                card["fact_ids"] = list(plan["fact_ids"])
            normalized_cards.append(card)
        normalized["cards"] = normalized_cards
        raw = normalized
    cards, questions = _validate_and_build(
        raw,
        bundle,
        profile,
        model_id=model_id,
        require_runtime_anchor=False,
        prep_unit_id=prep_unit_id,
    )
    if len(cards) != 1:
        raise ArtifactGenerationError("单卡落地阶段必须且只能返回一张卡")
    card = cards[0]
    if card.type != plan["type"] or card.title != plan["title"]:
        raise ArtifactGenerationError(
            f"单卡落地必须沿用计划类型与标题: {plan['type']} / {plan['title']}"
        )
    card = card.model_copy(
        update={
            "runtime_group_ids": list(plan.get("runtime_group_ids") or []),
            "chapter_ids": list(plan.get("chapter_ids") or []),
            "story_beat_ids": list(plan.get("story_beat_ids") or []),
        }
    )
    return {
        "cards": [card.model_dump(mode="json")],
        "open_questions": questions,
    }


def _validated_card_output(
    raw: Any,
    bundle: ExampleBundle,
    profile: RuleProfile,
    *,
    model_id: str,
    require_runtime_anchor: bool = True,
    prep_unit_id: str | None = None,
) -> dict[str, Any]:
    cards, questions = _validate_and_build(
        raw,
        bundle,
        profile,
        model_id=model_id,
        require_runtime_anchor=require_runtime_anchor,
        prep_unit_id=prep_unit_id,
    )
    return {
        "cards": [card.model_dump(mode="json") for card in cards],
        "open_questions": questions,
    }


def _generate_direct_step(
    job: ArtifactDraftJob,
    bundle: ExampleBundle,
    profile: RuleProfile,
    client: Any,
    facts: list[Any],
    *,
    prep_unit_id: str | None = None,
) -> tuple[list[DerivedCard], list[str]]:
    fact_payload = _fact_payload_from_facts(facts)
    direct_bundle = bundle.model_copy(update={"facts": facts, "cards": [], "plans": []})
    messages = _prompt_messages(
        direct_bundle,
        profile,
        fact_payload,
        require_runtime_anchor=True,
    )
    output = _run_json_step(
        job,
        stage="direct_generation",
        step_index=1,
        input_payload={
            "prompt_version": PROMPT_VERSION,
            "profile": profile.model_dump(mode="json"),
            "facts": fact_payload,
        },
        input_refs={"fact_ids": [fact.id for fact in facts]},
        messages=messages,
        client=client,
        max_tokens=ARTIFACT_MAX_TOKENS,
        validator=lambda raw: _validated_card_output(
            raw,
            direct_bundle,
            profile,
            model_id=getattr(client, "model", "configured-model"),
            require_runtime_anchor=True,
            prep_unit_id=prep_unit_id,
        ),
    )
    return (
        [DerivedCard.model_validate(card) for card in output["cards"]],
        list(output.get("open_questions") or []),
    )


def _identities_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether two plans may still be one card given what identity already decided.

    An absent key is unknown, not a conflict: the pre-identity title rule keeps
    working for every plan the identity step did not judge.  Two different keys are a
    decision that these are different entities, so a title match must not override it.
    """
    left_key = left.get("identity_key")
    right_key = right.get("identity_key")
    if not left_key or not right_key:
        return True
    return left_key == right_key


def _merge_match(
    merged: list[dict[str, Any]], incoming: dict[str, Any]
) -> dict[str, Any] | None:
    """Pick the card an incoming plan joins: title match first, identity second.

    A title match still wins, but only between plans the identity step did not judge
    to be different entities: a shared title is weak evidence, and once identity has
    read the facts and said "different", the title must not overrule it.  Identity is
    the fallback when no title match exists.
    """
    title = incoming["title"].casefold()
    match = next(
        (
            item
            for item in merged
            if item["type"] == incoming["type"]
            and item["title"].casefold() == title
            and _identities_compatible(item, incoming)
        ),
        None,
    )
    if match is not None:
        return match
    identity = incoming.get("identity_key")
    if not identity:
        return None
    return next(
        (
            item
            for item in merged
            if item["type"] == incoming["type"] and item.get("identity_key") == identity
        ),
        None,
    )


class IdentityClusterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    plan_ids: list[str] = Field(min_length=1, max_length=60)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("plan_ids")
    @classmethod
    def unique_plan_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class IdentityReconciliationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clusters: list[IdentityClusterPayload] = Field(min_length=1, max_length=200)


def _plan_thread_ids(
    plans: list[dict[str, Any]], bundle: ExampleBundle
) -> dict[str, set[str]]:
    dispositions = {item.fact_id: item for item in bundle.fact_dispositions}
    by_plan: dict[str, set[str]] = {}
    for plan in plans:
        threads: set[str] = set()
        for fact_id in plan.get("fact_ids") or []:
            disposition = dispositions.get(fact_id)
            if disposition is not None:
                threads.update(disposition.thread_ids or [])
        by_plan[plan["id"]] = threads
    return by_plan


def _plan_scope_key(plan: dict[str, Any]) -> tuple[str, ...]:
    """The preparation scope a plan was planned in: its reviewed chapters, if any."""
    return tuple(sorted(plan.get("chapter_ids") or []))


def _identity_candidate_components(
    plans: list[dict[str, Any]], bundle: ExampleBundle
) -> list[list[str]]:
    """Cards that may describe one entity, grouped for reconciliation.

    Two signals nominate a group.  The original one is a shared storyline or runtime
    group joined across chapters.  The added one is same-type cards inside a single
    preparation scope that cite a shared fact under different titles: a board with no
    chapters never reached this step at all, so an entity split under two titles could
    never be reconciled, no matter what the merge rule said.

    A shared fact is only a nomination.  Nothing merges on this signal alone; the
    reconcile step reads the fact text and decides, and a cluster of one means no
    merge.
    """
    if len(plans) < 2:
        return []
    threads_by_plan = _plan_thread_ids(plans, bundle)
    by_id = {plan["id"]: plan for plan in plans}
    parent = {plan["id"]: plan["id"] for plan in plans}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    buckets: dict[tuple[str, str], list[str]] = {}
    for plan in plans:
        plan_id = plan["id"]
        signals = {f"thread:{item}" for item in threads_by_plan.get(plan_id, set())}
        signals.update(f"group:{item}" for item in plan.get("runtime_group_ids") or [])
        for signal in signals:
            buckets.setdefault((plan["type"], signal), []).append(plan_id)
    for members in buckets.values():
        for other in members[1:]:
            union(members[0], other)

    # Same-type, same-scope fact sharing.  A card in another chapter is left to the
    # cross-chapter signal above, so this can never widen a cross-chapter judgement.
    same_scope_members: set[str] = set()
    fact_index: dict[tuple[str, tuple[str, ...], str], list[str]] = {}
    for plan in plans:
        if plan["type"] not in IDENTITY_SAME_SCOPE_TYPES:
            continue
        scope = _plan_scope_key(plan)
        for fact_id in plan.get("fact_ids") or []:
            index_key = (plan["type"], scope, fact_id)
            fact_index.setdefault(index_key, []).append(plan["id"])
    for members in fact_index.values():
        if len(members) < 2:
            continue
        same_scope_members.update(members)
        for other in members[1:]:
            union(members[0], other)

    components: dict[str, list[str]] = {}
    for plan in plans:
        components.setdefault(find(plan["id"]), []).append(plan["id"])
    candidates: list[list[str]] = []
    for members in components.values():
        if len(members) < 2:
            continue
        if not any(member in same_scope_members for member in members):
            chapters = {
                chapter_id
                for plan_id in members
                for chapter_id in by_id[plan_id].get("chapter_ids") or []
            }
            if len(chapters) < 2:
                continue
        candidates.append(sorted(members))
    return sorted(candidates, key=lambda item: item[0])


def _identity_candidate_payload(
    component: list[str],
    by_id: dict[str, dict[str, Any]],
    facts_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for plan_id in component:
        plan = by_id[plan_id]
        facts: list[dict[str, str]] = []
        for fact_id in list(plan.get("fact_ids") or [])[:IDENTITY_CANDIDATE_MAX_FACTS]:
            fact = facts_by_id.get(fact_id)
            if fact is None:
                continue
            text = " ".join(str(fact.get("text") or "").split())
            facts.append({"id": fact_id, "text": text[:IDENTITY_CANDIDATE_FACT_CHARS]})
        payload.append(
            {
                "plan_id": plan_id,
                "type": plan["type"],
                "title": plan["title"],
                "purpose": plan.get("purpose"),
                "focus": list(plan.get("focus") or [])[:8],
                "chapter_ids": list(plan.get("chapter_ids") or []),
                "facts": facts,
            }
        )
    return payload


def _identity_reconcile_messages(
    groups: list[list[dict[str, Any]]],
) -> list[dict[str, str]]:
    system = (
        "You reconcile card identity inside one TRPG preparation. Each candidate group "
        "lists planned cards that may describe the same thing: they share a storyline, "
        "a runtime group, or at least one source fact. Sharing a fact is only a hint, so "
        "read the supplied fact text before deciding. Split every group into clusters "
        "that describe the SAME single playable person, the SAME single place, or the "
        "SAME single named threat. Two cards about one entity under different titles "
        "belong in one cluster. Two different people, two different places, or two "
        "different threats stay in separate clusters even when they cite the same fact, "
        "appear in one scene, or were introduced by one another; a relation named in "
        "one card is never evidence that two people are one person. Never place two "
        "separate playable people in one cluster. A cluster of one card is a valid "
        "answer and means no merge. Return only one JSON object with key clusters, each "
        "item having label and plan_ids. label is a short Chinese name of the shared "
        "person, place or threat. Every candidate plan_id must appear in exactly one "
        "cluster. Use only supplied plan_ids and only what the supplied fact text "
        "states; never invent an id, a person or a fact. "
        + SOURCE_SEMANTIC_GUIDANCE + CLOCK_SEMANTIC_GUIDANCE
        + "All user-facing text must be Chinese."
    )
    user = json.dumps(
        {
            "schema": IdentityReconciliationPayload.model_json_schema(),
            "candidate_groups": groups,
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_identity_clusters(raw: Any, allowed_ids: set[str]) -> dict[str, Any]:
    try:
        payload = IdentityReconciliationPayload.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(f"跨章节身份重整 JSON 不符合约定: {error}") from error
    seen: list[str] = []
    for cluster in payload.clusters:
        unknown = [item for item in cluster.plan_ids if item not in allowed_ids]
        if unknown:
            raise ArtifactGenerationError(f"身份重整返回了候选之外的卡片: {unknown}")
        seen.extend(cluster.plan_ids)
    if len(seen) != len(set(seen)):
        raise ArtifactGenerationError("身份重整把同一张候选卡放进了多个簇")
    missing = sorted(allowed_ids - set(seen))
    if missing:
        raise ArtifactGenerationError(f"身份重整未覆盖候选卡: {missing}")
    return payload.model_dump(mode="json")


def _identity_keys_from_clusters(output: dict[str, Any]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for cluster in output.get("clusters") or []:
        members = list(dict.fromkeys(cluster.get("plan_ids") or []))
        if len(members) < 2:
            continue
        label = " ".join(str(cluster.get("label") or "").split()).casefold()
        # \W is Unicode-aware, so a Chinese label keeps its characters instead of
        # collapsing to the "shared" fallback an ASCII-only class produced.
        slug = re.sub(r"[\W_]+", "_", label, flags=re.UNICODE).strip("_")[:40] or "shared"
        digest = hashlib.sha256("|".join(sorted(members)).encode("utf-8")).hexdigest()[:10]
        for plan_id in members:
            keys[plan_id] = f"identity_{slug}_{digest}"
    return keys


def _assign_plan_identities(
    job: ArtifactDraftJob,
    plans: list[dict[str, Any]],
    bundle: ExampleBundle,
    question_summary: Any,
    client: Any,
) -> list[dict[str, Any]]:
    """Give cross-chapter cards one identity before the merge decides card count."""
    components = _identity_candidate_components(plans, bundle)
    if not components:
        return plans
    by_id = {plan["id"]: plan for plan in plans}
    facts_by_id = {item["id"]: item for item in _fact_payload_from_facts(bundle.facts)}
    oversized = [item for item in components if len(item) > MAX_IDENTITY_CANDIDATE_PLANS]
    components = [item for item in components if len(item) <= MAX_IDENTITY_CANDIDATE_PLANS]
    if oversized:
        question_summary.add([
            f"有 {len(oversized)} 组跨章节候选超过 {MAX_IDENTITY_CANDIDATE_PLANS} 张卡，"
            "本轮未做身份重整，这些卡片保持各自独立。"
        ])
    identity_by_plan: dict[str, str] = {}
    chunk: list[list[dict[str, Any]]] = []
    chunk_ids: list[str] = []
    chunk_type: str | None = None
    chunk_chars = 0
    step_index = 0

    def flush() -> None:
        nonlocal chunk, chunk_ids, chunk_type, chunk_chars, step_index
        if not chunk:
            return
        step_index += 1
        allowed = set(chunk_ids)
        try:
            output = _run_json_step(
                job,
                stage="identity_reconcile",
                step_index=step_index,
                input_payload={
                    "prompt_version": IDENTITY_RECONCILE_PROMPT_VERSION,
                    "candidate_groups": chunk,
                },
                input_refs={
                    "plan_ids": sorted(allowed),
                    # The fact text is part of the request and of its cached input, so
                    # an edited fact invalidates the identity judgement that used it.
                    "fact_ids": sorted(
                        {
                            fact_id
                            for plan_id in allowed
                            for fact_id in by_id[plan_id].get("fact_ids") or []
                        }
                    ),
                },
                messages=_identity_reconcile_messages(chunk),
                client=client,
                max_tokens=IDENTITY_RECONCILE_MAX_TOKENS,
                validator=lambda raw, expected=allowed: _validate_identity_clusters(
                    raw, expected
                ),
            )
        except Exception as error:  # noqa: BLE001
            question_summary.add([
                f"第 {step_index} 组跨章节身份重整失败：{error}；该组卡片保持各自独立。"
            ])
        else:
            identity_by_plan.update(_identity_keys_from_clusters(output))
        chunk = []
        chunk_ids = []
        chunk_type = None
        chunk_chars = 0

    for component in components:
        group = _identity_candidate_payload(component, by_id, facts_by_id)
        size = len(json.dumps(group, ensure_ascii=False))
        # Components are single-type, and a chunk stays single-type with them:
        # a mixed prompt let the model cluster across types, and _merge_match is
        # type-scoped in both passes, so those keys could never merge anything.
        component_type = by_id[component[0]]["type"]
        if chunk and (
            component_type != chunk_type
            or chunk_chars + size > IDENTITY_GROUP_CHAR_BUDGET
            or len(chunk_ids) + len(component) > IDENTITY_MAX_PLANS_PER_CHUNK
        ):
            flush()
        chunk.append(group)
        chunk_ids.extend(component)
        chunk_type = component_type
        chunk_chars += size
    flush()
    if not identity_by_plan:
        return plans
    return [
        (
            {**plan, "identity_key": identity_by_plan[plan["id"]]}
            if plan["id"] in identity_by_plan
            else plan
        )
        for plan in plans
    ]


def _plan_merge_keys(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """Every key under which this plan should have merged with another survivor."""
    keys = [("title", f"{plan['type']}\x00{plan['title'].casefold()}")]
    identity = plan.get("identity_key")
    if isinstance(identity, str) and identity:
        keys.append(("identity", f"{plan['type']}\x00{identity}"))
    return keys


def _report_unmerged_identities(
    plans: list[dict[str, Any]], question_summary: Any
) -> None:
    """Report survivors that still share a merge key, with the size that split them."""
    parent = {index: index for index in range(len(plans))}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    seen_keys: dict[tuple[str, str], int] = {}
    for index, plan in enumerate(plans):
        for key in _plan_merge_keys(plan):
            first = seen_keys.setdefault(key, index)
            left, right = find(first), find(index)
            if left != right:
                parent[right] = left
    groups: dict[int, list[int]] = {}
    for index in range(len(plans)):
        groups.setdefault(find(index), []).append(index)
    duplicated = [members for members in groups.values() if len(members) > 1]
    if not duplicated:
        return
    preview = "；".join(
        "/".join(dict.fromkeys(plans[index]["title"] for index in members[:3]))[:60]
        + f"（合并后 {sum(len(plans[index].get('fact_ids') or []) for index in members)} 条事实）"
        for members in duplicated[:3]
    )
    question_summary.add([
        f"有 {len(duplicated)} 组卡片共享同一合并键但仍各自落地。"
        f"合并守卫只在事实闭包不超过单卡预算时合并：{preview}。"
    ])


def _fact_closure_budget(
    fact_ids: list[str], facts_by_id: dict[str, Any]
) -> tuple[int, int]:
    """Measure one card's fact closure the way the materialize stage measures it."""
    selected = [facts_by_id[fact_id] for fact_id in fact_ids if fact_id in facts_by_id]
    payload = json.dumps(_fact_payload_from_facts(selected), ensure_ascii=False)
    return len(payload), _estimate_tokens(payload)


def _merge_chapter_plans(
    existing_plans: list[dict[str, Any]],
    incoming_plans: list[dict[str, Any]],
    facts_by_id: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Merge a repeated cross-chapter card only while it remains materializable.

    With facts supplied, the combined closure is measured against the same
    character and token budget the materialize stage enforces, so a merge is
    refused for the only reason that matters: the survivor could not be
    materialized.  Without facts the caller falls back to the fact-count ceiling,
    which callers holding no fact table still rely on.
    """
    merged = [dict(item) for item in existing_plans]
    for incoming in incoming_plans:
        match = _merge_match(merged, incoming)
        combined_fact_ids = list(
            dict.fromkeys(
                [*(match or {}).get("fact_ids", []), *incoming.get("fact_ids", [])]
            )
        )
        if match is None:
            merged.append(dict(incoming))
            continue
        if facts_by_id is None:
            too_large = len(combined_fact_ids) > MAX_MERGED_FACT_IDS
        else:
            chars, tokens = _fact_closure_budget(combined_fact_ids, facts_by_id)
            too_large = (
                chars > MAX_CARD_FACT_INPUT_CHARS or tokens > MAX_CARD_FACT_INPUT_TOKENS
            )
        if too_large:
            merged.append(dict(incoming))
            continue
        match["fact_ids"] = combined_fact_ids
        for field_name in (
            "focus",
            "open_questions",
            "runtime_group_ids",
            "chapter_ids",
            "story_beat_ids",
        ):
            match[field_name] = list(
                dict.fromkeys(
                    [*match.get(field_name, []), *incoming.get(field_name, [])]
                )
            )
        # The survivor now stands for both plans, so it must carry the identity
        # too; otherwise a later member of the same cluster stays apart unseen.
        match["identity_key"] = match.get("identity_key") or incoming.get("identity_key")
    return merged


def _local_digest_fact_limit(profile: RuleProfile) -> int | None:
    return LOCAL_DIGEST_MAX_FACTS_PER_BATCH if profile.profile_kind == "runtime" else None


def _artifact_batches(
    bundle: ExampleBundle,
    facts: list[Any],
    *,
    profile: RuleProfile | None = None,
) -> list[list[Any]]:
    max_facts = _local_digest_fact_limit(profile) if profile is not None else None
    if not bundle.adventure_chapters:
        return shared_fact_batches(
            facts,
            error_factory=ArtifactGenerationError,
            max_facts_per_batch=max_facts,
            max_batch_tokens=(
                LOCAL_DIGEST_MAX_FACT_TOKENS if max_facts is not None else None
            ),
        )
    return [batch for chapter in bundle.adventure_chapters
            for batch in shared_fact_batches(
                [fact for fact in facts if fact.id in set(chapter.fact_ids)],
                error_factory=ArtifactGenerationError,
                max_facts_per_batch=max_facts,
                max_batch_tokens=(
                    LOCAL_DIGEST_MAX_FACT_TOKENS if max_facts is not None else None
                ),
            )]


class PlanningIndexSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)


MAX_PLANNING_INDEX_REQUESTS = 120
PLANNING_INDEX_MIN_SUMMARY_CHARS = 240
PLANNING_INDEX_SUMMARY_CAP = 1200


def _bounded_plan_input(job, plan_index, profile, units, sizes, tokens, context, client):
    """Condense indexes while keeping every source fact available for card planning."""
    transport = PlanningTransport(client, list(sizes), context)
    current = _compact_global_units(units)
    initial_messages = _global_plan_messages(profile, current, sizes, tokens, context)
    if len(json.dumps(transport.messages(initial_messages), ensure_ascii=False)) <= MAX_FACT_INPUT_CHARS:
        return current, initial_messages, transport
    # A lower bound keeps all identifiers and budgets but no index prose.
    minimum_units = [{"id": "minimum", "kind": "background", "title": "", "summary": "",
                      "fact_ids": list(sizes)}]
    minimum_messages = _global_plan_messages(profile, minimum_units, sizes, tokens, context)
    minimum_chars = len(json.dumps(transport.messages(minimum_messages), ensure_ascii=False))
    if minimum_chars > MAX_FACT_INPUT_CHARS:
        raise ArtifactGenerationError(
            f"规划请求过大：固定章节上下文与事实编号至少占 {minimum_chars:,} 字符，"
            f"超过 {MAX_FACT_INPUT_CHARS:,} 字符预算；未调用索引汇总或截断事实。"
        )
    # Collapsing a fixed eight units per round discards independently runnable
    # situations long before the request needs it, and repeating the round compounds
    # the loss.  Derive the grouping from the real budget and condense only once.
    unit_count = len(current)
    blank_units = [dict(unit, title="", summary="", open_questions=[]) for unit in current]
    blank_chars = len(json.dumps(
        transport.messages(_global_plan_messages(profile, blank_units, sizes, tokens, context)),
        ensure_ascii=False,
    ))
    per_unit = max(1, (blank_chars - minimum_chars) // max(1, unit_count - 1))
    prose_budget = MAX_FACT_INPUT_CHARS - (minimum_chars - per_unit)
    group_size = unit_count
    for candidate in range(2, unit_count + 1):
        entries = -(-unit_count // candidate)
        if entries * (per_unit + 160 + PLANNING_INDEX_MIN_SUMMARY_CHARS) <= prose_budget:
            group_size = candidate
            break
    group_size = max(group_size, -(-unit_count // MAX_PLANNING_INDEX_REQUESTS))
    entries = -(-unit_count // group_size)
    summary_chars = min(
        PLANNING_INDEX_SUMMARY_CAP,
        max(PLANNING_INDEX_MIN_SUMMARY_CHARS, prose_budget // max(1, entries) - per_unit - 160),
    )
    condensed = []
    for group_index, offset in enumerate(range(0, unit_count, group_size), start=1):
        group = current[offset:offset + group_size]
        prompt = [{"role": "system", "content": (
            "Summarize this same-chapter TRPG planning index, not the original source. "
            'Return exactly one JSON object: {"title":"...","summary":"..."}. '
            "Keep the English field names title and summary; write their string values in Chinese. "
            "Do not return headings, Markdown, Chinese field names, or text outside the JSON object. "
            "Preserve named playable situations, "
            "NPC motives, actionable leads, conflicts, and alternatives. Do not invent connections or "
            "merge distinct places into one scene. This is a retrieval index, not a final card or "
            "a required route. All original fact IDs are preserved separately by the application. "
            f"Keep title within 160 characters and summary within {summary_chars} characters. "
            + SOURCE_SEMANTIC_GUIDANCE
            + CLOCK_SEMANTIC_GUIDANCE
        )}, {"role": "user", "content": json.dumps({
            "schema": PlanningIndexSummary.model_json_schema(),
            "units": [{k: v for k, v in unit.items() if k not in {"fact_ids", "id"}} for unit in group],
        }, ensure_ascii=False)}]
        if len(json.dumps(prompt, ensure_ascii=False)) > MAX_FACT_INPUT_CHARS:
            raise ArtifactGenerationError("规划索引汇总请求超过预算；未截断事实或发送请求")
        output = _run_json_step(
            job, stage=f"planning_index_{plan_index}_0", step_index=group_index,
            input_payload={"version": "planning-index-v3", "units": group,
                           "summary_chars": summary_chars},
            input_refs={"fact_ids": list(dict.fromkeys(fid for u in group for fid in u["fact_ids"]))},
            messages=prompt, client=client, max_tokens=8000,
            validator=lambda raw: PlanningIndexSummary.model_validate(raw).model_dump(mode="json"),
        )
        output["summary"] = (output.get("summary") or "")[:summary_chars]
        condensed.append({
            "id": f"planning_index_{plan_index}_0_{group_index}",
            "kind": "background", **output,
            "fact_ids": list(dict.fromkeys(fid for u in group for fid in u["fact_ids"])),
            "entity_keys": [], "relationship_hints": [], "open_questions": [],
        })
    messages = _global_plan_messages(profile, condensed, sizes, tokens, context)
    while len(json.dumps(transport.messages(messages), ensure_ascii=False)) > MAX_FACT_INPUT_CHARS:
        if not _shrink_summaries(condensed):
            raise ArtifactGenerationError(
                f"规划请求过大：索引已汇总，但必要章节上下文与事实编号仍超过 "
                f"{MAX_FACT_INPUT_CHARS:,} 字符预算。保留全部事实，未发送超预算请求。"
            )
        messages = _global_plan_messages(profile, condensed, sizes, tokens, context)
    return condensed, messages, transport


def _shrink_summaries(condensed: list[dict[str, Any]]) -> bool:
    """Shrink every planned index summary by a quarter.

    Reporting whether anything shrank lets the caller tell "still too big, keep
    shrinking" apart from "nothing left to shrink", so an over-budget planning
    request can never spin forever.  There is deliberately no per-summary floor:
    a floor of one character would leave a length-1 summary unchanged and stall
    the loop, so a length-1 summary shrinks to empty instead.
    """
    shrank = False
    for item in condensed:
        summary = item.get("summary")
        if summary:
            item["summary"] = summary[: len(summary) * 3 // 4]
            shrank = True
    return shrank


def _generate_hierarchical_artifacts(
    job: ArtifactDraftJob,
    bundle: ExampleBundle,
    profile: RuleProfile,
    client: Any,
    scoped_facts: list[Any],
    batches: list[list[Any]],
    *,
    prep_unit_id: str | None = None,
) -> tuple[
    list[DerivedCard],
    _OpenQuestionSummary,
    ArtifactDraftJob,
    dict[str, Any] | None,
    list[dict[str, Any]],
]:
    job = _save_job(job, phase="local_digest")
    local_units: list[dict[str, Any]] = []
    local_digest_fact_limit = _local_digest_fact_limit(profile)
    local_digest_max_tokens = (
        RUNTIME_LOCAL_DIGEST_MAX_TOKENS
        if local_digest_fact_limit is not None
        else LOCAL_DIGEST_MAX_TOKENS
    )
    local_digest_chat_kwargs = (
        {"fallback_reasoning_effort": "low"}
        if local_digest_fact_limit is not None
        else {"escalate_max_tokens": LOCAL_DIGEST_MAX_TOKENS_CEILING}
    )
    batch_chapters = [
        chapter.id
        for chapter in bundle.adventure_chapters
        for _ in shared_fact_batches(
            [fact for fact in scoped_facts if fact.id in set(chapter.fact_ids)],
            error_factory=ArtifactGenerationError,
            max_facts_per_batch=local_digest_fact_limit,
            max_batch_tokens=(
                LOCAL_DIGEST_MAX_FACT_TOKENS
                if local_digest_fact_limit is not None else None
            ),
        )
    ]
    question_summary = _OpenQuestionSummary()
    for batch_index, batch_facts in enumerate(batches, start=1):
        current = _job_from_store(job.id)
        if current.status != "running":
            return [], question_summary, current, None, []
        chapter_context = next(({
            "id": chapter.id, "title": chapter.title, "summary": chapter.summary,
        } for chapter in bundle.adventure_chapters
            if batch_chapters and chapter.id == batch_chapters[batch_index - 1]), None)
        try:
            if profile.profile_kind == "runtime":
                output = _run_local_digest_window(
                    current,
                    profile=profile,
                    facts=batch_facts,
                    chapter=chapter_context,
                    batch_index=batch_index,
                    batch_count=len(batches),
                    path="",
                    client=client,
                    max_tokens=local_digest_max_tokens,
                    chat_kwargs=local_digest_chat_kwargs,
                    step_budget=RequestBudget(),
                )
            else:
                fact_payload = _fact_payload_from_facts(batch_facts)
                output = _run_json_step(
                    current,
                    stage="local_digest",
                    step_index=batch_index,
                    input_payload={
                        "prompt_version": LOCAL_DIGEST_PROMPT_VERSION,
                        "profile_id": profile.id,
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "facts": fact_payload,
                        "chapter_id": batch_chapters[batch_index - 1] if batch_chapters else None,
                    },
                    input_refs={"fact_ids": [fact.id for fact in batch_facts]},
                    messages=_local_digest_messages(profile, fact_payload, chapter=chapter_context),
                    client=client,
                    max_tokens=local_digest_max_tokens,
                    validator=lambda raw, items=batch_facts, index=batch_index: _validate_local_digest(
                        raw, items, batch_index=index
                    ),
                    chat_kwargs=local_digest_chat_kwargs,
                )
        except Exception as error:  # noqa: BLE001
            raise ArtifactGenerationError(
                f"第 {batch_index}/{len(batches)} 批局部整理失败：{error}"
            ) from error
        local_units.extend({**unit, "chapter_id": batch_chapters[batch_index - 1] if batch_chapters else None}
                           for unit in output["units"])
        question_summary.add(list(output.get("open_questions") or []))
        job = _save_job(
            _job_from_store(job.id),
            completed_batches=batch_index,
            unit_count=len(local_units),
            **_question_summary_fields(question_summary),
        )

    facts_by_id = {fact.id: fact for fact in scoped_facts}
    fact_sizes = {
        fact.id: len(json.dumps(_fact_payload_from_facts([fact]), ensure_ascii=False))
        for fact in scoped_facts
    }
    fact_tokens = {
        fact.id: _estimate_tokens(json.dumps(_fact_payload_from_facts([fact]), ensure_ascii=False))
        for fact in scoped_facts
    }
    job = _save_job(_job_from_store(job.id), phase="global_plan")
    structure_context = None
    if bundle.adventure_chapters:
        structure_context = {
            "chapters": [
                {
                    "id": item.id,
                    "title": item.title,
                    "summary": item.summary,
                }
                for item in bundle.adventure_chapters
            ],
            "threads": [
                {
                    "id": item.id,
                    "title": item.title,
                    "kind": item.kind,
                    "summary": item.summary,
                    "chapter_roles": item.chapter_roles,
                }
                for item in bundle.story_threads
            ],
            "beats": [
                {
                    "id": item.id,
                    "title": item.title,
                    "summary": item.summary,
                    "chapter_id": item.chapter_id,
                    "thread_ids": item.thread_ids,
                    "thread_developments": {key: value.model_dump(mode="json") for key, value in item.thread_developments.items()},
                    "interplay": item.interplay,
                    "fact_ids": item.fact_ids,
                }
                for item in bundle.story_beats
            ],
            "fact_dispositions": [
                {
                    "fact_id": item.fact_id,
                    "kind": item.kind,
                    "chapter_ids": item.chapter_ids,
                    "thread_ids": item.thread_ids,
                }
                for item in bundle.fact_dispositions
                if item.fact_id in facts_by_id
            ],
        }
    try:
        planning_scopes: list[
            tuple[list[dict[str, Any]], list[Any], dict[str, Any] | None, set[str] | None]
        ] = [(local_units, scoped_facts, structure_context, None)]
        if structure_context:
            planning_scopes = []
            for chapter in bundle.adventure_chapters:
                scope_fact_ids = set(chapter.fact_ids)
                chapter_facts = [
                    fact for fact in scoped_facts if fact.id in scope_fact_ids
                ]
                chapter_units = []
                for unit in local_units:
                    if unit.get("chapter_id") != chapter.id:
                        continue
                    member_ids = [
                        fact_id
                        for fact_id in unit.get("fact_ids", [])
                        if fact_id in scope_fact_ids
                    ]
                    if member_ids:
                        chapter_units.append({**unit, "fact_ids": member_ids})
                # A request only carries the facts its units name, and the budget
                # maps are filtered to exactly those, so this is the set the plan
                # has to account for in this chapter.
                scope_ids = {
                    fact_id for unit in chapter_units for fact_id in unit["fact_ids"]
                }
                # A per-fact disposition record costs about 110 characters and a
                # large chapter holds several hundred, so the list alone pushed the
                # request past the planning budget.  Every fact the scope covers is
                # already named by its units, and its kind is always "chapter" for
                # this profile, so only two things still carry information: which
                # threads a fact supports, and whether it also belongs to another
                # chapter.  Both are sent transposed, which repeats a thread id
                # once instead of once per fact.
                fact_threads: dict[str, list[str]] = {}
                shared_with: dict[str, list[str]] = {}
                for item in structure_context["fact_dispositions"]:
                    if item["fact_id"] not in scope_ids:
                        continue
                    for thread_id in item["thread_ids"]:
                        fact_threads.setdefault(thread_id, []).append(item["fact_id"])
                    for other in item["chapter_ids"]:
                        if other != chapter.id:
                            shared_with.setdefault(other, []).append(item["fact_id"])
                chapter_context = {
                    "chapters": [
                        item
                        for item in structure_context["chapters"]
                        if item["id"] == chapter.id
                    ],
                    "adventure_index": structure_context["chapters"],
                    "threads": [
                        item
                        for item in structure_context["threads"]
                        if chapter.id in item["chapter_roles"]
                    ],
                    "beats": [
                        item
                        for item in structure_context["beats"]
                        if item["chapter_id"] == chapter.id
                    ],
                    # Records rather than maps on purpose: PlanningTransport only
                    # rewrites identifiers found under field names such as
                    # thread_id, chapter_id and fact_ids.  A map keyed by a thread
                    # id left every fact id at its full 40-character length, which
                    # cost more than the per-fact records this replaced.
                    # The facts this scope must account for, stated once so the
                    # planner never has to reconstruct them from the units.
                    "chapter_fact_ids": sorted(scope_ids),
                    "fact_threads": [
                        {"thread_id": thread_id, "fact_ids": sorted(fact_ids)}
                        for thread_id, fact_ids in sorted(fact_threads.items())
                    ],
                    "fact_shared_with_chapters": [
                        {"chapter_id": chapter_id, "fact_ids": sorted(fact_ids)}
                        for chapter_id, fact_ids in sorted(shared_with.items())
                    ],
                }
                planning_scopes.append(
                    (chapter_units, chapter_facts, chapter_context, {chapter.id})
                )

        job = _save_job(
            _job_from_store(job.id),
            planning_scope_count=len(planning_scopes),
            completed_planning_scopes=0,
        )
        plan_outputs: list[dict[str, Any]] = []
        for plan_index, (
            plan_units,
            plan_facts,
            plan_context,
            chapter_scope,
        ) in enumerate(planning_scopes, start=1):
            owned_ids = {fact.id for fact in plan_facts}
            plan_fact_sizes = {key: value for key, value in fact_sizes.items() if key in owned_ids}
            plan_fact_tokens = {key: value for key, value in fact_tokens.items() if key in owned_ids}
            plan_units, _, plan_client = _bounded_plan_input(
                job, plan_index, profile, plan_units, plan_fact_sizes, plan_fact_tokens,
                plan_context, client,
            )
            output = _run_global_plan_scope(
                job,
                plan_index=plan_index,
                profile=profile,
                bundle=bundle,
                plan_units=plan_units,
                plan_facts=plan_facts,
                plan_fact_sizes=plan_fact_sizes,
                plan_fact_tokens=plan_fact_tokens,
                plan_context=plan_context,
                chapter_scope=chapter_scope,
                plan_client=plan_client,
            )
            plan_outputs.append(output)
            job = _save_job(
                _job_from_store(job.id), completed_planning_scopes=plan_index
            )
    except Exception as error:  # noqa: BLE001
        if "API 请求超时" in str(error):
            raise ArtifactGenerationError(
                "全局规划单次请求超过 300 秒，上游未返回。请使用主按钮重试失败项；"
                "已完成的局部整理会复用。\n" + str(error)
            ) from error
        raise ArtifactGenerationError(f"全局规划失败：{error}") from error
    plans: list[dict[str, Any]] = []
    runtime_brief = None
    chapter_briefs = []
    uncovered = []
    collected_plans: list[dict[str, Any]] = []
    for output in plan_outputs:
        output = _normalize_runtime_group_anchors(output, profile)
        runtime_brief = runtime_brief or output.get("brief")
        chapter_briefs.extend(output.get("chapter_briefs") or [])
        question_summary.add(list(output.get("open_questions") or []))
        uncovered.extend(output.get("uncovered_fact_ids") or [])
        collected_plans.extend(output["cards"])
    # Identity must be settled before merging: the merge decides how many cards
    # exist, and each surviving card owns exactly one runtime state.
    collected_plans = _assign_plan_identities(
        job, collected_plans, bundle, question_summary, client
    )
    plans = _merge_chapter_plans([], collected_plans, facts_by_id)
    _report_unmerged_identities(plans, question_summary)
    if uncovered:
        question_summary.add(
            [f"全局计划未使用 {len(uncovered)} 条已提升事实；请在产物复核时检查覆盖范围。"]
        )
    job = _save_job(
        _job_from_store(job.id),
        phase="materializing",
        planned_card_count=len(plans),
        completed_cards=0,
        **_question_summary_fields(question_summary),
    )

    generated_cards: list[DerivedCard] = []
    failed_cards: list[str] = []
    consecutive_failures = 0
    for card_index, plan in enumerate(plans, start=1):
        current = _job_from_store(job.id)
        if current.status != "running":
            return generated_cards, question_summary, current, runtime_brief, chapter_briefs
        selected_facts = [facts_by_id[fact_id] for fact_id in plan["fact_ids"]]
        fact_payload = _fact_payload_from_facts(selected_facts)
        subset_bundle = bundle.model_copy(
            update={"facts": selected_facts, "cards": [], "plans": []}
        )
        try:
            output = _run_json_step(
                current,
                stage="materialize",
                step_index=card_index,
                input_payload={
                    "prompt_version": PROMPT_VERSION,
                    "profile_id": profile.id,
                    "plan": _prompt_plan(plan),
                    "facts": fact_payload,
                },
                input_refs={
                    "planned_card_id": plan["id"],
                    "fact_ids": plan["fact_ids"],
                },
                messages=_materialization_messages(profile, _prompt_plan(plan), fact_payload),
                client=client,
                max_tokens=ARTIFACT_MAX_TOKENS,
                validator=lambda raw, selected_bundle=subset_bundle, card_plan=plan: (
                    _validate_materialized_card(
                        raw,
                        selected_bundle,
                        profile,
                        card_plan,
                        model_id=getattr(client, "model", "configured-model"),
                        prep_unit_id=prep_unit_id,
                    )
                ),
            )
        except Exception as error:  # noqa: BLE001
            failed_cards.append(
                f"第 {card_index}/{len(plans)} 张计划卡（{plan['title']}）：{error}"
            )
            consecutive_failures += 1
            job = _save_job(
                _job_from_store(job.id),
                completed_cards=card_index,
                card_count=len(generated_cards),
                **_question_summary_fields(question_summary),
            )
            if consecutive_failures >= MAX_CONSECUTIVE_MATERIALIZE_FAILURES:
                raise ArtifactGenerationError(
                    f"连续 {consecutive_failures} 张计划卡素材化失败（最后一张："
                    f"{plan['title']}）。上游可能不可用，已中止本轮生成；已成功的 "
                    f"{len(generated_cards)} 张卡保存在步骤缓存中，重试会直接复用它们。"
                ) from error
            continue
        consecutive_failures = 0
        generated_cards.extend(DerivedCard.model_validate(card) for card in output["cards"])
        question_summary.add(list(output.get("open_questions") or []))
        job = _save_job(
            _job_from_store(job.id),
            completed_cards=card_index,
            card_count=len(generated_cards),
            **_question_summary_fields(question_summary),
        )
    if failed_cards:
        preview = "；".join(failed_cards[:5])
        if len(failed_cards) > 5:
            preview += f"；另有 {len(failed_cards) - 5} 项失败"
        raise ArtifactGenerationError(
            f"本轮 {len(plans)} 张计划卡已全部尝试，其中 {len(failed_cards)} 项失败。"
            f"请使用‘重试失败项’；已成功步骤会复用。{preview}"
        )
    return generated_cards, question_summary, job, runtime_brief, chapter_briefs


def _prompt_messages(
    bundle: ExampleBundle,
    profile: RuleProfile,
    facts: list[dict[str, Any]],
    *,
    require_runtime_anchor: bool = True,
    batch_index: int | None = None,
    batch_count: int | None = None,
) -> list[dict[str, str]]:
    definitions = [item.model_dump(mode="json") for item in profile.card_definitions]
    field_shapes = {
        key: "array of concise strings"
        for key in sorted(
            LIST_FIELDS
            & {
                field
                for definition in profile.card_definitions
                for field in [*definition.required_fields, *definition.optional_fields]
            }
        )
    }
    system = (
        "You turn reviewed TRPG source facts into preparation artifact drafts for a GM. "
        "Return only one JSON object with keys cards and open_questions. Each card must have "
        "type, title, subtitle, fact_ids, fields, field_sources, and open_questions. Use only "
        "the supplied facts. Every factual claim and every field_sources id must be supported "
        "by fact_ids. Never invent a rule stat, fixed player route, or player dialogue. "
        "Operational reframing is allowed when it helps a modern narrative engine run older source "
        "material. For clock cards, you may create a concise situation track with a starting state, "
        "progression stages, advancement conditions, visible changes, and an endpoint consequence; "
        "do not model it as attack counts or numbered actions. Any named source fact still belongs "
        "in fact_ids, while the clock structure itself may be a GM-facing transformation. If support "
        "is missing for a factual claim, record a short open question instead of asserting it. Generate a coherent one-session working set, not "
        "one card per fact. Repeating a card type is allowed when the material has multiple "
        "scenes, people, threats, or clocks. "
        + SOURCE_SEMANTIC_GUIDANCE
        + CLOCK_SEMANTIC_GUIDANCE
        + BOARD_REFERENCE_BOUNDARY
        + "All user-facing text must be Chinese."
    )
    anchor_instruction = (
        "A runtime profile must include at least one location or environment card. "
        if require_runtime_anchor
        else "This is one partial fact batch; do not invent missing context or assume it is the complete chapter. "
    )
    batch_instruction = ""
    if batch_index is not None and batch_count is not None and batch_count > 1:
        batch_instruction = (
            f"This request is transport batch {batch_index} of {batch_count}. Related facts may be in other batches; "
            "cite only facts in this request and put unresolved cross-batch relationships in open_questions. "
        )
    user = (
        "[TASK:prep:artifact_draft]\n"
        f"WORKSPACE_ID_JSON={json.dumps(bundle.id, ensure_ascii=False)}\n"
        f"WORKSPACE_NAME_JSON={json.dumps(bundle.name, ensure_ascii=False)}\n"
        f"BOARD_PROFILE_JSON={json.dumps(_board_prompt_payload(profile, definitions=definitions), ensure_ascii=False)}\n"
        f"ALLOWED_CARD_DEFINITIONS_JSON={json.dumps(definitions, ensure_ascii=False)}\n"
        f"LIST_FIELD_SHAPES_JSON={json.dumps(field_shapes, ensure_ascii=False)}\n"
        "For each card, fields may contain only keys declared by its card definition. Fill every "
        "required field with a non-empty value. field_sources must map each populated field to "
        "one or more ids from that card's fact_ids. Use arrays for fields listed in "
        "LIST_FIELD_SHAPES_JSON. "
        f"{batch_instruction}"
        f"{anchor_instruction}"
        "Do not output ids for cards; the server assigns them.\n"
        f"REVIEWED_FACTS_JSON={json.dumps(facts, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _stable_card_id(
    bundle_id: str, draft: DraftCardPayload, prep_unit_id: str | None = None
) -> str:
    content = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(
        f"{bundle_id}:{prep_unit_id or 'unscoped'}:{content}".encode("utf-8")
    ).hexdigest()[:14]
    type_slug = re.sub(r"[^a-z0-9_]+", "_", draft.type.lower()).strip("_") or "artifact"
    return f"card_generated_{type_slug}_{digest}"


def _validate_and_build(
    raw: Any,
    bundle: ExampleBundle,
    profile: RuleProfile,
    *,
    model_id: str,
    require_runtime_anchor: bool = True,
    prep_unit_id: str | None = None,
) -> tuple[list[DerivedCard], list[str]]:
    if isinstance(raw, list):
        raw = {"cards": raw, "open_questions": []}
    # Some OpenAI-compatible gateways/models ignore the requested array shape
    # and return a concise string for a list field. Normalize only the fields
    # whose contract explicitly says "array"; source IDs and object fields are
    # intentionally left strict so malformed provenance still fails closed.
    if isinstance(raw, dict) and isinstance(raw.get("cards"), list):
        normalized_cards = []
        for card in raw["cards"]:
            if isinstance(card, dict) and isinstance(card.get("fields"), dict):
                card = _normalize_nested_field_provenance(dict(card))
                card = _normalize_list_item_provenance(card)
                fields = dict(card["fields"])
                statistics = fields.get("source_statistics")
                if isinstance(statistics, list) and all(isinstance(item, str) for item in statistics):
                    fields["source_statistics"] = "\n".join(statistics)
                if fields.get("current_stage", "") is None:
                    fields["current_stage"] = ""
                for field_name in LIST_FIELDS:
                    value = fields.get(field_name)
                    if isinstance(value, str) and value.strip():
                        fields[field_name] = [value.strip()]
                    elif isinstance(value, list) and any(
                        not isinstance(item, str) for item in value
                    ):
                        raise ArtifactGenerationError(
                            f"产物 {card.get('title', '未命名')} 的 {field_name} 必须是字符串数组"
                        )
                card["fields"] = fields
                field_sources = card.get("field_sources")
                if isinstance(field_sources, dict):
                    card["field_sources"] = {
                        field_name: _normalize_field_source_refs(refs)
                        for field_name, refs in field_sources.items()
                    }
            normalized_cards.append(card)
        raw = dict(raw)
        raw["cards"] = normalized_cards
    try:
        payload = DraftResponsePayload.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(f"备团产物 JSON 不符合约定: {error}") from error

    allowed_facts = {
        fact.id: fact for fact in bundle.facts if fact.evidence_status != "model_candidate"
    }
    definitions = {item.type: item for item in profile.card_definitions}
    seen_keys: set[tuple[str, str]] = set()
    cards: list[DerivedCard] = []
    generated_at = storage.now()

    for draft in payload.cards:
        definition = definitions.get(draft.type)
        if definition is None:
            raise ArtifactGenerationError(f"模型返回了当前板块未定义的卡型: {draft.type}")
        duplicate_key = (draft.type, draft.title.casefold())
        if duplicate_key in seen_keys:
            raise ArtifactGenerationError(f"模型返回了重复产物: {draft.type} / {draft.title}")
        seen_keys.add(duplicate_key)

        unknown_fact_ids = [item for item in draft.fact_ids if item not in allowed_facts]
        if unknown_fact_ids:
            raise ArtifactGenerationError(
                f"产物 {draft.title} 引用了未提升或不存在的事实: {unknown_fact_ids}"
            )
        allowed_fields = set(definition.required_fields) | set(definition.optional_fields)
        unknown_fields = sorted(set(draft.fields) - allowed_fields)
        if unknown_fields:
            raise ArtifactGenerationError(f"产物 {draft.title} 包含未定义字段: {unknown_fields}")
        missing_fields = [
            key for key in definition.required_fields if not _nonempty(draft.fields.get(key))
        ]
        if missing_fields:
            raise ArtifactGenerationError(f"产物 {draft.title} 缺少必填字段: {missing_fields}")
        for field_name in LIST_FIELDS & set(draft.fields):
            if not isinstance(draft.fields[field_name], list):
                raise ArtifactGenerationError(f"产物 {draft.title} 的 {field_name} 必须是数组")
        scalar_fields = set(draft.fields) - LIST_FIELDS
        invalid_scalar_fields = sorted(
            field_name
            for field_name in scalar_fields
            if not isinstance(draft.fields[field_name], str)
        )
        if invalid_scalar_fields:
            raise ArtifactGenerationError(
                f"产物 {draft.title} 的字段必须是字符串: {invalid_scalar_fields}"
            )

        # Some upstreams correctly cite a fact in `field_sources` but omit the
        # same id from the card-level closure.  When that fact is part of this
        # request, repair the declaration deterministically; only references
        # outside the supplied input remain a hard validation error.
        field_sources: dict[str, list[str]] = {}
        closure = list(draft.fact_ids)
        for field_name in draft.fields:
            if field_name in GM_AUTHORED_FIELDS and not draft.field_sources.get(field_name):
                # Explicit GM operating prompts may be authored without claiming provenance.
                # They must never silently claim source provenance.
                field_sources[field_name] = []
                continue
            refs = list(dict.fromkeys(draft.field_sources.get(field_name) or list(draft.fact_ids)))
            unknown_refs = [item for item in refs if item not in allowed_facts]
            if unknown_refs:
                raise ArtifactGenerationError(
                    f"产物 {draft.title} 的字段 {field_name} 引用了未提升或不存在的事实: {unknown_refs}"
                )
            for fact_id in refs:
                if fact_id not in closure:
                    closure.append(fact_id)
            field_sources[field_name] = refs
        # The operative limit is the character/token budget shared with the
        # planning and merge guards; the fact count is only a structural ceiling,
        # so a card that passed planning can never be refused here for its count.
        closure_chars, closure_tokens = _fact_closure_budget(
            closure, {fact.id: fact for fact in bundle.facts}
        )
        if (
            len(closure) > MAX_MERGED_FACT_IDS
            or closure_chars > MAX_CARD_FACT_INPUT_CHARS
            or closure_tokens > MAX_CARD_FACT_INPUT_TOKENS
        ):
            raise ArtifactGenerationError(
                f"产物 {draft.title} 的事实闭包 {len(closure)} 条 / "
                f"{closure_chars} 字符超过允许上限"
            )

        cards.append(
            DerivedCard(
                id=_stable_card_id(bundle.id, draft, prep_unit_id),
                profile_id=profile.id,
                prep_unit_id=prep_unit_id,
                type=draft.type,
                title=draft.title,
                subtitle=draft.subtitle,
                fact_ids=closure,
                fields=draft.fields,
                field_sources=field_sources,
                open_questions=draft.open_questions,
                runtime_group_ids=draft.runtime_group_ids,
                generation=CardGeneration(
                    model_id=model_id,
                    prompt_version=PROMPT_VERSION,
                    generated_at=generated_at,
                ),
                edit_state="generated",
            )
        )

    if (
        require_runtime_anchor
        and profile.profile_kind == "runtime"
        and not any(card.type in RUNTIME_ANCHOR_TYPES for card in cards)
    ):
        raise ArtifactGenerationError("运行板块的产物缺少场景或环境卡")
    response_questions = _unique_questions(
        [
            *payload.open_questions,
            *[question for draft in payload.cards for question in draft.open_questions],
        ]
    )
    return cards, response_questions
