"""P1.1 shadow-model task lifecycle with no path into approved runtime data."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..domain import (
    ShadowCandidate,
    ShadowCandidateDraft,
    ShadowCandidateEdit,
    ShadowCandidateMergeIn,
    ShadowCandidateSplitIn,
    ShadowResponse,
    ShadowRun,
    ShadowTask,
    ShadowTaskSpec,
)
from ..domain.shadow import ShadowReviewEvent
from ..domain.models import SourceRef
from . import storage
from .llm import parse_json


class ShadowModeError(ValueError):
    """Base error for the isolated candidate/review queue."""


class ShadowTaskNotFoundError(ShadowModeError):
    pass


class ShadowTaskConflictError(ShadowModeError):
    pass


class ShadowCandidateNotFoundError(ShadowModeError):
    pass


class ShadowResultValidationError(ShadowModeError):
    pass


def _transport_error_kind(error: str) -> str:
    text = str(error or "").casefold()
    if "cancel" in text or "取消" in text:
        return "cancelled"
    if "account_muted" in text or "账号访问被暂停" in text:
        return "account_access"
    if any(token in text for token in ("429", "502", "503", "524", "rate limit", "temporarily", "timeout", "timed out", "network", "网络", "上游")):
        return "upstream_unavailable"
    if any(token in text for token in ("401", "403", "422", "api key", "配置", "密钥", "invalid model")):
        return "input_config"
    if "worker" in text or "子进程" in text:
        return "worker"
    return "upstream_unavailable"


def _task_spec_values(task: ShadowTask) -> dict:
    return {
        field_name: getattr(task, field_name)
        for field_name in ShadowTaskSpec.model_fields
    }


def _task_from_store(task_id: str) -> ShadowTask:
    raw = storage.load_shadow_task(task_id)
    if raw is None:
        raise ShadowTaskNotFoundError(f"unknown shadow task: {task_id}")
    try:
        return ShadowTask.model_validate(raw)
    except ValidationError as error:
        raise ShadowResultValidationError(
            f"stored shadow task {task_id} is invalid: {error}"
        ) from error


def _candidate_from_store(candidate_id: str) -> ShadowCandidate:
    raw = storage.load_shadow_candidate(candidate_id)
    if raw is None:
        raise ShadowCandidateNotFoundError(f"unknown shadow candidate: {candidate_id}")
    try:
        return ShadowCandidate.model_validate(raw)
    except ValidationError as error:
        raise ShadowResultValidationError(
            f"stored shadow candidate {candidate_id} is invalid: {error}"
        ) from error


def create_shadow_task(spec: ShadowTaskSpec) -> tuple[ShadowTask, bool]:
    """Create once per idempotency key; divergent reuse is rejected."""
    existing_raw = storage.load_shadow_task_by_idempotency_key(spec.idempotency_key)
    if existing_raw is not None:
        existing = ShadowTask.model_validate(existing_raw)
        if _task_spec_values(existing) != spec.model_dump(mode="json"):
            raise ShadowTaskConflictError(
                "idempotency key already belongs to a different shadow task"
            )
        return existing, False

    timestamp = storage.now()
    task = ShadowTask(
        id=f"shadow_task_{uuid.uuid4().hex}",
        **spec.model_dump(mode="json"),
        created_at=timestamp,
        updated_at=timestamp,
    )
    if storage.create_shadow_task(task.model_dump(mode="json")):
        return task, True

    # A concurrent request may have created the same idempotency key first.
    existing_raw = storage.load_shadow_task_by_idempotency_key(spec.idempotency_key)
    if existing_raw is None:
        raise ShadowModeError("could not create or reload shadow task")
    existing = ShadowTask.model_validate(existing_raw)
    if _task_spec_values(existing) != spec.model_dump(mode="json"):
        raise ShadowTaskConflictError(
            "idempotency key already belongs to a different shadow task"
        )
    return existing, False


def list_shadow_tasks(*, include_internal: bool = False) -> list[ShadowTask]:
    tasks = [ShadowTask.model_validate(item) for item in storage.list_shadow_tasks()]
    if include_internal:
        return tasks
    return [task for task in tasks if task.queue_visibility == "review"]


def list_shadow_runs(task_id: str) -> list[ShadowRun]:
    _task_from_store(task_id)
    return [ShadowRun.model_validate(item) for item in storage.list_shadow_runs(task_id)]


def list_shadow_candidates(
    task_id: str | None = None,
    review_state: str | None = "needs_review",
    *,
    include_internal: bool = False,
) -> list[ShadowCandidate]:
    if task_id is not None:
        _task_from_store(task_id)
    if review_state not in {None, "needs_review", "accepted", "rejected"}:
        raise ShadowResultValidationError(f"invalid shadow review state: {review_state}")
    return [
        ShadowCandidate.model_validate(item)
        for item in storage.list_shadow_candidates(
            task_id,
            review_state,
            queue_visibility=None if include_internal else "review",
        )
    ]


def shadow_task_detail(task_id: str) -> dict:
    task = _task_from_store(task_id)
    return {
        "task": task,
        "runs": list_shadow_runs(task_id),
        "candidates": list_shadow_candidates(
            task_id, review_state=None, include_internal=True
        ),
    }


def set_shadow_task_visibility(task_id: str, queue_visibility: str) -> ShadowTask:
    """Change queue visibility for a task and all of its current candidates."""
    _task_from_store(task_id)
    if queue_visibility not in {"review", "internal"}:
        raise ShadowResultValidationError("invalid shadow task queue visibility")
    storage.set_shadow_task_visibility(task_id, queue_visibility)
    return _task_from_store(task_id)


def cancel_shadow_task(task_id: str) -> ShadowTask:
    task = _task_from_store(task_id)
    if task.status == "completed":
        raise ShadowTaskConflictError("completed shadow tasks cannot be cancelled")
    if task.status == "cancelled":
        return task
    cancelled = task.model_copy(
        update={"status": "cancelled", "updated_at": storage.now()}
    )
    storage.save_shadow_task(cancelled.model_dump(mode="json"))
    return cancelled


def _review_candidate(
    candidate: ShadowCandidate,
    *,
    review_state: str,
    text: str | None,
    review_note: str | None,
    update_text: bool,
    update_review_note: bool,
    content_basis: str | None = None,
    action: str = "review",
    source_changes: list[str] | None = None,
    field_paths: list[str] | None = None,
    related_candidate_ids: list[str] | None = None,
    timestamp: str,
) -> ShadowCandidate:
    if review_state not in {"needs_review", "accepted", "rejected"}:
        raise ShadowResultValidationError(f"invalid shadow review state: {review_state}")
    if update_text and review_state != "needs_review":
        raise ShadowResultValidationError(
            "edited candidate content must return to needs_review before acceptance"
        )
    if update_text and text is not None:
        text = text.strip() or None
    if update_review_note and review_note is not None:
        review_note = review_note.strip() or None
    final_text = text.strip() if update_text and text else candidate.text
    final_note = review_note if update_review_note else candidate.review_note
    event = ShadowReviewEvent(
        id=f"shadow_review_{uuid.uuid4().hex}",
        action="edit" if update_text else action,
        review_state=review_state,
        note=final_note,
        source_changes=source_changes or [],
        field_paths=field_paths or (["text"] if update_text else []),
        related_candidate_ids=related_candidate_ids or [],
        created_at=timestamp,
    )
    candidate_data = candidate.model_dump(mode="json")
    candidate_data.update(
        {
            "review_state": review_state,
            "text": final_text,
            "review_note": final_note,
            "reviewed_at": timestamp,
            "content_basis": content_basis or (
                "inference" if update_text and final_text != candidate.text
                else candidate.content_basis
            ),
            "review_history": [
                *candidate_data["review_history"],
                event.model_dump(mode="json"),
            ],
        }
    )
    return ShadowCandidate.model_validate(candidate_data)


def review_shadow_candidate(
    candidate_id: str,
    *,
    review_state: str,
    text: str | None = None,
    review_note: str | None = None,
    update_text: bool = False,
    update_review_note: bool = False,
    content_basis: str | None = None,
) -> ShadowCandidate:
    """Apply one review mark to the current candidate record."""
    candidate = _candidate_from_store(candidate_id)
    updated = _review_candidate(
        candidate,
        review_state=review_state,
        text=text,
        review_note=review_note,
        update_text=update_text,
        update_review_note=update_review_note,
        content_basis=content_basis,
        timestamp=storage.now(),
    )
    storage.save_shadow_candidate(updated.model_dump(mode="json"))
    return updated


def review_shadow_candidates(
    candidate_ids: list[str],
    *,
    review_state: str,
    review_note: str | None = None,
    update_review_note: bool = False,
) -> list[ShadowCandidate]:
    """Apply one review mark to several candidates in a single storage transaction."""
    cleaned_ids = [candidate_id.strip() for candidate_id in candidate_ids if candidate_id.strip()]
    if not cleaned_ids:
        raise ShadowResultValidationError("batch review needs at least one candidate id")
    if len(cleaned_ids) != len(set(cleaned_ids)):
        raise ShadowResultValidationError("batch review candidate ids must be unique")
    candidates = [_candidate_from_store(candidate_id) for candidate_id in cleaned_ids]
    timestamp = storage.now()
    updated = [
        _review_candidate(
            candidate,
            review_state=review_state,
            text=None,
            review_note=review_note,
            update_text=False,
            update_review_note=update_review_note,
            timestamp=timestamp,
        )
        for candidate in candidates
    ]
    storage.save_shadow_candidates(
        [candidate.model_dump(mode="json") for candidate in updated]
    )
    return updated


def _dedupe_source_refs(refs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for ref in refs:
        key = json.dumps(ref, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _replacement_event(
    *,
    action: str,
    note: str | None,
    related_candidate_ids: list[str],
    timestamp: str,
) -> ShadowReviewEvent:
    return ShadowReviewEvent(
        id=f"shadow_review_{uuid.uuid4().hex}",
        action=action,
        review_state="needs_review",
        note=note,
        related_candidate_ids=related_candidate_ids,
        created_at=timestamp,
    )


def _candidate_from_replacement(
    *,
    task: ShadowTask,
    run_id: str,
    draft: ShadowCandidateDraft,
    content_basis: str,
    event: ShadowReviewEvent,
) -> ShadowCandidate:
    candidate_data = draft.model_dump(mode="json")
    candidate_data["source_refs"] = _validate_candidate_sources(draft, task)
    return ShadowCandidate(
        id=f"shadow_candidate_{uuid.uuid4().hex}",
        task_id=task.id,
        run_id=run_id,
        evidence_status="model_candidate",
        queue_visibility=task.queue_visibility,
        candidate_role=(
            "segment_result"
            if task.task_kind == "semantic_consolidation"
            else "window_observation"
            if task.task_kind == "prep_window"
            else "standalone"
        ),
        semantic_segment_id=task.semantic_segment_id,
        content_basis=content_basis,
        review_state="needs_review",
        review_note=event.note,
        reviewed_at=event.created_at,
        review_history=[event],
        created_at=storage.now(),
        **candidate_data,
    )


def edit_shadow_candidate(candidate_id: str, edit: ShadowCandidateEdit) -> ShadowCandidate:
    """Replace current candidate content and return it to the review queue."""
    candidate = _candidate_from_store(candidate_id)
    task = _task_from_store(candidate.task_id)
    changes: list[str] = []
    source_changes: list[str] = []
    data = candidate.model_dump(mode="json")
    for field_name in (
        "text",
        "kind",
        "source_refs",
        "possible_links",
        "open_questions",
        "content_basis",
    ):
        value = getattr(edit, field_name)
        if value is None:
            continue
        if field_name == "source_refs":
            draft = ShadowCandidateDraft(
                text=data["text"],
                kind=data["kind"],
                source_refs=value,
                possible_links=data["possible_links"],
                open_questions=data["open_questions"],
            )
            value = _validate_candidate_sources(draft, task)
        elif hasattr(value, "value"):
            value = value.value
        data[field_name] = value
        changes.append(field_name)
        if field_name == "source_refs":
            source_changes.append("source_refs")
    review_note_provided = "review_note" in edit.model_fields_set
    final_note = edit.review_note if review_note_provided else candidate.review_note
    if review_note_provided:
        changes.append("review_note")
    timestamp = storage.now()
    event = _replacement_event(
        action="edit",
        note=final_note,
        related_candidate_ids=[candidate.id],
        timestamp=timestamp,
    )
    data.update(
        {
            "review_state": "needs_review",
            "review_note": event.note,
            "reviewed_at": timestamp,
            "review_history": [*candidate.review_history, event],
        }
    )
    event_data = event.model_dump(mode="json")
    event_data["field_paths"] = changes
    event_data["source_changes"] = source_changes
    data["review_history"][-1] = event_data
    updated = ShadowCandidate.model_validate(data)
    storage.save_shadow_candidate(updated.model_dump(mode="json"))
    return updated


def split_shadow_candidate(
    candidate_id: str, request: ShadowCandidateSplitIn
) -> list[ShadowCandidate]:
    """Replace one candidate with reviewed child candidates atomically."""
    candidate = _candidate_from_store(candidate_id)
    task = _task_from_store(candidate.task_id)
    event = _replacement_event(
        action="split",
        note=request.review_note,
        related_candidate_ids=[candidate.id],
        timestamp=storage.now(),
    )
    replacements = [
        _candidate_from_replacement(
            task=task,
            run_id=candidate.run_id,
            draft=part,
            content_basis=request.content_basis,
            event=event.model_copy(update={"id": f"shadow_review_{uuid.uuid4().hex}"}),
        )
        for part in request.parts
    ]
    storage.replace_shadow_candidates(
        task.id,
        [candidate.id],
        [item.model_dump(mode="json") for item in replacements],
    )
    return replacements


def merge_shadow_candidates(request: ShadowCandidateMergeIn) -> ShadowCandidate:
    """Replace several candidates with one reviewed merged candidate atomically."""
    candidates = [_candidate_from_store(candidate_id) for candidate_id in request.candidate_ids]
    task_ids = {candidate.task_id for candidate in candidates}
    if len(task_ids) != 1:
        raise ShadowResultValidationError("merge candidates must belong to one shadow task")
    task = _task_from_store(candidates[0].task_id)
    source_refs = request.source_refs
    if source_refs is None:
        source_refs = [
            SourceRef.model_validate(ref)
            for candidate in candidates
            for ref in candidate.source_refs
        ]
        source_refs = [SourceRef.model_validate(ref) for ref in _dedupe_source_refs([ref.model_dump(mode="json") for ref in source_refs])]
    kind = request.kind or (candidates[0].kind if len({candidate.kind for candidate in candidates}) == 1 else "clue")
    possible_links = request.possible_links
    if possible_links is None:
        possible_links = list(dict.fromkeys(link for candidate in candidates for link in candidate.possible_links))
    open_questions = request.open_questions
    if open_questions is None:
        open_questions = list(dict.fromkeys(item for candidate in candidates for item in candidate.open_questions))
    draft = ShadowCandidateDraft(
        text=request.text,
        kind=kind,
        source_refs=source_refs,
        possible_links=possible_links,
        open_questions=open_questions,
    )
    event = _replacement_event(
        action="merge",
        note=request.review_note,
        related_candidate_ids=[candidate.id for candidate in candidates],
        timestamp=storage.now(),
    )
    replacement = _candidate_from_replacement(
        task=task,
        run_id=candidates[0].run_id,
        draft=draft,
        content_basis=request.content_basis,
        event=event,
    )
    storage.replace_shadow_candidates(
        task.id,
        request.candidate_ids,
        [replacement.model_dump(mode="json")],
    )
    return replacement


def _strip_json_fence(raw_response: str) -> str:
    text = raw_response.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline == -1:
            return text
        text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _response_summary(raw_response: str | None) -> str | None:
    if raw_response is None:
        return None
    text = raw_response.strip()
    if len(text) <= 2000:
        return text
    return text[:1997] + "..."


def _validate_candidate_sources(
    draft: ShadowCandidateDraft, task: ShadowTask
) -> list[dict]:
    refs: list[dict] = []
    allowed_pages = set(task.source_pages)
    for reference in draft.source_refs:
        if reference.file != task.source_file:
            raise ShadowResultValidationError(
                f"candidate source file {reference.file!r} does not match task source"
            )
        if reference.page not in allowed_pages:
            raise ShadowResultValidationError(
                f"candidate source page {reference.page} is outside the task range"
            )
        if (
            reference.source_version is not None
            and reference.source_version != task.source_version
        ):
            raise ShadowResultValidationError(
                "candidate source version does not match the task source version"
            )
        item = reference.model_dump(mode="json")
        item["source_version"] = task.source_version
        refs.append(item)
    return refs


def _finish_run(
    task: ShadowTask,
    run: ShadowRun,
    *,
    status: str,
    candidates: list[ShadowCandidate] | None = None,
    parse_error: str | None = None,
    transport_error: str | None = None,
    error_kind: str | None = None,
) -> tuple[ShadowTask, ShadowRun, list[ShadowCandidate]]:
    timestamp = storage.now()
    completed_candidates = candidates or []
    completed_run = run.model_copy(
        update={
            "status": status,
            "finished_at": timestamp,
            "parse_error": parse_error,
            "transport_error": transport_error,
            "error_kind": error_kind,
            "candidate_count": len(completed_candidates),
        }
    )
    completed_task = task.model_copy(
        update={
            "status": "completed" if status == "succeeded" else status,
            "latest_run_id": completed_run.id,
            "updated_at": timestamp,
        }
    )
    storage.save_shadow_run_result(
        completed_task.model_dump(mode="json"),
        completed_run.model_dump(mode="json"),
        [candidate.model_dump(mode="json") for candidate in completed_candidates],
    )
    return completed_task, completed_run, completed_candidates


def submit_shadow_result(
    task_id: str,
    *,
    raw_response: str | None = None,
    transport_error: str | None = None,
    response_transform: Callable[[Any, ShadowTask], Any] | None = None,
) -> tuple[ShadowTask, ShadowRun, list[ShadowCandidate]]:
    """Record one model attempt; failures remain retryable and isolated."""
    if bool(raw_response and raw_response.strip()) == bool(
        transport_error and transport_error.strip()
    ):
        raise ShadowResultValidationError(
            "provide exactly one of raw_response or transport_error"
        )
    task = _task_from_store(task_id)
    if task.status == "cancelled":
        raise ShadowTaskConflictError("cancelled shadow tasks cannot be run")

    timestamp = storage.now()
    running_task = task.model_copy(
        update={
            "status": "running",
            "attempt_count": task.attempt_count + 1,
            "updated_at": timestamp,
        }
    )
    run = ShadowRun(
        id=f"shadow_run_{uuid.uuid4().hex}",
        task_id=task.id,
        attempt=running_task.attempt_count,
        model_id=task.model_id,
        prompt_version=task.prompt_version,
        schema_version=task.schema_version,
        started_at=timestamp,
        raw_response_summary=_response_summary(raw_response),
    )
    storage.save_shadow_task(running_task.model_dump(mode="json"))

    if transport_error and transport_error.strip():
        return _finish_run(
            running_task,
            run,
            status="failed",
            transport_error=transport_error.strip()[:2000],
            error_kind=_transport_error_kind(transport_error),
        )

    assert raw_response is not None
    try:
        # Reuse the shared parser so fenced, prefixed, and safely truncated
        # model JSON gets one consistent treatment across the workbench.
        parsed = parse_json(_strip_json_fence(raw_response))
        if response_transform is not None:
            parsed = response_transform(parsed, running_task)
        response = ShadowResponse.model_validate(parsed)
        candidates: list[ShadowCandidate] = []
        for index, draft in enumerate(response.candidates, start=1):
            candidate_data = draft.model_dump(mode="json")
            candidate_data["source_refs"] = _validate_candidate_sources(draft, task)
            candidates.append(
                ShadowCandidate(
                    id=f"shadow_candidate_{run.id.removeprefix('shadow_run_')}_{index}",
                    task_id=task.id,
                    run_id=run.id,
                    queue_visibility=task.queue_visibility,
                    candidate_role=(
                        "segment_result"
                        if task.task_kind == "semantic_consolidation"
                        else "window_observation"
                        if task.task_kind == "prep_window"
                        else "standalone"
                    ),
                    semantic_segment_id=task.semantic_segment_id,
                    **candidate_data,
                    created_at=storage.now(),
                )
            )
    except (
        json.JSONDecodeError,
        TypeError,
        ValueError,
        ValidationError,
        ShadowResultValidationError,
    ) as error:
        return _finish_run(
            running_task,
            run,
            status="failed",
            parse_error=str(error)[:2000],
            error_kind="model_format",
        )

    return _finish_run(running_task, run, status="succeeded", candidates=candidates)
