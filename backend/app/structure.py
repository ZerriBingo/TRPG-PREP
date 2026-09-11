"""Generate and review narrative chapters, possible beats, and story threads."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import artifacts, storage
from .llm import make_client
from ..domain import (
    AdventureChapter,
    FactDisposition,
    StoryBeat,
    StoryThread,
    StructureDraftJob,
    is_handout_fact,
    load_profiles,
    validate_bundle,
)
from ..domain.models import ExampleBundle
from ..domain.prep import ThreadDevelopment


STRUCTURE_PROMPT_VERSION = "adventure-structure-v5"
STRUCTURE_MAX_TOKENS = 8000
MAX_CHAPTERS = 24
MAX_THREADS = 60
MAX_BEATS = 120

_active_jobs: set[str] = set()
_active_jobs_lock = threading.Lock()


class StructureGenerationError(ValueError):
    """Raised when an adventure structure cannot be completed without data loss."""


class StructureJobConflictError(RuntimeError):
    """Raised when the workspace already has structure work in progress."""


class LocalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    fact_ids: list[str] = Field(min_length=1, max_length=200)
    chapter_hints: list[str] = Field(default_factory=list, max_length=12)
    thread_hints: list[str] = Field(default_factory=list, max_length=20)
    beat_hints: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("fact_ids", "chapter_hints", "thread_hints", "beat_hints")
    @classmethod
    def unique_lists(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class LocalObservationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[LocalObservation] = Field(min_length=1, max_length=40)


class ChapterProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)


class ThreadProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=160)
    kind: Literal["faction", "truth_chain", "location", "character", "event", "free"] = "free"
    summary: str = Field(default="", max_length=2000)
    chapter_roles: dict[
        str, Literal["introduce", "develop", "resolve", "background"]
    ] = Field(default_factory=dict, max_length=24)


class BeatProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(pattern=r"^[a-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    chapter_key: str = Field(min_length=1, max_length=160)
    thread_keys: list[str] = Field(min_length=1, max_length=24)
    thread_developments: dict[str, ThreadDevelopment] = Field(min_length=1, max_length=24)
    interplay: str = Field(min_length=1, max_length=1200)
    fact_ids: list[str] = Field(default_factory=list, max_length=500)


class StructureSkeletonPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapters: list[ChapterProposal] = Field(min_length=1, max_length=MAX_CHAPTERS)
    threads: list[ThreadProposal] = Field(default_factory=list, max_length=MAX_THREADS)
    beats: list[BeatProposal] = Field(default_factory=list, max_length=MAX_BEATS)


class FactAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    kind: Literal["chapter", "global_reference", "display_material", "deferred"]
    chapter_keys: list[str] = Field(default_factory=list, max_length=24)
    thread_keys: list[str] = Field(default_factory=list, max_length=24)
    reason: str = Field(default="", max_length=500)


class FactAssignmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[FactAssignment] = Field(min_length=1, max_length=200)


def _stable_id(prefix: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _job_from_store(job_id: str) -> StructureDraftJob:
    raw = storage.load_structure_job(job_id)
    if raw is None:
        raise StructureGenerationError(f"unknown structure job: {job_id}")
    try:
        return StructureDraftJob.model_validate(raw)
    except ValidationError as error:
        raise StructureGenerationError(f"stored structure job is invalid: {error}") from error


def _save_job(job: StructureDraftJob, **updates: Any) -> StructureDraftJob:
    data = job.model_dump(mode="json")
    data.update(updates)
    data["updated_at"] = storage.now()
    updated = StructureDraftJob.model_validate(data)
    storage.save_structure_job(updated.model_dump(mode="json"))
    return updated


def list_structure_jobs(workspace_id: str) -> list[StructureDraftJob]:
    jobs: list[StructureDraftJob] = []
    for raw in storage.list_structure_jobs(workspace_id):
        try:
            jobs.append(StructureDraftJob.model_validate(raw))
        except ValidationError:
            continue
    return jobs


def latest_structure_job(workspace_id: str) -> StructureDraftJob | None:
    jobs = list_structure_jobs(workspace_id)
    active = [job for job in jobs if job.status in {"running", "queued"}]
    failed = [job for job in jobs if job.status == "failed"]
    return (active or failed or jobs or [None])[0]


def get_structure_job(job_id: str) -> StructureDraftJob:
    return _job_from_store(job_id)


def create_structure_job(
    workspace_id: str,
    profile_id: str,
    *,
    model_id: str,
    fake_model: bool,
) -> tuple[StructureDraftJob, bool]:
    with _active_jobs_lock:
        existing = latest_structure_job(workspace_id)
        if existing and existing.status in {"queued", "running"}:
            return existing, False
        if existing and existing.status == "failed":
            return _save_job(
                existing,
                status="queued",
                phase="queued",
                error=None,
                model_id=model_id,
                fake_model=fake_model,
            ), True
        timestamp = storage.now()
        job = StructureDraftJob(
            id=f"structure_job_{uuid.uuid4().hex}",
            workspace_id=workspace_id,
            profile_id=profile_id,
            model_id=model_id,
            fake_model=fake_model,
            created_at=timestamp,
            updated_at=timestamp,
        )
        storage.create_structure_job(job.model_dump(mode="json"))
        return job, True


def _local_messages(facts: list[dict[str, Any]], batch_index: int, batch_count: int):
    return [
        {
            "role": "system",
            "content": (
                "You identify narrative structure signals in a bounded batch of reviewed TRPG facts. "
                "Return JSON with observations. Each observation has title, summary, fact_ids, "
                "chapter_hints, thread_hints, and beat_hints. Cover every supplied fact at "
                "least once. Chapters are narrative preparation phases, never page ranges or fixed "
                "sessions. Beats organize which threads to use during a chapter's games, "
                "not another cross-chapter storyline or a mandatory route. Preserve "
                "beat_hints as possible combinations of thread changes and unresolved pressures, "
                "not a list of locations or encounters. Preserve "
                "factions, truth chains, objectives, character fates, and off-screen developments "
                "as thread hints. Do not design maps, encounters, or final cards. "
                "Follow RESPONSE_SCHEMA_JSON; do not add undeclared fields. "
                "Write descriptive text in Chinese, preserving JSON field names and fact IDs exactly."
            ),
        },
        {
            "role": "user",
            "content": (
                "[TASK:prep:structure_local]\n"
                f"PROMPT_VERSION_JSON={json.dumps(STRUCTURE_PROMPT_VERSION)}\n"
                f"BATCH_INDEX_JSON={batch_index}\n"
                f"BATCH_COUNT_JSON={batch_count}\n"
                f"RESPONSE_SCHEMA_JSON={json.dumps(LocalObservationPayload.model_json_schema())}\n"
                f"STRUCTURE_FACTS_JSON={json.dumps(facts, ensure_ascii=False)}"
            ),
        },
    ]


def _validate_local(raw: Any, batch_facts: list[Any]) -> dict[str, Any]:
    try:
        payload = LocalObservationPayload.model_validate(raw)
    except ValidationError as error:
        raise StructureGenerationError(f"局部结构观察 JSON 不符合约定: {error}") from error
    allowed = {fact.id for fact in batch_facts}
    covered: set[str] = set()
    observations: list[dict[str, Any]] = []
    for observation in payload.observations:
        unknown = sorted(set(observation.fact_ids) - allowed)
        if unknown:
            raise StructureGenerationError(f"局部结构观察引用批次外事实: {unknown}")
        covered.update(observation.fact_ids)
        observations.append(observation.model_dump(mode="json"))
    missing = sorted(allowed - covered)
    if missing:
        observations.append(
            {
                "title": "待归入叙事结构",
                "summary": f"本批仍有 {len(missing)} 条事实需要在全局结构中确定用途。",
                "fact_ids": missing,
                "chapter_hints": [],
                "thread_hints": [],
                "beat_hints": [],
            }
        )
    return {"observations": observations}


def _skeleton_messages(observations: list[dict[str, Any]]):
    compact = [
        {
            "title": item["title"][:160],
            "summary": item["summary"][:500],
            "fact_ids": item["fact_ids"],
            "chapter_hints": item["chapter_hints"][:8],
            "thread_hints": item["thread_hints"][:12],
            "beat_hints": item["beat_hints"][:12],
        }
        for item in observations
    ]
    return [
        {
            "role": "system",
            "content": (
                "You consolidate compact TRPG observations into a reviewable narrative structure. "
                "Return JSON with chapters, threads, and beats. Every item needs a short stable "
                "lowercase ASCII key. Chapters are broad narrative preparation phases, not source "
                "chapters, page ranges, sessions, maps, or a required route. Threads cross chapters "
                "with introduce/develop/resolve/background roles. Threads may cross chapters. Each beat "
                "belongs to exactly one chapter_key and combines threads applicable to that chapter; "
                "beats must never span chapters. A beat should describe a playable focus for a game "
                "within that chapter: which threads interact, the immediate pressure, and possible "
                "changes. Do not restate a whole storyline as a beat or prescribe a fixed session count. "
                "A beat organizes a possible game's narrative focus, not a location, encounter, or environment card. "
                "For each selected thread_key, thread_developments must contain exactly one object with "
                "development (what may change in this beat) and carry_forward (what remains open, "
                "or what resolving it would change later). interplay explains how these developments "
                "can affect each other, with alternatives for different player choices. For a single "
                "thread, explain its tension and possible consequences without inventing another thread. "
                "Across a chapter's beats, vary the thread developments rather than repeating the "
                "same thread summaries or ordering scene visits. Do not force two threads per beat, "
                "a fixed number of games, or guaranteed future outcomes. Keep each development and "
                "carry_forward to one concise sentence. These are source-grounded preparation "
                "suggestions, never new historical facts or a scripted player route. "
                "Do not label threads as main or sub. Prefer a small useful skeleton over page-shaped "
                "fragmentation. Use only supplied fact_ids. Follow RESPONSE_SCHEMA_JSON exactly. "
                "chapters, threads, and beats are arrays. Chapter objects contain only key, title, "
                "summary. Thread chapter_roles is an object mapping exact chapter keys to roles, "
                "not a single role. Only beats may contain fact_ids at this skeleton stage; "
                "chapter and thread fact membership will be assigned in the next stage. "
                "Write descriptions in Chinese; preserve JSON field names, keys, enum values, and IDs."
            ),
        },
        {
            "role": "user",
            "content": (
                "[TASK:prep:structure_consolidate]\n"
                f"PROMPT_VERSION_JSON={json.dumps(STRUCTURE_PROMPT_VERSION)}\n"
                f"RESPONSE_SCHEMA_JSON={json.dumps(StructureSkeletonPayload.model_json_schema())}\n"
                f"STRUCTURE_OBSERVATIONS_JSON={json.dumps(compact, ensure_ascii=False)}"
            ),
        },
    ]


def _validate_skeleton(raw: Any, allowed_fact_ids: set[str]) -> dict[str, Any]:
    try:
        payload = StructureSkeletonPayload.model_validate(raw)
    except ValidationError as error:
        raise StructureGenerationError(f"冒险结构 JSON 不符合约定: {error}") from error
    chapter_keys = [chapter.key for chapter in payload.chapters]
    thread_keys = [thread.key for thread in payload.threads]
    if len(chapter_keys) != len(set(chapter_keys)):
        raise StructureGenerationError("冒险结构包含重复章节 key")
    if len(thread_keys) != len(set(thread_keys)):
        raise StructureGenerationError("冒险结构包含重复剧情线 key")
    for thread in payload.threads:
        unknown = sorted(set(thread.chapter_roles) - set(chapter_keys))
        if unknown:
            raise StructureGenerationError(f"剧情线引用未知章节: {unknown}")
    beat_keys = [beat.key for beat in payload.beats]
    if len(beat_keys) != len(set(beat_keys)):
        raise StructureGenerationError("冒险结构包含重复拍 key")
    thread_by_key = {thread.key: thread for thread in payload.threads}
    for beat in payload.beats:
        if set(beat.thread_developments) != set(beat.thread_keys):
            raise StructureGenerationError("拍的剧情线进展必须与所选剧情线逐一对应")
        unknown_chapters = sorted({beat.chapter_key} - set(chapter_keys))
        unknown_threads = sorted(set(beat.thread_keys) - set(thread_keys))
        unknown_facts = sorted(set(beat.fact_ids) - allowed_fact_ids)
        if unknown_chapters or unknown_threads or unknown_facts:
            raise StructureGenerationError(
                "拍引用未知内容: "
                f"chapters={unknown_chapters}, threads={unknown_threads}, facts={unknown_facts}"
            )
        inapplicable = sorted(
            key for key in beat.thread_keys
            if beat.chapter_key not in thread_by_key[key].chapter_roles
        )
        if inapplicable:
            raise StructureGenerationError(
                f"拍引用了不适用于其章节的剧情线: chapter={beat.chapter_key}, threads={inapplicable}"
            )
    return payload.model_dump(mode="json")


def _assignment_messages(
    facts: list[dict[str, Any]], skeleton: dict[str, Any], batch_index: int, batch_count: int
):
    compact_skeleton = {
        "chapters": skeleton["chapters"],
        "threads": skeleton["threads"],
    }
    schema = FactAssignmentPayload.model_json_schema()
    fields = schema["$defs"]["FactAssignment"]["properties"]
    fields["fact_id"]["enum"] = [fact["id"] for fact in facts]
    fields["chapter_keys"]["items"]["enum"] = [item["key"] for item in skeleton["chapters"]]
    if skeleton["threads"]:
        fields["thread_keys"]["items"]["enum"] = [item["key"] for item in skeleton["threads"]]
    else:
        fields["thread_keys"]["maxItems"] = 0
    example = {"assignments": [{
        "fact_id": facts[0]["id"],
        "kind": "chapter",
        "chapter_keys": [skeleton["chapters"][0]["key"]],
        "thread_keys": [],
        "reason": "",
    }]}
    return [
        {
            "role": "system",
            "content": (
                "Assign every supplied reviewed fact to a proposed preparation use for GM review. Return JSON "
                "with assignments. kind is chapter, global_reference, display_material, or deferred. "
                "Chapter facts may name one or several chapter_keys. Global reference is for "
                "setting, rules, or reusable context that should remain reachable but need not "
                "become a card. Display material is only for an actual showable source asset. "
                "Do not use global_reference merely because a fact spans chapters or to reduce "
                "chapter size. Actionable leads, NPC motives, investigable locations, threats, and "
                "situation changes must belong to all applicable chapters. Pure setting background, "
                "general rules commentary, and non-actionable context may remain global_reference. "
                "A reusable actionable fact can belong to multiple chapters. "
                "Deferred requires a concise reason. thread_keys may cross chapters. Return each "
                "fact_id exactly once and use only supplied keys. Follow RESPONSE_SCHEMA_JSON. "
                "assignments MUST be an array of objects, each with an explicit fact_id; never "
                "use fact IDs as dictionary keys, either at the root or inside assignments. "
                "Copy chapter_keys and thread_keys exactly from the supplied skeleton; never "
                "abbreviate or renumber them. For kind=chapter supply at least one chapter_key; "
                "for all other kinds chapter_keys must be empty. The example shows one entry's "
                "shape only, not the required classification or the complete response. "
                "Return all supplied facts. Chinese descriptions only; preserve field names, "
                "IDs, keys, and enum values exactly."
            ),
        },
        {
            "role": "user",
            "content": (
                "[TASK:prep:structure_assign]\n"
                f"PROMPT_VERSION_JSON={json.dumps(STRUCTURE_PROMPT_VERSION)}\n"
                f"BATCH_INDEX_JSON={batch_index}\n"
                f"BATCH_COUNT_JSON={batch_count}\n"
                f"RESPONSE_SCHEMA_JSON={json.dumps(schema)}\n"
                f"RESPONSE_EXAMPLE_JSON={json.dumps(example)}\n"
                f"STRUCTURE_SKELETON_JSON={json.dumps(compact_skeleton, ensure_ascii=False)}\n"
                f"STRUCTURE_FACTS_JSON={json.dumps(facts, ensure_ascii=False)}"
            ),
        },
    ]


def _validate_assignments(
    raw: Any,
    batch_facts: list[Any],
    skeleton: dict[str, Any],
) -> dict[str, Any]:
    expected = {fact.id for fact in batch_facts}
    raw = _normalize_assignment_shape(raw, expected)
    try:
        payload = FactAssignmentPayload.model_validate(raw)
    except ValidationError as error:
        raise StructureGenerationError(f"事实结构归属 JSON 不符合约定: {error}") from error
    actual = [item.fact_id for item in payload.assignments]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        missing = sorted(expected - set(actual))
        extra = sorted(set(actual) - expected)
        raise StructureGenerationError(
            f"事实结构归属不闭合: missing={missing[:20]}, extra={extra[:20]}"
        )
    chapter_keys = {item["key"] for item in skeleton["chapters"]}
    thread_keys = {item["key"] for item in skeleton["threads"]}
    facts_by_id = {fact.id: fact for fact in batch_facts}
    assignments: list[dict[str, Any]] = []
    for item in payload.assignments:
        item.chapter_keys = list(dict.fromkeys(
            _resolve_chapter_key(key, chapter_keys) for key in item.chapter_keys
        ))
        unknown_chapters = sorted(set(item.chapter_keys) - chapter_keys)
        unknown_threads = sorted(set(item.thread_keys) - thread_keys)
        if unknown_chapters or unknown_threads:
            raise StructureGenerationError(
                f"事实归属引用未知结构: chapters={unknown_chapters}, threads={unknown_threads}; "
                f"allowed_chapter_keys={sorted(chapter_keys)}, allowed_thread_keys={sorted(thread_keys)}"
            )
        data = item.model_dump(mode="json")
        if is_handout_fact(facts_by_id[item.fact_id]):
            data.update(kind="display_material", chapter_keys=[], reason="")
        if data["kind"] == "chapter" and not data["chapter_keys"]:
            raise StructureGenerationError(f"章节事实缺少章节归属: {item.fact_id}")
        if data["kind"] != "chapter":
            data["chapter_keys"] = []
        if data["kind"] == "deferred" and not data["reason"].strip():
            raise StructureGenerationError(f"暂缓事实缺少理由: {item.fact_id}")
        assignments.append(data)
    return {"assignments": assignments}


def _resolve_chapter_key(key: str, allowed: set[str]) -> str:
    if key in allowed:
        return key
    # Match only complete prefix components; chapter1 must not match chapter10.
    matches = [candidate for candidate in allowed if candidate.startswith((key + "_", key + "-"))]
    return matches[0] if len(matches) == 1 else key


def _normalize_assignment_shape(raw: Any, expected: set[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    if "assignments" in raw:
        entries = raw["assignments"]
        wrapper = dict(raw)
    elif raw and set(raw) <= expected:
        entries = raw
        wrapper = {}
    else:
        return raw
    if not isinstance(entries, dict):
        return raw
    assignments = []
    for fact_id, entry in entries.items():
        if not isinstance(entry, dict):
            raise StructureGenerationError("事实归属字典的每个值必须是归属对象")
        if "fact_id" in entry and entry["fact_id"] != fact_id:
            raise StructureGenerationError(f"事实归属字典键与 fact_id 冲突: {fact_id}")
        assignments.append({**entry, "fact_id": fact_id})
    # Preserve extra wrapper/entry fields so strict validation can reject them.
    wrapper["assignments"] = assignments
    return wrapper


def _materialize_structure(
    skeleton: dict[str, Any], raw_assignments: list[dict[str, Any]]
) -> tuple[list[AdventureChapter], list[StoryThread], list[StoryBeat], list[FactDisposition]]:
    chapter_ids = {
        item["key"]: _stable_id("chapter", item["key"]) for item in skeleton["chapters"]
    }
    thread_ids = {
        item["key"]: _stable_id("thread", item["key"]) for item in skeleton["threads"]
    }
    dispositions = [
        FactDisposition(
            fact_id=item["fact_id"],
            kind=item["kind"],
            chapter_ids=[chapter_ids[key] for key in item["chapter_keys"]],
            thread_ids=[thread_ids[key] for key in item["thread_keys"]],
            reason=item["reason"],
        )
        for item in raw_assignments
    ]
    chapter_facts = {chapter_id: [] for chapter_id in chapter_ids.values()}
    thread_facts = {thread_id: [] for thread_id in thread_ids.values()}
    for disposition in dispositions:
        for chapter_id in disposition.chapter_ids:
            chapter_facts[chapter_id].append(disposition.fact_id)
        for thread_id in disposition.thread_ids:
            thread_facts[thread_id].append(disposition.fact_id)
    empty_chapters = [
        item["title"]
        for item in skeleton["chapters"]
        if not chapter_facts[chapter_ids[item["key"]]]
    ]
    if empty_chapters:
        raise StructureGenerationError(
            "冒险结构包含没有适用事实的章节: " + "、".join(empty_chapters)
        )
    chapters = [
        AdventureChapter(
            id=chapter_ids[item["key"]],
            title=item["title"],
            summary=item["summary"],
            fact_ids=chapter_facts[chapter_ids[item["key"]]],
        )
        for item in skeleton["chapters"]
    ]
    threads = [
        StoryThread(
            id=thread_ids[item["key"]],
            title=item["title"],
            kind=item["kind"],
            summary=item["summary"],
            chapter_roles={
                chapter_ids[key]: role for key, role in item["chapter_roles"].items()
            },
            fact_ids=thread_facts[thread_ids[item["key"]]],
        )
        for item in skeleton["threads"]
    ]
    beats = [
        StoryBeat(
            id=_stable_id("story_beat", item["key"]),
            title=item["title"],
            summary=item["summary"],
            chapter_id=chapter_ids[item["chapter_key"]],
            thread_ids=[thread_ids[key] for key in item["thread_keys"]],
            thread_developments={thread_ids[key]: value for key, value in item["thread_developments"].items()},
            interplay=item["interplay"],
            fact_ids=item["fact_ids"],
        )
        for item in skeleton["beats"]
    ]
    return chapters, threads, beats, dispositions


def execute_structure_job(job_id: str) -> None:
    with _active_jobs_lock:
        if job_id in _active_jobs:
            return
        _active_jobs.add(job_id)
    try:
        job = _save_job(_job_from_store(job_id), status="running", error=None)
        saved = storage.load_domain_bundle(job.workspace_id)
        if not saved:
            raise StructureGenerationError("书架工作区不存在")
        bundle = ExampleBundle.model_validate(saved[0])
        profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
        validate_bundle(bundle, profiles)
        profile = profiles.get(job.profile_id)
        if profile is None or profile.runtime_navigation_mode != "environment":
            raise StructureGenerationError("冒险结构整理仅用于奇幻冒险板块")
        facts = [
            fact for fact in bundle.facts if fact.evidence_status != "model_candidate"
        ]
        if not facts:
            raise StructureGenerationError("书架没有可整理的已提升事实")
        batches = artifacts._fact_batches(facts)
        client = make_client(model_id=job.model_id, force_fake=job.fake_model)
        job = _save_job(
            job,
            fact_count=len(facts),
            batch_count=len(batches),
            completed_batches=0,
            chapter_count=0,
            thread_count=0,
            beat_count=0,
        )
        observations: list[dict[str, Any]] = []
        job = _save_job(job, phase="local_observations")
        for index, batch in enumerate(batches, start=1):
            fact_payload = artifacts._fact_payload_from_facts(batch)
            output = artifacts._run_json_step(
                job,
                stage="structure_local",
                step_index=index,
                input_payload={"version": STRUCTURE_PROMPT_VERSION, "facts": fact_payload},
                input_refs={"fact_ids": [fact.id for fact in batch]},
                messages=_local_messages(fact_payload, index, len(batches)),
                client=client,
                max_tokens=STRUCTURE_MAX_TOKENS,
                validator=lambda raw, owned=batch: _validate_local(raw, owned),
            )
            observations.extend(output["observations"])
            job = _save_job(_job_from_store(job.id), completed_batches=index)
        allowed_ids = {fact.id for fact in facts}
        job = _save_job(_job_from_store(job.id), phase="consolidating")
        skeleton = artifacts._run_json_step(
            job,
            stage="structure_consolidate",
            step_index=1,
            input_payload={"version": STRUCTURE_PROMPT_VERSION, "observations": observations},
            input_refs={"fact_ids": sorted(allowed_ids)},
            messages=_skeleton_messages(observations),
            client=client,
            max_tokens=STRUCTURE_MAX_TOKENS,
            validator=lambda raw: _validate_skeleton(raw, allowed_ids),
        )
        assignments: list[dict[str, Any]] = []
        job = _save_job(
            _job_from_store(job.id),
            phase="assigning_facts",
            completed_batches=0,
            chapter_count=len(skeleton["chapters"]),
            thread_count=len(skeleton["threads"]),
            beat_count=len(skeleton["beats"]),
        )
        for index, batch in enumerate(batches, start=1):
            fact_payload = artifacts._fact_payload_from_facts(batch)
            output = artifacts._run_json_step(
                job,
                stage="structure_assign",
                step_index=index,
                input_payload={
                    "version": STRUCTURE_PROMPT_VERSION,
                    "skeleton": skeleton,
                    "facts": fact_payload,
                },
                input_refs={"fact_ids": [fact.id for fact in batch]},
                messages=_assignment_messages(fact_payload, skeleton, index, len(batches)),
                client=client,
                max_tokens=STRUCTURE_MAX_TOKENS,
                validator=lambda raw, owned=batch: _validate_assignments(raw, owned, skeleton),
            )
            assignments.extend(output["assignments"])
            job = _save_job(_job_from_store(job.id), completed_batches=index)
        chapters, threads, beats, dispositions = _materialize_structure(
            skeleton, assignments
        )
        job = _save_job(_job_from_store(job.id), phase="validating")
        latest = storage.load_domain_bundle(job.workspace_id)
        if not latest:
            raise StructureGenerationError("书架工作区在整理期间被删除")
        bundle = ExampleBundle.model_validate(latest[0])
        bundle.adventure_chapters = chapters
        bundle.story_threads = threads
        bundle.story_beats = beats
        bundle.fact_dispositions = dispositions
        validate_bundle(bundle, profiles)
        storage.save_domain_bundle(
            job.workspace_id, bundle.model_dump(mode="json", by_alias=True)
        )
        _save_job(
            job,
            status="completed",
            phase="completed",
            completed_batches=len(batches),
            error=None,
        )
    except Exception as error:  # noqa: BLE001
        try:
            _save_job(_job_from_store(job_id), status="failed", error=str(error)[:2000])
        except Exception:  # noqa: BLE001
            pass
    finally:
        with _active_jobs_lock:
            _active_jobs.discard(job_id)


def approve_structure(workspace_id: str) -> ExampleBundle:
    saved = storage.load_domain_bundle(workspace_id)
    if not saved:
        raise StructureGenerationError("书架工作区不存在")
    bundle = ExampleBundle.model_validate(saved[0])
    if not bundle.adventure_chapters:
        raise StructureGenerationError("尚无可确认的冒险结构")
    bundle.adventure_chapters = [
        item.model_copy(update={"review_state": "approved"})
        for item in bundle.adventure_chapters
    ]
    bundle.story_threads = [
        item.model_copy(update={"review_state": "approved"})
        for item in bundle.story_threads
    ]
    bundle.story_beats = [
        item.model_copy(update={"review_state": "approved"})
        for item in bundle.story_beats
    ]
    profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
    validate_bundle(bundle, profiles)
    storage.save_domain_bundle(
        workspace_id, bundle.model_dump(mode="json", by_alias=True)
    )
    return bundle


def recover_interrupted_structure_jobs() -> int:
    recovered = 0
    for raw in storage.list_structure_jobs():
        try:
            job = StructureDraftJob.model_validate(raw)
        except ValidationError:
            continue
        if job.status not in {"queued", "running"} or job.id in _active_jobs:
            continue
        _save_job(
            job,
            status="failed",
            error="服务重启，之前的冒险结构整理已停止；请重新尝试。",
        )
        recovered += 1
    return recovered
