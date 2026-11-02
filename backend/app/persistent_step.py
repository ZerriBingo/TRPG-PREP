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
from .llm import RequestBudget

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


def _attempt_diagnostics(
    client: Any, budget: RequestBudget | None = None, *, own_request: bool = True
) -> dict[str, Any] | None:
    """Describe one attempt: its own requests plus the step's shared counters.

    The client stores only non-sensitive facts — effective budget and effort,
    finish_reason, content length, reported usage, status and duration.  The step-level
    counters say how much of the shared cap the step has used and which durable steps
    spent it, so several calls inside one step stay distinguishable.  Nothing here
    touches prompts, keys or URLs, and none of it participates in the step hash.

    `own_request=False` is for an attempt that issued no request of its own (a recovery
    or a saved response).  The client keeps only the *last* request it made, and parent
    and child steps share the client, so reading it there would copy a child window's
    request into the parent's list.  Such an attempt reports `requests: []` and keeps
    only the shared counters.
    """
    requests = getattr(client, "last_request_diagnostics", None) if own_request else []
    if not isinstance(requests, list):
        requests = []
    if not requests and (budget is None or not budget.requests):
        return None
    recoveries = getattr(client, "last_recovery_actions", None)
    diagnostics = {
        "requests": [dict(entry) for entry in requests if isinstance(entry, dict)],
        "recoveries": list(recoveries) if isinstance(recoveries, list) else [],
    }
    if budget is not None:
        diagnostics.update(budget.diagnostics())
    return diagnostics


def _recorded_own_budget(
    step_record: dict[str, Any] | None, max_tokens: int, ceiling: int | None
) -> int:
    """The largest budget *this* step already sent, so a retry does not shrink it.

    Only this step's own request diagnostics count: the record is looked up by the same
    input hash, so a changed prompt version, model or input finds no record and starts
    from the ordinary budget.  A child window's requests are recorded on the child, so
    they can never raise their parent's starting budget, and the value never exceeds the
    ceiling the stage allows.
    """
    floor = int(max_tokens)
    if not step_record:
        return floor
    for attempt in step_record.get("attempts") or []:
        for request in (attempt.get("diagnostics") or {}).get("requests") or []:
            value = request.get("max_tokens")
            if isinstance(value, int) and value > floor:
                floor = value
    if ceiling is not None:
        floor = min(floor, int(ceiling))
    return floor


def _stored_length_attempt(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest attempt the provider stopped for length, if one was recorded.

    A recovery failure is appended as its own attempt, and it does not carry the length
    marker.  Only looking at the last attempt therefore hid the original budget stop and
    made the next explicit retry pay for the same known-failing request again.  Scanning
    back finds the reason recovery exists without rewriting history or weakening the
    splittability check the caller applies to it.
    """
    for attempt in reversed(attempts):
        if "finish_reason=length" in str(attempt.get("error") or ""):
            return attempt
    return None


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
    recover_error: Callable[[Any, Exception], Any] | None = None,
    recover_existing_failure: Callable[[Any, Exception], bool] | None = None,
    startup_recovery: Callable[[], Any] | None = None,
    fatal_error_types: tuple[type, ...] = (),
    error_factory: Callable[[str], Exception] = RuntimeError,
    chat_kwargs: dict[str, Any] | None = None,
    allow_correction: bool = True,
    step_budget: RequestBudget | None = None,
    budget_identity: str | None = None,
    max_budget: int | None = None,
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
    - `recover_error(raw, error)`: recover a failed model request at the caller's
      seam (for example by splitting a transport window). The returned value is
      validated and persisted as this step's successful output, so a later retry
      reuses the recovered parent and its durable child steps.
    - `recover_existing_failure(raw, error)`: decide whether a stored length failure
      may be recovered without issuing the request again. Return False when the
      request can no longer be reduced (a leaf window): the step then asks the
      provider again instead of replaying the same stored failure forever.
    - `startup_recovery()`: supply a response already assembled from durable child
      steps (for example a prompt-version upgrade reusing an exact prior failure). It
      runs inside this step's attempt, so its start, failure and success are recorded
      here, and it goes through the normal validator and cache without issuing a
      redundant full-scope request.
    - `fatal_error_types`: errors that end the step instead of taking the bounded
      correction retry.
    - `allow_correction`: when False, a reply that parsed but failed deterministic
      validation ends the step instead of resending the whole request.  An entry that
      already refused to send its own request because the input exceeds the boundary
      must not turn its correction into exactly that oversize request.  Defaults to
      True, so ordinary replies keep their one correction.
    - `chat_kwargs`: extra client options forwarded to `chat_json` (for example the
      one-time budget raise a stage allows). They change how a request is sent, never
      what it asks, so they stay out of the step hash and out of cache identity.
    - `step_budget`: share the parent's budget when this is a nested recovery step.
      Separate persistence does not grant recovery another set of requests.
    - `budget_identity`: the stable prompt/window identity a raised budget belongs to.
      Sibling windows and nested leaves keep their own identity, so one window's raise
      never changes what another window sends first.
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
    # One budget for the whole generation step: the deterministic correction below can
    # call the model again, and it must share the request cap and the single budget
    # raise instead of starting over.
    step_budget = step_budget if step_budget is not None else RequestBudget()
    # An explicit retry starts from the larger budget this very step already spent,
    # instead of paying for the smaller request that already failed.
    max_tokens = _recorded_own_budget(existing, max_tokens, max_budget)
    # A previous run already proved that this exact request ends in the same provider
    # length failure.  Re-entering the caller's recovery from durable child steps saves
    # that known-failing request, but only while the request can still be reduced: a
    # leaf that cannot shrink must be asked again, or an explicit retry would replay
    # the stored failure without ever contacting the provider.
    startup: Callable[[], Any] | None = None
    if resume_plan is None:
        if (
            startup_recovery is not None
        ):
            startup = startup_recovery
        elif recover_error is not None and existing is not None and existing.get("status") == "failed":
            stored = _stored_length_attempt(attempts)
            if stored is not None:
                stored_response = stored.get("response")
                stored_error = RuntimeError(str(stored.get("error") or ""))
                if (
                    recover_existing_failure is None
                    or recover_existing_failure(stored_response, stored_error)
                ):
                    def startup() -> Any:  # noqa: B023 - bound once for this step
                        return recover_error(stored_response, stored_error)
    working_messages = [dict(message) for message in messages]
    step.update(status="running", updated_at=timestamp, error=None)
    storage.save_artifact_job_step(step)

    last_error: Exception | None = None
    for correction_attempt in range(2):
        raw: Any = None
        attempt_number = len(attempts) + 1
        started_at = storage.now()
        using_startup = startup is not None and resume_plan is None
        requested = resume_plan is None and not using_startup
        recovery_source = "saved_response" if resume_plan is not None else (
            "recovery" if using_startup else "model"
        )
        diagnostics: dict[str, Any] | None = None
        requests_before = step_budget.requests
        try:
            if resume_plan is not None:
                raw = resume_plan
            elif using_startup:
                # Recovery runs inside this step's attempt: a failure here is recorded
                # on this step rather than escaping before it was created.
                raw = startup()
                startup = None
            else:
                raw = client.chat_json(
                    working_messages,
                    step_budget=step_budget,
                    budget_identity=budget_identity,
                    temperature=0.15,
                    max_tokens=max_tokens,
                    request_timeout=request_timeout,
                    **(chat_kwargs or {}),
                )
            resume_plan = None
            diagnostics = _attempt_diagnostics(client, step_budget) if requested else None
            try:
                output = validator(raw)
            except Exception as validation_error:  # noqa: BLE001
                if recover_validation is None:
                    raise
                raw = recover_validation(raw, validation_error)
                output = validator(raw)
            finally:
                # A recovery may run a separately persisted child. Keep this attempt's
                # own request summaries, but report the full shared recovery count.
                if diagnostics is not None:
                    diagnostics.update(step_budget.diagnostics())
                elif step_budget.requests:
                    diagnostics = {
                        "requests": [], "recoveries": [], **step_budget.diagnostics(),
                    }
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
                    "request_source": recovery_source,
                    # A failed recovery still records the shared step counters and the
                    # steps that spent them; its own request list stays empty rather
                    # than borrowing a child window's requests.
                    "diagnostics": diagnostics or _attempt_diagnostics(
                        client, step_budget, own_request=requested
                    ),
                }
            )
            if step_budget.requests > requests_before and step["id"] not in step_budget.steps:
                step_budget.steps.append(step["id"])
            step.update(
                status="failed",
                attempts=attempts,
                error=str(error)[:2000],
                updated_at=storage.now(),
            )
            storage.save_artifact_job_step(step)
            # Only a failed model request goes back into the caller's transport
            # recovery.  A failed recovery ends this attempt: re-entering it here would
            # repeat the same split or replayed leaf inside one run.
            # Only a request that produced no parsed result goes to the caller's
            # transport recovery.  A reply that parsed and then failed deterministic
            # validation belongs to the bounded correction below: sending it to the
            # window splitter would end the step before that correction ever ran, and a
            # splitter that only accepts length failures would swallow the real error.
            if (
                recover_error is not None
                and recovery_source == "model"
                and raw is None
                and not isinstance(error, fatal_error_types)
            ):
                try:
                    recovered_raw = recover_error(raw, error)
                    try:
                        recovered_output = validator(recovered_raw)
                    except Exception as recovered_validation_error:  # noqa: BLE001
                        # A transport recovery can return a structurally valid
                        # response whose merged coverage still needs the same
                        # deterministic repair as an ordinary model response.
                        if recover_validation is None:
                            raise
                        recovered_raw = recover_validation(
                            recovered_raw, recovered_validation_error
                        )
                        recovered_output = validator(recovered_raw)
                except Exception as recovery_error:  # noqa: BLE001
                    last_error = recovery_error
                    # The recovery's start, failure and success all belong to this
                    # step: append its own failed attempt (no request of its own, but
                    # the shared counters, the window steps that did send requests and
                    # the split actions), and leave the model attempt's history intact.
                    if step_budget.requests > requests_before and step["id"] not in step_budget.steps:
                        step_budget.steps.append(step["id"])
                    attempts.append(
                        {
                            "attempt": len(attempts) + 1,
                            "status": "failed",
                            "model_id": job.model_id,
                            "fake_model": job.fake_model,
                            "started_at": started_at,
                            "finished_at": storage.now(),
                            "response": None,
                            "error": str(recovery_error)[:2000],
                            "request_source": "recovery",
                            "diagnostics": _attempt_diagnostics(
                                client, step_budget, own_request=False
                            ),
                        }
                    )
                    step.update(
                        status="failed",
                        attempts=attempts,
                        error=str(recovery_error)[:2000],
                        updated_at=storage.now(),
                    )
                    storage.save_artifact_job_step(step)
                    raise
                attempts.append(
                    {
                        "attempt": len(attempts) + 1,
                        "status": "succeeded",
                        "model_id": job.model_id,
                        "fake_model": job.fake_model,
                        "started_at": started_at,
                        "finished_at": storage.now(),
                        "response": recovered_raw,
                        "error": None,
                        "request_source": "recovery",
                        # A recovery issues no request of its own; the client keeps only
                        # its last request and the child windows share it, so reading it
                        # here would copy a child window's request into this attempt.
                        "diagnostics": _attempt_diagnostics(
                            client, step_budget, own_request=False
                        ),
                    }
                )
                step.update(
                    status="succeeded",
                    attempts=attempts,
                    output=recovered_output,
                    error=None,
                    updated_at=storage.now(),
                )
                storage.save_artifact_job_step(step)
                return recovered_output
            if (
                raw is None
                or correction_attempt == 1
                or not allow_correction
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
                "request_source": recovery_source,
                "diagnostics": diagnostics or _attempt_diagnostics(
                    client, step_budget, own_request=requested
                ),
            }
        )
        if step_budget.requests > requests_before and step["id"] not in step_budget.steps:
            step_budget.steps.append(step["id"])
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
