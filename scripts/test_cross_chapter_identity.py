"""Cross-chapter identity is settled before the merge decides the card count."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a, storage  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef  # noqa: E402

FACT_COUNT = 6
DISPOSITIONS = [
    ("chapter_first", "thread_sacrifice"),
    ("chapter_first", "thread_sacrifice"),
    ("chapter_first", "thread_other"),
    ("chapter_second", "thread_sacrifice"),
    ("chapter_second", "thread_sacrifice"),
    ("chapter_second", "thread_other"),
]


def _bundle(*, threads: bool = True):
    facts = [
        SourceFact(
            id=f"fact_promoted_source_{index:016d}",
            text=f"Fact {index}",
            kind="event",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://identity", page=1)],
        )
        for index in range(FACT_COUNT)
    ]
    ids = [fact.id for fact in facts]
    bundle = ExampleBundle.model_validate(
        {
            "id": "identity_case",
            "name": "Identity",
            "profile_ids": ["daggerheart"],
            "facts": [fact.model_dump() for fact in facts],
            "cards": [],
            "plans": [],
            "adventure_chapters": [
                {
                    "id": "chapter_first",
                    "title": "First",
                    "summary": "First",
                    "fact_ids": ids[:3],
                },
                {
                    "id": "chapter_second",
                    "title": "Second",
                    "summary": "Second",
                    "fact_ids": ids[3:],
                },
            ],
            "fact_dispositions": [
                {
                    "fact_id": fact_id,
                    "kind": "chapter",
                    "chapter_ids": [chapter_id],
                    "thread_ids": [thread_id] if threads else [],
                }
                for (chapter_id, thread_id), fact_id in zip(DISPOSITIONS, ids)
            ],
        }
    )
    return bundle, ids


def _plan(plan_id, card_type, title, fact_ids, chapter_id):
    return {
        "id": plan_id,
        "type": card_type,
        "title": title,
        "purpose": f"{title} 的用途",
        "fact_ids": list(fact_ids),
        "focus": [],
        "open_questions": [],
        "runtime_group_ids": [],
        "chapter_ids": [chapter_id],
        "story_beat_ids": [],
    }


def _plans(ids):
    return [
        _plan("planned_card_1", "clock", "双胞胎献祭期限", ids[0:2], "chapter_first"),
        _plan("planned_card_2", "clock", "献祭期限", ids[3:5], "chapter_second"),
        _plan("planned_card_3", "clock", "仪式干扰", [ids[1]], "chapter_first"),
        _plan("planned_card_4", "environment", "费内鲁斯哨站", [ids[2]], "chapter_first"),
        _plan("planned_card_5", "environment", "黄色尖顶", [ids[5]], "chapter_second"),
    ]


def _scripted(responder):
    class Client:
        def __init__(self):
            self.calls = 0
            self.requests = []

        def chat_json(self, messages, **kwargs):
            self.calls += 1
            self.requests.append(messages)
            return responder(messages)

    return Client()


def _candidate_ids(messages):
    body = json.loads(messages[1]["content"])
    return {item["plan_id"] for group in body["candidate_groups"] for item in group}


class _Harness:
    """A fresh database and job per scenario so no step is reused across them."""

    def __enter__(self):
        self._temp = tempfile.TemporaryDirectory()
        self._patch = patch.object(storage, "DB_PATH", Path(self._temp.name) / "test.db")
        self._patch.start()
        storage.init_db()
        self.bundle, self.ids = _bundle()
        self.job, _ = a.create_artifact_job(
            self.bundle.id, "daggerheart", model_id="scripted", fake_model=True
        )
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        self._temp.cleanup()
        return False


def main():
    allowed = {"planned_card_1", "planned_card_2"}
    valid = a._validate_identity_clusters(
        {"clusters": [{"label": "同一压力", "plan_ids": ["planned_card_1", "planned_card_2"]}]},
        allowed,
    )
    assert valid["clusters"][0]["plan_ids"] == ["planned_card_1", "planned_card_2"]
    for bad in (
        {"clusters": [{"label": "x", "plan_ids": ["planned_card_1", "planned_card_9"]}]},
        {
            "clusters": [
                {"label": "a", "plan_ids": ["planned_card_1"]},
                {"label": "b", "plan_ids": ["planned_card_1", "planned_card_2"]},
            ]
        },
        {"clusters": [{"label": "x", "plan_ids": ["planned_card_1"]}]},
    ):
        try:
            a._validate_identity_clusters(bad, allowed)
        except a.ArtifactGenerationError:
            continue
        raise AssertionError(f"invalid cluster payload accepted: {bad}")

    keys = a._identity_keys_from_clusters(
        {
            "clusters": [
                {"label": "同一压力", "plan_ids": ["a", "b"]},
                {"label": "单独情境", "plan_ids": ["c"]},
            ]
        }
    )
    assert set(keys) == {"a", "b"}
    assert keys["a"] == keys["b"] and keys["a"].startswith("identity_")

    # The reconciliation key is internal and must never reach a prompt.
    assert a._prompt_plan(
        {"id": "p", "type": "clock", "title": "钟", "identity_key": "identity_x_0"}
    ) == {"id": "p", "type": "clock", "title": "钟"}

    with _Harness() as harness:
        plans = _plans(harness.ids)
        assert a._identity_candidate_components(plans, harness.bundle) == [
            ["planned_card_1", "planned_card_2", "planned_card_3"],
            ["planned_card_4", "planned_card_5"],
        ]
        quiet_bundle, _ = _bundle(threads=False)
        assert a._identity_candidate_components(plans, quiet_bundle) == []

        left = _plan("x", "clock", "同名压力", [], "chapter_first")
        right = _plan("y", "clock", "同名压力", [], "chapter_second")
        # Title stays the primary key, so the old selection is untouched.
        assert a._merge_match([left], right) is left
        assert len(a._merge_chapter_plans([], [left, right])) == 1
        assert a._merge_match(
            [left], _plan("z", "npc", "同名压力", [], "chapter_first")
        ) is None

        # Different titles merge only through the identity fallback.
        other_left = _plan("p", "clock", "压力甲", [], "chapter_first")
        other_right = _plan("q", "clock", "压力乙", [], "chapter_second")
        assert len(a._merge_chapter_plans([], [other_left, other_right])) == 2
        assert a._merge_match(
            [{**other_left, "identity_key": "identity_same_0"}],
            {**other_right, "identity_key": "identity_same_0"},
        ) is not None
        assert a._merge_match(
            [{**other_left, "identity_key": "identity_a_0"}],
            {**other_right, "identity_key": "identity_b_0"},
        ) is None

        def _bulk(plan_id, title, count, identity=None):
            plan = _plan(plan_id, "clock", title, [], "chapter_first")
            plan["fact_ids"] = [f"{plan_id}_fact_{index}" for index in range(count)]
            if identity:
                plan["identity_key"] = identity
            return plan

        # Identities must never leave MORE cards alive than the title-only rule.
        incoming = [
            _bulk("a", "甲", 55, "identity_k_0"),
            _bulk("b", "乙", 40),
            _bulk("c", "乙", 50, "identity_k_0"),
        ]
        legacy = a._merge_chapter_plans(
            [],
            [
                {key: value for key, value in plan.items() if key != "identity_key"}
                for plan in incoming
            ],
        )
        assert len(legacy) == 2, len(legacy)
        assert len(a._merge_chapter_plans([], incoming)) == len(legacy)

        # A title merge must not silently drop the incoming identity.
        keyless = _plan("m", "clock", "乙", [], "chapter_first")
        keyed = {
            **_plan("n", "clock", "乙", [], "chapter_second"),
            "identity_key": "identity_k_0",
        }
        orphan = {
            **_plan("o", "clock", "丙", [], "chapter_second"),
            "identity_key": "identity_k_0",
        }
        absorbed = a._merge_chapter_plans([], [keyless, keyed, orphan])
        assert len(absorbed) == 1, absorbed
        assert absorbed[0]["identity_key"] == "identity_k_0"

        # A cap-blocked same-title pair must be reported, never silently kept.
        blocked = a._merge_chapter_plans(
            [], [_bulk("p1", "同名", a.MAX_MERGED_FACT_IDS // 2 + 1), _bulk("p2", "同名", a.MAX_MERGED_FACT_IDS // 2 + 1)]
        )
        assert len(blocked) == 2, blocked
        blocked_summary = a._OpenQuestionSummary()
        a._report_unmerged_identities(blocked, blocked_summary)
        assert blocked_summary.count == 1, blocked_summary.preview

        def _merge_pair(messages):
            present = _candidate_ids(messages)
            clusters = []
            paired = {"planned_card_1", "planned_card_2"}
            if paired <= present:
                clusters.append(
                    {"label": "双胞胎献祭期限", "plan_ids": ["planned_card_1", "planned_card_2"]}
                )
            for plan_id in sorted(present - paired):
                clusters.append({"label": f"独立情境 {plan_id}", "plan_ids": [plan_id]})
            return {"clusters": clusters}

        summary = a._OpenQuestionSummary()
        client = _scripted(_merge_pair)
        identified = a._assign_plan_identities(
            harness.job, plans, harness.bundle, summary, client
        )
        assert client.calls >= 1, client.calls
        by_id = {plan["id"]: plan.get("identity_key") for plan in identified}
        assert by_id["planned_card_1"] == by_id["planned_card_2"]
        assert by_id["planned_card_1"].startswith("identity_")
        assert by_id["planned_card_3"] is None
        assert by_id["planned_card_4"] is None and by_id["planned_card_5"] is None

        merged = a._merge_chapter_plans([], identified)
        assert [plan["title"] for plan in merged] == [
            "双胞胎献祭期限",
            "仪式干扰",
            "费内鲁斯哨站",
            "黄色尖顶",
        ]
        assert set(merged[0]["fact_ids"]) == set(harness.ids[0:2] + harness.ids[3:5])
        assert set(merged[0]["chapter_ids"]) == {"chapter_first", "chapter_second"}
        a._report_unmerged_identities(merged, summary)
        assert summary.count == 0, summary.preview

    with _Harness() as harness:
        plans = _plans(harness.ids)

        def _all_singletons(messages):
            present = _candidate_ids(messages)
            return {
                "clusters": [
                    {"label": f"独立情境 {plan_id}", "plan_ids": [plan_id]}
                    for plan_id in sorted(present)
                ]
            }

        lazy = _scripted(_all_singletons)
        lazy_identified = a._assign_plan_identities(
            harness.job, plans, harness.bundle, a._OpenQuestionSummary(), lazy
        )
        # One request per card type: merges are type-scoped in both passes.
        assert lazy.calls >= 1, lazy.calls
        assert all("identity_key" not in plan for plan in lazy_identified)
        assert len(a._merge_chapter_plans([], lazy_identified)) == 5

    with _Harness() as harness:
        plans = _plans(harness.ids)
        quiet_bundle, _ = _bundle(threads=False)
        quiet = _scripted(lambda messages: {})
        result = a._assign_plan_identities(
            harness.job, plans, quiet_bundle, a._OpenQuestionSummary(), quiet
        )
        assert quiet.calls == 0, quiet.calls
        assert result == plans

    with _Harness() as harness:
        plans = _plans(harness.ids)

        def _boom(messages):
            raise RuntimeError("upstream unavailable")

        failing = _scripted(_boom)
        summary = a._OpenQuestionSummary()
        degraded = a._assign_plan_identities(
            harness.job, plans, harness.bundle, summary, failing
        )
        assert failing.calls >= 1, failing.calls
        assert degraded == plans
        # One request per card type, so a failing upstream can report more than once.
        assert summary.count >= 1, summary.preview
        assert "身份重整失败" in summary.preview[0]

    with _Harness() as harness:
        plans = _plans(harness.ids)
        with patch.object(a, "IDENTITY_GROUP_CHAR_BUDGET", 1):
            client = _scripted(_merge_pair)
            identified = a._assign_plan_identities(
                harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
            )
        assert client.calls >= 2, client.calls
        steps = [
            step
            for step in storage.list_artifact_job_steps(harness.job.id)
            if step["stage"] == "identity_reconcile"
        ]
        indices = sorted(step["step_index"] for step in steps)
        assert indices == list(range(1, len(indices) + 1)), indices
        assert len(indices) >= 2, indices
        by_id = {plan["id"]: plan.get("identity_key") for plan in identified}
        assert by_id["planned_card_1"] == by_id["planned_card_2"]
        assert by_id["planned_card_1"].startswith("identity_")
        assert by_id["planned_card_3"] is None

    with _Harness() as harness:
        # A component wider than the old 40-plan cap must still be judged: on the
        # real 8-chapter material the two largest components were silently dropped.
        wide = [
            _plan(
                f"wide_{index:03d}",
                "clock",
                f"压力{index}",
                [harness.ids[0]],
                "chapter_first",
            )
            for index in range(60)
        ]
        for index, plan in enumerate(wide):
            plan["chapter_ids"] = ["chapter_first" if index % 2 else "chapter_second"]
        components = a._identity_candidate_components(wide, harness.bundle)
        assert len(components) == 1, [len(item) for item in components]
        assert len(components[0]) == 60, len(components[0])
        wide_client = _scripted(
            lambda messages: {
                "clusters": [
                    {"label": f"独立情境 {plan_id}", "plan_ids": [plan_id]}
                    for plan_id in sorted(_candidate_ids(messages))
                ]
            }
        )
        a._assign_plan_identities(
            harness.job, wide, harness.bundle, a._OpenQuestionSummary(), wide_client
        )
        assert wide_client.calls == 1, wide_client.calls

    print("PASS: cross-chapter identity reconciliation, merge superset, and degradation")


if __name__ == "__main__":
    main()
