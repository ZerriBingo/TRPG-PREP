"""Same-scope identity: split entities are reconciled, different entities stay apart.

The reconciliation step used to reach only cards that shared a storyline or runtime
group *across chapters*, so a board with no chapters never reached it and one hotel,
publisher, NPC or threat planned under two titles stayed as two cards forever.  These
checks drive the production path — candidate grouping, the reconcile request, then the
merge — and assert the final plans, never a hand-built identity_key.
"""
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

FACTS = {
    "fact_hotel": "切尔西酒店是埃利亚斯每次来纽约时的固定落脚点。",
    "fact_publisher": "埃利亚斯会长时间待在纽约造访普洛斯佩罗出版社。",
    "fact_shared": "埃利亚斯在纽约没有固定住址，从不在任何地方久留。",
    "fact_telegram": "埃利亚斯从海上发来电报，召集调查员到纽约会面。",
    "fact_meeting": "埃利亚斯计划1925年1月15日与调查员见面。",
    "fact_cult": "某个未具名邪教组织被指向谋杀杰克逊·埃利亚斯。",
    "fact_erica": "艾丽卡·卡莱尔住在纽约市附近，能证实媒体报道真假。",
    "fact_jonah": "调查员之后可能为卡莱尔探险队信息会见约拿·肯辛顿。",
}


def _bundle(chapters: bool = False) -> ExampleBundle:
    facts = [
        SourceFact(
            id=fact_id,
            text=text,
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://same-scope", page=1)],
        )
        for fact_id, text in FACTS.items()
    ]
    chapter_ids = ["chapter_first", "chapter_second"] if chapters else []
    payload = {
        "id": "same_scope_case",
        "name": "Same scope",
        "profile_ids": ["cthulhu-dark-2e"],
        "facts": [fact.model_dump() for fact in facts],
        "cards": [],
        "plans": [],
    }
    if chapters:
        payload["adventure_chapters"] = [
            {
                "id": chapter_id,
                "title": chapter_id,
                "summary": chapter_id,
                "fact_ids": [fact.id for fact in facts],
            }
            for chapter_id in chapter_ids
        ]
    return ExampleBundle.model_validate(payload)


def _plan(plan_id, card_type, title, fact_ids, chapter_ids=None):
    return {
        "id": plan_id,
        "type": card_type,
        "title": title,
        "purpose": f"{title} 的用途",
        "fact_ids": list(fact_ids),
        "focus": [],
        "open_questions": [],
        "runtime_group_ids": [],
        "chapter_ids": list(chapter_ids or []),
        "story_beat_ids": [],
    }


def _sample_plans():
    """The shape of the 2026-11-02 sample: one entity split under two titles."""
    return [
        _plan("p_hotel_a", "location", "切尔西酒店", ["fact_hotel", "fact_shared"]),
        _plan(
            "p_hotel_b",
            "location",
            "切尔西酒店：埃利亚斯的纽约落脚点",
            ["fact_hotel"],
        ),
        _plan("p_publisher_a", "location", "普洛斯佩罗出版社", ["fact_publisher", "fact_shared"]),
        _plan(
            "p_publisher_b",
            "location",
            "普洛斯佩罗出版社：出版关系线索点",
            ["fact_publisher"],
        ),
        _plan(
            "p_elias_a",
            "npc",
            "杰克逊·埃利亚斯",
            ["fact_telegram", "fact_meeting", "fact_shared"],
        ),
        _plan("p_elias_b", "npc", "杰克逊·埃利亚斯：初始委托人", ["fact_telegram"]),
        _plan("p_cult_a", "threat", "未具名邪教组织", ["fact_cult"]),
        _plan("p_cult_b", "threat", "邪教线索：谋杀指认", ["fact_cult"]),
    ]


class _Harness:
    def __init__(self, chapters: bool = False):
        self.chapters = chapters

    def __enter__(self):
        self._temp = tempfile.TemporaryDirectory()
        self._patch = patch.object(storage, "DB_PATH", Path(self._temp.name) / "test.db")
        self._patch.start()
        storage.init_db()
        self.bundle = _bundle(self.chapters)
        self.job, _ = a.create_artifact_job(
            self.bundle.id, "cthulhu-dark-2e", model_id="scripted", fake_model=True
        )
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        self._temp.cleanup()
        return False


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


def _candidate_groups(messages):
    return json.loads(messages[1]["content"])["candidate_groups"]


def _candidate_ids(messages):
    return {item["plan_id"] for group in _candidate_groups(messages) for item in group}


def _cluster(pairs):
    def responder(messages):
        present = _candidate_ids(messages)
        clusters = []
        for members in pairs:
            members = [item for item in members if item in present]
            if len(members) < 2:
                continue
            clusters.append({"label": "同一实体", "plan_ids": members})
            present -= set(members)
        clusters.extend(
            {"label": f"独立实体 {plan_id}", "plan_ids": [plan_id]} for plan_id in sorted(present)
        )
        return {"clusters": clusters}

    return responder


def _titles(plans):
    return sorted(plan["title"] for plan in plans)


def check_no_chapter_board_reaches_reconciliation():
    """Four entities split under two titles each are all nominated and merged."""
    with _Harness() as harness:
        plans = _sample_plans()
        components = a._identity_candidate_components(plans, harness.bundle)
        assert components == [
            ["p_cult_a", "p_cult_b"],
            ["p_elias_a", "p_elias_b"],
            ["p_hotel_a", "p_hotel_b", "p_publisher_a", "p_publisher_b"],
        ], components

        client = _scripted(
            _cluster(
                [
                    ["p_hotel_a", "p_hotel_b"],
                    ["p_publisher_a", "p_publisher_b"],
                    ["p_elias_a", "p_elias_b"],
                    ["p_cult_a", "p_cult_b"],
                ]
            )
        )
        summary = a._OpenQuestionSummary()
        identified = a._assign_plan_identities(
            harness.job, plans, harness.bundle, summary, client
        )
        assert client.calls >= 1, client.calls
        merged = a._merge_chapter_plans([], identified)
        assert _titles(merged) == sorted(
            [
                "切尔西酒店",
                "普洛斯佩罗出版社",
                "杰克逊·埃利亚斯",
                "未具名邪教组织",
            ]
        ), _titles(merged)
        hotel = next(plan for plan in merged if plan["title"] == "切尔西酒店")
        publisher = next(plan for plan in merged if plan["title"] == "普洛斯佩罗出版社")
        # One shared fact made them candidates, not one entity.
        assert hotel["id"] != publisher["id"]
        assert "fact_shared" in hotel["fact_ids"] and "fact_shared" in publisher["fact_ids"]
        assert set(hotel["fact_ids"]) == {"fact_hotel", "fact_shared"}
        assert set(publisher["fact_ids"]) == {"fact_publisher", "fact_shared"}
        assert summary.count == 0, summary.preview


def check_two_people_never_become_one_card():
    """Jonah and Erica stay two NPCs; the pair card is not merged into either."""
    with _Harness() as harness:
        plans = [
            _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
            _plan("p_jonah", "npc", "约拿·肯辛顿", ["fact_jonah"]),
            _plan(
                "p_pair",
                "npc",
                "约拿·肯辛顿与艾丽卡·卡莱尔：条件性后续联系人",
                ["fact_jonah", "fact_erica"],
            ),
        ]
        components = a._identity_candidate_components(plans, harness.bundle)
        assert components == [["p_erica", "p_jonah", "p_pair"]], components

        client = _scripted(_cluster([]))
        identified = a._assign_plan_identities(
            harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
        )
        merged = a._merge_chapter_plans([], identified)
        assert len(merged) == 3, _titles(merged)
        people = {plan["title"] for plan in merged}
        assert "艾丽卡·卡莱尔" in people and "约拿·肯辛顿" in people
        for plan in merged:
            if plan["title"] in {"艾丽卡·卡莱尔", "约拿·肯辛顿"}:
                assert len(plan["fact_ids"]) == 1, plan["fact_ids"]


def check_identity_vetoes_a_title_match():
    """Two different entities that happen to share a title stay two cards."""
    with _Harness():
        left = {**_plan("t_a", "location", "书房", ["fact_hotel"]), "identity_key": "identity_a_0"}
        right = {**_plan("t_b", "location", "书房", ["fact_publisher"]), "identity_key": "identity_b_0"}
        assert a._merge_match([left], right) is None
        merged = a._merge_chapter_plans([], [left, right])
        assert len(merged) == 2, merged

        # Without an identity judgement the old title rule is unchanged.
        plain_left = _plan("t_c", "location", "书房", ["fact_hotel"])
        plain_right = _plan("t_d", "location", "书房", ["fact_publisher"])
        assert a._merge_match([plain_left], plain_right) is plain_left
        assert len(a._merge_chapter_plans([], [plain_left, plain_right])) == 1


def check_reconciliation_request_is_bounded_and_cached():
    """Requests stay inside the chunk limits, cover every candidate, and cache."""
    with _Harness() as harness:
        plans = [
            _plan(
                f"p_bulk_{index:02d}",
                "npc",
                f"人物{index:02d}",
                ["fact_shared", "fact_erica", "fact_jonah"],
            )
            for index in range(30)
        ]
        plans.extend(
            _plan(f"p_loc_{index:03d}", "location", f"地点{index:03d}", ["fact_shared"])
            for index in range(110)
        )
        client = _scripted(_cluster([]))
        summary = a._OpenQuestionSummary()
        first = a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        assert client.calls >= 1, client.calls
        covered: set[str] = set()
        for messages in client.requests:
            groups = _candidate_groups(messages)
            ids = {item["plan_id"] for group in groups for item in group}
            assert len(ids) <= a.IDENTITY_MAX_PLANS_PER_CHUNK, len(ids)
            # A chunk may exceed the budget by at most the group that opened it, so a
            # single component can still be judged as a whole; nothing is unbounded.
            largest = max(
                (len(json.dumps(group, ensure_ascii=False)) for group in groups), default=0
            )
            size = len(json.dumps({"candidate_groups": groups}, ensure_ascii=False))
            assert size <= a.IDENTITY_GROUP_CHAR_BUDGET + largest, (size, largest)
            covered |= ids
        # Every candidate is judged: a chunk overflow may not silently drop cards.
        expected = {
            plan_id
            for component in a._identity_candidate_components(plans, harness.bundle)
            for plan_id in component
        }
        assert covered == expected, (len(covered), len(expected))
        assert not any("超过" in text for text in summary.preview), summary.preview

        # Re-entering the same judgement costs no new request.
        cached = _scripted(_cluster([]))
        again = a._assign_plan_identities(harness.job, plans, harness.bundle, summary, cached)
        assert cached.calls == 0, cached.calls
        assert again == first


def check_oversized_component_is_reported_not_sent():
    """A component bigger than one chunk is reported, not truncated or chunked."""
    with _Harness() as harness:
        plans = [
            _plan(f"p_{index:03d}", "location", f"地点{index:03d}", ["fact_shared"])
            for index in range(a.MAX_IDENTITY_CANDIDATE_PLANS + 5)
        ]
        client = _scripted(_cluster([]))
        summary = a._OpenQuestionSummary()
        result = a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        assert client.calls == 0, client.calls
        assert result == plans
        assert any("超过" in text for text in summary.preview), summary.preview


def check_fact_edit_invalidates_the_judgement():
    """Editing a fact re-runs the judgement instead of reusing the old answer."""
    with _Harness() as harness:
        plans = _sample_plans()
        client = _scripted(_cluster([["p_hotel_a", "p_hotel_b"]]))
        summary = a._OpenQuestionSummary()
        a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        assert client.calls >= 1, client.calls
        before = client.calls

        edited = harness.bundle.model_copy(deep=True)
        for fact in edited.facts:
            if fact.id == "fact_hotel":
                fact.text = fact.text + "（复核后补充：酒店门厅保留旧式前台）"
        refreshed = _scripted(_cluster([["p_hotel_a", "p_hotel_b"]]))
        a._assign_plan_identities(harness.job, plans, edited, summary, refreshed)
        assert refreshed.calls >= 1, "an edited fact must invalidate the cached identity step"
        # Only the judgement that read the edited fact is redone, and never more work
        # than a cold run.
        assert refreshed.calls <= before, (refreshed.calls, before)
        rejudged = set()
        for messages in refreshed.requests:
            rejudged |= _candidate_ids(messages)
        assert {"p_hotel_a", "p_hotel_b"} <= rejudged, rejudged


def check_failure_stays_bounded():
    """A failing reconcile request is reported once per chunk, not retried forever."""
    with _Harness() as harness:
        plans = _sample_plans()

        def boom(messages):
            raise RuntimeError("upstream unavailable")

        client = _scripted(boom)
        summary = a._OpenQuestionSummary()
        result = a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        assert result == plans
        # One attempt per chunk and no second try for the same chunk.
        attempted = [frozenset(_candidate_ids(messages)) for messages in client.requests]
        assert client.calls == len(attempted), (client.calls, len(attempted))
        assert len(attempted) == len(set(attempted)), attempted
        assert client.calls <= 4, client.calls
        assert any("身份重整失败" in text for text in summary.preview), summary.preview


def check_cross_chapter_identity_still_works():
    """The original cross-chapter path keeps producing candidates and merges."""
    with _Harness(chapters=True) as harness:
        plans = [
            {
                **_plan("c_a", "location", "费内鲁斯哨站", ["fact_hotel"], ["chapter_first"]),
                "runtime_group_ids": ["encounter_outpost_0a1b2c3d4e"],
            },
            {
                **_plan("c_b", "location", "哨站旧址", ["fact_hotel"], ["chapter_second"]),
                "runtime_group_ids": ["encounter_outpost_0a1b2c3d4e"],
            },
        ]
        components = a._identity_candidate_components(plans, harness.bundle)
        assert any(set(component) == {"c_a", "c_b"} for component in components), components
        client = _scripted(_cluster([["c_a", "c_b"]]))
        identified = a._assign_plan_identities(
            harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
        )
        assert len(a._merge_chapter_plans([], identified)) == 1


def check_prompts_carry_the_person_rule():
    """Both planning prompts and materialization state the one-person NPC rule."""
    from backend.app.prep import load_profiles  # noqa: PLC0415

    profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
    profile = profiles.get("cthulhu-dark-2e")
    assert profile is not None
    leaf = a._leaf_plan_messages(profile, [], {}, {}, None, fact_payload=[])
    assert any("exactly one playable person" in message["content"] for message in leaf), leaf
    global_messages = a._global_plan_messages(profile, [], {}, {}, None)
    assert any(
        "exactly one playable person" in message["content"] for message in global_messages
    ), "the whole-scope plan prompt must carry the person rule"
    materialize = a._materialization_messages(
        profile,
        _plan("p_m", "npc", "某人", ["fact_erica"]),
        [{"id": "fact_erica", "text": FACTS["fact_erica"]}],
    )
    assert any(
        "One NPC card is one playable person" in message["content"] for message in materialize
    ), "materialization must not combine people either"


def main():
    check_no_chapter_board_reaches_reconciliation()
    check_two_people_never_become_one_card()
    check_identity_vetoes_a_title_match()
    check_reconciliation_request_is_bounded_and_cached()
    check_oversized_component_is_reported_not_sent()
    check_fact_edit_invalidates_the_judgement()
    check_failure_stays_bounded()
    check_cross_chapter_identity_still_works()
    check_prompts_carry_the_person_rule()
    print("PASS: same-scope identity reconciles split entities and keeps distinct ones apart")


if __name__ == "__main__":
    main()
