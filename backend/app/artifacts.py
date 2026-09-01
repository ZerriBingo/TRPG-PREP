"""Generate source-bound preparation artifact drafts from promoted shelf facts."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import prep, storage
from .llm import make_client
from ..domain import load_profiles, validate_bundle
from ..domain.models import (
    ArtifactDraftJob,
    CardGeneration,
    DerivedCard,
    ExampleBundle,
    RuleProfile,
)


PROMPT_VERSION = "prep-artifact-draft-v2"
GLOBAL_PLAN_PROMPT_VERSION = "prep-artifact-global-plan-v3"
MAX_FACT_INPUT_CHARS = 120_000
# Keep individual model requests comfortably below the hard input ceiling.  A
# single artifact job may contain several of these batches; the final cards are
# merged and validated before they are written to the shelf.
MAX_FACT_BATCH_CHARS = 80_000
MAX_SINGLE_FACT_CHARS = MAX_FACT_BATCH_CHARS
MAX_FACT_BATCH_TOKENS = 22_000
MAX_SINGLE_FACT_TOKENS = MAX_FACT_BATCH_TOKENS
MAX_DRAFT_CARDS = 50
MAX_LOCAL_UNITS = 32
MAX_CARD_FACT_INPUT_CHARS = 72_000
MAX_CARD_FACT_INPUT_TOKENS = 22_000
LOCAL_DIGEST_MAX_TOKENS = 7000
GLOBAL_PLAN_MAX_TOKENS = 7000
ARTIFACT_REQUEST_TIMEOUT = 300
ARTIFACT_MAX_TOKENS = 9000
LIST_FIELDS = {
    "immediate_actions",
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
}
RUNTIME_ANCHOR_TYPES = {"location", "environment"}

_active_jobs: set[str] = set()
_active_jobs_lock = threading.Lock()


class ArtifactGenerationError(ValueError):
    """Raised when facts or model output cannot produce a valid artifact set."""


class ArtifactJobConflictError(RuntimeError):
    """Raised when another incompatible artifact job is already active."""


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _estimate_tokens(value: Any) -> int:
    """Conservative fallback when the configured upstream exposes no tokenizer."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    cjk_count = sum(
        1
        for character in text
        if "\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff"
    )
    other_count = max(len(text) - cjk_count, 0)
    return cjk_count + (other_count + 3) // 4


def _step_id(job_id: str, stage: str, step_index: int, input_hash: str) -> str:
    digest = input_hash.removeprefix("sha256:")[:16]
    return f"artifact_step_{job_id.removeprefix('artifact_job_')}_{stage}_{step_index}_{digest}"


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
) -> dict[str, Any]:
    """Run or resume one validated LLM step while preserving every attempt."""
    # A model switch is a new generation attempt, even when the source facts
    # and stage inputs are identical.  Include the effective client identity
    # in the step key so a retry cannot silently reuse a successful response
    # produced by the model that just failed or was rate-limited.
    step_input = {
        **input_payload,
        "_model_id": job.model_id,
        "_fake_model": job.fake_model,
    }
    input_hash = _stable_hash(step_input)
    existing = storage.load_artifact_job_step(job.id, stage, step_index, input_hash)
    if existing and existing.get("status") == "succeeded" and existing.get("output"):
        return existing["output"]

    timestamp = storage.now()
    step = existing or {
        "id": _step_id(job.id, stage, step_index, input_hash),
        "job_id": job.id,
        "stage": stage,
        "step_index": step_index,
        "input_hash": input_hash,
        "input_refs": {
            **input_refs,
            "model_id": job.model_id,
            "fake_model": job.fake_model,
        },
        "attempts": [],
        "created_at": timestamp,
    }
    attempts = list(step.get("attempts") or [])
    working_messages = [dict(message) for message in messages]
    step.update(status="running", updated_at=timestamp, error=None)
    storage.save_artifact_job_step(step)

    last_error: Exception | None = None
    for correction_attempt in range(2):
        raw: Any = None
        attempt_number = len(attempts) + 1
        started_at = storage.now()
        try:
            raw = client.chat_json(
                working_messages,
                temperature=0.15,
                max_tokens=max_tokens,
                request_timeout=ARTIFACT_REQUEST_TIMEOUT,
            )
            output = validator(raw)
        except Exception as error:  # noqa: BLE001
            last_error = error
            attempts.append(
                {
                    "attempt": attempt_number,
                    "status": "failed",
                    "model_id": job.model_id,
                    "fake_model": job.fake_model,
                    "started_at": started_at,
                    "finished_at": storage.now(),
                    "response": raw,
                    "error": str(error)[:2000],
                }
            )
            step.update(
                status="failed",
                attempts=attempts,
                error=str(error)[:2000],
                updated_at=storage.now(),
            )
            storage.save_artifact_job_step(step)
            if raw is None or correction_attempt == 1:
                raise
            working_messages.extend(
                [
                    {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                    {
                        "role": "user",
                        "content": (
                            "The JSON was rejected by deterministic validation: "
                            f"{error}. Return the complete corrected JSON object only."
                        ),
                    },
                ]
            )
            step.update(status="running", error=None, updated_at=storage.now())
            storage.save_artifact_job_step(step)
            continue

        attempts.append(
            {
                "attempt": attempt_number,
                "status": "succeeded",
                "model_id": job.model_id,
                "fake_model": job.fake_model,
                "started_at": started_at,
                "finished_at": storage.now(),
                "response": raw,
                "error": None,
            }
        )
        step.update(
            status="succeeded",
            attempts=attempts,
            output=output,
            error=None,
            updated_at=storage.now(),
        )
        storage.save_artifact_job_step(step)
        return output

    raise last_error or ArtifactGenerationError(f"artifact step failed: {stage}/{step_index}")


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
    profile_rank = lambda job: 0 if profile_id is None or job.profile_id == profile_id else 1
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
                and active.model_id == model_id
                and active.fake_model == fake_model
            )
            if same_request:
                return active, False
            raise ArtifactJobConflictError(
                "当前已有整板生成任务在运行，请等待该任务完成后再试"
            )
        timestamp = storage.now()
        job = ArtifactDraftJob(
            id=f"artifact_job_{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            profile_id=profile_id,
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


def has_profile_artifacts(bundle: ExampleBundle, profile_id: str) -> bool:
    """Return whether a bookshelf already contains cards for one profile."""
    return any(card.profile_id == profile_id for card in bundle.cards)


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
        if not scoped_facts:
            raise ArtifactGenerationError("书架没有可用于生成产物的已提升事实")
        batches = _fact_batches(scoped_facts)
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
            planned_card_count=0,
            completed_cards=0,
            input_fingerprint=input_fingerprint,
            budget_method="conservative-cjk-v1",
            open_questions=[],
        )

        # Runtime boards always pass through the planner so location coverage
        # and chapter-overview requirements are checked before materializing
        # cards. The compact direct path is reserved for non-runtime boards.
        if len(batches) == 1 and profile.profile_kind != "runtime":
            job = _save_job(job, phase="direct_generation")
            generated_cards, open_questions = _generate_direct_step(
                job,
                bundle,
                profile,
                client,
                scoped_facts,
            )
            job = _save_job(
                _job_from_store(job_id),
                completed_batches=1,
                planned_card_count=len(generated_cards),
                completed_cards=len(generated_cards),
                card_count=len(generated_cards),
                open_questions=open_questions,
            )
        else:
            generated_cards, open_questions, job = _generate_hierarchical_artifacts(
                job,
                bundle,
                profile,
                client,
                scoped_facts,
                batches,
            )

        if not generated_cards:
            raise ArtifactGenerationError("模型没有返回可用的备团产物")
        if profile.profile_kind == "runtime" and not any(
            card.type in RUNTIME_ANCHOR_TYPES for card in generated_cards
        ):
            raise ArtifactGenerationError("运行板块的产物缺少场景或环境卡")
        if len(generated_cards) > MAX_DRAFT_CARDS:
            raise ArtifactGenerationError(
                f"合并后的备团产物超过 {MAX_DRAFT_CARDS} 项，请缩小备团范围"
            )

        job = _save_job(_job_from_store(job_id), phase="validating")
        # Reload after the model call so a concurrent manual edit cannot be overwritten.
        latest = storage.load_domain_bundle(workspace_id)
        if not latest:
            raise ArtifactGenerationError("书架工作区在生成期间被删除")
        bundle = ExampleBundle.model_validate(latest[0])
        validate_bundle(bundle, profiles)
        if has_profile_artifacts(bundle, profile_id):
            raise ArtifactGenerationError(
                "生成期间已有同一板块的备团产物，请刷新书架后继续复核"
            )
        bundle.cards.extend(generated_cards)
        validate_bundle(bundle, profiles)
        storage.save_domain_bundle(
            workspace_id, bundle.model_dump(mode="json", by_alias=True)
        )
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
            open_questions=open_questions,
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
    fact_ids: list[str] = Field(min_length=1, max_length=100)
    fields: dict[str, Any]
    field_sources: dict[str, list[str]] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list, max_length=30)

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
    "clue_cluster",
    "threat",
    "timeline",
    "outcome",
    "resource",
}


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
    open_questions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("open_questions")
    @classmethod
    def normalize_questions(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class PlannedCardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=800)
    fact_ids: list[str] = Field(min_length=1, max_length=100)
    focus: list[str] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", "purpose")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("fact_ids", "focus", "open_questions")
    @classmethod
    def unique_text_items(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class GlobalPlanResponsePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[PlannedCardPayload] = Field(min_length=1, max_length=MAX_DRAFT_CARDS)
    open_questions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("open_questions")
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


def _fact_payload_from_facts(facts: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": fact.id,
            "text": fact.text,
            "kind": fact.kind,
            "visibility": fact.visibility,
            "evidence_status": fact.evidence_status,
            "source_refs": [ref.model_dump(mode="json") for ref in fact.source_refs],
            "tags": fact.tags,
            "notes": fact.notes,
        }
        for fact in facts
        if fact.evidence_status != "model_candidate"
    ]


def _facts_for_workspace(bundle: ExampleBundle, workspace_id: str) -> list[Any]:
    """Limit task-owned shelves to the source file and selected page scope."""
    task = prep.find_prep_job_by_workspace(workspace_id)
    # Display materials are source assets, not GM-facing artifact input. They
    # are created and associated separately after the board build.
    usable = [
        fact
        for fact in bundle.facts
        if fact.evidence_status != "model_candidate" and fact.kind != "handout"
    ]
    if task is None:
        return sorted(usable, key=lambda fact: _fact_source_sort_key(fact, None))
    allowed_pages = {
        page for span in task.scope.page_spans for page in span.pages()
    }
    selected: list[Any] = []
    for fact in usable:
        refs = list(fact.source_refs)
        if not refs or any(
            ref.file == task.scope.source_file and ref.page in allowed_pages
            for ref in refs
        ):
            selected.append(fact)
    return sorted(selected, key=lambda fact: _fact_source_sort_key(fact, task.scope.source_file))


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


def _fact_batches(facts: list[Any]) -> list[list[Any]]:
    """Pack facts in source order using a conservative tokenizer fallback."""
    batches: list[list[Any]] = []
    current: list[Any] = []
    for fact in facts:
        single_payload = _fact_payload_from_facts([fact])
        single_serialized = json.dumps(single_payload, ensure_ascii=False)
        single_size = len(single_serialized)
        single_tokens = _estimate_tokens(single_serialized)
        if (
            single_size > MAX_SINGLE_FACT_CHARS
            or single_tokens > MAX_SINGLE_FACT_TOKENS
        ):
            raise ArtifactGenerationError(
                f"事实 {fact.id} 单条输入约 {single_size} 字符 / {single_tokens} 估算 token，"
                "超过单批保护上限；请拆分或缩短该事实后重试。"
            )
        candidate = [*current, fact]
        serialized = json.dumps(_fact_payload_from_facts(candidate), ensure_ascii=False)
        size = len(serialized)
        tokens = _estimate_tokens(serialized)
        if current and (
            size > MAX_FACT_BATCH_CHARS or tokens > MAX_FACT_BATCH_TOKENS
        ):
            batches.append(current)
            current = [fact]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _local_digest_messages(
    profile: RuleProfile,
    facts: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
) -> list[dict[str, str]]:
    system = (
        "You build a compact planning index from reviewed TRPG source facts. Return only one "
        "JSON object with keys units and open_questions. Every unit must contain kind, title, "
        "summary, fact_ids, entity_keys, relationship_hints, and open_questions. Use only supplied "
        "facts. Cover every supplied fact_id in at least one unit. Group facts into useful local "
        "material such as background, scenes, locations, NPCs, clue clusters, threats, timelines, "
        "outcomes, or resources. Preserve actionable details and uncertainty, but do not write final "
        "cards. For location material, create one location unit per distinct place that players can "
        "enter, search, revisit, seek help at, or trigger an event in. Put that place's canonical name "
        "in entity_keys; do not combine a dock, house, generator shed, workshop, searchable room, or "
        "other actionable sublocation merely because they share an island or building. "
        "cards and do not invent cross-batch relationships. All user-facing text must be Chinese."
    )
    user = (
        "[TASK:prep:artifact_local_digest]\n"
        f"PROMPT_VERSION_JSON={json.dumps(PROMPT_VERSION)}\n"
        f"BATCH_INDEX_JSON={batch_index}\n"
        f"BATCH_COUNT_JSON={batch_count}\n"
        f"TARGET_PROFILE_JSON={json.dumps(profile.model_dump(mode='json'), ensure_ascii=False)}\n"
        f"ALLOWED_UNIT_KINDS_JSON={json.dumps(sorted(LOCAL_UNIT_KINDS), ensure_ascii=False)}\n"
        f"LOCAL_FACTS_JSON={json.dumps(facts, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_local_digest(
    raw: Any,
    batch_facts: list[Any],
    *,
    batch_index: int,
) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("open_questions"), list):
        raw = dict(raw)
        raw["open_questions"] = raw["open_questions"][:50]
    try:
        payload = LocalDigestResponsePayload.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(f"局部整理 JSON 不符合约定: {error}") from error

    facts_by_id = {fact.id: fact for fact in batch_facts}
    covered: set[str] = set()
    units: list[dict[str, Any]] = []
    for unit_index, unit in enumerate(payload.units, start=1):
        unknown = [fact_id for fact_id in unit.fact_ids if fact_id not in facts_by_id]
        if unknown:
            raise ArtifactGenerationError(
                f"局部整理单元 {unit.title} 引用了批次外事实: {unknown}"
            )
        covered.update(unit.fact_ids)
        source_refs: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, int]] = set()
        for fact_id in unit.fact_ids:
            for ref in facts_by_id[fact_id].source_refs:
                key = (ref.file, ref.page)
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                source_refs.append({"file": ref.file, "page": ref.page})
        unit_data = unit.model_dump(mode="json")
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
                        {"file": ref.file, "page": ref.page}
                        for ref in fact.source_refs
                    ],
                }
            )
    return {"units": units, "open_questions": payload.open_questions}


def _global_plan_messages(
    profile: RuleProfile,
    units: list[dict[str, Any]],
    fact_sizes: dict[str, int],
    fact_tokens: dict[str, int],
) -> list[dict[str, str]]:
    planning_units = _compact_global_units(units)
    system = (
        "You are the global planning stage for a TRPG preparation workspace. Return only one JSON "
        "object with keys cards and open_questions. Each planned card must contain type, title, "
        "purpose, fact_ids, focus, and open_questions. Reconcile aliases and relationships across all "
        "local units, then plan a coherent working set for the whole selected scope. The compact units "
        "are planning indexes only: do not write final card fields and do not treat their prose as a "
        "new source. Each card will later reread the original fact_ids you select. Use only listed "
        "fact_ids, avoid duplicate cards, preserve conflicts as open questions, and keep each card's "
        "estimated original-fact input within the supplied character budget. For runtime profiles, "
        "every named explorable, returnable, investigable, or help-providing place present in the local units must receive its own location "
        "or environment card; do not collapse places such as a police station, "
        "funeral, publisher, residence, or office into one generic scene. If a place is only a minor "
        "mention with no possible player action, leave it in open_questions rather than inventing a card. "
        "When one source passage names several rooms or sublocations, create separate cards for each room "
        "that can be entered, searched, revisited, used for help, or contain a clue/event; never represent "
        "the whole building as one card merely because the rooms share a parent location. Player-facing source assets "
        "such as maps, letters, photos, newspapers, logs, and records are tracked separately and must "
        "not become artifact cards. All user-facing text "
        "must be Chinese."
    )
    user = (
        "[TASK:prep:artifact_global_plan]\n"
        f"PROMPT_VERSION_JSON={json.dumps(GLOBAL_PLAN_PROMPT_VERSION)}\n"
        f"TARGET_PROFILE_JSON={json.dumps(profile.model_dump(mode='json'), ensure_ascii=False)}\n"
        f"MAX_CARD_FACT_INPUT_CHARS_JSON={MAX_CARD_FACT_INPUT_CHARS}\n"
        f"MAX_CARD_FACT_INPUT_TOKENS_JSON={MAX_CARD_FACT_INPUT_TOKENS}\n"
        f"FACT_INPUT_CHARS_JSON={json.dumps(fact_sizes, ensure_ascii=False)}\n"
        f"FACT_INPUT_TOKENS_JSON={json.dumps(fact_tokens, ensure_ascii=False)}\n"
        f"GLOBAL_UNITS_JSON={json.dumps(planning_units, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_global_plan(
    raw: Any,
    units: list[dict[str, Any]],
    facts: list[Any],
    profile: RuleProfile,
) -> dict[str, Any]:
    try:
        payload = GlobalPlanResponsePayload.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(f"全局卡片计划 JSON 不符合约定: {error}") from error

    allowed_fact_ids = {
        fact_id for unit in units for fact_id in unit.get("fact_ids", [])
    }
    facts_by_id = {fact.id: fact for fact in facts}
    definitions = {definition.type: definition for definition in profile.card_definitions}
    seen: set[tuple[str, str]] = set()
    planned: list[dict[str, Any]] = []
    used_fact_ids: set[str] = set()
    for index, card in enumerate(payload.cards, start=1):
        if card.type not in definitions:
            raise ArtifactGenerationError(f"全局计划包含未定义卡型: {card.type}")
        key = (card.type, card.title.casefold())
        if key in seen:
            raise ArtifactGenerationError(f"全局计划包含重复产物: {card.type} / {card.title}")
        seen.add(key)
        unknown = [fact_id for fact_id in card.fact_ids if fact_id not in allowed_fact_ids]
        if unknown:
            raise ArtifactGenerationError(
                f"全局计划 {card.title} 引用了局部索引外事实: {unknown}"
            )
        selected_facts = [facts_by_id[fact_id] for fact_id in card.fact_ids]
        input_chars = len(
            json.dumps(_fact_payload_from_facts(selected_facts), ensure_ascii=False)
        )
        input_tokens = _estimate_tokens(
            json.dumps(_fact_payload_from_facts(selected_facts), ensure_ascii=False)
        )
        if (
            input_chars > MAX_CARD_FACT_INPUT_CHARS
            or input_tokens > MAX_CARD_FACT_INPUT_TOKENS
        ):
            raise ArtifactGenerationError(
                f"全局计划 {card.title} 的原始事实输入约 {input_chars} 字符 / "
                f"{input_tokens} 估算 token，超过单卡保护上限；请拆成多张用途明确的卡。"
            )
        data = card.model_dump(mode="json")
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

    requires_overview = any(
        definition.type == "chapter_overview" for definition in profile.card_definitions
    )
    if requires_overview and not any(card["type"] == "chapter_overview" for card in planned):
        raise ArtifactGenerationError("全局计划缺少章节总览卡")
    if profile.profile_kind == "runtime" and not any(
        card["type"] in RUNTIME_ANCHOR_TYPES for card in planned
    ):
        raise ArtifactGenerationError("全局计划缺少场景或环境类运行锚点")
    uncovered = sorted(allowed_fact_ids - used_fact_ids)
    return {
        "cards": planned,
        "uncovered_fact_ids": uncovered,
        "open_questions": payload.open_questions,
    }


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


def _materialization_messages(
    profile: RuleProfile,
    plan: dict[str, Any],
    facts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    definition = next(
        item for item in profile.card_definitions if item.type == plan["type"]
    )
    list_fields = sorted(
        LIST_FIELDS & set([*definition.required_fields, *definition.optional_fields])
    )
    required_shapes = {
        field: ("array of concise strings" if field in LIST_FIELDS else "concise string")
        for field in definition.required_fields
    }
    system = (
        "You materialize exactly one planned TRPG preparation card by rereading its original reviewed "
        "facts. Return only one JSON object with keys cards and open_questions; cards must contain "
        "exactly one card. Use the planned type and title exactly. The card must contain type, title, "
        "subtitle, fact_ids, fields, field_sources, and open_questions. Use only supplied original "
        "facts. Every factual field must cite one or more fact_ids from that card, and every populated "
        "field must have field_sources. Do not fill gaps from the planning summary; record an open "
        "question instead. Every required field listed below MUST be present and non-empty. "
        "Before returning, check the fields object key by key against REQUIRED_FIELD_SHAPES_JSON; "
        "array-shaped fields must be JSON arrays, never scalar strings. "
        "For every optional field, actively inspect the supplied facts and include it whenever the "
        "facts support useful table-side content; omit it only when no useful content exists. In "
        "particular, check relevant_characters, hidden_clues, and return_changes separately. "
        "gm_moves may be concise GM-authored pressure or opportunity prompts grounded in the "
        "location's fiction; mark them with no field_sources unless the source explicitly supports one. "
        f"REQUIRED_FIELD_SHAPES_JSON={json.dumps(required_shapes, ensure_ascii=False)} "
        "All user-facing text must be Chinese."
    )
    user = (
        "[TASK:prep:artifact_materialize]\n"
        f"PROMPT_VERSION_JSON={json.dumps(PROMPT_VERSION)}\n"
        f"TARGET_PROFILE_JSON={json.dumps(profile.model_dump(mode='json'), ensure_ascii=False)}\n"
        f"CARD_DEFINITION_JSON={json.dumps(definition.model_dump(mode='json'), ensure_ascii=False)}\n"
        f"LIST_FIELDS_JSON={json.dumps(list_fields, ensure_ascii=False)}\n"
        f"CARD_PLAN_JSON={json.dumps(plan, ensure_ascii=False)}\n"
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
) -> dict[str, Any]:
    cards, questions = _validate_and_build(
        raw,
        bundle,
        profile,
        model_id=model_id,
        require_runtime_anchor=False,
    )
    if len(cards) != 1:
        raise ArtifactGenerationError("单卡落地阶段必须且只能返回一张卡")
    card = cards[0]
    if card.type != plan["type"] or card.title != plan["title"]:
        raise ArtifactGenerationError(
            f"单卡落地必须沿用计划类型与标题: {plan['type']} / {plan['title']}"
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
) -> dict[str, Any]:
    cards, questions = _validate_and_build(
        raw,
        bundle,
        profile,
        model_id=model_id,
        require_runtime_anchor=require_runtime_anchor,
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
        ),
    )
    return (
        [DerivedCard.model_validate(card) for card in output["cards"]],
        list(output.get("open_questions") or []),
    )


def _generate_hierarchical_artifacts(
    job: ArtifactDraftJob,
    bundle: ExampleBundle,
    profile: RuleProfile,
    client: Any,
    scoped_facts: list[Any],
    batches: list[list[Any]],
) -> tuple[list[DerivedCard], list[str], ArtifactDraftJob]:
    job = _save_job(job, phase="local_digest")
    local_units: list[dict[str, Any]] = []
    open_questions: list[str] = []
    for batch_index, batch_facts in enumerate(batches, start=1):
        current = _job_from_store(job.id)
        if current.status != "running":
            return [], open_questions, current
        fact_payload = _fact_payload_from_facts(batch_facts)
        try:
            output = _run_json_step(
                current,
                stage="local_digest",
                step_index=batch_index,
                input_payload={
                    "prompt_version": PROMPT_VERSION,
                    "profile_id": profile.id,
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "facts": fact_payload,
                },
                input_refs={"fact_ids": [fact.id for fact in batch_facts]},
                messages=_local_digest_messages(
                    profile,
                    fact_payload,
                    batch_index=batch_index,
                    batch_count=len(batches),
                ),
                client=client,
                max_tokens=LOCAL_DIGEST_MAX_TOKENS,
                validator=lambda raw, items=batch_facts, index=batch_index: _validate_local_digest(
                    raw, items, batch_index=index
                ),
            )
        except Exception as error:  # noqa: BLE001
            raise ArtifactGenerationError(
                f"第 {batch_index}/{len(batches)} 批局部整理失败：{error}"
            ) from error
        local_units.extend(output["units"])
        open_questions = _merge_questions(
            open_questions, list(output.get("open_questions") or [])
        )
        job = _save_job(
            _job_from_store(job.id),
            completed_batches=batch_index,
            unit_count=len(local_units),
            open_questions=open_questions,
        )

    serialized_units = json.dumps(local_units, ensure_ascii=False)
    if len(serialized_units) > MAX_FACT_INPUT_CHARS:
        raise ArtifactGenerationError(
            "局部整理索引仍然过大，暂不能可靠进入全局规划；需要增加递归区域汇总。"
        )
    facts_by_id = {fact.id: fact for fact in scoped_facts}
    fact_sizes = {
        fact.id: len(json.dumps(_fact_payload_from_facts([fact]), ensure_ascii=False))
        for fact in scoped_facts
    }
    fact_tokens = {
        fact.id: _estimate_tokens(
            json.dumps(_fact_payload_from_facts([fact]), ensure_ascii=False)
        )
        for fact in scoped_facts
    }
    job = _save_job(_job_from_store(job.id), phase="global_plan")
    try:
        plan_output = _run_json_step(
            job,
            stage="global_plan",
            step_index=1,
            input_payload={
                "prompt_version": GLOBAL_PLAN_PROMPT_VERSION,
                "profile": profile.model_dump(mode="json"),
                "units": _compact_global_units(local_units),
                "fact_sizes": fact_sizes,
                "fact_tokens": fact_tokens,
            },
            input_refs={
                "unit_ids": [unit["id"] for unit in local_units],
                "fact_ids": list(facts_by_id),
            },
            messages=_global_plan_messages(
                profile,
                local_units,
                fact_sizes,
                fact_tokens,
            ),
            client=client,
            max_tokens=GLOBAL_PLAN_MAX_TOKENS,
            validator=lambda raw: _validate_global_plan(
                raw, local_units, scoped_facts, profile
            ),
        )
    except Exception as error:  # noqa: BLE001
        if "API 请求超时" in str(error):
            raise ArtifactGenerationError(
                "全局规划单次请求超过 300 秒，上游未返回。请使用主按钮重试失败项；"
                "已完成的局部整理会复用。\n" + str(error)
            ) from error
        raise ArtifactGenerationError(f"全局规划失败：{error}") from error
    plans = plan_output["cards"]
    open_questions = _merge_questions(
        open_questions, list(plan_output.get("open_questions") or [])
    )
    uncovered = list(plan_output.get("uncovered_fact_ids") or [])
    if uncovered:
        open_questions = _merge_questions(
            open_questions,
            [f"全局计划未使用 {len(uncovered)} 条已提升事实；请在产物复核时检查覆盖范围。"],
        )
    job = _save_job(
        _job_from_store(job.id),
        phase="materializing",
        planned_card_count=len(plans),
        completed_cards=0,
        open_questions=open_questions,
    )

    generated_cards: list[DerivedCard] = []
    failed_cards: list[str] = []
    for card_index, plan in enumerate(plans, start=1):
        current = _job_from_store(job.id)
        if current.status != "running":
            return generated_cards, open_questions, current
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
                    "plan": plan,
                    "facts": fact_payload,
                },
                input_refs={
                    "planned_card_id": plan["id"],
                    "fact_ids": plan["fact_ids"],
                },
                messages=_materialization_messages(
                    profile, plan, fact_payload
                ),
                client=client,
                max_tokens=ARTIFACT_MAX_TOKENS,
                validator=lambda raw, selected_bundle=subset_bundle, card_plan=plan: _validate_materialized_card(
                    raw,
                    selected_bundle,
                    profile,
                    card_plan,
                    model_id=getattr(client, "model", "configured-model"),
                ),
            )
        except Exception as error:  # noqa: BLE001
            failed_cards.append(
                f"第 {card_index}/{len(plans)} 张计划卡（{plan['title']}）：{error}"
            )
            job = _save_job(
                _job_from_store(job.id),
                completed_cards=card_index,
                card_count=len(generated_cards),
                open_questions=open_questions,
            )
            continue
        generated_cards.extend(
            DerivedCard.model_validate(card) for card in output["cards"]
        )
        open_questions = _merge_questions(
            open_questions, list(output.get("open_questions") or [])
        )
        job = _save_job(
            _job_from_store(job.id),
            completed_cards=card_index,
            card_count=len(generated_cards),
            open_questions=open_questions,
        )
    if failed_cards:
        preview = "；".join(failed_cards[:5])
        if len(failed_cards) > 5:
            preview += f"；另有 {len(failed_cards) - 5} 项失败"
        raise ArtifactGenerationError(
            f"本轮 {len(plans)} 张计划卡已全部尝试，其中 {len(failed_cards)} 项失败。"
            f"请使用‘重试失败项’；已成功步骤会复用。{preview}"
        )
    return generated_cards, open_questions, job


def _merge_questions(existing: list[str], incoming: list[str]) -> list[str]:
    return list(dict.fromkeys([*existing, *incoming]))[:50]


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
        "scenes, people, threats, or clocks. All user-facing text must be Chinese."
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
        f"TARGET_PROFILE_JSON={json.dumps(profile.model_dump(mode='json'), ensure_ascii=False)}\n"
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


def _stable_card_id(bundle_id: str, draft: DraftCardPayload) -> str:
    content = json.dumps(draft.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(f"{bundle_id}:{content}".encode("utf-8")).hexdigest()[:14]
    type_slug = re.sub(r"[^a-z0-9_]+", "_", draft.type.lower()).strip("_") or "artifact"
    return f"card_generated_{type_slug}_{digest}"


def _validate_and_build(
    raw: Any,
    bundle: ExampleBundle,
    profile: RuleProfile,
    *,
    model_id: str,
    require_runtime_anchor: bool = True,
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
                card = dict(card)
                fields = dict(card["fields"])
                for field_name in LIST_FIELDS:
                    value = fields.get(field_name)
                    if isinstance(value, str) and value.strip():
                        fields[field_name] = [value.strip()]
                card["fields"] = fields
            normalized_cards.append(card)
        raw = dict(raw)
        raw["cards"] = normalized_cards
    try:
        payload = DraftResponsePayload.model_validate(raw)
    except ValidationError as error:
        raise ArtifactGenerationError(f"备团产物 JSON 不符合约定: {error}") from error

    allowed_facts = {
        fact.id: fact for fact in bundle.facts
        if fact.evidence_status != "model_candidate"
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
            raise ArtifactGenerationError(
                f"产物 {draft.title} 包含未定义字段: {unknown_fields}"
            )
        missing_fields = [
            key for key in definition.required_fields if not _nonempty(draft.fields.get(key))
        ]
        if missing_fields:
            raise ArtifactGenerationError(
                f"产物 {draft.title} 缺少必填字段: {missing_fields}"
            )
        for field_name in LIST_FIELDS & set(draft.fields):
            if not isinstance(draft.fields[field_name], list):
                raise ArtifactGenerationError(
                    f"产物 {draft.title} 的 {field_name} 必须是数组"
                )

        # Some upstreams correctly cite a fact in `field_sources` but omit the
        # same id from the card-level closure.  When that fact is part of this
        # request, repair the declaration deterministically; only references
        # outside the supplied input remain a hard validation error.
        field_sources: dict[str, list[str]] = {}
        closure = list(draft.fact_ids)
        for field_name in draft.fields:
            if field_name == "gm_moves" and not draft.field_sources.get(field_name):
                # GM moves are explicitly allowed to be authored operating prompts.
                # They must never silently claim source provenance.
                field_sources[field_name] = []
                continue
            refs = list(
                dict.fromkeys(
                    draft.field_sources.get(field_name) or list(draft.fact_ids)
                )
            )
            unknown_refs = [item for item in refs if item not in allowed_facts]
            if unknown_refs:
                raise ArtifactGenerationError(
                    f"产物 {draft.title} 的字段 {field_name} 引用了未提升或不存在的事实: {unknown_refs}"
                )
            for fact_id in refs:
                if fact_id not in closure:
                    closure.append(fact_id)
            field_sources[field_name] = refs
        if len(closure) > 100:
            raise ArtifactGenerationError(
                f"产物 {draft.title} 的事实闭包超过允许上限"
            )

        cards.append(
            DerivedCard(
                id=_stable_card_id(bundle.id, draft),
                profile_id=profile.id,
                type=draft.type,
                title=draft.title,
                subtitle=draft.subtitle,
                fact_ids=closure,
                fields=draft.fields,
                field_sources=field_sources,
                open_questions=draft.open_questions,
                generation=CardGeneration(
                    model_id=model_id,
                    prompt_version=PROMPT_VERSION,
                    generated_at=generated_at,
                ),
                edit_state="generated",
            )
        )

    if require_runtime_anchor and profile.profile_kind == "runtime" and not any(
        card.type in RUNTIME_ANCHOR_TYPES for card in cards
    ):
        raise ArtifactGenerationError("运行板块的产物缺少场景或环境卡")
    return cards, payload.open_questions
