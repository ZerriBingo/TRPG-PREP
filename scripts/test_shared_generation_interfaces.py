"""The shared fact-input and durable-step interfaces, exercised directly.

Both the artifact and structure stages depend on these; the checks here protect the
properties their callers rely on rather than mirroring the implementation: the same
facts are projected and batched without loss, a successful step is never requested
twice, a model or input change is a different step, deterministic correction is
bounded and recorded, and an unresponsive client stops instead of looping.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import storage  # noqa: E402
from backend.app.fact_input import (  # noqa: E402
    MAX_FACT_BATCH_CHARS,
    MAX_FACTS_PER_BATCH,
    FactInputError,
    estimate_tokens,
    fact_batches,
    fact_payload,
)
from backend.app.persistent_step import run_json_step, stable_hash, step_id  # noqa: E402
from backend.domain import SourceFact, SourceRef  # noqa: E402


def fact(fact_id: str, text: str, *, kind: str = "event", status: str = "source_fact") -> SourceFact:
    return SourceFact(
        id=fact_id,
        source_refs=[SourceRef(file="fixture://module", page=1)],
        evidence_status=status,
        text=text,
        kind=kind,
        visibility="explicit",
    )


class CountingClient:
    """A client that answers from a script and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_json(self, messages, **kwargs):
        self.calls += 1
        response = self.responses.pop(0) if self.responses else None
        if isinstance(response, Exception):
            raise response
        return response


def validator(raw):
    if not isinstance(raw, dict) or "ok" not in raw:
        raise ValueError("需要包含 ok 字段")
    return raw


def check_fact_input() -> None:
    facts = [fact(f"fact_f{index}", f"事实 {index}") for index in range(5)]
    draft = fact("fact_draft", "模型草案", status="model_candidate")
    projected = fact_payload([*facts, draft])
    assert [item["id"] for item in projected] == [f"fact_f{index}" for index in range(5)], (
        "projection must keep source order and drop model candidates"
    )
    assert set(projected[0]) == {
        "id", "text", "kind", "visibility", "evidence_status", "source_refs", "tags", "notes"
    }, projected[0]
    assert projected[0]["source_refs"][0]["page"] == 1

    # Callers filter first (projection drops candidates); batching itself must keep
    # every fact it is given, in order.
    batches = fact_batches(facts)
    assert [item.id for batch in batches for item in batch] == [f"fact_f{index}" for index in range(5)], (
        "batching must not drop or reorder facts"
    )
    assert all(len(batch) <= MAX_FACTS_PER_BATCH for batch in batches)

    # A response-limited caller can request smaller transport batches without
    # changing the default batching contract used by structure preparation.
    small_batches = fact_batches(
        [fact(f"fact_small_{index}", f"short fact {index}") for index in range(35)],
        max_facts_per_batch=16,
        max_batch_tokens=3200,
    )
    assert [len(batch) for batch in small_batches] == [16, 16, 3]
    assert [item.id for batch in small_batches for item in batch] == [
        f"fact_small_{index}" for index in range(35)
    ]
    assert all(len(batch) <= 16 for batch in small_batches)

    size_limited = fact_batches(
        [fact(f"fact_size_{index}", "x" * 360) for index in range(10)],
        max_facts_per_batch=16,
        max_batch_tokens=1000,
    )
    assert len(size_limited) > 1
    assert [item.id for batch in size_limited for item in batch] == [
        f"fact_size_{index}" for index in range(10)
    ]
    assert all(
        len(batch) == 1
        or estimate_tokens(fact_payload(batch)) <= 1000
        for batch in size_limited
    )

    # A single fact larger than one whole request keeps the caller's error type.
    huge = fact("fact_huge", "x" * (MAX_FACT_BATCH_CHARS + 10))
    try:
        fact_batches([huge])
    except FactInputError as error:
        assert "单条输入" in str(error), error
    else:
        raise AssertionError("an oversized single fact must be rejected")

    class CallerError(RuntimeError):
        pass

    try:
        fact_batches([huge], error_factory=CallerError)
    except CallerError:
        pass
    else:
        raise AssertionError("the caller's error type must be used")


def check_step_identity_is_pinned() -> None:
    """The step hash and id are persisted; existing successful steps must still hit."""
    payload = {
        "version": "v1",
        "facts": ["fact_a"],
        "_model_id": "model-a",
        "_fake_model": False,
    }
    digest = stable_hash(payload)
    assert digest == "sha256:aa15d6ee27392176d0d583ebb4d74eb6aceac19609e58b6d4bc2842410b4589d", digest
    assert step_id("artifact_job_abc", "global_plan", 1, digest) == (
        "artifact_step_abc_global_plan_1_aa15d6ee27392176"
    ), step_id("artifact_job_abc", "global_plan", 1, digest)


def check_step_reuse_and_isolation() -> None:
    job = SimpleNamespace(id="artifact_job_shared_1", model_id="model-a", fake_model=False)
    client = CountingClient({"ok": 1}, {"ok": 1}, {"ok": 1})
    first = run_json_step(
        job,
        stage="shared_stage",
        step_index=1,
        input_payload={"version": "v1", "facts": ["f1"]},
        input_refs={"fact_ids": ["f1"]},
        messages=[{"role": "user", "content": "hi"}],
        client=client,
        max_tokens=100,
        validator=validator,
    )
    assert first == {"ok": 1} and client.calls == 1

    # A succeeded step is reused without calling the model again.
    again = run_json_step(
        job,
        stage="shared_stage",
        step_index=1,
        input_payload={"version": "v1", "facts": ["f1"]},
        input_refs={"fact_ids": ["f1"]},
        messages=[{"role": "user", "content": "hi"}],
        client=client,
        max_tokens=100,
        validator=validator,
    )
    assert again == {"ok": 1} and client.calls == 1, "a successful step must not be requested twice"

    # A different model is a different attempt, even with identical inputs.
    other_model = SimpleNamespace(id="artifact_job_shared_1", model_id="model-b", fake_model=False)
    run_json_step(
        other_model,
        stage="shared_stage",
        step_index=1,
        input_payload={"version": "v1", "facts": ["f1"]},
        input_refs={"fact_ids": ["f1"]},
        messages=[{"role": "user", "content": "hi"}],
        client=client,
        max_tokens=100,
        validator=validator,
    )
    assert client.calls == 2, "a model switch must not reuse the other model's response"

    # A different input is a different step too.
    run_json_step(
        job,
        stage="shared_stage",
        step_index=1,
        input_payload={"version": "v1", "facts": ["f2"]},
        input_refs={"fact_ids": ["f2"]},
        messages=[{"role": "user", "content": "hi"}],
        client=client,
        max_tokens=100,
        validator=validator,
    )
    assert client.calls == 3

    # The request messages are never mutated in place by the correction loop.
    messages = [{"role": "user", "content": "original"}]
    client_correction = CountingClient({"bad": True}, {"ok": 2})
    run_json_step(
        SimpleNamespace(id="artifact_job_shared_2", model_id="m", fake_model=False),
        stage="shared_stage",
        step_index=1,
        input_payload={"version": "v1"},
        input_refs={},
        messages=messages,
        client=client_correction,
        max_tokens=100,
        validator=validator,
    )
    assert messages == [{"role": "user", "content": "original"}], "caller messages must stay unchanged"


def check_correction_and_failures() -> None:
    storage_record = "artifact_job_shared_3"

    # A deterministic validation failure gets exactly one corrected request.
    client = CountingClient({"bad": True}, {"ok": 3})
    output = run_json_step(
        SimpleNamespace(id=storage_record, model_id="m", fake_model=False),
        stage="shared_correction",
        step_index=1,
        input_payload={"version": "v1"},
        input_refs={},
        messages=[{"role": "user", "content": "hi"}],
        client=client,
        max_tokens=100,
        validator=validator,
    )
    assert output == {"ok": 3} and client.calls == 2
    step = storage.load_artifact_job_step(
        storage_record, "shared_correction", 1,
        stable_hash({"version": "v1", "_model_id": "m", "_fake_model": False}),
    )
    assert [item["status"] for item in step["attempts"]] == ["failed", "succeeded"], step
    assert step["attempts"][0]["error"], step["attempts"][0]
    assert step["status"] == "succeeded"

    # An unresponsive client stops immediately: one attempt, no correction loop.
    silent = CountingClient(RuntimeError("上游无响应"))
    try:
        run_json_step(
            SimpleNamespace(id="artifact_job_shared_4", model_id="m", fake_model=False),
            stage="shared_silent",
            step_index=1,
            input_payload={"version": "v1"},
            input_refs={},
            messages=[{"role": "user", "content": "hi"}],
            client=silent,
            max_tokens=100,
            validator=validator,
        )
    except RuntimeError as error:
        assert "上游无响应" in str(error)
    else:
        raise AssertionError("an unresponsive client must fail the step")
    assert silent.calls == 1, "a transport failure must not be retried at this layer"

    # Two failed validations raise, with both attempts kept.
    always_bad = CountingClient({"bad": True}, {"bad": True})
    try:
        run_json_step(
            SimpleNamespace(id="artifact_job_shared_5", model_id="m", fake_model=False),
            stage="shared_exhausted",
            step_index=1,
            input_payload={"version": "v1"},
            input_refs={},
            messages=[{"role": "user", "content": "hi"}],
            client=always_bad,
            max_tokens=100,
            validator=validator,
        )
    except ValueError as error:
        assert "ok 字段" in str(error), error
    else:
        raise AssertionError("a repeated validation failure must raise")
    assert always_bad.calls == 2, "correction must be bounded to one retry"

    # The caller's recovery hook repairs once; a fatal error type skips correction.
    recovered_client = CountingClient({"bad": True})
    recovered = run_json_step(
        SimpleNamespace(id="artifact_job_shared_6", model_id="m", fake_model=False),
        stage="shared_recover",
        step_index=1,
        input_payload={"version": "v1"},
        input_refs={},
        messages=[{"role": "user", "content": "hi"}],
        client=recovered_client,
        max_tokens=100,
        validator=validator,
        recover_validation=lambda raw, error: {"ok": "repaired"},
    )
    assert recovered == {"ok": "repaired"} and recovered_client.calls == 1

    fatal_client = CountingClient({"bad": True})
    try:
        run_json_step(
            SimpleNamespace(id="artifact_job_shared_7", model_id="m", fake_model=False),
            stage="shared_fatal",
            step_index=1,
            input_payload={"version": "v1"},
            input_refs={},
            messages=[{"role": "user", "content": "hi"}],
            client=fatal_client,
            max_tokens=100,
            validator=validator,
            fatal_error_types=(ValueError,),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("a fatal error type must end the step")
    assert fatal_client.calls == 1, "a fatal validation error must not take the correction retry"

    # A caller-provided saved response is reused instead of calling the model; an
    # unusable one falls back to a fresh request.
    resumable = CountingClient(RuntimeError("saved response should avoid this request"))
    seen = []

    def resume(raw):
        seen.append(raw)
        return {"ok": 9}

    resumed = run_json_step(
        SimpleNamespace(id="artifact_job_shared_5", model_id="m", fake_model=False),
        stage="shared_exhausted",
        step_index=1,
        input_payload={"version": "v1"},
        input_refs={},
        messages=[{"role": "user", "content": "hi"}],
        client=resumable,
        max_tokens=100,
        validator=validator,
        resume_saved_response=resume,
    )
    assert resumed == {"ok": 9} and resumable.calls == 0
    assert seen == [{"bad": True}], "the hook must see the persisted failed response"

    def unusable(raw):
        raise ValueError("saved response cannot be used")

    fresh = CountingClient({"ok": 10})
    assert run_json_step(
        SimpleNamespace(id="artifact_job_shared_7", model_id="m", fake_model=False),
        stage="shared_fatal",
        step_index=1,
        input_payload={"version": "v1"},
        input_refs={},
        messages=[{"role": "user", "content": "hi"}],
        client=fresh,
        max_tokens=100,
        validator=validator,
        resume_saved_response=unusable,
    ) == {"ok": 10}
    assert fresh.calls == 1


def main() -> None:
    original_db_path = storage.DB_PATH
    original_upload_dir = storage.UPLOAD_DIR
    with tempfile.TemporaryDirectory(prefix="shared-generation-test-") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "shared-interfaces.db"
        storage.UPLOAD_DIR = Path(temp_dir) / "uploads"
        try:
            storage.init_db()
            check_step_identity_is_pinned()
            check_fact_input()
            check_step_reuse_and_isolation()
            check_correction_and_failures()
        finally:
            storage.DB_PATH = original_db_path
            storage.UPLOAD_DIR = original_upload_dir
    print("PASS: the shared fact-input and durable-step interfaces keep their callers' contracts")


if __name__ == "__main__":
    main()
