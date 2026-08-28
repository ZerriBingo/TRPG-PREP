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
    ShadowResponse,
    ShadowRun,
    ShadowTask,
    ShadowTaskSpec,
)
from ..domain.shadow import ShadowReviewEvent
from . import storage


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


def list_shadow_tasks() -> list[ShadowTask]:
    return [ShadowTask.model_validate(item) for item in storage.list_shadow_tasks()]


def list_shadow_runs(task_id: str) -> list[ShadowRun]:
    _task_from_store(task_id)
    return [ShadowRun.model_validate(item) for item in storage.list_shadow_runs(task_id)]


def list_shadow_candidates(
    task_id: str | None = None, review_state: str | None = "needs_review"
) -> list[ShadowCandidate]:
    if task_id is not None:
        _task_from_store(task_id)
    if review_state not in {None, "needs_review", "accepted", "rejected"}:
        raise ShadowResultValidationError(f"invalid shadow review state: {review_state}")
    return [
        ShadowCandidate.model_validate(item)
        for item in storage.list_shadow_candidates(task_id, review_state)
    ]


def shadow_task_detail(task_id: str) -> dict:
    task = _task_from_store(task_id)
    return {
        "task": task,
        "runs": list_shadow_runs(task_id),
        "candidates": list_shadow_candidates(task_id, review_state=None),
    }


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
    reviewed_text: str | None,
    review_note: str | None,
    update_reviewed_text: bool,
    update_review_note: bool,
    timestamp: str,
) -> ShadowCandidate:
    if review_state not in {"needs_review", "accepted", "rejected"}:
        raise ShadowResultValidationError(f"invalid shadow review state: {review_state}")
    if update_reviewed_text and reviewed_text is not None:
        reviewed_text = reviewed_text.strip() or None
    if update_review_note and review_note is not None:
        review_note = review_note.strip() or None
    final_text = reviewed_text if update_reviewed_text else candidate.reviewed_text
    final_note = review_note if update_review_note else candidate.review_note
    event = ShadowReviewEvent(
        id=f"shadow_review_{uuid.uuid4().hex}",
        review_state=review_state,
        reviewed_text=final_text,
        review_note=final_note,
        created_at=timestamp,
    )
    candidate_data = candidate.model_dump(mode="json")
    candidate_data.update(
        {
            "review_state": review_state,
            "reviewed_text": final_text,
            "review_note": final_note,
            "reviewed_at": timestamp,
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
    reviewed_text: str | None = None,
    review_note: str | None = None,
    update_reviewed_text: bool = False,
    update_review_note: bool = False,
) -> ShadowCandidate:
    """Append one GM review action while retaining the original model candidate."""
    candidate = _candidate_from_store(candidate_id)
    updated = _review_candidate(
        candidate,
        review_state=review_state,
        reviewed_text=reviewed_text,
        review_note=review_note,
        update_reviewed_text=update_reviewed_text,
        update_review_note=update_review_note,
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
            reviewed_text=None,
            review_note=review_note,
            update_reviewed_text=False,
            update_review_note=update_review_note,
            timestamp=timestamp,
        )
        for candidate in candidates
    ]
    storage.save_shadow_candidates(
        [candidate.model_dump(mode="json") for candidate in updated]
    )
    return updated


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
) -> tuple[ShadowTask, ShadowRun, list[ShadowCandidate]]:
    timestamp = storage.now()
    completed_candidates = candidates or []
    completed_run = run.model_copy(
        update={
            "status": status,
            "finished_at": timestamp,
            "parse_error": parse_error,
            "transport_error": transport_error,
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
        )

    assert raw_response is not None
    try:
        parsed = json.loads(_strip_json_fence(raw_response))
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
        )

    return _finish_run(running_task, run, status="succeeded", candidates=candidates)
