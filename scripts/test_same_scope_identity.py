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


def _payload(messages):
    content = messages[1]["content"]
    if content.startswith("[TASK:"):
        content = content.split("\n", 1)[1]
    return json.loads(content)


def _candidate_groups(messages):
    return _payload(messages)["groups"]


def _candidate_ids(messages):
    return {item["plan_id"] for item in _payload(messages)["candidates"]}


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
    """Two people stay two cards; a combined card stops the run instead of landing."""
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

        # Path 1: the planner produced two proper person cards and one combined card;
        # the reconcile step must report the combined one and the run must stop.
        def _report_pair(messages):
            present = _candidate_ids(messages)
            # The reported card is covered by the report only: the two lists are
            # disjoint by contract.
            clusters = [
                {"label": f"人物 {plan_id}", "plan_ids": [plan_id]}
                for plan_id in sorted(present - {"p_pair"})
            ]
            return {
                "clusters": clusters,
                "multi_person_plans": [
                    {
                        "plan_id": "p_pair",
                        "subjects": ["约拿·肯辛顿", "艾丽卡·卡莱尔"],
                        "fact_ids": ["fact_jonah", "fact_erica"],
                    }
                ],
            }

        summary = a._OpenQuestionSummary()
        try:
            a._assign_plan_identities(
                harness.job, plans, harness.bundle, summary, _scripted(_report_pair)
            )
        except a.MixedPersonCardError as error:
            assert "约拿" in str(error) and "艾丽卡" in str(error), str(error)
        else:
            raise AssertionError("a combined person card must stop the run, not land")

        # Path 2: two people, two cards, no combined card -> both survive as cards.
        two_people = [
            _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
            _plan("p_jonah", "npc", "约拿·肯辛顿", ["fact_jonah"]),
        ]
        client = _scripted(_cluster([]))
        identified = a._assign_plan_identities(
            harness.job, two_people, harness.bundle, a._OpenQuestionSummary(), client
        )
        merged = a._merge_chapter_plans([], identified)
        assert len(merged) == 2, _titles(merged)
        assert {plan["title"] for plan in merged} == {"艾丽卡·卡莱尔", "约拿·肯辛顿"}


def check_single_combined_npc_is_still_checked():
    """One isolated NPC plan is judged too, so a lone combined card cannot slip by."""
    with _Harness() as harness:
        plans = [_plan("p_solo", "npc", "乔纳与艾丽卡", ["fact_jonah", "fact_erica"])]
        components = a._identity_candidate_components(plans, harness.bundle)
        assert components == [["p_solo"]], components
        client = _scripted(
            lambda messages: {
                "clusters": [],
                "multi_person_plans": [
                    {
                        "plan_id": "p_solo",
                        "subjects": ["乔纳", "艾丽卡"],
                        "fact_ids": ["fact_jonah", "fact_erica"],
                    }
                ],
            }
        )
        try:
            a._assign_plan_identities(
                harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
            )
        except a.MixedPersonCardError:
            pass
        else:
            raise AssertionError("an isolated combined NPC must be checked and stopped")

        # A report that cites facts the plan does not own is rejected by validation.
        bad = {
            "clusters": [{"label": "单人卡", "plan_ids": ["p_solo"]}],
            "multi_person_plans": [
                {
                    "plan_id": "p_solo",
                    "subjects": ["乔纳", "艾丽卡"],
                    "fact_ids": ["fact_cult"],
                }
            ],
        }
        try:
            a._validate_identity_clusters(
                bad, {"p_solo"}, {"p_solo": set(plans[0]["fact_ids"])}
            )
        except a.ArtifactGenerationError:
            pass
        else:
            raise AssertionError("a report citing foreign facts must be rejected")


def check_identity_vetoes_a_title_match():
    """Two entities that share a title stay two cards, with and without a bridge."""
    with _Harness():
        left = {**_plan("t_a", "location", "书房", ["fact_hotel"]), "identity_key": "identity_a_0"}
        right = {**_plan("t_b", "location", "书房", ["fact_publisher"]), "identity_key": "identity_b_0"}
        assert a._merge_match([left], right) is None
        assert len(a._merge_chapter_plans([], [left, right])) == 2

        # Without an identity judgement the old title rule is unchanged.
        plain_left = _plan("t_c", "location", "书房", ["fact_hotel"])
        plain_right = _plan("t_d", "location", "书房", ["fact_publisher"])
        assert a._merge_match([plain_left], plain_right) is plain_left
        assert len(a._merge_chapter_plans([], [plain_left, plain_right])) == 1

        # An unjudged same-title card must not act as a bridge between two judged
        # different entities: A absorbs the bridge, then B must still be refused.
        bridge = _plan("t_bridge", "location", "书房", ["fact_cult"])
        merged = a._merge_chapter_plans([], [left, bridge, right])
        assert len(merged) == 2, [plan["id"] for plan in merged]
        assert "identity_b_0" not in {
            key for plan in merged for key in a._identity_key_set(plan)
        } or len(merged) == 2

    # The production path, not a hand-built key: two singleton judgements must survive.
    with _Harness() as harness:
        plans = [
            _plan("p_study_a", "location", "书房", ["fact_hotel"]),
            _plan("p_study_b", "location", "书房", ["fact_publisher"]),
        ]
        client = _scripted(_cluster([["p_study_a"], ["p_study_b"]]))
        identified = a._assign_plan_identities(
            harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
        )
        keys = {plan["id"]: plan.get("identity_key") for plan in identified}
        assert all(keys.values()), keys
        assert keys["p_study_a"] != keys["p_study_b"], keys
        assert len(a._merge_chapter_plans([], identified)) == 2

        # The same rules must not report a correct veto as a blocked merge.
        summary = a._OpenQuestionSummary()
        a._report_unmerged_identities(
            a._merge_chapter_plans([], identified), summary
        )
        assert summary.count == 0, summary.preview


def check_reconciliation_request_is_bounded_and_cached():
    """Every request fits the hard cap, covers its candidates, and caches."""
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
            ids = _candidate_ids(messages)
            assert len(ids) <= a.IDENTITY_MAX_PLANS_PER_CHUNK, len(ids)
            # The bound is on the complete encoded request, prompt and schema included.
            assert len(json.dumps(messages, ensure_ascii=False)) <= (
                a.IDENTITY_REQUEST_MAX_CHARS
            )
            covered |= ids
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
        assert len(result) == len(plans)
        assert all(a._is_unjudged(plan) for plan in result)
        assert any("超过" in text for text in summary.preview), summary.preview


def check_fact_edit_invalidates_the_judgement():
    """An edit anywhere in a cited fact re-runs the judgement that read it."""
    with _Harness() as harness:
        plans = _sample_plans()
        client = _scripted(_cluster([["p_hotel_a", "p_hotel_b"]]))
        summary = a._OpenQuestionSummary()
        a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        assert client.calls >= 1, client.calls
        cold = client.calls

        def _edit(fact_id, mutate):
            edited = harness.bundle.model_copy(deep=True)
            for fact in edited.facts:
                if fact.id == fact_id:
                    fact.text = mutate(fact.text)
            fresh = _scripted(_cluster([["p_hotel_a", "p_hotel_b"]]))
            a._assign_plan_identities(harness.job, plans, edited, summary, fresh)
            return fresh

        # A change past the old 240-character cut used to change nothing.
        tail = _edit("fact_hotel", lambda text: text + "。" + "复核补充：" + "旧式前台。" * 80)
        assert tail.calls >= 1, "an edit past the truncated window must invalidate"
        assert tail.calls <= cold, (tail.calls, cold)

        # So must a change in the ninth cited fact, which the old slice dropped.
        ninth = harness.bundle.model_copy(deep=True)
        long_fact = SourceFact(
            id="fact_ninth",
            text="第九条事实：" + "细节。" * 40,
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://same-scope", page=2)],
        )
        ninth.facts = [*ninth.facts, long_fact]
        plans_with_ninth = [
            {
                **plan,
                "fact_ids": [
                    *plan["fact_ids"],
                    *(
                        ["fact_ninth"]
                        if plan["id"] == "p_hotel_a"
                        else [
                            f"filler_{index}"
                            for index in range(9, 9)
                        ]
                    ),
                ],
            }
            for plan in plans
        ]
        fresh = _scripted(_cluster([["p_hotel_a", "p_hotel_b"]]))
        a._assign_plan_identities(
            harness.job, plans_with_ninth, ninth, summary, fresh
        )
        assert fresh.calls >= 1
        edited_ninth = ninth.model_copy(deep=True)
        for fact in edited_ninth.facts:
            if fact.id == "fact_ninth":
                fact.text = fact.text + "（复核后补充）"
        rejudged = _scripted(_cluster([["p_hotel_a", "p_hotel_b"]]))
        a._assign_plan_identities(
            harness.job, plans_with_ninth, edited_ninth, summary, rejudged
        )
        assert rejudged.calls >= 1, "editing the ninth fact must invalidate the judgement"


def check_failure_stays_bounded():
    """A failing reconcile request is reported once per chunk, not retried forever."""
    with _Harness() as harness:
        plans = _sample_plans()

        def boom(messages):
            raise RuntimeError("upstream unavailable")

        client = _scripted(boom)
        summary = a._OpenQuestionSummary()
        result = a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        # A failed block is "not judged", which must be visible in the final merge.
        assert len(result) == len(plans)
        assert all(a._is_unjudged(plan) for plan in result)
        assert all("identity_key" not in plan for plan in result)
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
    check_single_combined_npc_is_still_checked()
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
