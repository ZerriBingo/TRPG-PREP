"""A truncated runtime digest is recovered by persistent transport splitting."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts, llm, storage  # noqa: E402
from backend.domain import SourceFact, SourceRef, load_profiles  # noqa: E402


class Worker:
    def __init__(
        self,
        client: llm.LLMClient,
        facts: list[SourceFact],
        *,
        first_content: str = "",
    ) -> None:
        self.client = client
        self.facts = facts
        self.first_content = first_content
        self.calls = 0

    def __call__(self, messages, temperature, max_tokens, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {
                "content": self.first_content,
                "finish_reason": "length",
                "usage": {"completion_tokens": max_tokens},
            }
        marker = next(
            line for line in messages[-1]["content"].splitlines()
            if line.startswith("LOCAL_FACTS_JSON=")
        )
        payload = json.loads(marker.split("=", 1)[1])
        result = {
            "units": [
                {
                    "kind": "scene",
                    "title": f"事实 {fact['id'][-4:]}",
                    "summary": "局部整理摘要",
                    "fact_ids": [fact["id"]],
                    "entity_keys": [],
                    "relationship_hints": [],
                    "open_questions": [],
                }
                for fact in payload
            ],
            "open_questions": [],
        }
        return {"content": json.dumps(result, ensure_ascii=False), "finish_reason": "stop"}



def check_retry_after_failed_recovery_skips_the_parent() -> None:
    """After a recovery failure, retry only the failed window, never the parent batch.

    The reviewer's probe: 4 facts -> parent length, left window succeeds, right window
    fails, so the parent's own recovery fails and is recorded as its own attempt.  The
    next explicit retry then looked at that last attempt, found no length marker, and
    paid for the same known-failing 4-fact request again.
    """
    from backend.app import storage as storage_module

    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    facts = [
        SourceFact(
            id=f"fact_retry_{index}",
            text=f"Fact {index}",
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://retry-scope", page=index + 1)],
        )
        for index in range(4)
    ]
    job = SimpleNamespace(id="artifact_job_local_scope_retry", model_id="probe", fake_model=False)

    def facts_in(messages):
        marker = next(
            line for line in messages[-1]["content"].splitlines()
            if line.startswith("LOCAL_FACTS_JSON=")
        )
        return json.loads(marker.split("=", 1)[1])

    def plan_content(payload):
        return json.dumps({
            "units": [
                {
                    "kind": "scene",
                    "title": "单元",
                    "summary": "局部摘要",
                    "fact_ids": [fact["id"]],
                    "entity_keys": [],
                    "relationship_hints": [],
                    "open_questions": [],
                }
                for fact in payload
            ],
            "open_questions": [],
        }, ensure_ascii=False)

    with tempfile.TemporaryDirectory(prefix="trpg-local-scope-retry-") as directory,             patch.object(storage_module, "DB_PATH", Path(directory) / "test.db"):
        storage_module.init_db()
        first_calls: list[int] = []

        def failing_worker(messages, temperature, max_tokens, **kwargs):
            payload = facts_in(messages)
            first_calls.append(len(payload))
            if len(payload) == 4:
                return {"content": None, "finish_reason": "length", "content_length": None,
                        "usage": {"completion_tokens": max_tokens}}
            if [fact["id"] for fact in payload] == ["fact_retry_0", "fact_retry_1"]:
                return {"content": plan_content(payload), "finish_reason": "stop",
                        "content_length": None, "usage": None}
            raise RuntimeError("window transport failed")

        client = llm.LLMClient({
            "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
            "reasoning_effort": "",
        })
        client._worker_call = failing_worker  # type: ignore[method-assign]

        def run(target_client):
            return artifacts._run_local_digest_window(
                job,
                profile=profile,
                facts=facts,
                chapter=None,
                batch_index=37,
                batch_count=40,
                path="",
                max_tokens=12000,
                chat_kwargs={},
                client=target_client,
                step_budget=llm.RequestBudget(),
            )

        try:
            run(client)
        except Exception as error:  # noqa: BLE001
            assert "window transport failed" in str(error), error
        else:  # pragma: no cover - defensive
            raise AssertionError("a failing window must fail the batch")
        assert first_calls == [4, 2, 2], first_calls

        retry_calls: list[list[str]] = []

        def retry_worker(messages, temperature, max_tokens, **kwargs):
            payload = facts_in(messages)
            retry_calls.append([fact["id"] for fact in payload])
            return {"content": plan_content(payload), "finish_reason": "stop",
                    "content_length": None, "usage": None}

        retry_client = llm.LLMClient({
            "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
            "reasoning_effort": "",
        })
        retry_client._worker_call = retry_worker  # type: ignore[method-assign]
        output = run(retry_client)
        assert retry_calls == [["fact_retry_2", "fact_retry_3"]], (
            "the retry must resume the failed window instead of the whole batch"
        )
        assert {fid for unit in output["units"] for fid in unit["fact_ids"]} == {
            fact.id for fact in facts
        }


def main() -> None:
    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    facts = [
        SourceFact(
            id=f"fact_split_{index}",
            text=f"Fact {index}",
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://split", page=index + 1)],
        )
        for index in range(4)
    ]
    job = SimpleNamespace(id="artifact_job_split_recovery", model_id="probe", fake_model=False)

    with tempfile.TemporaryDirectory(prefix="trpg-local-digest-split-") as directory, \
            patch.object(storage, "DB_PATH", Path(directory) / "test.db"):
        storage.init_db()
        client = llm.LLMClient({
            "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
            "reasoning_effort": "low",
        })
        worker = Worker(client, facts)
        client._worker_call = worker  # type: ignore[method-assign]
        output = artifacts._run_local_digest_window(
            job,
            profile=profile,
            facts=facts,
            chapter=None,
            batch_index=32,
            batch_count=40,
            path="",
            max_tokens=12000,
            chat_kwargs={},
            client=client,
            step_budget=llm.RequestBudget(),
        )
        assert worker.calls == 3, worker.calls
        assert {fact_id for unit in output["units"] for fact_id in unit["fact_ids"]} == {
            fact.id for fact in facts
        }
        steps = storage.list_artifact_job_steps(job.id)
        root = next(step for step in steps if step["stage"] == "local_digest")
        children = [step for step in steps if step["stage"].startswith("local_digest_window_32_")]
        assert root["status"] == "succeeded"
        assert root["attempts"][0]["status"] == "failed"
        assert root["attempts"][-1]["request_source"] == "recovery"
        assert len(children) == 2 and all(step["status"] == "succeeded" for step in children)

        # The recovered parent is durable: a retry uses no parent or child request.
        second = artifacts._run_local_digest_window(
            job,
            profile=profile,
            facts=facts,
            chapter=None,
            batch_index=32,
            batch_count=40,
            path="",
            max_tokens=12000,
            chat_kwargs={},
            client=client,
            step_budget=llm.RequestBudget(),
        )
        assert second == output
        assert worker.calls == 3

    # The production failure had a non-empty JSON prefix before the upstream
    # cut it off. It must take the same recovery path as an empty length reply.
    with tempfile.TemporaryDirectory(prefix="trpg-local-digest-truncated-") as directory, \
            patch.object(storage, "DB_PATH", Path(directory) / "test.db"):
        storage.init_db()
        client = llm.LLMClient({
            "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
            "reasoning_effort": "low",
        })
        worker = Worker(
            client,
            facts,
            first_content=(
                '{"units": [{"kind": "scene", "title": "截断", '
                '"summary": "部分", "fact_ids": ["fact_split_0"]}]'
            ),
        )
        client._worker_call = worker  # type: ignore[method-assign]
        output = artifacts._run_local_digest_window(
            job,
            profile=profile,
            facts=facts,
            chapter=None,
            batch_index=32,
            batch_count=40,
            path="",
            max_tokens=12000,
            chat_kwargs={},
            client=client,
            step_budget=llm.RequestBudget(),
        )
        assert {fact_id for unit in output["units"] for fact_id in unit["fact_ids"]} == {
            fact.id for fact in facts
        }
        assert worker.calls == 3
    # A one-fact window is already a leaf: an explicit retry must ask the provider
    # again instead of replaying the stored length failure, and a retry that fails
    # again must stop after one request.
    with tempfile.TemporaryDirectory(prefix="trpg-local-digest-leaf-") as directory, \
            patch.object(storage, "DB_PATH", Path(directory) / "test.db"):
        storage.init_db()
        leaf_facts = facts[:1]
        leaf_job = SimpleNamespace(
            id="artifact_job_local_digest_leaf_retry", model_id="probe", fake_model=False,
        )
        leaf_client = llm.LLMClient({
            "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
            "reasoning_effort": "",
        })
        replies = ["length", "plan", "plan"]
        calls: list[int] = []

        def leaf_worker(messages, temperature, max_tokens, **kwargs):
            calls.append(1)
            if replies.pop(0) == "length":
                return {
                    "content": None,
                    "finish_reason": "length",
                    "content_length": None,
                    "usage": {"completion_tokens": 12000},
                }
            payload = json.loads(
                next(
                    line for line in messages[-1]["content"].splitlines()
                    if line.startswith("LOCAL_FACTS_JSON=")
                ).split("=", 1)[1]
            )
            result = {
                "units": [{
                    "kind": "scene",
                    "title": "叶子",
                    "summary": "局部整理摘要",
                    "fact_ids": [fact["id"]],
                    "entity_keys": [],
                    "relationship_hints": [],
                    "open_questions": [],
                } for fact in payload],
                "open_questions": [],
            }
            return {
                "content": json.dumps(result, ensure_ascii=False),
                "finish_reason": "stop",
                "content_length": None,
                "usage": {"completion_tokens": 40},
            }

        leaf_client._worker_call = leaf_worker  # type: ignore[method-assign]

        def run_leaf(client, target_job):
            return artifacts._run_local_digest_window(
                target_job,
                profile=profile,
                facts=leaf_facts,
                chapter=None,
                batch_index=39,
                batch_count=40,
                path="",
                max_tokens=12000,
                chat_kwargs={},
                client=client,
                step_budget=llm.RequestBudget(),
            )

        try:
            run_leaf(leaf_client, leaf_job)
        except Exception as error:  # noqa: BLE001
            message = str(error)
            assert "已无法继续拆分" in message, message
            assert "局部整理窗口 39" in message, message
        else:  # pragma: no cover - defensive
            raise AssertionError("a one-fact window must fail on its first length reply")
        assert len(calls) == 1, calls

        output = run_leaf(leaf_client, leaf_job)
        assert len(calls) == 2, "the retry must issue one new request"
        assert {fact_id for unit in output["units"] for fact_id in unit["fact_ids"]} == {
            leaf_facts[0].id
        }
        again = run_leaf(leaf_client, leaf_job)
        assert again == output and len(calls) == 2, calls

        failing_calls: list[int] = []

        def failing_worker(messages, temperature, max_tokens, **kwargs):
            failing_calls.append(1)
            return {
                "content": None,
                "finish_reason": "length",
                "content_length": None,
                "usage": None,
            }

        failing_client = llm.LLMClient({
            "base_url": "http://127.0.0.1:1", "api_key": "unused", "model": "probe",
            "reasoning_effort": "",
        })
        failing_client._worker_call = failing_worker  # type: ignore[method-assign]
        retry_job = SimpleNamespace(
            id="artifact_job_local_digest_leaf_retry_2", model_id="probe", fake_model=False,
        )
        for expected in (1, 2):
            try:
                run_leaf(failing_client, retry_job)
            except Exception as error:  # noqa: BLE001
                assert "已无法继续拆分" in str(error), error
            else:  # pragma: no cover - defensive
                raise AssertionError("a repeated length failure must fail the step")
            assert len(failing_calls) == expected, (failing_calls, expected)

    print("PASS: truncated local digest splits, persists children, and reuses recovered parent")
    check_retry_after_failed_recovery_skips_the_parent()
    print("PASS: a local digest leaf is re-requested on retry and stops when it fails again")
    print("PASS: a retry after a failed local recovery resumes the window, not the batch")


if __name__ == "__main__":
    main()
