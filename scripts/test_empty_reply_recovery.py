"""A failed generation attempt must explain itself, and must stop on its own.

These checks pin the empty-reply policy that the live trial needed: the worker reports
non-sensitive response metadata, an empty reply with finish_reason=length is recovered
by bounded policies (at most one explicit budget raise, plus the local-digest-only
fallback from an unset reasoning hint),
a reply that still arrives empty reports the budget, the recoveries and the usage it
actually observed, and a logical step cannot issue unbounded requests.  Every scenario
here stubs the worker, so nothing touches a real model or a real database.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import llm, storage  # noqa: E402
from backend.app.llm_worker import _usage_summary, build_frame  # noqa: E402
from backend.app.persistent_step import run_json_step, stable_hash  # noqa: E402

BASE = {"base_url": "http://127.0.0.1:3050", "api_key": "unused-key", "model": "probe"}


def frame(content=None, finish=None, usage=None, *, reasoning_content=None,
          refusal_present=False, tool_call_count=0) -> dict:
    return {
        "content": content,
        "finish_reason": finish,
        "content_length": len(content) if isinstance(content, str) else None,
        "reasoning_content_present": reasoning_content is not None,
        "reasoning_content_length": (
            len(reasoning_content) if isinstance(reasoning_content, str) else None
        ),
        "refusal_present": refusal_present,
        "tool_call_count": tool_call_count,
        "usage": usage,
    }


def error_frame(status: int) -> dict:
    return {
        "error": f"HTTPError: status {status}",
        "kind": "http",
        "status": status,
        "body": json.dumps({"error": {"message": f"status {status}"}}),
        "error_type": "HTTPError",
    }


class ScriptedWorker:
    """A worker stub that answers from a script and records what it was asked."""

    def __init__(self, client: llm.LLMClient, *frames):
        self.client = client
        self.frames = list(frames)
        self.budgets: list[int] = []
        self.efforts: list[str] = []

    def __call__(self, messages, temperature, max_tokens, stream=False, request_timeout=None):
        self.budgets.append(max_tokens)
        self.efforts.append(self.client.reasoning_effort)
        item = self.frames.pop(0) if self.frames else frame("unexpected extra request")
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def calls(self) -> int:
        return len(self.budgets)


def make_client(*frames, effort: str = "high") -> tuple[llm.LLMClient, ScriptedWorker]:
    client = llm.LLMClient({**BASE, "reasoning_effort": effort})
    worker = ScriptedWorker(client, *frames)
    client._worker_call = worker  # type: ignore[method-assign]
    return client, worker


def no_sensitive_fields(payload) -> None:
    blob = json.dumps(payload, ensure_ascii=False).casefold()
    for forbidden in ("api_key", "authorization", "bearer", "http://", "https://", "unused-key"):
        assert forbidden not in blob, f"diagnostics must not carry {forbidden}: {payload}"


def check_worker_frame_metadata() -> None:
    reported = build_frame(
        {
            "choices": [{"finish_reason": "length", "message": {
                "content": None, "reasoning_content": "PRIVATE_THOUGHT",
            }}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 0,
                "total_tokens": 10,
                "completion_tokens_details": {"reasoning_tokens": 9},
                "prompt_tokens_details": {"cached_tokens": 4},
            },
        },
        None,
    )
    assert set(reported) == {
        "content", "finish_reason", "content_length", "reasoning_content_present",
        "reasoning_content_length", "refusal_present", "tool_call_count", "usage",
    }, reported
    assert reported["content"] is None and reported["finish_reason"] == "length"
    assert reported["content_length"] is None
    assert reported["reasoning_content_present"] is True
    assert reported["reasoning_content_length"] == len("PRIVATE_THOUGHT")
    assert reported["refusal_present"] is False
    assert reported["tool_call_count"] == 0
    assert reported["usage"] == {
        "prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10, "reasoning_tokens": 9,
    }, reported["usage"]
    no_sensitive_fields(reported)
    assert "PRIVATE_THOUGHT" not in json.dumps(reported)

    text = build_frame(
        {"choices": [{"finish_reason": "length", "message": {
            "content": "abc", "reasoning_content": "think", "tool_calls": [{"id": "x"}],
        }}]},
        "abc",
    )
    assert text["content_length"] == 3 and text["usage"] is None
    assert text["reasoning_content_present"] is True
    assert text["reasoning_content_length"] == 5
    assert text["tool_call_count"] == 1
    assert "think" not in json.dumps(text)

    # An upstream that reports nothing, or nothing numeric, yields no usage at all.
    assert _usage_summary({}) is None
    assert _usage_summary({"usage": {"unexpected": "value"}}) is None
    assert build_frame({}, None)["finish_reason"] is None

    # A reasoning/tool-only response may omit content entirely. The worker frame must
    # preserve diagnostic metadata instead of turning that shape into KeyError.
    tool_only = build_frame(
        {"choices": [{"finish_reason": "length", "message": {
            "reasoning_content": "thinking", "tool_calls": [{"id": "call-1"}],
        }}]},
        None,
    )
    assert tool_only["content"] is None
    assert tool_only["reasoning_content_present"] is True
    assert tool_only["reasoning_content_length"] == 8
    assert tool_only["tool_call_count"] == 1


def check_one_bounded_recovery_succeeds() -> None:
    reasoning = {"prompt_tokens": 7000, "completion_tokens": 0, "reasoning_tokens": 7000}
    client, worker = make_client(
        frame("", "length", reasoning),
        frame('{"ok": 1}', "stop", {"prompt_tokens": 20, "completion_tokens": 5}),
        effort="high",
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        output = client.chat_json(
            [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
        )
    assert output == {"ok": 1}, output
    assert worker.calls == 2, worker.budgets
    assert worker.budgets == [7000, 12000], worker.budgets
    assert worker.efforts == ["high", "high"], worker.efforts
    assert [entry["outcome"] for entry in client.last_request_diagnostics] == [
        "empty", "content",
    ], client.last_request_diagnostics
    assert client.last_recovery_actions == ["把输出预算从 7000 提到 12000"], client.last_recovery_actions
    first = client.last_request_diagnostics[0]
    assert first["usage"]["reasoning_tokens"] == 7000, first
    assert isinstance(first["duration_ms"], int) and first["duration_ms"] >= 0, first
    no_sensitive_fields(client.last_request_diagnostics)


def check_unset_effort_gets_one_local_digest_fallback() -> None:
    """A reasoning model may spend the default budget without writing JSON."""
    client, worker = make_client(
        frame("", "length", {"completion_tokens": 12000}),
        frame('{"ok": 7}', "stop", {"completion_tokens": 20}),
        effort="",
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        output = client.chat_json(
            [{"role": "user", "content": "hi"}],
            max_tokens=12000,
            fallback_reasoning_effort="low",
        )
    assert output == {"ok": 7}, output
    assert worker.calls == 2, worker.budgets
    assert worker.budgets == [12000, 12000], worker.budgets
    assert worker.efforts == ["", "low"], worker.efforts
    assert [entry["reasoning_effort"] for entry in client.last_request_diagnostics] == [
        "", "low",
    ], client.last_request_diagnostics
    assert client.last_recovery_actions == ["将 reasoning_effort 从未设置调整为 low"]
    assert client.reasoning_effort == "", "the fallback must not change saved configuration"
    no_sensitive_fields(client.last_request_diagnostics)


def check_truncated_json_gets_local_effort_fallback() -> None:
    truncated = frame('{"units": [{"fact_ids": ["f1"]},', "length")
    client, worker = make_client(
        truncated,
        frame('{"ok": 6}', "stop"),
        effort="",
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        output = client.chat_json(
            [{"role": "user", "content": "hi"}],
            max_tokens=12000,
            fallback_reasoning_effort="low",
        )
    assert output == {"ok": 6}, output
    assert worker.calls == 2, worker.budgets
    assert worker.efforts == ["", "low"], worker.efforts
    assert client.last_recovery_actions == ["将 reasoning_effort 从未设置调整为 low"]


def check_explicit_effort_is_never_overridden() -> None:
    client, worker = make_client(frame("", "length"), effort="high")
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json(
                [{"role": "user", "content": "hi"}],
                max_tokens=12000,
                fallback_reasoning_effort="low",
            )
        except RuntimeError as error:
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("the explicit effort response must fail without fallback")
    assert worker.calls == 1, worker.budgets
    assert worker.efforts == ["high"], worker.efforts
    assert "reasoning_effort=high" in message, message
    assert client.last_recovery_actions == []


def check_step_passes_local_effort_fallback() -> None:
    db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(prefix="slice-a-effort-fallback-") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "fallback.db"
        try:
            storage.init_db()
            job = SimpleNamespace(id="artifact_job_effort_fallback", model_id="m", fake_model=False)
            client, worker = make_client(
                frame("", "length"), frame('{"ok": 8}', "stop"), effort=""
            )
            output = run_json_step(
                job,
                stage="local_digest",
                step_index=1,
                input_payload={"prompt_version": "fallback-v1"},
                input_refs={"fact_ids": ["f1"]},
                messages=[{"role": "user", "content": "hi"}],
                client=client,
                max_tokens=12000,
                validator=validator,
                chat_kwargs={"fallback_reasoning_effort": "low"},
            )
            assert output == {"ok": 8}, output
            assert worker.efforts == ["", "low"], worker.efforts
            expected_hash = stable_hash({
                "prompt_version": "fallback-v1", "_model_id": "m", "_fake_model": False,
            })
            step = storage.load_artifact_job_step(job.id, "local_digest", 1, expected_hash)
            assert step and step["status"] == "succeeded", step
            assert step["attempts"][0]["diagnostics"]["recoveries"] == [
                "将 reasoning_effort 从未设置调整为 low"
            ]
        finally:
            storage.DB_PATH = db_path


def check_terminal_empty_length_states_the_facts() -> None:
    client, worker = make_client(
        frame(
            "", "length", {"completion_tokens": 0, "reasoning_tokens": 7000},
            reasoning_content="PRIVATE_ANALYSIS",
        ),
        frame("", "length", None, reasoning_content="PRIVATE_ANALYSIS"),
        effort="high",
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json(
                [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
            )
        except RuntimeError as error:
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("an empty reply must fail the step")

    assert worker.calls == 2, "the recovery must be bounded, not endless"
    for expected in (
        "空正文",
        "finish_reason=length",
        "max_tokens=12000",          # the budget the last request actually used
        "reasoning_effort=high",
        "返回正文长度 0 字符",
        "reasoning_content，长度 16 字符",
        "上游没有报告用量",           # the raised-budget frame reported none
        "把输出预算从 7000 提到 12000",
        "现已停止",                   # and that it stopped
    ):
        assert expected in message, (expected, message)
    for forbidden in (
        "该模型会先进行推理",         # the client never observed the model's reasoning
        "请提高 max_tokens",          # the UI has no such setting to offer
        "改用不会推理的模型",
    ):
        assert forbidden not in message, (forbidden, message)


def check_no_recovery_available_stops_at_once() -> None:
    client, worker = make_client(
        frame("", "length"), frame('{"ok": 2}', "stop"), effort=""
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json([{"role": "user", "content": "hi"}], max_tokens=7000)
        except RuntimeError as error:
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("an empty reply must fail the step")
    assert worker.calls == 1, "with nothing to recover, no extra request may be sent"
    assert "没有可用的恢复手段" in message, message
    assert "max_tokens=7000" in message, message

    # Diagnostics belong to one step: a later call starts its own list.
    client._worker_call = ScriptedWorker(client, frame('{"ok": 3}', "stop"))  # type: ignore[method-assign]
    assert client.chat_json([{"role": "user", "content": "hi"}]) == {"ok": 3}
    assert len(client.last_request_diagnostics) == 1, client.last_request_diagnostics


def check_length_with_body_is_reported_separately() -> None:
    truncated = frame('{"title": "x", "items": [', "length")
    client, worker = make_client(truncated, truncated, effort="")
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json([{"role": "user", "content": "hi"}], max_tokens=7000)
        except RuntimeError as error:
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("invalid JSON must fail the step")
    assert worker.calls == 2, "a truncated body still gets the one JSON correction"
    assert "finish_reason=length" in message and "被截断" in message and "max_tokens=7000" in message, message
    assert "空正文" not in message, message

    client, worker = make_client(frame("", None), frame("   ", "stop"), effort="")
    try:
        client.chat_json([{"role": "user", "content": "hi"}], max_tokens=7000)
    except RuntimeError as error:
        message = str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("two empty replies must fail the step")
    assert worker.calls == 2, worker.budgets
    assert "连续两次返回空响应" in message and "没有报告 finish_reason=length" in message, message


def check_http_retries_unchanged() -> None:
    sleeps: list[float] = []
    client, worker = make_client(
        error_frame(429), frame("recovered", "stop"), error_frame(503), frame("also", "stop"),
        frame("plain", "stop"), effort="",
    )
    with patch.object(llm.time, "sleep", sleeps.append):
        assert client.chat([{"role": "user", "content": "hi"}]) == "recovered"
        assert [entry["outcome"] for entry in client.last_request_diagnostics] == [
            "http_retry", "content",
        ], client.last_request_diagnostics
        assert client.last_request_diagnostics[0]["status"] == 429
        assert client.chat([{"role": "user", "content": "hi"}]) == "also"
        assert client.last_request_diagnostics[0]["status"] == 503
        assert client.chat([{"role": "user", "content": "hi"}]) == "plain"
    assert worker.calls == 5, worker.budgets
    assert len(sleeps) == 2 and all(value > 0 for value in sleeps), sleeps

    # A budget the upstream rejects stops the step instead of retrying it or quietly
    # keeping the smaller budget: the reply is a real HTTP answer, not an empty one.
    client, worker = make_client(
        frame("", "length"), error_frame(400), effort=""
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json(
                [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
            )
        except RuntimeError as error:
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("a rejected budget must fail the step")
    assert worker.calls == 2 and worker.budgets == [7000, 12000], worker.budgets
    assert "HTTP 400" in message, message
    last = client.last_request_diagnostics[-1]
    assert last["outcome"] == "error" and last["status"] == 400, last
    assert last["max_tokens"] == 12000, last

    # A non-retryable status is not retried.
    client, worker = make_client(error_frame(400), frame("unused", "stop"), effort="")
    try:
        client.chat([{"role": "user", "content": "hi"}])
    except RuntimeError as error:
        assert "HTTP 400" in str(error), error
    else:  # pragma: no cover - defensive
        raise AssertionError("HTTP 400 must fail")
    assert worker.calls == 1, worker.budgets


def check_request_cap_counts_every_kind() -> None:
    client, worker = make_client(
        error_frame(429), error_frame(429), frame("", "length"),
        error_frame(429), error_frame(429), frame("not json", "stop"),
        error_frame(429), error_frame(429), error_frame(429),
        effort="high",
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json(
                [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
            )
        except RuntimeError as error:
            message = str(error)
        else:  # pragma: no cover - defensive
            raise AssertionError("the step must end instead of retrying forever")
    assert worker.calls == llm.MAX_REQUESTS_PER_STEP, worker.budgets
    assert f"已发出 {llm.MAX_REQUESTS_PER_STEP} 次模型请求" in message, message
    assert "含 HTTP 重试" in message, message
    assert len(client.last_request_diagnostics) == llm.MAX_REQUESTS_PER_STEP


def check_step_cap_spans_correction() -> None:
    """The declared cap belongs to the generation step, not to one chat_json call.

    The reviewer's probe: a transient HTTP status, a reply that fails deterministic
    validation, and the correction resend used to add up to nine worker requests while
    each layer checked its own limit.
    """
    transient = error_frame(429)
    client, worker = make_client(
        transient, transient, frame("not-json", "stop"),
        transient, transient, frame('{"ok": 0}', "stop"),
        transient, transient, frame('{"ok": 1}', "stop"),
        effort="",
    )

    def validator(raw):
        if not isinstance(raw, dict) or raw.get("ok") != 1:
            raise ValueError("synthetic deterministic failure")
        return raw

    db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(prefix="slice-a-cap-") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "cap.db"
        try:
            storage.init_db()
            job = SimpleNamespace(id="artifact_job_cap", model_id="m", fake_model=False)
            with patch.object(llm.time, "sleep", lambda _seconds: None):
                try:
                    run_json_step(
                        job,
                        stage="local_digest",
                        step_index=1,
                        input_payload={"synthetic": True},
                        input_refs={},
                        messages=[{"role": "user", "content": "hi"}],
                        client=client,
                        max_tokens=7000,
                        validator=validator,
                    )
                except RuntimeError as error:
                    message = str(error)
                else:  # pragma: no cover - defensive
                    raise AssertionError("the step must stop at its request cap")
            assert worker.calls == llm.MAX_REQUESTS_PER_STEP, worker.budgets
            assert f"已发出 {llm.MAX_REQUESTS_PER_STEP} 次模型请求" in message, message
            step = storage.load_artifact_job_step(
                job.id, "local_digest", 1,
                stable_hash({"synthetic": True, "_model_id": "m", "_fake_model": False}),
            )
            recorded = sum(
                len((attempt.get("diagnostics") or {}).get("requests") or [])
                for attempt in step["attempts"]
            )
            assert recorded == worker.calls, (recorded, worker.calls)
            assert step["attempts"][-1]["diagnostics"]["step_requests"] == worker.calls
        finally:
            storage.DB_PATH = db_path


def check_truncated_reply_is_not_accepted() -> None:
    """A reply that parses only after closing missing structure is not a success.

    The reviewer's probe: a digest reply truncated inside open_questions is repaired to
    an empty list and the real digest validator then accepts it, so lost model content
    could be stored as a completed step.
    """
    from backend.app.artifacts import _validate_local_digest
    from backend.domain import SourceFact, SourceRef

    fact = SourceFact(
        id="fact_truncation_letter",
        text="合成信件：钥匙在北塔地板下。",
        kind="handout",
        visibility="explicit",
        evidence_status="source_fact",
        source_refs=[SourceRef(file="fixture://truncation", page=1)],
    )
    unit = {
        "kind": "scene",
        "title": "合成地点",
        "summary": "合成线索",
        "fact_ids": [fact.id],
        "entity_keys": [],
        "relationship_hints": [],
        "open_questions": [],
    }
    truncated = (
        '{"units":[' + json.dumps(unit, ensure_ascii=False)
        + '],"open_questions":["known gap","unfinished'
    )

    # The repaired payload really is accepted by the production validator: that is why
    # the client must not hand it over as a complete response.
    assert _validate_local_digest(llm.parse_json(truncated), [fact], batch_index=1)

    client, worker = make_client(frame(truncated, "length"), effort="")
    try:
        client.chat_json([{"role": "user", "content": "hi"}], max_tokens=7000)
    except RuntimeError as error:
        message = str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("a repaired truncated reply must not be accepted")
    assert worker.calls == 1, worker.budgets
    assert "finish_reason=length" in message and "被截断" in message, message
    assert "max_tokens=7000" in message, message

    # When the caller allows one budget raise, the client may retry once at the raised
    # budget instead of accepting the repair; it still never returns repaired content.
    client, worker = make_client(
        frame(truncated, "length"), frame(truncated, "length"), effort=""
    )
    try:
        client.chat_json(
            [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
        )
    except RuntimeError as error:
        message = str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("a repaired truncated reply must not be accepted")
    assert worker.budgets == [7000, 12000], worker.budgets
    assert "把输出预算从 7000 提到 12000" in message, message

    # A complete response that happens to report length is still a valid answer.
    complete = json.dumps({"units": [unit], "open_questions": []}, ensure_ascii=False)
    client, worker = make_client(frame(complete, "length"), effort="")
    assert client.chat_json([{"role": "user", "content": "hi"}], max_tokens=7000)["units"]


def check_one_budget_raise_per_step() -> None:
    """One logical step may raise the budget once and never fall back to the old one."""
    client, worker = make_client(
        frame("", "length"), frame('{"ok": 1}', "stop"), effort=""
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        assert client.chat_json(
            [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
        ) == {"ok": 1}
    assert worker.budgets == [7000, 12000], worker.budgets
    raises = [action for action in client.last_recovery_actions if "12000" in action]
    assert len(raises) == 1, client.last_recovery_actions

    # The reviewer's probe: a JSON correction inside the same step must not start over
    # from the budget that already failed.
    client, worker = make_client(
        frame("", "length"), frame("not-json", "stop"), frame("", "length"),
        frame('{"ok": 2}', "stop"), effort="",
    )
    with patch.object(llm.time, "sleep", lambda _seconds: None):
        try:
            client.chat_json(
                [{"role": "user", "content": "hi"}], max_tokens=7000, escalate_max_tokens=12000
            )
        except RuntimeError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("an empty reply after the raised budget must fail")
    assert worker.budgets[0] == 7000, worker.budgets
    assert 7000 not in worker.budgets[1:], worker.budgets
    assert worker.budgets.count(12000) >= 1, worker.budgets
    assert sum(1 for action in client.last_recovery_actions if "12000" in action) <= 1, (
        client.last_recovery_actions
    )


def validator(raw):
    if not isinstance(raw, dict) or "ok" not in raw:
        raise ValueError("需要包含 ok 字段")
    return raw


def check_step_records_diagnostics_and_keeps_identity() -> None:
    db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(prefix="slice-a-step-") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "slice-a.db"
        try:
            storage.init_db()
            job = SimpleNamespace(id="artifact_job_slice_a", model_id="m", fake_model=False)
            payload = {"prompt_version": "v1", "facts": ["f1"]}
            kwargs = dict(
                stage="local_digest",
                step_index=1,
                input_payload=payload,
                input_refs={"fact_ids": ["f1"]},
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=7000,
                validator=validator,
                chat_kwargs={"escalate_max_tokens": 12000},
            )

            client, worker = make_client(
                frame(
                    "", "length", {"reasoning_tokens": 7000},
                    reasoning_content="PRIVATE_PERSISTED_THOUGHT",
                ),
                frame("", "length"),
                effort="high",
            )
            with patch.object(llm.time, "sleep", lambda _seconds: None):
                try:
                    run_json_step(job, client=client, **kwargs)
                except RuntimeError as error:
                    message = str(error)
                else:  # pragma: no cover - defensive
                    raise AssertionError("the step must fail")
            assert "max_tokens=12000" in message, message

            expected_hash = stable_hash(
                {**payload, "_model_id": job.model_id, "_fake_model": job.fake_model}
            )
            step = storage.load_artifact_job_step(job.id, "local_digest", 1, expected_hash)
            assert step is not None, "the failed attempt must be queryable"
            assert step["status"] == "failed" and step["job_id"] == job.id, step
            attempt = step["attempts"][0]
            assert attempt["status"] == "failed" and attempt["request_source"] == "model", attempt
            diagnostics = attempt["diagnostics"]
            assert diagnostics["requests"] and len(diagnostics["requests"]) == 2, diagnostics
            assert [item["max_tokens"] for item in diagnostics["requests"]] == [7000, 12000]
            assert diagnostics["requests"][0]["reasoning_content_length"] == len(
                "PRIVATE_PERSISTED_THOUGHT"
            )
            assert diagnostics["recoveries"] == ["把输出预算从 7000 提到 12000"]
            assert attempt["error"].startswith("上游") or "空正文" in attempt["error"], attempt
            no_sensitive_fields(attempt)

            # Retrying the same logical step keeps task ownership, reuses the stored
            # attempt record, and reuses the successful steps once one exists.
            retry_client, retry_worker = make_client(frame('{"ok": 9}', "stop"), effort="")
            assert run_json_step(job, client=retry_client, **kwargs) == {"ok": 9}
            step = storage.load_artifact_job_step(job.id, "local_digest", 1, expected_hash)
            assert [item["status"] for item in step["attempts"]] == ["failed", "succeeded"], step
            assert [item["attempt"] for item in step["attempts"]] == [1, 2], step
            assert all(item["model_id"] == job.model_id for item in step["attempts"]), step
            assert step["job_id"] == job.id, "the retry must keep the same task"
            assert retry_worker.calls == 1

            idle, idle_worker = make_client(frame("unused", "stop"), effort="")
            assert run_json_step(job, client=idle, **kwargs) == {"ok": 9}
            assert idle_worker.calls == 0, "a succeeded step must never be requested twice"

            # Budget options are not part of the semantic input: without them the same
            # step is still the same stored step, so nothing is regenerated or cached
            # differently.
            same_identity, same_worker = make_client(frame("unused", "stop"), effort="")
            assert run_json_step(
                job,
                client=same_identity,
                **{**kwargs, "chat_kwargs": None},
            ) == {"ok": 9}
            assert same_worker.calls == 0, "budget options must not change the step hash"
        finally:
            storage.DB_PATH = db_path


def check_budget_resets_between_steps() -> None:
    """The shared budget belongs to one step: independent steps and cache hits reset it."""
    transient = error_frame(429)
    client, worker = make_client(
        transient, transient, frame("not-json", "stop"),
        transient, transient, frame('{"ok": 0}', "stop"),
        transient, transient, frame('{"ok": 1}', "stop"),
        effort="",
    )

    def strict(raw):
        if not isinstance(raw, dict) or raw.get("ok") != 1:
            raise ValueError("synthetic deterministic failure")
        return raw

    ok_client, ok_worker = make_client(frame('{"ok": 1}', "stop"), effort="")
    db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(prefix="slice-a-reset-") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "reset.db"
        try:
            storage.init_db()
            job = SimpleNamespace(id="artifact_job_reset", model_id="m", fake_model=False)
            common = dict(
                input_payload={"synthetic": True},
                input_refs={},
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=7000,
            )
            with patch.object(llm.time, "sleep", lambda _seconds: None):
                try:
                    run_json_step(
                        job, stage="local_digest", step_index=1,
                        client=client, validator=strict, **common,
                    )
                except RuntimeError as error:
                    assert "已发出" in str(error), str(error)
                else:  # pragma: no cover - defensive
                    raise AssertionError("the exhausted step must fail")
            assert worker.calls == llm.MAX_REQUESTS_PER_STEP, worker.budgets

            # A different step is unaffected by the previous step's spent budget.
            assert run_json_step(
                job, stage="local_digest", step_index=2,
                client=ok_client, validator=validator, **common,
            ) == {"ok": 1}
            assert ok_worker.calls == 1
            step = storage.load_artifact_job_step(
                job.id, "local_digest", 2,
                stable_hash({"synthetic": True, "_model_id": "m", "_fake_model": False}),
            )
            diagnostics = step["attempts"][0]["diagnostics"]
            assert diagnostics["step_requests"] == 1, diagnostics
            assert diagnostics["step_request_limit"] == llm.MAX_REQUESTS_PER_STEP, diagnostics

            # A cache hit costs nothing: reuse must not consume the new step's budget.
            idle, idle_worker = make_client(frame("unused", "stop"), effort="")
            assert run_json_step(
                job, stage="local_digest", step_index=2,
                client=idle, validator=validator, **common,
            ) == {"ok": 1}
            assert idle_worker.calls == 0, "a reused step must not issue a request"
        finally:
            storage.DB_PATH = db_path


def check_nested_coverage_budget_and_diagnostics() -> None:
    """Coverage shares the parent cap and preserves diagnostics through transport.

    The parent and repair remain separate persisted records. Their request lists
    must not be duplicated; their counters describe the same recovery chain.
    """
    from backend.app import artifacts
    from backend.app.planning_transport import PlanningTransport
    from backend.domain import SourceFact, SourceRef

    facts = [SourceFact(
        id=f"fact_{name}", kind="clue", visibility="explicit",
        evidence_status="source_fact", text=f"Synthetic {name}",
        source_refs=[SourceRef(file="fixture://coverage-budget", page=1)],
    ) for name in ("first", "second")]
    plan = {"cards": [{
        "type": "location", "title": "Synthetic", "purpose": "Synthetic pressure",
        "fact_ids": ["fact_first"],
    }]}
    decisions = {"decisions": [{
        "fact_id": "fact_second", "chapter_id": None,
        "destination": "global_reference", "card_index": None, "reason": "Synthetic",
    }]}

    def validator(raw):
        if "fact_second" not in raw.get("global_reference_fact_ids", []):
            raise artifacts.PlanCoverageError({}, ["fact_second"], facts)
        return raw

    with tempfile.TemporaryDirectory(prefix="nested-coverage-budget-") as directory, \
            patch.object(storage, "DB_PATH", Path(directory) / "steps.db"), \
            patch.object(llm.time, "sleep", lambda _seconds: None):
        storage.init_db()
        for scenario in ("exhausted", "success", "failed_repair"):
            gateway, _ = make_client(effort="")
            transport = PlanningTransport(gateway, [fact.id for fact in facts])
            plan_frame = frame(json.dumps(transport._translate(plan, transport.encode_ids)), "stop")
            repair_frame = frame(json.dumps(transport._translate(decisions, transport.encode_ids)), "stop")
            if scenario == "exhausted":
                transient = error_frame(429)
                replies = [
                    transient, transient, frame("not-json", "stop"),
                    transient, transient, frame("", "stop"),
                    transient, plan_frame, repair_frame,
                ]
            elif scenario == "failed_repair":
                replies = [plan_frame, error_frame(400)]
            else:
                replies = [plan_frame, repair_frame]
            worker = ScriptedWorker(gateway, *replies)
            gateway._worker_call = worker
            job = SimpleNamespace(
                id=f"artifact_job_nested_{scenario}", model_id="m", fake_model=False,
            )
            arguments = dict(
                job=job, stage="global_plan", step_index=1,
                input_payload={"synthetic": scenario}, input_refs={},
                messages=[{"role": "user", "content": "Synthetic plan"}],
                client=transport, max_tokens=12000, validator=validator,
            )
            try:
                output = artifacts._run_json_step(**arguments)
            except artifacts.PlanCoverageError as error:
                assert scenario != "success", str(error)
                assert "8 次模型请求" in str(error) if scenario == "exhausted" else "HTTP 400" in str(error)
            else:
                assert scenario == "success"
                assert output["global_reference_fact_ids"] == ["fact_second"]
            steps = {step["stage"]: step for step in storage.list_artifact_job_steps(job.id)}
            parent = steps["global_plan"]["attempts"][-1]["diagnostics"]
            child = steps["global_plan_coverage"]["attempts"][-1]["diagnostics"]
            expected = llm.MAX_REQUESTS_PER_STEP if scenario == "exhausted" else 2
            assert worker.calls == parent["step_requests"] == child["step_requests"] == expected
            assert len(parent["requests"]) + len(child["requests"]) == expected
            assert parent["requests"][0]["max_tokens"] == 12000
            assert not child["requests"] if scenario == "exhausted" else child["requests"][0]["max_tokens"] == 8000
            no_sensitive_fields(parent)
            no_sensitive_fields(child)

            if scenario == "success":
                assert artifacts._run_json_step(**arguments) == output
                assert worker.calls == 2, "cached parent and coverage must issue no requests"
            elif scenario == "failed_repair":
                # Manual retry starts a new run budget but reuses the saved plan.
                worker.frames = [repair_frame]
                assert artifacts._run_json_step(**arguments)["global_reference_fact_ids"] == ["fact_second"]
                assert worker.calls == 3, "retry must repair the saved plan without regenerating it"
                steps = {step["stage"]: step for step in storage.list_artifact_job_steps(job.id)}
                resumed = steps["global_plan"]["attempts"][-1]
                assert resumed["request_source"] == "saved_response"
                assert resumed["diagnostics"]["requests"] == []
                assert resumed["diagnostics"]["step_requests"] == 1
                assert steps["global_plan_coverage"]["attempts"][-1]["diagnostics"]["step_requests"] == 1
                artifacts._run_json_step(**arguments)
                assert worker.calls == 3



def check_truncation_escalation_respects_budget_identity() -> None:
    """A truncated reply's raise belongs to its prompt identity, not the whole step."""
    # Repairable truncation: closing the missing bracket lets the parser succeed, which
    # is exactly the branch that used to ignore the identity.
    truncated = '{"title": "x", "items": [1, 2'

    script = [
        frame(truncated, "length"),
        frame('{"title": "ok", "items": []}', "stop"),
        frame(truncated, "length"),
        frame('{"title": "other", "items": []}', "stop"),
    ]
    client, worker = make_client(*script, effort="")
    state = llm.RequestBudget(max_requests=8)
    identity = "global_plan:1:leaf"

    with patch.object(llm.time, "sleep", lambda _seconds: None):
        first = client.chat_json(
            [{"role": "user", "content": "hi"}],
            step_budget=state,
            budget_identity=identity,
            max_tokens=7000,
            escalate_max_tokens=12000,
        )
        assert first["title"] == "ok", first
        assert worker.budgets == [7000, 12000], worker.budgets
        assert state.escalations == {identity: 12000}, state.escalations
        assert state.floor_for(identity) == 12000, state.floor_for(identity)

        # The same identity cannot raise a second time.
        try:
            client.chat_json(
                [{"role": "user", "content": "hi"}],
                step_budget=state,
                budget_identity=identity,
                max_tokens=7000,
                escalate_max_tokens=12000,
            )
        except RuntimeError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError("a second truncation for the same identity must fail")
        assert worker.budgets == [7000, 12000, 12000], worker.budgets
        assert state.escalations == {identity: 12000}, state.escalations

        # A different window keeps its ordinary starting budget.
        other = client.chat_json(
            [{"role": "user", "content": "hi"}],
            step_budget=state,
            budget_identity="global_plan:1:1.2",
            max_tokens=7000,
            escalate_max_tokens=12000,
        )
        assert other["title"] == "other", other
        assert worker.budgets[-1] == 7000, worker.budgets
        assert state.diagnostics()["step_escalations"] == {identity: 12000}, state.diagnostics()


def main() -> None:
    check_worker_frame_metadata()
    check_one_bounded_recovery_succeeds()
    check_unset_effort_gets_one_local_digest_fallback()
    check_truncated_json_gets_local_effort_fallback()
    check_explicit_effort_is_never_overridden()
    check_step_passes_local_effort_fallback()
    check_terminal_empty_length_states_the_facts()
    check_no_recovery_available_stops_at_once()
    check_length_with_body_is_reported_separately()
    check_http_retries_unchanged()
    check_request_cap_counts_every_kind()
    check_step_cap_spans_correction()
    check_truncated_reply_is_not_accepted()
    check_one_budget_raise_per_step()
    check_budget_resets_between_steps()
    check_truncation_escalation_respects_budget_identity()
    check_step_records_diagnostics_and_keeps_identity()
    check_nested_coverage_budget_and_diagnostics()
    print(
        "PASS: worker metadata, one bounded empty-reply recovery, provable failure copy, "
        "unchanged HTTP retries, a per-step request cap, identity-scoped raises and "
        "stored step diagnostics"
    )


if __name__ == "__main__":
    main()
