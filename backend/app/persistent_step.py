"""Run or resume one validated model step while preserving every attempt.

Both the artifact and structure stages need the same durable step: a stable input
hash, a stored attempt record, a bounded deterministic correction, and reuse of an
earlier successful response. This module owns that mechanism. Stage-specific
recovery — reusing a saved plan or completing a plan's coverage — is supplied by the
caller, so no stage name or artifact exception type lives here.

The step id prefix and the hash layout are persisted contracts: existing successful
steps must keep matching, so they stay exactly as they were.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from . import storage

# Persisted step-id namespace. It predates this module and changing it would orphan
# every stored step, so it is kept verbatim for both callers.
STEP_ID_PREFIX = "artifact_step"
DEFAULT_REQUEST_TIMEOUT = 300


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def step_id(job_id: str, stage: str, step_index: int, input_hash: str) -> str:
    digest = input_hash.removeprefix("sha256:")[:16]
    return f"{STEP_ID_PREFIX}_{job_id.removeprefix('artifact_job_')}_{stage}_{step_index}_{digest}"


def run_json_step(
    job: Any,
    *,
    stage: str,
    step_index: int,
    input_payload: dict[str, Any],
    input_refs: dict[str, Any],
    messages: list[dict[str, str]],
    client: Any,
    max_tokens: int,
    validator: Callable[[Any], dict[str, Any]],
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
    resume_saved_response: Callable[[Any], Any] | None = None,
    recover_validation: Callable[[Any, Exception], Any] | None = None,
    fatal_error_types: tuple[type, ...] = (),
    error_factory: Callable[[str], Exception] = RuntimeError,
) -> dict[str, Any]:
    """Run or resume one validated step.

    `job` only needs `id`, `model_id` and `fake_model`: a model switch is a new
    generation attempt even when the inputs are identical, so the effective client
    identity participates in the step hash.

    Caller seams:
    - `resume_saved_response(raw)`: return a stored response to reuse instead of
      calling the client (only consulted when earlier attempts exist).
    - `recover_validation(raw, error)`: repair a deterministic validation failure and
      return the repaired payload; re-raise errors the caller does not repair.
    - `fatal_error_types`: errors that end the step instead of taking the bounded
      correction retry.
    """
    step_input = {
        **input_payload,
        "_model_id": job.model_id,
        "_fake_model": job.fake_model,
    }
    input_hash = stable_hash(step_input)
    existing = storage.load_artifact_job_step(job.id, stage, step_index, input_hash)
    if existing and existing.get("status") == "succeeded" and existing.get("output"):
        return existing["output"]

    timestamp = storage.now()
    step = existing or {
        "id": step_id(job.id, stage, step_index, input_hash),
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
    resume_plan = None
    if resume_saved_response is not None and attempts and attempts[-1].get("response"):
        try:
            resume_plan = resume_saved_response(attempts[-1]["response"])
        except Exception:  # noqa: BLE001 - an unusable saved response just means a new call
            resume_plan = None
    working_messages = [dict(message) for message in messages]
    step.update(status="running", updated_at=timestamp, error=None)
    storage.save_artifact_job_step(step)

    last_error: Exception | None = None
    for correction_attempt in range(2):
        raw: Any = None
        attempt_number = len(attempts) + 1
        started_at = storage.now()
        try:
            raw = resume_plan if resume_plan is not None else client.chat_json(
                working_messages,
                temperature=0.15,
                max_tokens=max_tokens,
                request_timeout=request_timeout,
            )
            resume_plan = None
            try:
                output = validator(raw)
            except Exception as validation_error:  # noqa: BLE001
                if recover_validation is None:
                    raise
                raw = recover_validation(raw, validation_error)
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
            if (
                raw is None
                or correction_attempt == 1
                or isinstance(error, fatal_error_types)
            ):
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

    raise last_error or error_factory(f"artifact step failed: {stage}/{step_index}")
