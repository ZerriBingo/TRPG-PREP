"""The shared planning budget follows the work a scope actually needs.

B is the number of first-level entries the production chunker produces; the shared limit is
min(64, max(16, B + 15)).  These checks drive the real scope entry with scripted workers, so
they prove behaviour, not the formula alone.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "split_recovery", ROOT / "scripts" / "test_global_plan_split_recovery.py"
)
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)


def check_formula() -> None:
    assert artifacts._global_plan_shared_request_limit(1) == 16, "single unit keeps 16"
    assert artifacts._global_plan_shared_request_limit(22) == 37, "22 entries -> 37"
    assert artifacts._global_plan_shared_request_limit(64) == 64, "hard cap"
    assert artifacts._global_plan_shared_request_limit(80) == 64, "capped above 64"


def _single_unit_chunks(units, *_args, **_kwargs):
    """A chunker that yields exactly one entry per unit, as the real one does at scale."""
    return [[unit] for unit in units]


def _run(units_count: int, *, limit_patch=None):
    profile, facts, units, sizes, tokens = split._fixture(units_count)
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                f"workspace-scale-{units_count}", profile.id, model_id="scripted",
                fake_model=True,
            )
            calls, worker = split._length_worker(fail_above=1)
            client = split._client(worker)
            error = None
            result = None
            patch = (
                mock.patch.object(artifacts, "_global_plan_shared_request_limit", limit_patch)
                if limit_patch else mock.patch.object(artifacts, "_global_plan_shared_request_limit",
                                                      artifacts._global_plan_shared_request_limit)
            )
            with patch, mock.patch.object(
                artifacts, "_global_plan_window_chunks", _single_unit_chunks
            ):
                try:
                    result = split._scope_call(
                        job, profile, facts, units, sizes, tokens, client
                    )
                except Exception as exc:  # noqa: BLE001
                    error = exc
            steps = storage.list_artifact_job_steps(job.id)
            root = next(step for step in steps if step["stage"] == "global_plan")
            return {
                "calls": calls, "error": error, "result": result, "root": root,
                "facts": facts,
            }


def check_large_scope_completes_at_entry_count() -> None:
    """22 first-level entries: the new limit finishes them, the old 16 could not."""
    out = _run(22)
    assert out["error"] is None, out["error"]
    # One scope request that runs out of budget, then one per first-level entry.
    assert len(out["calls"]) == 23, out["calls"]
    assert len(out["calls"]) > artifacts.GLOBAL_PLAN_MAX_REQUESTS, "old cap could not finish"
    assert out["root"]["status"] == "succeeded", out["root"]["status"]
    cited = {
        fact_id for card in out["result"]["cards"] for fact_id in card.get("fact_ids", [])
    }
    assert cited == {fact.id for fact in out["facts"]}, cited


def check_cache_reuse_after_completion() -> None:
    """Re-entering the same scope costs no request."""
    profile, facts, units, sizes, tokens = split._fixture(22)
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                "workspace-scale-reuse", profile.id, model_id="scripted", fake_model=True
            )
            calls, worker = split._length_worker(fail_above=1)
            with mock.patch.object(
                artifacts, "_global_plan_window_chunks", _single_unit_chunks
            ):
                split._scope_call(
                    job, profile, facts, units, sizes, tokens, split._client(worker)
                )
                first = len(calls)
                again, worker2 = split._length_worker(fail_above=1)
                split._scope_call(
                    job, profile, facts, units, sizes, tokens, split._client(worker2)
                )
            assert len(again) == 0, (first, again)


def check_scope_above_the_hard_cap_is_refused() -> None:
    """A scope whose first-level work already exceeds 64 is refused before sending."""
    profile, facts, units, sizes, tokens = split._fixture(65)
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                "workspace-scale-refused", profile.id, model_id="scripted", fake_model=True
            )
            calls, worker = split._length_worker(fail_above=1)
            try:
                with mock.patch.object(
                    artifacts, "_global_plan_window_chunks", _single_unit_chunks
                ):
                    split._scope_call(
                        job, profile, facts, units, sizes, tokens, split._client(worker)
                    )
            except artifacts.ArtifactGenerationError as error:
                assert "超过单次执行上界" in str(error), str(error)
            else:
                raise AssertionError("a scope above the hard cap must be refused")
            assert calls == [], calls
            steps = storage.list_artifact_job_steps(job.id)
            assert not any(step["status"] == "succeeded" for step in steps), "nothing may run"


def check_exact_succeeded_root_survives_the_scale_refusal() -> None:
    """A stored complete plan above the cap is a cache hit, not a refused new run."""
    profile, facts, units, sizes, tokens = split._fixture(65)
    with tempfile.TemporaryDirectory() as temp_dir:
        with mock.patch.object(storage, "DB_PATH", Path(temp_dir) / "test.db"):
            storage.init_db()
            job, _ = artifacts.create_artifact_job(
                "workspace-scale-cached", profile.id, model_id="scripted", fake_model=True
            )
            calls, worker = split._length_worker(fail_above=1)
            # Build the stored plan under a temporarily raised cap, exactly as a larger
            # policy would have produced it.
            with mock.patch.object(artifacts, "_global_plan_window_chunks", _single_unit_chunks),                     mock.patch.object(
                        artifacts, "_global_plan_shared_request_limit", lambda first_level: 500
                    ), mock.patch.object(artifacts, "GLOBAL_PLAN_REQUEST_HARD_CAP", 500):
                first = split._scope_call(
                    job, profile, facts, units, sizes, tokens, split._client(worker)
                )
            root = next(
                step for step in storage.list_artifact_job_steps(job.id)
                if step["stage"] == "global_plan"
            )
            assert root["status"] == "succeeded", root["status"]

            # Default policy: B=65 is above the cap, but the stored plan must still return.
            again, worker2 = split._length_worker(fail_above=1)
            with mock.patch.object(artifacts, "_global_plan_window_chunks", _single_unit_chunks):
                cached = split._scope_call(
                    job, profile, facts, units, sizes, tokens, split._client(worker2)
                )
            assert len(again) == 0, again
            assert cached == first, "the cached plan must be returned unchanged"


def main() -> None:
    check_formula()
    check_large_scope_completes_at_entry_count()
    check_cache_reuse_after_completion()
    check_scope_above_the_hard_cap_is_refused()
    check_exact_succeeded_root_survives_the_scale_refusal()
    print("PASS: the shared planning budget follows the scope's first-level work")


if __name__ == "__main__":
    main()
