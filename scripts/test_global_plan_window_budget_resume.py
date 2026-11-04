"""A retry after the window recovery exhausted the shared budget resumes the windows.

It must not pay for the full-scope request again: the current-version windows that already
succeeded are reused, and only the windows still missing are asked for.  The checks reuse
the split-recovery fixture so both files describe the same scope shape.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "split_recovery", ROOT / "scripts" / "test_global_plan_split_recovery.py"
)
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)


def _failed_record(limit: int, used: int, step_ids: list[str], error: str) -> dict:
    return {
        "status": "failed",
        "attempts": [
            {
                "error": f"全局规划失败：{error}",
                "diagnostics": {
                    "step_request_limit": limit,
                    "step_requests": used,
                    "step_ids": step_ids,
                },
            }
        ],
    }


def check_predicate_is_narrow() -> None:
    """Only a budget-exhausted recovery with recorded children may resume windows."""
    accepted = _failed_record(
        16,
        16,
        ["global_plan_window_1_1"],
        "本步骤已发出 16 次模型请求（含 HTTP 重试…），为避免无限重试已停止。",
    )
    accepted["attempts"][0]["request_source"] = "recovery"
    assert artifacts._global_plan_window_budget_failure(accepted) is not None

    root_only = _failed_record(
        16, 16, ["global_plan"], "本步骤已发出 16 次模型请求（…）为避免无限重试已停止。"
    )
    assert artifacts._global_plan_window_budget_failure(root_only) is None, "root id only"
    model_attempt = _failed_record(
        16, 16, ["global_plan_window_1_1"], "本步骤已发出 16 次模型请求（…）"
    )
    model_attempt["attempts"][0]["request_source"] = "model"
    assert artifacts._global_plan_window_budget_failure(model_attempt) is None, "not a recovery"

    rejected = {
        "plain failure": {"status": "failed", "attempts": [{"error": "上游返回了空正文"}]},
        "no children": _failed_record(
            16, 16, [], "本步骤已发出 16 次模型请求（含 HTTP 重试…），为避免无限重试已停止。"
        ),
        "budget not spent": _failed_record(
            16, 4, ["global_plan_window_1_1"], "本步骤已发出 16 次模型请求（…）。"
        ),
        "succeeded": {"status": "succeeded", "attempts": []},
    }
    for name, record in rejected.items():
        assert artifacts._global_plan_window_budget_failure(record) is None, name


def _unit_ids(messages) -> list[str]:
    return [unit["id"] for unit in split._units_in(messages)]


def check_retry_reuses_successful_windows() -> None:
    """The retry asks only for the unfinished branch, then the root passes validation."""
    profile, facts, units, sizes, tokens = split._fixture(4)
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                "workspace-window-budget", profile.id, model_id="scripted", fake_model=True
            )
            with mock.patch.object(artifacts, "_global_plan_shared_request_limit", lambda first_level: 4):
                _, worker = split._length_worker(fail_above=1)
                try:
                    split._scope_call(
                        job, profile, facts, units, sizes, tokens, split._client(worker)
                    )
                except Exception:  # noqa: BLE001 - the first run is expected to stop
                    pass
                root = [
                    step for step in storage.list_artifact_job_steps(job.id)
                    if step["stage"] == "global_plan"
                ][0]
                assert root["status"] == "failed", root["status"]
                attempt = (root.get("attempts") or [])[-1]
                assert attempt.get("request_source") == "recovery", attempt.get("request_source")
                diagnostics = attempt.get("diagnostics") or {}
                assert diagnostics.get("step_request_limit") == 4, diagnostics
                assert artifacts._global_plan_window_budget_failure(root) is not None
                succeeded = {
                    step["stage"] for step in storage.list_artifact_job_steps(job.id)
                    if step["stage"].startswith("global_plan_window_")
                    and step["status"] == "succeeded"
                }
                assert any(stage.endswith("_1") or stage.endswith("_2") for stage in succeeded), (
                    succeeded
                )

                seen: list[list[str]] = []
                _, retry_worker = split._length_worker(fail_above=1)

                def spy(messages, temperature, max_tokens, **kwargs):
                    seen.append(_unit_ids(messages))
                    return retry_worker(messages, temperature, max_tokens, **kwargs)

                result = split._scope_call(
                    job, profile, facts, units, sizes, tokens, split._client(spy)
                )

                # Only the unfinished branch and its leaves; the finished branch is reused.
                assert seen == [["unit_2", "unit_3"], ["unit_2"], ["unit_3"]], seen
                for request in seen:
                    assert "unit_0" not in request and "unit_1" not in request, seen

                after = [
                    step for step in storage.list_artifact_job_steps(job.id)
                    if step["stage"] == "global_plan"
                ][0]
                assert after["status"] == "succeeded", after["status"]
                assert result and result.get("cards"), "the retry must return a plan"
                cited = {
                    fact_id
                    for card in result["cards"]
                    for fact_id in card.get("fact_ids", [])
                }
                assert cited == {fact.id for fact in facts}, cited

    # A retry that still hits the cap must fail again, keep its recovery diagnostics and
    # store no partial plan.
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                "workspace-window-budget-2", profile.id, model_id="scripted", fake_model=True
            )
            with mock.patch.object(
                artifacts, "_global_plan_shared_request_limit", lambda first_level: 4
            ):
                _, worker = split._length_worker(fail_above=1)
                try:
                    split._scope_call(
                        job, profile, facts, units, sizes, tokens, split._client(worker)
                    )
                except Exception:  # noqa: BLE001 - first exhaustion
                    pass
            with mock.patch.object(
                artifacts, "_global_plan_shared_request_limit", lambda first_level: 2
            ):
                _, worker2 = split._length_worker(fail_above=1)
                failure = None
                try:
                    split._scope_call(
                        job, profile, facts, units, sizes, tokens, split._client(worker2)
                    )
                except (artifacts.ArtifactGenerationError, RuntimeError) as error:
                    failure = error
                assert failure is not None, "a second exhaustion must raise the bounded error"
                assert "次模型请求" in str(failure), str(failure)
                root = next(
                    step for step in storage.list_artifact_job_steps(job.id)
                    if step["stage"] == "global_plan"
                )
                assert root["status"] == "failed", root["status"]
                last = (root.get("attempts") or [])[-1]
                assert last.get("request_source") == "recovery", last.get("request_source")
                diagnostics = last.get("diagnostics") or {}
                assert diagnostics.get("step_request_limit") == 2, diagnostics
                assert diagnostics.get("step_requests") == 2, diagnostics
                assert not root.get("output"), "no partial plan may be stored"


def check_leaf_escalation_after_length() -> None:
    """Each leaf spends its own 12000 first, then succeeds at its one 16000 raise."""
    profile, facts, units, sizes, tokens = split._fixture(2)
    seen: list[tuple[tuple[str, ...], int]] = []
    attempts: dict[tuple[str, ...], int] = {}

    def worker(messages, temperature, max_tokens, **kwargs):
        ids = tuple(unit["id"] for unit in split._units_in(messages))
        seen.append((ids, max_tokens))
        attempts[ids] = attempts.get(ids, 0) + 1
        if len(ids) > 1:
            return split._length_frame()
        if attempts[ids] == 1:
            return split._length_frame()
        return split._plan_frame(messages, f"窗口 {len(seen)}")

    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                "workspace-leaf-escalation", profile.id, model_id="scripted", fake_model=True
            )
            result = split._scope_call(
                job, profile, facts, units, sizes, tokens, split._client(worker)
            )

    plan_ids = [unit["id"] for unit in units]
    budgets = {pid: [b for ids, b in seen if ids == (pid,)] for pid in plan_ids}
    for plan_id, values in budgets.items():
        assert values == [12000, 16000], (plan_id, values, seen)
    assert len([ids for ids, _ in seen if len(ids) > 1]) == 1, seen
    assert result and result.get("cards"), "the escalated leaves must produce the plan"


def main() -> None:
    check_predicate_is_narrow()
    check_retry_reuses_successful_windows()
    check_leaf_escalation_after_length()
    print("PASS: a budget-exhausted window recovery resumes windows on retry")


if __name__ == "__main__":
    main()
