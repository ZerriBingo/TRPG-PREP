"""A truncated global plan is recovered through durable planning windows."""
from __future__ import annotations

import json
import sys
import tempfile
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts, llm, storage  # noqa: E402
from backend.domain import (  # noqa: E402
    ExampleBundle,
    SourceFact,
    SourceRef,
    load_profiles,
)


class Client:
    def __init__(
        self,
        facts: list[SourceFact],
        *,
        fail_calls: set[int] | None = None,
        fail_first: bool = True,
    ) -> None:
        self.facts = facts
        self.calls = 0
        self.fail_calls = fail_calls or set()
        self.fail_first = fail_first

    def chat_json(self, messages, **kwargs):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError(
                "上游返回了空正文，并报告 finish_reason=length；"
                "本次请求实际发出的 max_tokens=12000"
            )
        if self.calls in self.fail_calls:
            raise RuntimeError("window transport failed")
        marker = next(
            line for line in messages[-1]["content"].splitlines()
            if line.startswith("GLOBAL_UNITS_JSON=")
        )
        units = json.loads(marker.split("=", 1)[1])
        fact_ids = [fact_id for unit in units for fact_id in unit.get("fact_ids", [])]
        return {
            "cards": [{
                "type": "scene_extract",
                "title": f"窗口 {self.calls}",
                "purpose": "保留当前窗口可调用的场景入口。",
                "fact_ids": list(dict.fromkeys(fact_ids)),
                "focus": ["现场"],
                "open_questions": [],
                "runtime_groups": [],
                "chapter_ids": [],
                "story_beat_ids": [],
                "situation": "",
                "case_scope": "current",
            }],
            "brief": None,
            "chapter_briefs": [],
            "global_reference_fact_ids": [],
            "open_questions": [],
        }



def _fixture(count: int = 4):
    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["module-prep"]
    facts = [
        SourceFact(
            id=f"fact_plan_split_{index}",
            text=f"Fact {index}",
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://global-plan-split", page=index + 1)],
        )
        for index in range(count)
    ]
    units = [
        {
            "id": f"unit_{index}",
            "kind": "scene",
            "title": f"场景 {index}",
            "summary": "一个可以独立调用的场景。",
            "fact_ids": [fact.id],
            "entity_keys": [],
            "relationship_hints": [],
            "open_questions": [],
        }
        for index, fact in enumerate(facts)
    ]
    sizes = {
        fact.id: len(json.dumps(artifacts._fact_payload_from_facts([fact]), ensure_ascii=False))
        for fact in facts
    }
    tokens = {
        fact.id: artifacts._estimate_tokens(
            json.dumps(artifacts._fact_payload_from_facts([fact]), ensure_ascii=False)
        )
        for fact in facts
    }
    return profile, facts, units, sizes, tokens


def _window_plan(messages, label: str) -> dict:
    units = _units_in(messages)
    fact_ids = [fact_id for unit in units for fact_id in unit.get("fact_ids", [])]
    return {
        "cards": [{
            "type": "scene_extract",
            "title": label,
            "purpose": "保留当前窗口可调用的场景入口。",
            "fact_ids": list(dict.fromkeys(fact_ids)),
            "focus": ["现场"],
            "open_questions": [],
            "runtime_groups": [],
            "chapter_ids": [],
            "story_beat_ids": [],
            "situation": "",
            "case_scope": "current",
        }],
        "brief": None,
        "chapter_briefs": [],
        "global_reference_fact_ids": [],
        "open_questions": [],
    }


def _units_in(messages) -> list:
    """Read the planning units the request carries, the way the prompt encodes them."""
    if not messages:
        return []
    # A correction request appends the rejected reply and the correction note, so the
    # planning units are not necessarily in the last message.
    for message in reversed(messages):
        marker = next(
            (
                line for line in str(message.get("content", "")).splitlines()
                if line.startswith("GLOBAL_UNITS_JSON=")
            ),
            None,
        )
        if marker:
            return json.loads(marker.split("=", 1)[1])
    return []


def _length_frame() -> dict:
    # What the live provider returned: no content string, finish_reason=length, usage
    # that reports the completion tokens it burned on reasoning.
    return {
        "content": None,
        "finish_reason": "length",
        "content_length": None,
        "usage": {"completion_tokens": 12000, "total_tokens": 15400},
    }


def _plan_frame(messages, label: str) -> dict:
    content = json.dumps(_window_plan(messages, label), ensure_ascii=False)
    return {
        "content": content,
        "finish_reason": "stop",
        "content_length": len(content),
        "usage": {"completion_tokens": 200, "total_tokens": 900},
    }


def _client(worker) -> llm.LLMClient:
    client = llm.LLMClient({
        "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
        "reasoning_effort": "",
    })
    client._worker_call = worker  # type: ignore[method-assign]
    return client


def _length_worker(fail_above: int | None = None):
    """Answer like the provider: multi-unit windows run out of budget, leaves succeed."""
    calls: list[int] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        units = _units_in(messages)
        calls.append(len(units))
        if fail_above is None or len(units) > fail_above:
            return _length_frame()
        return _plan_frame(messages, f"窗口 {len(calls)}")

    return calls, worker


def _transport_fail_worker():
    calls: list[int] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        calls.append(len(_units_in(messages)))
        raise RuntimeError("window transport failed")

    return calls, worker


def _scope_call(job, profile, facts, units, sizes, tokens, client):
    return artifacts._run_global_plan_scope(
        job,
        plan_index=1,
        profile=profile,
        bundle=None,
        plan_units=units,
        plan_facts=facts,
        plan_fact_sizes=sizes,
        plan_fact_tokens=tokens,
        plan_context=None,
        chapter_scope=None,
        plan_client=client,
    )


def check_upgrade_recovery_failure_is_recorded() -> None:
    """An old-version failure is not reused once the output contract changed."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_upgrade_failure", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-upgrade-failure-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        legacy_payload = {
            "prompt_version": artifacts.GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            "profile": profile.model_dump(mode="json"),
            "units": artifacts._compact_global_units(units),
            "fact_sizes": sizes,
            "fact_tokens": tokens,
            "structure": None,
        }
        _, legacy_worker = _length_worker()
        try:
            artifacts._run_json_step(
                job,
                stage="global_plan",
                step_index=1,
                input_payload=legacy_payload,
                input_refs={"unit_ids": [unit["id"] for unit in units]},
                messages=[],
                client=_client(legacy_worker),
                max_tokens=artifacts.GLOBAL_PLAN_MAX_TOKENS,
                validator=lambda raw: raw,
            )
        except Exception as error:  # noqa: BLE001
            assert "finish_reason=length" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("the legacy full-scope request must fail with length")

        # The output contract changed, so the old-version failure is NOT a reusable seed
        # for the current request: the scope asks for itself instead of replaying the old
        # failure's split path.
        calls, worker = _transport_fail_worker()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        except Exception as error:  # noqa: BLE001
            message = str(error)
            assert "window transport failed" in message, message
        else:  # pragma: no cover - defensive
            raise AssertionError("a failing request must fail the scope")
        assert calls == [4], calls
        steps = storage.list_artifact_job_steps(job.id)
        parents = [step for step in steps if step["stage"] == "global_plan"]
        assert len(parents) == 2, [(step["stage"], step["status"]) for step in steps]
        legacy = next(step for step in parents if len(step["attempts"]) == 1)
        current = next(step for step in parents if step is not legacy)
        assert legacy["attempts"][0]["status"] == "failed" and not legacy.get("output"), legacy
        assert current["attempts"][0]["request_source"] == "model", current["attempts"][0]
        assert not any(
            step["stage"].startswith("global_plan_window_") for step in steps
        ), "the old-version failure must not seed a split"


def check_leaf_failure_can_be_retried() -> None:
    """A leaf that cannot be split must be re-requested by the next explicit retry."""
    profile, facts, units, sizes, tokens = _fixture(1)

    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-leaf-retry-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        job = SimpleNamespace(
            id="artifact_job_global_plan_leaf_retry", model_id="probe", fake_model=False,
        )
        calls: list[int] = []
        replies = ["length", "length", "plan", "plan"]

        def worker(messages, temperature, max_tokens, **kwargs):
            calls.append(len(_units_in(messages)))
            return _length_frame() if replies.pop(0) == "length" else _plan_frame(
                messages, "叶子"
            )

        client = _client(worker)
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, client)
        except Exception as error:  # noqa: BLE001
            message = str(error)
            assert "已无法继续拆分" in message, message
        else:  # pragma: no cover - defensive
            raise AssertionError("a one-unit scope must fail after its one raise")
        assert len(calls) == 2, calls  # 12000, then the in-step 16000
        failed = storage.load_artifact_job_step(
            job.id, "global_plan", 1,
            artifacts._stable_hash({
                "prompt_version": artifacts.GLOBAL_PLAN_PROMPT_VERSION,
                "leaf_contract": "compact-v1",
                "profile": profile.model_dump(mode="json"),
                "units": artifacts._compact_global_units(units),
                "fact_sizes": sizes,
                "fact_tokens": tokens,
                "fact_digest": artifacts._gestalt_fact_digest(facts),
                "facts_in_prompt": True,
                "structure": None,
                "_model_id": job.model_id,
                "_fake_model": job.fake_model,
            }),
        )
        # The model attempt keeps its own length failure; the recovery failure that
        # followed is recorded separately instead of rewriting that history.
        assert any(
            "finish_reason=length" in str(attempt["error"]) for attempt in failed["attempts"]
        ), failed["attempts"]
        assert failed["attempts"][-1]["request_source"] == "recovery", failed["attempts"][-1]

        # The explicit retry must really ask the provider again instead of replaying the
        # stored failure; the improved request is the only change between the two runs.
        output = _scope_call(job, profile, facts, units, sizes, tokens, client)
        assert len(calls) == 3, "the retry must resume at the recorded 16000"
        assert len(output["cards"]) == 1, output

        # The recovered parent is durable: entering again needs no request at all.
        again = _scope_call(job, profile, facts, units, sizes, tokens, client)
        assert again == output and len(calls) == 3, calls

        # A retry that fails again stops after one request instead of looping.
        retry_job = SimpleNamespace(
            id="artifact_job_global_plan_leaf_retry_2", model_id="probe", fake_model=False,
        )
        failing_calls, failing_worker = _length_worker()
        failing = _client(failing_worker)
        for expected_calls in (2, 3):
            try:
                _scope_call(retry_job, profile, facts, units, sizes, tokens, failing)
            except Exception as error:  # noqa: BLE001
                assert "已无法继续拆分" in str(error), error
            else:  # pragma: no cover - defensive
                raise AssertionError("a repeated length failure must fail the step")
            assert len(failing_calls) == expected_calls, (failing_calls, expected_calls)

        # A one-unit scope with a prior-version failure must still ask the provider:
        # resuming recovery cannot help a window that has nothing to split.
        upgrade_job = SimpleNamespace(
            id="artifact_job_global_plan_leaf_upgrade", model_id="probe", fake_model=False,
        )
        legacy_payload = {
            "prompt_version": artifacts.GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            "profile": profile.model_dump(mode="json"),
            "units": artifacts._compact_global_units(units),
            "fact_sizes": sizes,
            "fact_tokens": tokens,
            "structure": None,
        }
        _, legacy_worker = _length_worker()
        try:
            artifacts._run_json_step(
                upgrade_job,
                stage="global_plan",
                step_index=1,
                input_payload=legacy_payload,
                input_refs={"unit_ids": [unit["id"] for unit in units]},
                messages=[],
                client=_client(legacy_worker),
                max_tokens=artifacts.GLOBAL_PLAN_MAX_TOKENS,
                validator=lambda raw: raw,
            )
        except Exception as error:  # noqa: BLE001
            assert "finish_reason=length" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("the legacy full-scope request must fail with length")
        leaf_calls, leaf_worker = _length_worker(fail_above=0)
        leaf_client = _client(leaf_worker)
        try:
            _scope_call(upgrade_job, profile, facts, units, sizes, tokens, leaf_client)
        except Exception:  # noqa: BLE001
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("a one-unit leaf must fail on a length reply")
        assert len(leaf_calls) == 2, (
            "a leaf must be re-requested (and may spend its one raise) instead of "
            "replaying the prior-version failure"
        )


def check_window_failures_stop_with_a_truthful_reason() -> None:
    """Continuous length replies stop early and say why, without claiming the cap."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_all_windows_fail", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-all-windows-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        calls, worker = _length_worker()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("continuous length replies must fail the step")

        # parent, first window, leaf, then the leaf's single raised retry.
        assert calls == [4, 2, 1, 1], calls
        assert "已无法继续拆分" in message, message
        # The leaf step is stored as global_plan_window_1_1_1; its readable path is 1.1
        # (plan index 1, then the split path), which is what the message must name.
        assert "全局规划窗口 1.1" in message, message
        assert "本步骤已发出 4 次请求" in message, message
        assert (
            f"已发出 {artifacts.GLOBAL_PLAN_MAX_REQUESTS} 次模型请求" not in message
        ), message
        steps = storage.list_artifact_job_steps(job.id)
        assert all(step["status"] == "failed" for step in steps), [
            (step["stage"], step["status"]) for step in steps
        ]
        assert not any(step.get("output") for step in steps), "no partial plan may be stored"


def check_request_cap_stops_the_split() -> None:
    """Splitting without progress stops at the shared cap and reports it truthfully."""
    profile, facts, units, sizes, tokens = _fixture(16)
    job = SimpleNamespace(
        id="artifact_job_global_plan_cap", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-cap-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        # Every multi-unit window runs out of budget; every leaf succeeds, so the
        # recursion keeps making progress until the shared cap ends it.
        calls, worker = _length_worker(fail_above=1)
        # The shared limit is now derived from the first-level entry count; pin it to the
        # old fixed value so this check keeps testing "stops exactly at the cap".
        with mock.patch.object(
            artifacts, "_global_plan_shared_request_limit",
            lambda first_level: artifacts.GLOBAL_PLAN_MAX_REQUESTS,
        ):
            try:
                _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
            except Exception as error:  # noqa: BLE001
                message = str(error)
            else:  # pragma: no cover - defensive
                raise AssertionError("an exhausted budget must fail the step")

        assert len(calls) == artifacts.GLOBAL_PLAN_MAX_REQUESTS, calls
        assert f"已发出 {artifacts.GLOBAL_PLAN_MAX_REQUESTS} 次模型请求" in message, message
        steps = storage.list_artifact_job_steps(job.id)
        parent = next(step for step in steps if step["stage"] == "global_plan")
        assert parent["status"] == "failed" and not parent.get("output"), (
            "an incomplete plan must not be stored as the scope's result"
        )
        # Successful leaf windows stay reusable, which is what the stop message promises.
        assert any(step.get("output") for step in steps), "finished windows must be kept"



def check_retry_after_failed_upgrade_recovery() -> None:
    """A retry resumes the durable windows instead of repeating the full-scope request."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_upgrade_retry", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-upgrade-retry-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        legacy_payload = {
            "prompt_version": artifacts.GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            "profile": profile.model_dump(mode="json"),
            "units": artifacts._compact_global_units(units),
            "fact_sizes": sizes,
            "fact_tokens": tokens,
            "structure": None,
        }
        _, legacy_worker = _length_worker()
        try:
            artifacts._run_json_step(
                job,
                stage="global_plan",
                step_index=1,
                input_payload=legacy_payload,
                input_refs={"unit_ids": [unit["id"] for unit in units]},
                messages=[],
                client=_client(legacy_worker),
                max_tokens=artifacts.GLOBAL_PLAN_MAX_TOKENS,
                validator=lambda raw: raw,
            )
        except Exception as error:  # noqa: BLE001
            assert "finish_reason=length" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("the legacy full-scope request must fail with length")

        first_calls: list[int] = []

        def failing_worker(messages, temperature, max_tokens, **kwargs):
            window = _units_in(messages)
            first_calls.append(len(window))
            if len(window) == 4:
                return _length_frame()
            if [unit["id"] for unit in window] == ["unit_0", "unit_1"]:
                return _plan_frame(messages, "前窗")
            raise RuntimeError("window transport failed")

        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(failing_worker))
        except Exception as error:  # noqa: BLE001
            assert "window transport failed" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("a failing upgrade recovery must fail the scope")
        # The output contract changed, so the old-version failure is not reused: the
        # scope sends its own request once and then splits naturally.
        assert first_calls == [4, 2, 2], first_calls

        retry_calls: list[int] = []
        retry_windows: list[list[str]] = []

        def retry_worker(messages, temperature, max_tokens, **kwargs):
            window = _units_in(messages)
            retry_calls.append(len(window))
            retry_windows.append([unit["id"] for unit in window])
            return _plan_frame(messages, f"窗口 {len(retry_calls)}")

        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(retry_worker))
        assert retry_calls == [2], "only the failed window may be requested again"
        assert retry_windows == [["unit_2", "unit_3"]], retry_windows
        assert len(output["cards"]) == 2, output
        steps = storage.list_artifact_job_steps(job.id)
        parent = next(
            step for step in steps
            if step["stage"] == "global_plan" and step["status"] == "succeeded"
        )
        assert parent["attempts"][-1]["request_source"] == "recovery", parent["attempts"][-1]


def check_stop_reason_distinguishes_empty_from_truncated() -> None:
    """An unsplittable leaf must say whether any text arrived, not assume either way."""
    profile, facts, units, sizes, tokens = _fixture(1)

    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-empty-leaf-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        _, empty_worker = _length_worker()
        job = SimpleNamespace(
            id="artifact_job_global_plan_empty_leaf", model_id="probe", fake_model=False,
        )
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(empty_worker))
        except Exception as error:  # noqa: BLE001
            empty_message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("an empty reply must fail the step")
        assert "返回空正文" in empty_message, empty_message
        assert "正文但在预算处被截断" not in empty_message, empty_message

    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-truncated-leaf-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        truncated = (
            '{"cards":[{"type":"scene_extract","title":"截断","purpose":"部分事实",'
            '"fact_ids":["fact_plan_split_0"]}],"open_questions":["已写","未写完'
        )

        def truncated_worker(messages, temperature, max_tokens, **kwargs):
            return {
                "content": truncated,
                "finish_reason": "length",
                "content_length": len(truncated),
                "usage": {"completion_tokens": 12000},
            }

        job = SimpleNamespace(
            id="artifact_job_global_plan_truncated_leaf", model_id="probe", fake_model=False,
        )
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(truncated_worker))
        except Exception as error:  # noqa: BLE001
            truncated_message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("a repaired truncated reply must fail the step")
        assert "正文但在预算处被截断" in truncated_message, truncated_message
        assert "返回空正文" not in truncated_message, truncated_message
        assert "未采用修补结果" in truncated_message, truncated_message



def check_retry_resumes_windows_without_a_version_upgrade() -> None:
    """A retry after a mid-window failure resumes windows even with no prior version."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_plain_retry", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-plain-retry-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        first_calls: list[int] = []

        def failing_worker(messages, temperature, max_tokens, **kwargs):
            window = _units_in(messages)
            first_calls.append(len(window))
            if len(window) == 4:
                return _length_frame()
            if [unit["id"] for unit in window] == ["unit_0", "unit_1"]:
                return _plan_frame(messages, "前窗")
            raise RuntimeError("window transport failed")

        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(failing_worker))
        except Exception as error:  # noqa: BLE001
            assert "window transport failed" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("a failing window must fail the scope")
        assert first_calls == [4, 2, 2], first_calls

        retry_calls: list[list[str]] = []

        def retry_worker(messages, temperature, max_tokens, **kwargs):
            window = _units_in(messages)
            retry_calls.append([unit["id"] for unit in window])
            return _plan_frame(messages, f"窗口 {len(retry_calls)}")

        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(retry_worker))
        assert retry_calls == [["unit_2", "unit_3"]], (
            "the retry must resume the windows instead of repeating the full scope"
        )
        assert len(output["cards"]) == 2, output



def check_recovery_attempt_does_not_borrow_a_child_request() -> None:
    """A parent recovery attempt reports the step's counters, never a child's request.

    Parent and child steps share one LLM client, and the client keeps only its last
    request.  The failed parent attempt therefore used to copy the leaf window's request
    summary into its own diagnostics; the review probe caught it once a child window
    actually reached the client before the recovery failed.
    """
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_child_request", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-child-request-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        # The upgrade path runs the recovery as this step's own attempt, which is where
        # the child request used to be copied from.
        legacy_payload = {
            "prompt_version": artifacts.GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            "profile": profile.model_dump(mode="json"),
            "units": artifacts._compact_global_units(units),
            "fact_sizes": sizes,
            "fact_tokens": tokens,
            "structure": None,
        }
        _, legacy_worker = _length_worker()
        try:
            artifacts._run_json_step(
                job,
                stage="global_plan",
                step_index=1,
                input_payload=legacy_payload,
                input_refs={"unit_ids": [unit["id"] for unit in units]},
                messages=[],
                client=_client(legacy_worker),
                max_tokens=artifacts.GLOBAL_PLAN_MAX_TOKENS,
                validator=lambda raw: raw,
            )
        except Exception as error:  # noqa: BLE001
            assert "finish_reason=length" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("the legacy full-scope request must fail with length")

        calls, worker = _length_worker()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        except Exception:  # noqa: BLE001
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("a leaf that cannot split must fail the scope")
        assert calls == [4, 2, 1, 1], calls

        steps = storage.list_artifact_job_steps(job.id)
        parent = next(
            step for step in steps
            if step["stage"] == "global_plan"
            and step["attempts"][-1].get("request_source") == "recovery"
        )
        diagnostics = parent["attempts"][-1]["diagnostics"]
        assert parent["attempts"][-1]["request_source"] == "recovery", parent["attempts"][-1]
        assert diagnostics["requests"] == [], diagnostics["requests"]
        assert diagnostics["step_requests"] == 4, diagnostics
        # The shared limit is derived from the first-level entry count (B=2 here).
        assert diagnostics["step_request_limit"] == (
            artifacts._global_plan_shared_request_limit(2)
        ), diagnostics
        assert any("_global_plan_window_" in step_id for step_id in diagnostics["step_ids"]), (
            diagnostics["step_ids"]
        )
        recoveries = diagnostics["step_recoveries"]
        assert any("拆分全局规划窗口" in item for item in recoveries), recoveries
        assert any("已无法拆分" in item for item in recoveries), recoveries



def check_plain_recovery_success_keeps_request_ownership() -> None:
    """Recovery success on a plain failure: the recovery attempt owns no request."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_plain_success", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-plain-ok-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        calls: list[int] = []
        first = {"pending": True}

        def worker(messages, temperature, max_tokens, **kwargs):
            calls.append(len(_units_in(messages)))
            if first["pending"]:
                first["pending"] = False
                return _length_frame()
            return _plan_frame(messages, f"窗口 {len(calls)}")

        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        assert calls == [4, 2, 2], calls
        assert len(output["cards"]) == 2, output

        parent = next(
            step for step in storage.list_artifact_job_steps(job.id)
            if step["stage"] == "global_plan"
        )
        model_attempt, recovery_attempt = parent["attempts"]
        assert model_attempt["request_source"] == "model", model_attempt
        assert len(model_attempt["diagnostics"]["requests"]) == 1, model_attempt["diagnostics"]
        assert model_attempt["diagnostics"]["step_requests"] == 1, model_attempt["diagnostics"]
        assert recovery_attempt["request_source"] == "recovery", recovery_attempt
        assert recovery_attempt["status"] == "succeeded", recovery_attempt
        diagnostics = recovery_attempt["diagnostics"]
        assert diagnostics["requests"] == [], diagnostics["requests"]
        assert diagnostics["step_requests"] == 3, diagnostics
        assert any("_global_plan_window_" in step_id for step_id in diagnostics["step_ids"]), (
            diagnostics["step_ids"]
        )
        assert any("拆分全局规划窗口" in item for item in diagnostics["step_recoveries"]), diagnostics


def check_plain_recovery_failure_appends_a_recovery_attempt() -> None:
    """Recovery failure on a plain failure records itself without rewriting history."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_plain_failure", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-plain-fail-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        calls, worker = _length_worker()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("continuous length replies must fail the step")
        assert calls == [4, 2, 1, 1], calls
        assert "已无法继续拆分" in message, message

        parent = next(
            step for step in storage.list_artifact_job_steps(job.id)
            if step["stage"] == "global_plan"
        )
        assert parent["status"] == "failed", parent
        assert len(parent["attempts"]) == 2, parent["attempts"]
        model_attempt, recovery_attempt = parent["attempts"]
        assert model_attempt["request_source"] == "model", model_attempt
        assert "finish_reason=length" in str(model_attempt["error"]), model_attempt
        assert model_attempt["diagnostics"]["step_requests"] == 1, model_attempt["diagnostics"]
        assert recovery_attempt["request_source"] == "recovery", recovery_attempt
        assert recovery_attempt["status"] == "failed", recovery_attempt
        assert "已无法继续拆分" in str(recovery_attempt["error"]), recovery_attempt
        diagnostics = recovery_attempt["diagnostics"]
        assert diagnostics["requests"] == [], diagnostics["requests"]
        assert diagnostics["step_requests"] == 4, diagnostics
        assert any("_global_plan_window_" in step_id for step_id in diagnostics["step_ids"]), (
            diagnostics["step_ids"]
        )
        assert any("已无法拆分" in item for item in diagnostics["step_recoveries"]), diagnostics



def check_retry_after_nested_recovery_failure_skips_ancestors() -> None:
    """A nested window recovery failure must not make the retry resend its ancestors."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_global_plan_nested_retry", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-nested-retry-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        first_calls: list[list[str]] = []

        def failing_worker(messages, temperature, max_tokens, **kwargs):
            window = _units_in(messages)
            ids = [unit["id"] for unit in window]
            first_calls.append(ids)
            if len(window) == 4:
                return _length_frame()
            if ids == ["unit_0", "unit_1"]:
                return _plan_frame(messages, "左窗")
            if len(window) == 2:
                return _length_frame()
            raise RuntimeError("leaf transport failed")

        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(failing_worker))
        except Exception as error:  # noqa: BLE001
            assert "leaf transport failed" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("a failing leaf must fail the scope")
        assert first_calls == [
            ["unit_0", "unit_1", "unit_2", "unit_3"],
            ["unit_0", "unit_1"],
            ["unit_2", "unit_3"],
            ["unit_2"],
        ], first_calls

        retry_calls: list[list[str]] = []

        def retry_worker(messages, temperature, max_tokens, **kwargs):
            ids = [unit["id"] for unit in _units_in(messages)]
            retry_calls.append(ids)
            return _plan_frame(messages, f"窗口 {len(retry_calls)}")

        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(retry_worker))
        assert retry_calls == [["unit_2"], ["unit_3"]], (
            "the retry must resume the failed leaf and its sibling, not the ancestors"
        )
        covered = {
            fact_id for card in output["cards"] for fact_id in card.get("fact_ids", [])
        }
        assert covered == {fact.id for fact in facts}, covered



def check_leaf_escalation_is_bounded_once() -> None:
    """An unsplittable window gets exactly one larger budget, only on a length stop."""
    profile, facts, units, sizes, tokens = _fixture(1)

    # 1) An empty length reply buys one 16000 request, which is validated and cached.
    calls: list[int] = []

    def empty_then_plan(messages, temperature, max_tokens, **kwargs):
        calls.append(max_tokens)
        if len(calls) == 1:
            return _length_frame()
        return _plan_frame(messages, "升级后窗口")

    with tempfile.TemporaryDirectory(prefix="trpg-leaf-raise-ok-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        job = SimpleNamespace(id="artifact_job_leaf_raise_ok", model_id="probe", fake_model=False)
        output = _scope_call(
            job, profile, facts, units, sizes, tokens, _client(empty_then_plan)
        )
        assert calls == [12000, 16000], calls
        assert len(output["cards"]) == 1, output
        reentry_calls: list[int] = []

        def counting(messages, temperature, max_tokens, **kwargs):
            reentry_calls.append(max_tokens)
            return _plan_frame(messages, "不应发出")

        again = _scope_call(job, profile, facts, units, sizes, tokens, _client(counting))
        assert again == output and not reentry_calls, "a validated window must be cached"

    # 2) The same one-shot raise applies to a non-empty truncated reply.
    truncated = (
        '{"cards":[{"type":"scene_extract","title":"截断","purpose":"部分事实",'
        '"fact_ids":["fact_plan_split_0"]}],"open_questions":["已写","未写完'
    )
    truncated_calls: list[int] = []

    def truncated_then_plan(messages, temperature, max_tokens, **kwargs):
        truncated_calls.append(max_tokens)
        if len(truncated_calls) == 1:
            return {
                "content": truncated,
                "finish_reason": "length",
                "content_length": len(truncated),
                "usage": {"completion_tokens": max_tokens},
            }
        return _plan_frame(messages, "升级后窗口")

    with tempfile.TemporaryDirectory(prefix="trpg-leaf-raise-truncated-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        job = SimpleNamespace(
            id="artifact_job_leaf_raise_truncated", model_id="probe", fake_model=False
        )
        output = _scope_call(
            job, profile, facts, units, sizes, tokens, _client(truncated_then_plan)
        )
        assert truncated_calls == [12000, 16000], truncated_calls
        assert len(output["cards"]) == 1, output

    # 3) Still truncated at the raised budget: stop after two requests.
    always_calls, always_worker = _length_worker()
    with tempfile.TemporaryDirectory(prefix="trpg-leaf-raise-stop-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        job = SimpleNamespace(id="artifact_job_leaf_raise_stop", model_id="probe", fake_model=False)
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(always_worker))
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("a reply truncated at both budgets must fail the step")
        assert always_calls == [1, 1], always_calls
        assert "已无法继续拆分" in message, message
        budget = storage.list_artifact_job_steps(job.id)
        assert not any(step.get("output") for step in budget), "nothing may be cached"

    # 4) A non-length failure never raises the budget.
    network_calls: list[int] = []

    def network_counting(messages, temperature, max_tokens, **kwargs):
        network_calls.append(max_tokens)
        raise RuntimeError("network down")

    with tempfile.TemporaryDirectory(prefix="trpg-leaf-no-raise-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        job = SimpleNamespace(id="artifact_job_leaf_no_raise", model_id="probe", fake_model=False)
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(network_counting))
        except Exception as error:  # noqa: BLE001
            assert "network down" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("a transport failure must fail the step")
        assert network_calls == [12000], network_calls

    # 5) A budget with no room left cannot raise, even on a length stop.
    # The one raise is the client's, scoped to the window identity: a spent budget has
    # no room, a second call for the same identity cannot raise again, and a sibling
    # identity still starts from the ordinary budget.
    spent = llm.RequestBudget(max_requests=1)
    spent.requests = 1
    assert spent.requests >= spent.max_requests
    fresh = llm.RequestBudget(max_requests=8)
    identity = "global_plan:1:1.1"
    assert fresh.can_escalate(artifacts.GLOBAL_PLAN_WINDOW_MAX_TOKENS, 12000, identity)
    fresh.escalate(artifacts.GLOBAL_PLAN_WINDOW_MAX_TOKENS, 12000, identity)
    assert not fresh.can_escalate(artifacts.GLOBAL_PLAN_WINDOW_MAX_TOKENS, 12000, identity)
    assert fresh.floor_for(identity) == artifacts.GLOBAL_PLAN_WINDOW_MAX_TOKENS
    assert fresh.floor_for("global_plan:1:1.2") == 0



def check_two_leaves_each_raise_once() -> None:
    """Each window owns its raise: a sibling still starts at the ordinary budget."""
    profile, facts, units, sizes, tokens = _fixture(4)
    job = SimpleNamespace(
        id="artifact_job_two_leaf_raise", model_id="probe", fake_model=False,
    )
    seen: dict[str, list[int]] = {}
    order: list[tuple[list[str], int]] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        ids = [unit["id"] for unit in _units_in(messages)]
        order.append((ids, max_tokens))
        if len(ids) > 1:
            return _length_frame()
        key = ids[0]
        seen.setdefault(key, []).append(max_tokens)
        if len(seen[key]) == 1:
            return _length_frame()
        return _plan_frame(messages, f"窗口 {key}")

    with tempfile.TemporaryDirectory(prefix="trpg-two-leaf-raise-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))

    # Every leaf starts at 12000 and spends exactly one 16000 of its own.
    assert seen == {
        "unit_0": [12000, 16000],
        "unit_1": [12000, 16000],
        "unit_2": [12000, 16000],
        "unit_3": [12000, 16000],
    }, seen
    # A multi-unit request starts at the 32k candidate budget and never escalates;
    # only a single-unit leaf keeps the 12000 -> 16000 pair.
    assert all(
        budget == artifacts.GLOBAL_PLAN_MULTI_UNIT_MAX_TOKENS
        for ids, budget in order if len(ids) > 1
    ), order
    assert all(budget in (12000, 16000) for ids, budget in order if len(ids) == 1), order
    assert len(order) <= artifacts.GLOBAL_PLAN_MAX_REQUESTS, len(order)
    covered = {fact_id for card in output["cards"] for fact_id in card.get("fact_ids", [])}
    assert covered == {fact.id for fact in facts}, covered

    # The same leaf truncating again at the raised budget stops instead of raising twice.
    repeated: dict[str, list[int]] = {}

    def stubborn(messages, temperature, max_tokens, **kwargs):
        ids = [unit["id"] for unit in _units_in(messages)]
        if len(ids) == 1:  # only leaf requests belong to a leaf's budget history
            repeated.setdefault(ids[0], []).append(max_tokens)
        return _length_frame()

    job2 = SimpleNamespace(
        id="artifact_job_two_leaf_raise_stop", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-two-leaf-stop-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        try:
            _scope_call(job2, profile, facts, units, sizes, tokens, _client(stubborn))
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("a leaf truncated at both budgets must fail the step")
    assert all(budgets == [12000, 16000] for budgets in repeated.values()), repeated
    assert "已无法继续拆分" in message, message




def check_leaf_window_uses_the_compact_contract() -> None:
    """A leaf asks only for the plan; its message and chapter rules are bounded."""
    profile, facts, units, sizes, tokens = _fixture(4)
    full = artifacts._global_plan_messages(
        profile, units, sizes, tokens, None, partial_window=True, lead_window=False
    )
    leaf = artifacts._global_plan_messages(
        profile,
        units[:1],
        {facts[0].id: sizes[facts[0].id]},
        {facts[0].id: tokens[facts[0].id]},
        None,
        partial_window=True,
        lead_window=False,
        leaf_window=True,
    )
    full_chars = len(full[0]["content"]) + len(full[1]["content"])
    leaf_chars = len(leaf[0]["content"]) + len(leaf[1]["content"])
    # v24 places the shared semantic contract in every planning message, so the leaf is
    # no longer the smaller of the two: what distinguishes it is the contract it asks
    # for, not the byte count (message growth is measured in the packing evidence).
    assert leaf_chars > 0 and full_chars > 0
    assert "[TASK:prep:artifact_global_plan_leaf]" in leaf[1]["content"]
    assert "[TASK:prep:artifact_global_plan_leaf]" not in full[1]["content"]
    assert "LEAF_RESPONSE_SCHEMA_JSON=" in leaf[1]["content"]
    assert "LEAF_RESPONSE_SCHEMA_JSON=" not in full[1]["content"]
    assert "cannot split further" in leaf[0]["content"]
    assert "Output limits, read from the validator" in leaf[0]["content"]
    leaf_text = leaf[0]["content"] + leaf[1]["content"]
    assert "LEAF_RESPONSE_SCHEMA_JSON=" in leaf_text, leaf_text[:200]
    assert "No chapter scope exists for this window" in leaf[0]["content"], leaf[0]["content"]
    assert "chapter_ids must be []" in leaf[0]["content"], leaf[0]["content"]
    # The fields later stages consume stay in the leaf contract.
    for field_name in ("situation", "case_scope", "runtime_groups", "story_beat_ids"):
        assert field_name in leaf[1]["content"], field_name
    # A location board keeps its card-granularity rule even in the compact contract.
    cthulhu = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    cthulhu_leaf = artifacts._global_plan_messages(
        cthulhu,
        units[:1],
        {facts[0].id: sizes[facts[0].id]},
        {facts[0].id: tokens[facts[0].id]},
        None,
        partial_window=True,
        lead_window=False,
        leaf_window=True,
    )
    assert "keeps its own card" in cthulhu_leaf[0]["content"], (
        cthulhu_leaf[0]["content"][:300]
    )
    # An environment board keeps situation/case_scope and the beat-link rule.
    daggerheart = load_profiles(ROOT / "backend" / "domain" / "profiles")["daggerheart"]
    fantasy_leaf = artifacts._global_plan_messages(
        daggerheart,
        units[:1],
        {facts[0].id: sizes[facts[0].id]},
        {facts[0].id: tokens[facts[0].id]},
        None,
        partial_window=True,
        lead_window=False,
        leaf_window=True,
    )
    fantasy_text = fantasy_leaf[0]["content"] + fantasy_leaf[1]["content"]
    assert "case_scope" in fantasy_text and "story_beat_ids" in fantasy_text, fantasy_text[:300]
    assert "package brief" in fantasy_leaf[0]["content"], fantasy_leaf[0]["content"][:300]

    # A leaf request in a real run carries this contract, and an invented chapter id in
    # the reply is still rejected with the offending id named.
    prompts: list[str] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        ids = [unit["id"] for unit in _units_in(messages)]
        prompts.append(messages[0]["content"] + messages[1]["content"])
        if len(ids) > 1:
            return _length_frame()
        payload = _window_plan(messages, "叶子")
        payload["cards"][0]["chapter_ids"] = ["chapter_invented"]
        return {"content": json.dumps(payload, ensure_ascii=False), "finish_reason": "stop",
                "content_length": None, "usage": None}

    job = SimpleNamespace(
        id="artifact_job_leaf_contract", model_id="probe", fake_model=False,
    )
    with tempfile.TemporaryDirectory(prefix="trpg-leaf-contract-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("an invented chapter id must fail the window")
    # Multi-unit windows and leaves now share one compact producer; they are told
    # apart by the window addendum rather than by the schema block.
    assert any(
        "ONE WINDOW made of several supplied index units" not in prompt
        for prompt in prompts
    ), prompts
    assert any(
        "ONE WINDOW made of several supplied index units" in prompt
        for prompt in prompts
    ), prompts
    assert any("LEAF_RESPONSE_SCHEMA_JSON" in prompt for prompt in prompts), (
        "the leaf request must use the compact contract"
    )
    assert "chapter_invented" in message, message



def _board_fixture(board: str, *, chapters: bool = False):
    """Four facts and four planning units for one real board."""
    profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
    profile = profiles[board]
    facts = [
        SourceFact(
            id=f"fact_leaf_board_{index}",
            text=f"Board fact {index}",
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://board-leaf", page=index + 1)],
        )
        for index in range(4)
    ]
    units = [
        {
            "id": f"unit_{index}",
            "kind": "scene",
            "title": f"场景 {index}",
            "summary": "一个可以独立调用的场景。",
            "fact_ids": [fact.id],
            "entity_keys": [],
            "relationship_hints": [],
            "open_questions": [],
        }
        for index, fact in enumerate(facts)
    ]
    sizes = {
        fact.id: len(json.dumps(artifacts._fact_payload_from_facts([fact]), ensure_ascii=False))
        for fact in facts
    }
    tokens = {
        fact.id: artifacts._estimate_tokens(
            json.dumps(artifacts._fact_payload_from_facts([fact]), ensure_ascii=False)
        )
        for fact in facts
    }
    chapter_id = "chapter_board_leaf"
    bundle = None
    context = None
    if chapters:
        bundle = ExampleBundle.model_validate({
            "id": f"leaf_board_{board.replace('-', '_')}",
            "name": "Leaf board",
            "profile_ids": [board],
            "facts": [fact.model_dump(mode="json") for fact in facts],
            "cards": [],
            "plans": [],
            "adventure_chapters": [{
                "id": chapter_id,
                "title": "章节",
                "summary": "章节摘要",
                "fact_ids": [fact.id for fact in facts],
            }],
            "fact_dispositions": [
                {"fact_id": fact.id, "kind": "chapter", "chapter_ids": [chapter_id]}
                for fact in facts
            ],
            "story_beats": [{
                "id": "story_beat_x",
                "title": "拍",
                "summary": "拍摘要",
                "chapter_id": chapter_id,
                "thread_ids": [],
                "fact_ids": [],
            }],
        })
        context = {
            "chapters": [{"id": chapter_id, "title": "章节", "summary": "章节摘要"}],
            "threads": [],
            "beats": [{
                "id": "story_beat_x", "title": "拍", "summary": "拍摘要",
                "chapter_id": chapter_id, "thread_ids": [], "fact_ids": [],
            }],
            "fact_dispositions": [
                {"fact_id": fact.id, "kind": "chapter", "chapter_ids": [chapter_id]}
                for fact in facts
            ],
        }
    return profile, facts, units, sizes, tokens, bundle, context, chapter_id


def check_all_leaf_windows_satisfy_the_parent_contract() -> None:
    """Every window may split to leaves; the merged plan still passes validation."""
    anchor = {"cthulhu-dark-2e": "location", "daggerheart": "environment"}
    for board in ("cthulhu-dark-2e", "daggerheart"):
        for chapters in ((False, True) if board == "daggerheart" else (False,)):
            (
                profile, facts, units, sizes, tokens, bundle, context, chapter_id,
            ) = _board_fixture(board, chapters=chapters)
            chapter_ids = [chapter_id] if chapters else []

            requests: list = []

            def worker(messages, temperature, max_tokens, **kwargs):
                window = _units_in(messages)
                requests.append(len(window))
                if len(window) > 1:
                    return _length_frame()
                fact_ids = [fid for unit in window for fid in unit["fact_ids"]]
                has_overview = any(
                    definition.type == "chapter_overview"
                    for definition in profile.card_definitions
                )
                cards = [
                    {
                        "type": anchor[board],
                        "title": "叶子卡",
                        "purpose": "本窗口可调用的入口。",
                        "fact_ids": fact_ids,
                        "chapter_ids": chapter_ids,
                        "focus": [],
                        "open_questions": [],
                        "runtime_groups": [],
                        "story_beat_ids": [],
                        "situation": "叶子情境",
                        "case_scope": "current",
                    },
                ]
                if has_overview:
                    cards.append({
                        "type": "chapter_overview",
                        "title": "窗口概览",
                        "purpose": "本窗口事实在整体情势中的位置。",
                        "fact_ids": fact_ids,
                        "chapter_ids": chapter_ids,
                    })
                payload: dict = {"cards": cards, "open_questions": []}
                if chapters:
                    payload["chapter_briefs"] = [{
                        "chapter_id": chapter_id,
                        "brief": {"premise": "前提", "current_situation": "现状"},
                        "reference_fact_ids": fact_ids,
                    }]
                elif board == "daggerheart":
                    payload["brief"] = {"premise": "前提", "current_situation": "现状"}
                return {
                    "content": json.dumps(payload, ensure_ascii=False),
                    "finish_reason": "stop",
                    "content_length": None,
                    "usage": None,
                }

            job = SimpleNamespace(
                id=f"artifact_job_leaf_board_{board}_{chapters}",
                model_id="probe",
                fake_model=False,
            )
            with tempfile.TemporaryDirectory(prefix="trpg-leaf-board-") as directory:
                storage.DB_PATH = Path(directory) / "test.db"
                storage.init_db()
                output = artifacts._run_global_plan_scope(
                    job,
                    plan_index=1,
                    profile=profile,
                    bundle=bundle,
                    plan_units=units,
                    plan_facts=facts,
                    plan_fact_sizes=sizes,
                    plan_fact_tokens=tokens,
                    plan_context=context,
                    chapter_scope={chapter_id} if chapters else None,
                    plan_client=_client(worker),
                )
            covered = {
                fid for card in output["cards"] for fid in card.get("fact_ids", [])
            }
            assert covered == {fact.id for fact in facts}, (board, chapters, covered)
            if board == "cthulhu-dark-2e":
                overviews = [
                    card for card in output["cards"] if card["type"] == "chapter_overview"
                ]
                assert len(overviews) >= 1, overviews
                # Each branch's partial overview contributes its own facts, so the
                # merged overview covers the whole scope rather than the first leaf.
                overview_facts = {
                    fid for card in overviews for fid in card.get("fact_ids", [])
                }
                assert overview_facts == {fact.id for fact in facts}, overview_facts
            if chapters:
                assert output["chapter_briefs"], (board, output)
                assert output["chapter_briefs"][0]["chapter_id"] == chapter_id
            elif board == "daggerheart":
                assert output.get("brief"), (board, output)



def check_raise_is_one_step_and_retry_reuses_it() -> None:
    """Both budgets live in one persistent step, and a retry starts from the larger one."""
    profile, facts, units, sizes, tokens = _fixture(1)
    job = SimpleNamespace(
        id="artifact_job_leaf_one_step", model_id="probe", fake_model=False,
    )
    budgets: list = []

    def worker(messages, temperature, max_tokens, **kwargs):
        budgets.append(max_tokens)
        return _length_frame()

    with tempfile.TemporaryDirectory(prefix="trpg-leaf-one-step-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        client = _client(worker)
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, client)
        except Exception:  # noqa: BLE001
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("a leaf truncated at both budgets must fail the step")
        assert budgets == [12000, 16000], budgets

        steps = storage.list_artifact_job_steps(job.id)
        assert not any(
            step["stage"].startswith("global_plan_window_") for step in steps
        ), "a single-unit scope must not open a window child"
        root = next(step for step in steps if step["stage"] == "global_plan")
        assert len(root["attempts"]) == 2, root["attempts"]
        recorded = [
            request["max_tokens"]
            for request in root["attempts"][0]["diagnostics"]["requests"]
        ]
        assert recorded == [12000, 16000], recorded
        assert root["attempts"][0]["request_source"] == "model", root["attempts"][0]
        assert root["attempts"][1]["request_source"] == "recovery", root["attempts"][1]

        budgets.clear()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, client)
        except Exception:  # noqa: BLE001
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("a repeated truncation must keep failing the step")
        assert budgets == [16000], "the retry must not resend the failed 12000"
        root = next(
            step for step in storage.list_artifact_job_steps(job.id)
            if step["stage"] == "global_plan"
        )
        assert len(root["attempts"]) == 4, root["attempts"]  # + retry and its recovery



def check_fake_leaf_output_passes_parent_validation() -> None:
    """The FakeLLM leaf answer carries every contribution the parent validation needs."""
    for chapters in (False, True):
        (
            profile, facts, units, sizes, tokens, bundle, context, chapter_id,
        ) = _board_fixture("daggerheart", chapters=chapters)
        outputs = []
        for unit in units:
            unit_facts = [fact for fact in facts if fact.id in set(unit["fact_ids"])]
            messages = artifacts._global_plan_messages(
                profile,
                [unit],
                {fact.id: sizes[fact.id] for fact in unit_facts},
                {fact.id: tokens[fact.id] for fact in unit_facts},
                context,
                partial_window=True,
                lead_window=False,
                leaf_window=True,
            )
            outputs.append(llm._fake_leaf_plan_output(messages))
        merged = artifacts._merge_global_plan_outputs(outputs)
        validated = artifacts._validate_global_plan(
            merged,
            units,
            facts,
            profile,
            bundle,
            {chapter_id} if chapters else None,
        )
        covered = {
            fid for card in validated["cards"] for fid in card.get("fact_ids", [])
        }
        assert covered == {fact.id for fact in facts}, (chapters, covered)
        if chapters:
            assert validated["chapter_briefs"], validated
            assert validated["chapter_briefs"][0]["chapter_id"] == chapter_id
        else:
            assert validated["brief"], validated


def check_leaf_semantic_fields_survive_the_merge() -> None:
    """case_scope, runtime groups, beat links and branch overviews reach the parent."""
    profile, facts, units, sizes, tokens, bundle, context, chapter_id = _board_fixture(
        "daggerheart", chapters=True
    )
    beats = [{"id": "story_beat_x", "title": "拍", "summary": "拍摘要",
              "chapter_id": chapter_id, "thread_ids": [], "fact_ids": []}]
    context = {**context, "beats": beats}
    scope_choices = ["current", "related", "conditional_example", "current"]
    card_types = {definition.type for definition in profile.card_definitions}

    def leaf_payload(messages, index):
        fact_ids = [fid for unit in _units_in(messages) for fid in unit["fact_ids"]]
        card = {
            "type": "environment",
            "title": f"叶子 {index}",
            "purpose": "本窗口的可运行情境。",
            "fact_ids": fact_ids,
            "chapter_ids": [chapter_id],
            "focus": [],
            "open_questions": [],
            "runtime_groups": ["trollskull_tavern"] if index == 0 else [],
            "story_beat_ids": ["story_beat_x"] if index == 0 else [],
            "situation": f"情境 {index}",
            "case_scope": scope_choices[index],
        }
        payload = {
            "cards": [card, {
                "type": "chapter_overview",
                "title": "部分概览",
                "purpose": "本窗口的情势。",
                "fact_ids": fact_ids,
                "chapter_ids": [chapter_id],
            }],
            "chapter_briefs": [{
                "chapter_id": chapter_id,
                "brief": {"premise": "前提", "current_situation": "现状"},
                "reference_fact_ids": fact_ids,
            }],
            "open_questions": [],
        }
        if "chapter_overview" not in card_types:
            payload["cards"] = payload["cards"][:1]
        return payload

    outputs = []
    for index, unit in enumerate(units):
        unit_facts = [fact for fact in facts if fact.id in set(unit["fact_ids"])]
        messages = artifacts._global_plan_messages(
            profile, [unit],
            {fact.id: sizes[fact.id] for fact in unit_facts},
            {fact.id: tokens[fact.id] for fact in unit_facts},
            context, partial_window=True, lead_window=index == 0, leaf_window=True,
        )
        outputs.append(leaf_payload(messages, index))
    merged = artifacts._merge_global_plan_outputs(outputs)
    validated = artifacts._validate_global_plan(
        merged, units, facts, profile, bundle, {chapter_id}
    )
    by_title = {card["title"]: card for card in validated["cards"]}
    assert by_title["叶子 0"]["case_scope"] == "current", by_title["叶子 0"]
    assert by_title["叶子 1"]["case_scope"] == "related", by_title["叶子 1"]
    assert by_title["叶子 2"]["case_scope"] == "conditional_example", by_title["叶子 2"]
    # runtime_groups become canonical runtime_group_ids in the validated plan.
    assert by_title["叶子 0"]["runtime_group_ids"], by_title["叶子 0"]
    assert by_title["叶子 0"]["story_beat_ids"] == ["story_beat_x"], by_title["叶子 0"]



def check_mixed_level_windows_merge() -> None:
    """One 2-unit window succeeds while the other splits into leaves, then they merge."""
    profile, facts, units, sizes, tokens, bundle, context, chapter_id = _board_fixture(
        "daggerheart", chapters=True
    )
    calls: list = []

    def payload(messages, title):
        fact_ids = [fid for unit in _units_in(messages) for fid in unit["fact_ids"]]
        return {
            "cards": [{
                "type": "environment", "title": title, "purpose": "入口。",
                "fact_ids": fact_ids, "chapter_ids": [chapter_id],
                "focus": [], "open_questions": [], "runtime_groups": [],
                "story_beat_ids": [], "situation": "情境", "case_scope": "current",
            }],
            "chapter_briefs": [{
                "chapter_id": chapter_id,
                "brief": {"premise": "前提", "current_situation": "现状"},
                "reference_fact_ids": fact_ids,
            }],
            "open_questions": [],
        }

    def worker(messages, temperature, max_tokens, **kwargs):
        window = _units_in(messages)
        ids = [unit["id"] for unit in window]
        calls.append(ids)
        if len(window) >= 4:
            return _length_frame()
        if len(window) == 2 and ids == ["unit_0", "unit_1"]:
            return _frame(payload(messages, "窗口卡"))
        if len(window) == 2:
            return _length_frame()
        return _frame(payload(messages, f"叶子 {ids[0]}"))

    job = SimpleNamespace(id="artifact_job_mixed_windows", model_id="probe", fake_model=False)
    with tempfile.TemporaryDirectory(prefix="trpg-mixed-windows-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        output = artifacts._run_global_plan_scope(
            job, plan_index=1, profile=profile, bundle=bundle, plan_units=units,
            plan_facts=facts, plan_fact_sizes=sizes, plan_fact_tokens=tokens,
            plan_context=context, chapter_scope={chapter_id}, plan_client=_client(worker),
        )
    assert [len(ids) for ids in calls] == [4, 2, 2, 1, 1], calls
    titles = {card["title"] for card in output["cards"]}
    assert "窗口卡" in titles, titles
    assert {"叶子 unit_2", "叶子 unit_3"} <= titles, titles
    covered = {fid for card in output["cards"] for fid in card.get("fact_ids", [])}
    assert covered == {fact.id for fact in facts}, covered
    assert output["chapter_briefs"], output


def check_new_contract_does_not_reuse_an_old_window_cache() -> None:
    """A v19 window success cannot answer the v20 step the production entry creates."""
    profile, facts, units, sizes, tokens, bundle, context, chapter_id = _board_fixture(
        "daggerheart", chapters=True
    )
    unit = units[:1]
    unit_facts = [fact for fact in facts if fact.id in set(unit[0]["fact_ids"])]
    job = SimpleNamespace(id="artifact_job_contract_cache", model_id="probe", fake_model=False)
    # The historical window contract: same business input, v19, no leaf_contract marker.
    old_payload = {
        "prompt_version": "prep-artifact-global-plan-v19",
        "profile": profile.model_dump(mode="json"),
        "units": artifacts._compact_global_units(unit),
        "fact_sizes": {fact.id: sizes[fact.id] for fact in unit_facts},
        "fact_tokens": {fact.id: tokens[fact.id] for fact in unit_facts},
        "structure": context,
    }
    old_hash = artifacts._stable_hash({
        **old_payload,
        "_model_id": job.model_id,
        "_fake_model": job.fake_model,
    })
    calls: list = []

    def worker(messages, temperature, max_tokens, **kwargs):
        calls.append(max_tokens)
        fact_ids = [fid for item in _units_in(messages) for fid in item["fact_ids"]]
        return _frame({
            "cards": [{
                "type": "environment", "title": "新契约叶子", "purpose": "入口。",
                "fact_ids": fact_ids, "chapter_ids": [chapter_id],
                "focus": [], "open_questions": [], "runtime_groups": [],
                "story_beat_ids": [], "situation": "情境", "case_scope": "current",
            }],
            "chapter_briefs": [{
                "chapter_id": chapter_id,
                "brief": {"premise": "前提", "current_situation": "现状"},
                "reference_fact_ids": fact_ids,
            }],
            "open_questions": [],
        })

    with tempfile.TemporaryDirectory(prefix="trpg-contract-cache-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        storage.save_artifact_job_step({
            "id": "artifact_step_contract_cache_old",
            "job_id": job.id,
            "stage": "global_plan",
            "step_index": 1,
            "input_hash": old_hash,
            "input_refs": {"unit_ids": [unit[0]["id"]]},
            "status": "succeeded",
            "output": {"cards": [{"title": "旧契约输出"}], "open_questions": []},
            "attempts": [],
            "created_at": storage.now(),
            "updated_at": storage.now(),
        })
        client = _client(worker)
        first = artifacts._run_global_plan_scope(
            job, plan_index=1, profile=profile, bundle=bundle, plan_units=unit,
            plan_facts=unit_facts,
            plan_fact_sizes={fact.id: sizes[fact.id] for fact in unit_facts},
            plan_fact_tokens={fact.id: tokens[fact.id] for fact in unit_facts},
            plan_context=context, chapter_scope={chapter_id}, plan_client=client,
        )
        assert calls, "the v20 step must issue its own request"
        current = [
            step for step in storage.list_artifact_job_steps(job.id)
            if step["stage"] == "global_plan"
            and step["input_hash"] != old_hash
        ]
        assert current, "the production entry must record its own step identity"
        stored = next(
            step for step in storage.list_artifact_job_steps(job.id)
            if step["input_hash"] == old_hash
        )
        assert stored["status"] == "succeeded" and not stored.get("attempts"), stored
        assert stored["output"]["cards"][0]["title"] == "旧契约输出", stored["output"]
        titles = {card.get("title") for card in first["cards"]}
        assert "旧契约输出" not in titles, titles
        before = len(calls)
        again = artifacts._run_global_plan_scope(
            job, plan_index=1, profile=profile, bundle=bundle, plan_units=unit,
            plan_facts=unit_facts,
            plan_fact_sizes={fact.id: sizes[fact.id] for fact in unit_facts},
            plan_fact_tokens={fact.id: tokens[fact.id] for fact in unit_facts},
            plan_context=context, chapter_scope={chapter_id}, plan_client=client,
        )
        assert again == first and len(calls) == before, "the v20 success must be cached"


def _scope_leaf_payload(
    messages, *, case_scope="current", chapter_id=None, fact_ids=None,
    card_type="scene_extract",
):
    ids = fact_ids or [fid for unit in _units_in(messages) for fid in unit["fact_ids"]]
    card = {
        "type": card_type,
        "title": "叶子卡",
        "purpose": "入口。",
        "fact_ids": ids,
        "chapter_ids": [chapter_id] if chapter_id else [],
        "focus": [],
        "open_questions": [],
        "runtime_groups": [],
        "story_beat_ids": [],
        "situation": "情境",
        "case_scope": case_scope,
    }
    return {"cards": [card], "open_questions": []}


def _frame(payload):
    return {
        "content": json.dumps(payload, ensure_ascii=False),
        "finish_reason": "stop",
        "content_length": None,
        "usage": None,
    }


def check_schema_failure_gets_the_bounded_correction() -> None:
    """A parsed reply that fails schema validation is corrected, not split away."""
    profile, facts, units, sizes, tokens = _fixture(1)
    job = SimpleNamespace(
        id="artifact_job_schema_correction", model_id="probe", fake_model=False,
    )
    calls: list = []

    def worker(messages, temperature, max_tokens, **kwargs):
        calls.append(max_tokens)
        if len(calls) == 1:
            return _frame(_scope_leaf_payload(messages, case_scope="historical"))
        return _frame(_scope_leaf_payload(messages, case_scope="related"))

    with tempfile.TemporaryDirectory(prefix="trpg-schema-correction-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        step = next(
            step for step in storage.list_artifact_job_steps(job.id)
            if step["stage"] == "global_plan"
        )
        windows = [
            item for item in storage.list_artifact_job_steps(job.id)
            if item["stage"].startswith("global_plan_window_")
        ]
    assert calls == [12000, 12000], calls
    assert output["cards"][0]["case_scope"] == "related", output["cards"][0]
    assert step["status"] == "succeeded", step
    assert step["attempts"][0]["status"] == "failed", step["attempts"]
    assert "case_scope" in step["attempts"][0]["error"], step["attempts"][0]
    assert step["attempts"][-1]["request_source"] == "model", step["attempts"][-1]
    assert not windows, "a schema failure must not open a window split"


def check_only_the_failed_leaf_is_corrected() -> None:
    """After a split, the sibling that succeeded is not requested again."""
    profile, facts, units, sizes, tokens = _fixture(2)
    job = SimpleNamespace(
        id="artifact_job_leaf_correction", model_id="probe", fake_model=False,
    )
    per_window: dict = {}

    def worker(messages, temperature, max_tokens, **kwargs):
        window = _units_in(messages)
        key = tuple(unit["id"] for unit in window)
        per_window.setdefault(key, []).append(max_tokens)
        if len(window) > 1:
            return _length_frame()
        first = len(per_window[key]) == 1
        if key == ("unit_1",) and first:
            return _frame(_scope_leaf_payload(messages, case_scope="historical"))
        return _frame(_scope_leaf_payload(messages, case_scope="current"))

    with tempfile.TemporaryDirectory(prefix="trpg-leaf-correction-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
    # A multi-unit window starts at the 32k candidate budget; only the leaf keeps 12k.
    assert per_window[("unit_0", "unit_1")] == [
        artifacts.GLOBAL_PLAN_MULTI_UNIT_MAX_TOKENS
    ], per_window
    assert per_window[("unit_0",)] == [12000], per_window
    assert per_window[("unit_1",)] == [12000, 12000], per_window
    covered = {fid for card in output["cards"] for fid in card.get("fact_ids", [])}
    assert covered == {fact.id for fact in facts}, covered


def check_two_schema_failures_stop() -> None:
    """The correction is tried once; a second schema failure ends the step."""
    profile, facts, units, sizes, tokens = _fixture(1)
    job = SimpleNamespace(
        id="artifact_job_schema_twice", model_id="probe", fake_model=False,
    )
    calls: list = []

    def worker(messages, temperature, max_tokens, **kwargs):
        calls.append(max_tokens)
        return _frame(_scope_leaf_payload(messages, case_scope="historical"))

    with tempfile.TemporaryDirectory(prefix="trpg-schema-twice-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        try:
            _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("two schema failures must end the step")
    assert calls == [12000, 12000], calls
    assert "case_scope" in message, message


def check_correction_respects_the_shared_cap() -> None:
    """A correction cannot push the step past the shared request cap."""
    calls: list = []

    def worker(messages, temperature, max_tokens, **kwargs):
        calls.append(max_tokens)
        return _frame({"ok": False})

    def validator(raw):
        raise ValueError("synthetic schema failure")

    job = SimpleNamespace(id="artifact_job_cap_correction", model_id="probe", fake_model=False)
    with tempfile.TemporaryDirectory(prefix="trpg-cap-correction-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        try:
            artifacts._run_json_step(
                job,
                stage="global_plan",
                step_index=1,
                input_payload={"synthetic": True},
                input_refs={},
                messages=[{"role": "user", "content": "hi"}],
                client=_client(worker),
                max_tokens=12000,
                validator=validator,
                step_budget=llm.RequestBudget(max_requests=1),
            )
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("the capped budget must stop the correction")
    assert calls == [12000], calls
    assert "次模型请求" in message, message



def check_location_cards_need_source_supported_use() -> None:
    """The location contract separates a runnable place from a mere name in the source."""
    cthulhu = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    facts = [SourceFact(id=f"fact_contract_{i}", text="x", kind="clue",
                        visibility="explicit",
                        source_refs=[SourceRef(file="fixture://contract", page=1)])
             for i in range(1)]
    unit = [{"id": "unit_0", "kind": "scene", "title": "地点", "summary": "摘要",
             "fact_ids": [facts[0].id], "entity_keys": [], "relationship_hints": [],
             "open_questions": []}]
    sizes = {facts[0].id: 50}
    tokens = {facts[0].id: 20}
    # The contract that is really sent for a window is the compact one, so the location
    # duties are checked where production puts them.
    window = artifacts._global_plan_messages(
        cthulhu, unit, sizes, tokens, None, partial_window=True, lead_window=False,
        compact_window=True,
    )
    leaf = artifacts._global_plan_messages(
        cthulhu, unit, sizes, tokens, None, partial_window=True, lead_window=False,
        leaf_window=True,
    )
    for messages, label in ((window, "window"), (leaf, "leaf")):
        text = messages[0]["content"]
        for expected in (
            "source-supported runnable content",
            "pure geography",
            "historical note",
            "map reference",
            "specifically support",
            # Two institutions with the same use stay two entries; the no-duplicate rule
            # protects one entity or one state, not a second named entity.
            "two institutions with the same use do not merge",
            "police station",
            "publisher",
            "hotel",
        ):
            assert expected in text, (label, expected, text[:300])
        assert "must receive its own location card" not in text, (
            "a name alone must not demand a card")
    assert artifacts.GLOBAL_PLAN_PROMPT_VERSION == "prep-artifact-global-plan-v24"


def check_single_unit_scope_uses_the_leaf_contract() -> None:
    """The one-unit production entry sends the same compact contract as a leaf."""
    profile, facts, units, sizes, tokens = _fixture(1)
    job = SimpleNamespace(id="artifact_job_scope_leaf_contract", model_id="probe",
                          fake_model=False)
    prompts: list[str] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        prompts.append(messages[0]["content"] + messages[1]["content"])
        return _frame(_scope_leaf_payload(messages, case_scope="current"))

    with tempfile.TemporaryDirectory(prefix="trpg-scope-leaf-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        client = _client(worker)
        output = _scope_call(job, profile, facts, units, sizes, tokens, client)
        assert prompts, "the scope must send its own request"
        sent = prompts[0]
        assert "[TASK:prep:artifact_global_plan_leaf]" in sent, sent[:200]
        assert "ORIGINAL_FACTS_JSON=" in sent, "the leaf must see its facts"
        assert facts[0].id in sent and "Fact 0" in sent, sent[-200:]
        # The same request serves a re-entry without another model call.
        before = len(prompts)
        again = _scope_call(job, profile, facts, units, sizes, tokens, client)
        assert again == output and len(prompts) == before

    # A multi-unit scope keeps the full-scope contract.
    profile4, facts4, units4, sizes4, tokens4 = _fixture(4)
    window = artifacts._global_plan_messages(
        profile4, units4, sizes4, tokens4, None, partial_window=False
    )
    assert "[TASK:prep:artifact_global_plan]" in window[1]["content"], window[1]["content"][:200]
    assert "ORIGINAL_FACTS_JSON=" not in window[1]["content"]


def check_leaf_prompt_provides_fact_evidence() -> None:
    """Every fact reaches the leaf with its own text, and its content is its identity."""
    profile, facts, units, sizes, tokens = _fixture(2)
    messages = artifacts._global_plan_messages(
        profile, units, sizes, tokens, None, partial_window=True, lead_window=False,
        leaf_window=True, fact_payload=artifacts._fact_payload_from_facts(facts),
    )
    body = messages[1]["content"]
    for fact in facts:
        assert fact.id in body, fact.id
        assert fact.text in body, fact.text
    # The transport encodes the IDs but keeps each entry paired with its own text.
    from backend.app.planning_transport import PlanningTransport
    transport = PlanningTransport(None, [fact.id for fact in facts], None)
    encoded = transport.messages(messages)
    marker = next(
        line for line in encoded[1]["content"].splitlines()
        if line.startswith("ORIGINAL_FACTS_JSON=")
    )
    entries = json.loads(marker.split("=", 1)[1])
    by_text = {entry["text"]: entry for entry in entries}
    for fact in facts:
        assert fact.text in by_text, fact.text
        assert by_text[fact.text]["id"] != fact.id, "the transport must encode fact IDs"
    assert len({entry["id"] for entry in entries}) == len(facts)

    # A changed fact text changes the step identity.
    changed = [fact.model_copy(update={"text": fact.text + " (updated)"}) for fact in facts]
    assert artifacts._gestalt_fact_digest(facts) != artifacts._gestalt_fact_digest(changed)


def check_multi_unit_requests_carry_fact_evidence() -> None:
    """A scope that succeeds directly still sees every fact it may cite."""
    profile, facts, units, sizes, tokens = _fixture(2)
    job = SimpleNamespace(id="artifact_job_direct_scope_facts", model_id="probe",
                          fake_model=False)
    seen: list[str] = []

    def worker(messages, temperature, max_tokens, **kwargs):
        seen.append(messages[0]["content"] + messages[1]["content"])
        fact_ids = [fid for unit in _units_in(messages) for fid in unit["fact_ids"]]
        return _frame({"cards": [{
            "type": "scene_extract", "title": "直接成功", "purpose": "入口。",
            "fact_ids": fact_ids, "focus": [], "open_questions": [],
            "runtime_groups": [], "story_beat_ids": [], "situation": "",
            "case_scope": "current",
        }], "open_questions": []})

    with tempfile.TemporaryDirectory(prefix="trpg-direct-scope-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        output = _scope_call(job, profile, facts, units, sizes, tokens, _client(worker))
    assert len(seen) == 1, seen
    sent = seen[0]
    assert "ORIGINAL_FACTS_JSON=" in sent
    for fact in facts:
        assert fact.id in sent and fact.text in sent, fact.id
    covered = {fid for card in output["cards"] for fid in card.get("fact_ids", [])}
    assert covered == {fact.id for fact in facts}


def check_oversize_input_is_split_or_refused_before_sending() -> None:
    """Oversize input splits before the worker; a leaf that fits after encoding is sent."""
    from unittest.mock import patch as _patch

    profile, facts, units, sizes, tokens = _fixture(2)

    def plan_worker(counter):
        def worker(messages, temperature, max_tokens, **kwargs):
            counter.append(len(_units_in(messages)))
            fact_ids = [fid for unit in _units_in(messages) for fid in unit["fact_ids"]]
            return _frame({"cards": [{
                "type": "scene_extract", "title": "窗口卡", "purpose": "入口。",
                "fact_ids": fact_ids, "focus": [], "open_questions": [],
                "runtime_groups": [], "story_beat_ids": [], "situation": "",
                "case_scope": "current",
            }], "open_questions": []})
        return worker

    # A total input that is too large while each child fits: the root is split, the
    # children are requested, and the merged plan still passes the root validation.
    calls: list[int] = []
    with tempfile.TemporaryDirectory(prefix="trpg-oversize-split-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        # The v24 shared contract is larger, so the boundary must sit between one
        # compact leaf (~14.4k) and the two-unit request (~16.2k) to still force
        # exactly one split.
        with _patch.object(artifacts, "MAX_FACT_INPUT_CHARS", 15500):
            output = _scope_call(
                job := SimpleNamespace(id="artifact_job_oversize_split",
                                       model_id="probe", fake_model=False),
                profile, facts, units, sizes, tokens, _client(plan_worker(calls)),
            )
        step = next(
            item for item in storage.list_artifact_job_steps(job.id)
            if item["stage"] == "global_plan"
        )
    assert calls == [1, 1], calls
    assert step["status"] == "succeeded", step
    assert step["attempts"][-1]["request_source"] == "recovery", step["attempts"][-1]
    assert {fid for card in output["cards"] for fid in card.get("fact_ids", [])} == {
        fact.id for fact in facts
    }

    # A single unit that cannot be reduced any further is refused with no request.
    calls.clear()
    with tempfile.TemporaryDirectory(prefix="trpg-oversize-leaf-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        with _patch.object(artifacts, "MAX_FACT_INPUT_CHARS", 300):
            try:
                _scope_call(
                    SimpleNamespace(id="artifact_job_oversize_leaf", model_id="probe",
                                    fake_model=False),
                    profile, facts[:1], units[:1], sizes, tokens,
                    _client(plan_worker(calls)),
                )
            except artifacts.ArtifactGenerationError as error:
                assert "输入边界" in str(error), error
            else:  # pragma: no cover - defensive
                raise AssertionError("an oversize single unit must be refused")
    assert not calls, "an oversize leaf must not send a request"

    # Raw JSON larger than the limit but small enough after transport encoding is sent.
    calls.clear()
    from backend.app.planning_transport import PlanningTransport
    facts_models = facts[:1]
    messages = artifacts._global_plan_messages(
        profile, units[:1], sizes, tokens, None, partial_window=True,
        lead_window=False, leaf_window=True,
        fact_payload=artifacts._fact_payload_from_facts(facts_models),
    )
    raw_size = len(json.dumps(messages, ensure_ascii=False))
    encoded_size = len(json.dumps(
        PlanningTransport(None, [fact.id for fact in facts_models], None).messages(messages),
        ensure_ascii=False,
    ))
    assert encoded_size < raw_size, (encoded_size, raw_size)
    limit = (encoded_size + raw_size) // 2
    with tempfile.TemporaryDirectory(prefix="trpg-encoded-fits-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        with _patch.object(artifacts, "MAX_FACT_INPUT_CHARS", limit):
            base_client = _client(plan_worker(calls))
            transport_client = PlanningTransport(
                base_client, [fact.id for fact in facts_models], None
            )
            _scope_call(
                SimpleNamespace(id="artifact_job_encoded_fits", model_id="probe",
                                fake_model=False),
                profile, facts_models, units[:1], sizes, tokens, transport_client,
            )
    assert calls == [1], calls


def check_oversize_scope_still_faces_parent_validation() -> None:
    """An oversize root never turns its failed validation into an oversize request."""
    from unittest.mock import patch as _patch

    def run_board(*, with_overview: bool, times: int):
        profile, facts, units, sizes, tokens, bundle, context, chapter_id = _board_fixture(
            "cthulhu-dark-2e"
        )
        sent: list[dict] = []

        def worker(messages, temperature, max_tokens, **kwargs):
            units_in = _units_in(messages)
            sent.append({
                "units": len(units_in),
                "size": len(json.dumps(messages, ensure_ascii=False)),
            })
            fact_ids = [fid for unit in units_in for fid in unit["fact_ids"]]
            cards = [{
                "type": "location", "title": "地点卡", "purpose": "入口。",
                "fact_ids": fact_ids, "chapter_ids": [], "focus": [], "open_questions": [],
                "runtime_groups": [], "story_beat_ids": [], "situation": "",
                "case_scope": "current",
            }]
            if with_overview:
                cards.append({"type": "chapter_overview", "title": "概览",
                              "purpose": "本窗口的情势。", "fact_ids": fact_ids,
                              "chapter_ids": []})
            return _frame({"cards": cards, "open_questions": []})

        job = SimpleNamespace(id=f"artifact_job_oversize_{with_overview}", model_id="probe",
                              fake_model=False)
        results = []
        with tempfile.TemporaryDirectory(prefix="trpg-oversize-parent-") as directory:
            storage.DB_PATH = Path(directory) / "test.db"
            storage.init_db()
            # Between the two-unit window (~19.9k) and the three-unit window
            # (~20.5k), so a four-unit root still exceeds the boundary.
            with _patch.object(artifacts, "MAX_FACT_INPUT_CHARS", 20200):
                for _ in range(times):
                    try:
                        output = artifacts._run_global_plan_scope(
                            job, plan_index=1, profile=profile, bundle=bundle,
                            plan_units=units, plan_facts=facts, plan_fact_sizes=sizes,
                            plan_fact_tokens=tokens, plan_context=context,
                            chapter_scope=None, plan_client=_client(worker),
                        )
                    except artifacts.ArtifactGenerationError as error:
                        results.append(str(error))
                    else:
                        results.append(output)
            steps = storage.list_artifact_job_steps(job.id)
            root = next(item for item in steps if item["stage"] == "global_plan")
        return sent, results, root, steps

    # Missing overview: windows only, no full-range correction, root fails honestly.
    sent, results, root, steps = run_board(with_overview=False, times=1)
    # The compact contract fits more units in the same boundary, so a window may now
    # carry two units where the old contract forced one; what must hold is that every
    # request is a window and none is the oversize root.
    window_requests = [item for item in sent if item["units"] < 4]
    assert len(sent) == len(window_requests), sent
    assert all(item["units"] != 4 for item in sent), "an oversize root must not be sent"
    assert all(item["size"] <= 20200 for item in sent), sent
    assert isinstance(results[0], str) and ("总览卡" in results[0] or "overview" in results[0])
    assert root["status"] == "failed" and not root.get("output"), root
    assert any(a["request_source"] == "recovery" for a in root["attempts"]), root["attempts"]

    # Entering twice in the same database: the second entry reuses the windows.
    first_pass, _, _, _ = run_board(with_overview=False, times=1)
    sent, results, root, steps = run_board(with_overview=False, times=2)
    assert len(results) == 2 and all(isinstance(item, str) for item in results), results
    # The second entry reuses the windows the first one already paid for, whatever the
    # packing produced: the request count must not grow.
    assert len(sent) == len(first_pass), (sent, first_pass)
    assert all(item["units"] != 4 for item in sent), sent
    assert all(item["size"] <= 20200 for item in sent), sent
    assert root["status"] == "failed" and not root.get("output"), root
    recovery_attempts = [
        attempt for attempt in root["attempts"]
        if attempt["request_source"] == "recovery"
    ]
    assert len(recovery_attempts) == 2, root["attempts"]

    # A valid oversize scope completes in the same database and is cached.
    sent3_once, _, _, _ = run_board(with_overview=True, times=1)
    sent3, results3, root3, _ = run_board(with_overview=True, times=2)
    assert not isinstance(results3[0], str), results3[0]
    assert len(sent3) == len(sent3_once), (
        f"the second entry must reuse the windows, got {sent3}"
    )
    assert root3["status"] == "succeeded", root3

    # A normal (non-oversize) reply that fails schema still gets its one correction.
    profile, facts, units, sizes, tokens = _fixture(1)
    calls: list = []

    def correcting_worker(messages, temperature, max_tokens, **kwargs):
        calls.append(max_tokens)
        if len(calls) == 1:
            return _frame(_scope_leaf_payload(messages, case_scope="historical"))
        return _frame(_scope_leaf_payload(messages, case_scope="current"))

    with tempfile.TemporaryDirectory(prefix="trpg-correction-kept-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        _scope_call(
            SimpleNamespace(id="artifact_job_correction_kept", model_id="probe",
                            fake_model=False),
            profile, facts, units, sizes, tokens, _client(correcting_worker),
        )
    assert calls == [12000, 12000], calls


def main() -> None:
    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["module-prep"]
    facts = [
        SourceFact(
            id=f"fact_plan_split_{index}",
            text=f"Fact {index}",
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://global-plan-split", page=index + 1)],
        )
        for index in range(4)
    ]
    units = [
        {
            "id": f"unit_{index}",
            "kind": "scene",
            "title": f"场景 {index}",
            "summary": "一个可以独立调用的场景。",
            "fact_ids": [fact.id],
            "entity_keys": [],
            "relationship_hints": [],
            "open_questions": [],
        }
        for index, fact in enumerate(facts)
    ]
    sizes = {
        fact.id: len(json.dumps(artifacts._fact_payload_from_facts([fact]), ensure_ascii=False))
        for fact in facts
    }
    tokens = {
        fact.id: artifacts._estimate_tokens(
            json.dumps(artifacts._fact_payload_from_facts([fact]), ensure_ascii=False)
        )
        for fact in facts
    }
    job = SimpleNamespace(
        id="artifact_job_global_plan_split",
        model_id="probe",
        fake_model=False,
    )

    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-split-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        client = Client(facts)
        output = artifacts._run_global_plan_scope(
            job,
            plan_index=1,
            profile=profile,
            bundle=None,
            plan_units=units,
            plan_facts=facts,
            plan_fact_sizes=sizes,
            plan_fact_tokens=tokens,
            plan_context=None,
            chapter_scope=None,
            plan_client=client,
        )
        assert client.calls == 3, client.calls
        assert len(output["cards"]) == 2, output
        steps = storage.list_artifact_job_steps(job.id)
        root = next(step for step in steps if step["stage"] == "global_plan")
        children = [
            step for step in steps if step["stage"].startswith("global_plan_window_1_")
        ]
        assert root["status"] == "succeeded"
        assert root["attempts"][0]["status"] == "failed"
        assert root["attempts"][-1]["request_source"] == "recovery"
        assert len(children) == 2 and all(step["status"] == "succeeded" for step in children)

        resumed = artifacts._run_global_plan_scope(
            job,
            plan_index=1,
            profile=profile,
            bundle=None,
            plan_units=units,
            plan_facts=facts,
            plan_fact_sizes=sizes,
            plan_fact_tokens=tokens,
            plan_context=None,
            chapter_scope=None,
            plan_client=client,
        )
        assert resumed == output
        assert client.calls == 3

    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-resume-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        retry_client = Client(facts, fail_calls={3})
        retry_job = SimpleNamespace(
            id="artifact_job_global_plan_resume",
            model_id="probe",
            fake_model=False,
        )
        try:
            artifacts._run_global_plan_scope(
                retry_job,
                plan_index=1,
                profile=profile,
                bundle=None,
                plan_units=units,
                plan_facts=facts,
                plan_fact_sizes=sizes,
                plan_fact_tokens=tokens,
                plan_context=None,
                chapter_scope=None,
                plan_client=retry_client,
            )
        except RuntimeError as error:
            assert "window transport failed" in str(error), error
        else:
            raise AssertionError("a failed child window must fail the parent")
        assert retry_client.calls == 3

        resumed = artifacts._run_global_plan_scope(
            retry_job,
            plan_index=1,
            profile=profile,
            bundle=None,
            plan_units=units,
            plan_facts=facts,
            plan_fact_sizes=sizes,
            plan_fact_tokens=tokens,
            plan_context=None,
            chapter_scope=None,
            plan_client=retry_client,
        )
        assert len(resumed["cards"]) == 2
        assert retry_client.calls == 4, retry_client.calls

    with tempfile.TemporaryDirectory(prefix="trpg-global-plan-version-upgrade-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        upgraded_job = SimpleNamespace(
            id="artifact_job_global_plan_version_upgrade",
            model_id="probe",
            fake_model=False,
        )
        legacy_payload = {
            "prompt_version": artifacts.GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            "profile": profile.model_dump(mode="json"),
            "units": artifacts._compact_global_units(units),
            "fact_sizes": sizes,
            "fact_tokens": tokens,
            "structure": None,
        }
        legacy_client = Client(facts)
        try:
            artifacts._run_json_step(
                upgraded_job,
                stage="global_plan",
                step_index=1,
                input_payload=legacy_payload,
                input_refs={
                    "unit_ids": [unit["id"] for unit in units],
                    "fact_ids": [fact.id for fact in facts],
                },
                messages=[],
                client=legacy_client,
                max_tokens=artifacts.GLOBAL_PLAN_MAX_TOKENS,
                validator=lambda raw: raw,
            )
        except RuntimeError as error:
            assert "finish_reason=length" in str(error), error
        else:
            raise AssertionError("the legacy full-scope request must fail with length")

        expected_legacy_hash = artifacts._stable_hash({
            **{
                **legacy_payload,
                "prompt_version": artifacts.GLOBAL_PLAN_PREVIOUS_PROMPT_VERSION,
            },
            "_model_id": upgraded_job.model_id,
            "_fake_model": upgraded_job.fake_model,
        })
        assert storage.load_artifact_job_step(
            upgraded_job.id, "global_plan", 1, expected_legacy_hash
        ) is not None

        upgraded_client = Client(facts, fail_first=False)
        recovered = artifacts._run_global_plan_scope(
            upgraded_job,
            plan_index=1,
            profile=profile,
            bundle=None,
            plan_units=units,
            plan_facts=facts,
            plan_fact_sizes=sizes,
            plan_fact_tokens=tokens,
            plan_context=None,
            chapter_scope=None,
            plan_client=upgraded_client,
        )
        # The output contract changed (leaf_contract + fact_digest), so a v19 failure is
        # not a reusable seed for this request: the scope issues its own request instead
        # of replaying the old contract's failure path.
        assert len(recovered["cards"]) == 1, recovered
        assert upgraded_client.calls == 1, upgraded_client.calls
        assert storage.load_artifact_job_step(
            upgraded_job.id, "global_plan", 1, expected_legacy_hash
        ) is not None, "the historical v19 record must stay untouched"

    check_upgrade_recovery_failure_is_recorded()
    check_leaf_failure_can_be_retried()
    check_window_failures_stop_with_a_truthful_reason()
    check_request_cap_stops_the_split()
    check_retry_after_failed_upgrade_recovery()
    check_retry_resumes_windows_without_a_version_upgrade()
    check_recovery_attempt_does_not_borrow_a_child_request()
    check_plain_recovery_success_keeps_request_ownership()
    check_plain_recovery_failure_appends_a_recovery_attempt()
    check_retry_after_nested_recovery_failure_skips_ancestors()
    check_leaf_escalation_is_bounded_once()
    check_two_leaves_each_raise_once()
    check_leaf_window_uses_the_compact_contract()
    check_all_leaf_windows_satisfy_the_parent_contract()
    check_raise_is_one_step_and_retry_reuses_it()
    check_schema_failure_gets_the_bounded_correction()
    check_location_cards_need_source_supported_use()
    check_single_unit_scope_uses_the_leaf_contract()
    check_leaf_prompt_provides_fact_evidence()
    check_multi_unit_requests_carry_fact_evidence()
    check_oversize_input_is_split_or_refused_before_sending()
    check_oversize_scope_still_faces_parent_validation()
    check_only_the_failed_leaf_is_corrected()
    check_two_schema_failures_stop()
    check_correction_respects_the_shared_cap()
    check_fake_leaf_output_passes_parent_validation()
    check_leaf_semantic_fields_survive_the_merge()
    check_mixed_level_windows_merge()
    check_new_contract_does_not_reuse_an_old_window_cache()
    check_stop_reason_distinguishes_empty_from_truncated()
    print("PASS: truncated global planning splits, persists windows, and resumes failed children")
    print("PASS: prompt-version upgrades reuse an exact prior length failure without repeating its request")
    print("PASS: upgrade recovery failures are recorded on the parent step")
    print("PASS: a failed leaf is re-requested on retry instead of replaying its stored failure")
    print("PASS: continuous length replies stop early or at the cap with a truthful reason")
    print("PASS: a failed recovery keeps the step counters and names the windows that spent them")
    print("PASS: a retry after a failed upgrade recovery resumes windows instead of the full scope")
    print("PASS: a retry after a mid-window failure resumes windows without a version upgrade")
    print("PASS: stop reasons distinguish an empty reply from a truncated one")
    print("PASS: a parent recovery attempt reports the step counters, not a child's request")
    print("PASS: plain recovery records its own attempt on success and on failure")
    print("PASS: a retry after a nested recovery failure resumes leaves, not ancestors")
    print("PASS: an unsplittable window raises its budget once, only on a length stop")
    print("PASS: two leaves raise independently; a repeated truncation stops without a second raise")
    print("PASS: a leaf uses the compact plan contract and rejects invented chapter ids")
    print("PASS: all-leaf windows still satisfy the merged parent contract on both boards")
    print("PASS: both budgets stay in one step and a retry starts from the larger one")
    print("PASS: a schema failure is corrected inside its step instead of being split away")
    print("PASS: the location contract requires source-supported use and forbids name-only cards")
    print("PASS: a one-unit production scope sends the compact leaf contract with its facts")
    print("PASS: every fact reaches the leaf with its text, and its content is its identity")
    print("PASS: only the failed leaf is corrected; the successful sibling stays cached")
    print("PASS: a second schema failure stops the step at the shared cap")
    print("PASS: the FakeLLM leaf answer satisfies the merged parent validation")
    print("PASS: leaf case scope, groups, beat links and branch overviews survive the merge")
    print("PASS: a window success and leaf splits merge together")
    print("PASS: a v19 window success never answers a v20 leaf and v20 caches normally")


if __name__ == "__main__":
    main()
