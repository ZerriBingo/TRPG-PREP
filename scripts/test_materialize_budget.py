"""The materialization output budget follows the card responsibility.

Drives the real hierarchical job (`execute_artifact_job` → the materialize loop) and
records `max_tokens` where the fake client actually receives the request, not only the
caller's keyword.  Then it re-enters the same succeeded durable step, retries an old
9,000 failure, and plants an oversized historical budget to prove `max_budget` clamps the
step instead of letting another card's budget leak in.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import artifacts, storage  # noqa: E402
from backend.domain import (  # noqa: E402
    ExampleBundle,
    ExtractionWindow,
    PageSpan,
    PrepJob,
    PrepScope,
    SourceFact,
    SourceRef,
)


def workspace() -> ExampleBundle:
    facts = [
        SourceFact(
            id="fact_budget_location",
            source_refs=[SourceRef(file="fixture://budget", page=1)],
            evidence_status="source_fact",
            text="The keeper's room is abandoned, but a fresh lamp is burning.",
            kind="location", visibility="explicit",
        ),
        SourceFact(
            id="fact_budget_witness",
            source_refs=[SourceRef(file="fixture://budget", page=2)],
            evidence_status="source_fact",
            text="A survivor heard scratching beneath the eastern stairs.",
            kind="npc", visibility="explicit",
        ),
    ]
    return ExampleBundle(
        id="materialize_budget_test", name="Materialize budget test",
        profile_ids=["cthulhu-dark-2e"], facts=facts, cards=[], plans=[],
    )


def install_prep_job(bundle: ExampleBundle) -> None:
    storage.create_prep_job(PrepJob(
        id="prep_job_budget",
        status="completed",
        scope=PrepScope(
            source_file="fixture://budget", source_version="v1",
            page_spans=[PageSpan(start=1, end=2)], profile_id="cthulhu-dark-2e",
            objective="budget check",
        ),
        model_id="fake-budget", prompt_version="test", schema_version="test",
        workspace_id=bundle.id,
        windows=[ExtractionWindow(
            id="prep_window_budget", page_span=PageSpan(start=1, end=2),
            core_span=PageSpan(start=1, end=2), status="succeeded",
            candidate_count=0, input_chars=10,
        )],
        candidate_count=0, created_at=storage.now(), updated_at=storage.now(),
    ).model_dump(mode="json"))


def task_of(messages) -> str:
    markers = {
        part.split("[TASK:", 1)[1].rstrip("]")
        for message in messages
        for part in (message.get("content") or "").split()
        if part.startswith("[TASK:")
    }
    return markers.pop() if len(markers) == 1 else "AMBIGUOUS"


def card_of(messages) -> tuple[str, str]:
    for message in messages:
        for line in (message.get("content") or "").splitlines():
            if line.startswith("CARD_PLAN_JSON="):
                plan = json.loads(line[len("CARD_PLAN_JSON="):])
                return plan.get("type", ""), plan.get("title", "")
    return "", ""


def counting_client(sent: list[dict], inner_factory):
    """A client that records what the worker boundary is really asked for."""

    class CountingClient:
        def __init__(self, inner):
            self._inner = inner
            self.model = getattr(inner, "model", "fake-budget")

        def chat_json(self, messages, *args, **kwargs):
            card_type, card_title = card_of(messages)
            sent.append({
                "task": task_of(messages),
                "max_tokens": kwargs.get("max_tokens"),
                "card_type": card_type,
                "card_title": card_title,
            })
            result = self._inner.chat_json(messages, *args, **kwargs)
            self.last_request_diagnostics = getattr(
                self._inner, "last_request_diagnostics", [])
            return result

        def __getattr__(self, name):
            return getattr(self._inner, name)

    return lambda *args, **kwargs: CountingClient(inner_factory(*args, **kwargs))


def main() -> None:
    original = storage.DB_PATH
    with tempfile.TemporaryDirectory() as directory:
        storage.DB_PATH = Path(directory) / "budget.db"
        try:
            storage.init_db()
            storage.set_config({
                "base_url": "http://offline.invalid", "api_key": "",
                "model": "fake-budget", "fake": True,
            })
            bundle = workspace()
            storage.save_domain_bundle(bundle.id, bundle.model_dump(mode="json", by_alias=True))
            install_prep_job(bundle)
            job, _created = artifacts.create_artifact_job(
                bundle.id, "cthulhu-dark-2e", model_id="fake-budget", fake_model=True)

            calls: list[dict] = []
            captured: dict[str, dict] = {}
            sent: list[dict] = []
            real_make_client = artifacts.make_client
            original_step = artifacts._run_json_step

            def observed_step(job_arg, **kwargs):
                calls.append({
                    "stage": kwargs["stage"],
                    "max_tokens": kwargs.get("max_tokens"),
                    "max_budget": kwargs.get("max_budget"),
                    "input_hash": artifacts._stable_hash({
                        **kwargs["input_payload"],
                        "_model_id": job_arg.model_id,
                        "_fake_model": job_arg.fake_model,
                    }),
                })
                if kwargs["stage"] == "materialize":
                    captured[calls[-1]["input_hash"]] = dict(kwargs)
                return original_step(job_arg, **kwargs)

            with mock.patch.object(artifacts, "_run_json_step", observed_step), \
                 mock.patch.object(artifacts, "make_client", counting_client(sent, real_make_client)):
                artifacts.execute_artifact_job(
                    job.id, workspace_id=bundle.id, profile_id="cthulhu-dark-2e")

            materialize_sent = [item for item in sent
                                if item["task"] == "prep:artifact_materialize"]
            assert materialize_sent, sent
            overview_sent = [item for item in materialize_sent
                             if item["card_type"] == "chapter_overview"]
            other_sent = [item for item in materialize_sent
                          if item["card_type"] != "chapter_overview"]
            assert len(overview_sent) == 1, materialize_sent
            assert overview_sent[0]["max_tokens"] == 16000, overview_sent
            assert other_sent and all(item["max_tokens"] == 9000 for item in other_sent), other_sent

            overview_hash = next(item["input_hash"] for item in calls
                                 if item["stage"] == "materialize" and item["max_tokens"] == 16000)
            other_hash = next(key for key in captured if key != overview_hash)
            profile = artifacts.load_profiles(
                ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]

            # 1. Re-entering the same succeeded durable step sends nothing and keeps its
            #    identity and result.
            stored_before = storage.load_artifact_job_step(
                job.id, "materialize", captured[overview_hash]["step_index"], overview_hash)
            before = len(sent)
            with mock.patch.object(artifacts, "make_client",
                                   counting_client(sent, real_make_client)):
                same = original_step(job, **captured[overview_hash])
            stored_after = storage.load_artifact_job_step(
                job.id, "materialize", captured[overview_hash]["step_index"], overview_hash)
            assert len(sent) == before, "a succeeded step must not be sent again"
            assert stored_after["input_hash"] == stored_before["input_hash"]
            assert stored_after["output"] == stored_before["output"] == same
            assert stored_after["attempts"] == stored_before["attempts"]

            # 2. An old 9,000 failure is retried at the overview budget, the earlier
            #    attempt is kept, and the other card's own step still shows 9,000.
            legacy = dict(stored_before)
            legacy["status"] = "failed"
            legacy["error"] = "length reply (returned frame)"
            legacy["attempts"] = [{
                "attempt": 1, "status": "failed", "model_id": job.model_id,
                "fake_model": True, "started_at": storage.now(),
                "finished_at": storage.now(), "response": None,
                "error": "length reply (returned frame)", "request_source": "model",
                "diagnostics": {"requests": [{"index": 1, "max_tokens": 9000,
                                              "request_timeout": 300.0,
                                              "finish_reason": "length"}]},
            }]
            storage.save_artifact_job_step(legacy)
            before = len(sent)
            retry_args = {key: value for key, value in captured[overview_hash].items()
                          if key not in {"max_tokens", "max_budget"}}
            with mock.patch.object(artifacts, "make_client",
                                   counting_client(sent, real_make_client)):
                original_step(job, **retry_args, max_tokens=16000, max_budget=16000)
            retried = [item for item in sent[before:]
                       if item["task"] == "prep:artifact_materialize"]
            assert [item["max_tokens"] for item in retried] == [16000], retried
            stored = storage.load_artifact_job_step(
                job.id, "materialize", captured[overview_hash]["step_index"], overview_hash)
            assert len(stored["attempts"]) == 2, stored["attempts"]
            assert stored["attempts"][0]["diagnostics"]["requests"][0]["max_tokens"] == 9000
            assert stored["status"] == "succeeded", stored["status"]
            other_step = storage.load_artifact_job_step(
                job.id, "materialize", captured[other_hash]["step_index"], other_hash)
            assert other_step["status"] == "succeeded"
            # The other card was not called again, and its real request boundary stayed 9,000.
            assert len(other_step["attempts"]) == 1, other_step["attempts"]
            assert all(item["max_tokens"] == 9000 for item in other_sent), other_sent

            # 3. An oversized historical budget on the same step is clamped by max_budget,
            #    the input identity is unchanged, and no other card's budget leaks in.
            for card_hash, ceiling in ((overview_hash, 16000), (other_hash, 9000)):
                planted = dict(storage.load_artifact_job_step(
                    job.id, "materialize", captured[card_hash]["step_index"], card_hash))
                planted["status"] = "failed"
                planted["attempts"] = [{
                    "attempt": 1, "status": "failed", "model_id": job.model_id,
                    "fake_model": True, "started_at": storage.now(),
                    "finished_at": storage.now(), "response": None,
                    "error": "length reply (returned frame)", "request_source": "model",
                    "diagnostics": {"requests": [{"index": 1, "max_tokens": 32000,
                                                  "request_timeout": 300.0,
                                                  "finish_reason": "length"}]},
                }]
                storage.save_artifact_job_step(planted)
                before = len(sent)
                planted_args = {key: value for key, value in captured[card_hash].items()
                                if key not in {"max_tokens", "max_budget"}}
                with mock.patch.object(artifacts, "make_client",
                                       counting_client(sent, real_make_client)):
                    original_step(job, **planted_args, max_tokens=ceiling, max_budget=ceiling)
                fresh = [item for item in sent[before:]
                         if item["task"] == "prep:artifact_materialize"]
                assert len(fresh) == 1, fresh
                assert fresh[0]["max_tokens"] == ceiling, (card_hash, fresh)
                clamped = storage.load_artifact_job_step(
                    job.id, "materialize", captured[card_hash]["step_index"], card_hash)
                assert clamped["input_hash"] == card_hash, "the input identity must not change"
                assert len(clamped["attempts"]) == 2, clamped["attempts"]
                assert clamped["status"] == "succeeded", clamped["status"]

            # 4. Direct generation keeps the general ceiling, on the real call path.
            sent.clear()
            calls.clear()
            with mock.patch.object(artifacts, "_run_json_step", observed_step), \
                 mock.patch.object(artifacts, "make_client",
                                   counting_client(sent, real_make_client)):
                artifacts._generate_direct_step(
                    job, bundle, profile, artifacts.make_client(model_id="fake-budget"),
                    bundle.facts)
            direct = [item for item in sent if item["task"] == "prep:artifact_draft"]
            assert direct and all(item["max_tokens"] == 9000 for item in direct), sent

            # 5. The contracts themselves are untouched.
            assert artifacts.ARTIFACT_MAX_TOKENS == 9000
            assert artifacts.ARTIFACT_OVERVIEW_MAX_TOKENS == 16000
            assert artifacts._materialize_max_tokens("chapter_overview") == 16000
            assert artifacts._materialize_max_tokens("location") == 9000
            assert artifacts._materialize_max_tokens("npc") == 9000
            assert artifacts._materialize_max_tokens("direct_generation") == 9000
            assert artifacts.PROMPT_VERSION == "prep-artifact-draft-v16"
            assert artifacts.GLOBAL_PLAN_PROMPT_VERSION == "prep-artifact-global-plan-v24"
            assert artifacts.GLOBAL_PLAN_MULTI_UNIT_MAX_TOKENS == 32000
            assert artifacts.GLOBAL_PLAN_MAX_TOKENS == 12000
            assert artifacts.GLOBAL_PLAN_WINDOW_MAX_TOKENS == 16000
        finally:
            storage.DB_PATH = original
    print("PASS: overview 16000 and other cards 9000 at the real request boundary, "
          "exact re-entry sends nothing, an old 9000 failure retries at 16000, "
          "and a planted 32000 is clamped by max_budget")


if __name__ == "__main__":
    main()
