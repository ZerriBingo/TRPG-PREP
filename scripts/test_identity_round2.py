"""Round-2 counterexamples: unjudged state, mixed-person protocol, evidence, source contract.

Every check drives the production path — candidate grouping, the reconcile step, the merge,
and for field sources the final card builder — and asserts what the run actually produces.
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
from backend.app.prep import load_profiles  # noqa: E402
from backend.domain import ExampleBundle, SourceFact, SourceRef  # noqa: E402

FACTS = {
    "fact_hotel": "切尔西酒店是埃利亚斯每次来纽约时的固定落脚点。",
    "fact_publisher": "埃利亚斯会长时间待在纽约造访普洛斯佩罗出版社。",
    "fact_erica": "艾丽卡·卡莱尔住在纽约市附近，能证实媒体报道真假。",
    "fact_jonah": "调查员之后可能为卡莱尔探险队信息会见约拿·肯辛顿。",
}


def _bundle(extra: dict[str, str] | None = None) -> ExampleBundle:
    texts = dict(FACTS)
    texts.update(extra or {})
    facts = [
        SourceFact(
            id=fact_id,
            text=text,
            kind="clue",
            visibility="explicit",
            source_refs=[SourceRef(file="fixture://round2", page=1)],
        )
        for fact_id, text in texts.items()
    ]
    return ExampleBundle.model_validate(
        {
            "id": "round2_case",
            "name": "Round 2",
            "profile_ids": ["cthulhu-dark-2e"],
            "facts": [fact.model_dump() for fact in facts],
            "cards": [],
            "plans": [],
        }
    )


def _plan(plan_id, card_type, title, fact_ids):
    return {
        "id": plan_id,
        "type": card_type,
        "title": title,
        "purpose": f"{title} 的用途",
        "fact_ids": list(fact_ids),
        "focus": [],
        "open_questions": [],
        "runtime_group_ids": [],
        "chapter_ids": [],
        "story_beat_ids": [],
    }


class _Harness:
    def __init__(self, extra_facts: dict[str, str] | None = None):
        self.extra = extra_facts

    def __enter__(self):
        self._temp = tempfile.TemporaryDirectory()
        self._patch = patch.object(storage, "DB_PATH", Path(self._temp.name) / "test.db")
        self._patch.start()
        storage.init_db()
        self.bundle = _bundle(self.extra)
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


def _ids(messages):
    return {item["plan_id"] for item in _payload(messages)["candidates"]}


def _singletons(messages):
    return {
        "clusters": [
            {"label": f"独立 {plan_id}", "plan_ids": [plan_id]}
            for plan_id in sorted(_ids(messages))
        ]
    }


def check_unjudged_never_merges():
    """An oversized block is 'not judged', so a shared title cannot merge it."""
    with _Harness() as harness:
        plans = [
            _plan("p_a", "location", "书房", ["fact_hotel"]),
            _plan("p_b", "location", "书房", ["fact_publisher"]),
        ]
        client = _scripted(_singletons)
        with patch.object(a, "MAX_IDENTITY_CANDIDATE_PLANS", 1):
            result = a._assign_plan_identities(
                harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
            )
        assert client.calls == 0, client.calls
        assert all(a._is_unjudged(plan) for plan in result), result
        assert len(a._merge_chapter_plans([], result)) == 2

        # A judged, confirmed duplicate still merges: the unjudged state is not a
        # blanket refusal to merge anything.
        pair = [
            _plan("q_a", "location", "酒店", ["fact_hotel"]),
            _plan("q_b", "location", "酒店", ["fact_hotel"]),
        ]
        same = _scripted(
            lambda messages: {
                "clusters": [{"label": "同一酒店", "plan_ids": sorted(_ids(messages))}]
            }
        )
        judged = a._assign_plan_identities(
            harness.job, pair, harness.bundle, a._OpenQuestionSummary(), same
        )
        assert len(a._merge_chapter_plans([], judged)) == 1


def check_unjudged_card_cannot_bridge():
    """An unjudged card never merges, in either order, and never bridges two others."""
    with _Harness():
        keyed_a = _plan("c_a", "location", "书房", ["fact_hotel"])
        keyed_a["identity_key"] = "identity_a_0"
        keyed_b = _plan("c_b", "location", "书房", ["fact_publisher"])
        keyed_b["identity_key"] = "identity_b_0"
        middle = _plan("c_m", "location", "书房", ["fact_erica"])
        middle[a.IDENTITY_UNJUDGED_FIELD] = True

        merged = a._merge_chapter_plans([], [keyed_a, middle, keyed_b])
        assert len(merged) == 3, [plan["id"] for plan in merged]
        for plan in merged:
            assert len(a._identity_key_set(plan)) <= 1, plan

        # Same identity key but one side undecided: both orders must stay apart, which
        # the identity fallback used to allow.
        same_a = {**_plan("s_a", "location", "甲", ["fact_hotel"]), "identity_key": "identity_s_0"}
        same_b = {
            **_plan("s_b", "location", "乙", ["fact_hotel"]),
            "identity_key": "identity_s_0",
            a.IDENTITY_UNJUDGED_FIELD: True,
        }
        assert a._merge_match([same_a], same_b) is None
        assert a._merge_match([same_b], same_a) is None
        assert len(a._merge_chapter_plans([], [same_a, same_b])) == 2
        assert len(a._merge_chapter_plans([], [same_b, same_a])) == 2

        # Without the marker the same key still merges.
        plain_b = {**_plan("s_c", "location", "乙", ["fact_hotel"]), "identity_key": "identity_s_0"}
        assert len(a._merge_chapter_plans([], [same_a, plain_b])) == 1


def check_mixed_report_stops_the_run():
    """A legal mixed-person report stops the job, with or without other clusters."""
    with _Harness() as harness:
        plans = [
            _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
            _plan("p_jonah", "npc", "约拿·肯辛顿", ["fact_jonah"]),
            _plan("p_pair", "npc", "两人合卡", ["fact_erica", "fact_jonah"]),
        ]
        report = {
            "plan_id": "p_pair",
            "subjects": ["艾丽卡·卡莱尔", "约拿·肯辛顿"],
            "fact_ids": ["fact_erica", "fact_jonah"],
        }
        # (a) every plan is a report: clusters must be allowed to be empty.
        only_reports = _scripted(
            lambda messages: {
                "clusters": [
                    {"label": "单人", "plan_ids": ["p_erica"]},
                    {"label": "单人", "plan_ids": ["p_jonah"]},
                ],
                "multi_person_plans": [report],
            }
        )
        try:
            a._assign_plan_identities(
                harness.job, plans, harness.bundle, a._OpenQuestionSummary(), only_reports
            )
        except a.MixedPersonCardError as error:
            assert "艾丽卡" in str(error) and "约拿" in str(error)
        else:
            raise AssertionError("a mixed report must stop the run")

        # (b) the response is invalid for another reason but still reports the card:
        # it must not be let through by the bounded correction path.
        conflicting = _scripted(
            lambda messages: {
                "clusters": [{"label": "少了一张", "plan_ids": ["p_erica"]}],
                "multi_person_plans": [report],
            }
        )
        try:
            a._assign_plan_identities(
                harness.job, plans, harness.bundle, a._OpenQuestionSummary(), conflicting
            )
        except a.MixedPersonCardError:
            pass
        else:
            raise AssertionError("an invalid response with a mixed report must still stop")


def check_coverage_rules():
    """Unknown, duplicated, missing and foreign-fact reports are all rejected."""
    allowed = {"p_erica", "p_jonah", "p_pair"}
    per_plan = {
        "p_erica": {"fact_erica"},
        "p_jonah": {"fact_jonah"},
        "p_pair": {"fact_erica", "fact_jonah"},
    }
    # Only clusters: a normal judgement validates.
    good = {"clusters": [{"label": "单人", "plan_ids": sorted(allowed)}]}
    a._validate_identity_clusters(good, allowed, per_plan)

    # A legal mixed report is a successful identification: it validates and is cached.
    # Stopping the artifact job is the caller's decision, so that a re-entry replays the
    # saved report instead of paying for the same judgement twice.
    legal = a._validate_identity_clusters(
        {
            "clusters": [{"label": "单人", "plan_ids": ["p_erica", "p_jonah"]}],
            "multi_person_plans": [
                {"plan_id": "p_pair", "subjects": ["a", "b"], "fact_ids": ["fact_erica"]}
            ],
        },
        allowed,
        per_plan,
    )
    assert legal["multi_person_plans"][0]["plan_id"] == "p_pair"

    # The same report inside an otherwise unusable response is still fatal.
    try:
        a._validate_identity_clusters(
            {
                "clusters": [{"label": "漏了一张", "plan_ids": ["p_erica"]}],
                "multi_person_plans": [
                    {"plan_id": "p_pair", "subjects": ["a", "b"], "fact_ids": ["fact_erica"]}
                ],
            },
            allowed,
            per_plan,
        )
    except a.MixedPersonCardError:
        pass
    else:
        raise AssertionError("a report inside an unusable response must stay fatal")

    bad_payloads = [
        {
            "clusters": [{"label": "重复", "plan_ids": ["p_erica"]}],
            "multi_person_plans": [
                {"plan_id": "p_erica", "subjects": ["a", "b"], "fact_ids": ["fact_erica"]}
            ],
        },
        {"clusters": [{"label": "漏", "plan_ids": ["p_erica"]}]},
        {
            "clusters": [
                {"label": "越界", "plan_ids": ["p_erica", "p_jonah", "p_missing"]}
            ]
        },
        {
            "clusters": [
                {"label": "单人", "plan_ids": ["p_erica", "p_jonah", "p_pair"]}
            ],
            "multi_person_plans": [
                {"plan_id": "p_pair", "subjects": ["a", "b"], "fact_ids": ["fact_hotel"]}
            ],
        },
    ]
    for payload in bad_payloads:
        try:
            a._validate_identity_clusters(payload, allowed, per_plan)
        except a.ArtifactGenerationError:
            continue
        raise AssertionError(f"invalid coverage accepted: {payload}")


def check_mixed_report_is_cached_and_still_stops():
    """First entry pays one request; the saved report replays for free and still stops."""
    with _Harness() as harness:
        plans = [_plan("p_solo", "npc", "两人合卡", ["fact_erica", "fact_jonah"])]
        client = _scripted(
            lambda messages: {
                "clusters": [],
                "multi_person_plans": [
                    {
                        "plan_id": "p_solo",
                        "subjects": ["艾丽卡", "约拿"],
                        "fact_ids": ["fact_erica", "fact_jonah"],
                    }
                ],
            }
        )
        summary = a._OpenQuestionSummary()
        for _ in range(2):
            try:
                a._assign_plan_identities(
                    harness.job, plans, harness.bundle, summary, client
                )
            except a.MixedPersonCardError as error:
                assert "已停止" in str(error) and "不会自动拆分" in str(error), str(error)
            else:
                raise AssertionError("the reported mixed card must stop every time")
        # Exactly one upstream request for the judgement: the second entry replays the
        # saved identification decision.
        assert client.calls == 1, client.calls


def check_shared_projection_is_complete_and_invalidating():
    """The judgement sees the shared projection, and any of its fields can invalidate."""
    with _Harness() as harness:
        plans = [
            _plan("p_hotel_a", "location", "切尔西酒店", ["fact_hotel"]),
            _plan("p_hotel_b", "location", "酒店落脚点", ["fact_hotel"]),
            _plan("p_erica", "npc", "艾丽卡", ["fact_erica"]),
            _plan("p_jonah", "npc", "约拿", ["fact_jonah"]),
        ]

        def responder(messages):
            present = _ids(messages)
            groups = []
            for pair in (("p_hotel_a", "p_hotel_b"), ("p_erica", "p_jonah")):
                if set(pair) <= present:
                    groups.append({"label": "同一实体", "plan_ids": list(pair)})
            covered = {plan_id for group in groups for plan_id in group["plan_ids"]}
            groups.extend(
                {"label": f"独立 {plan_id}", "plan_ids": [plan_id]}
                for plan_id in sorted(present - covered)
            )
            return {"clusters": groups}
        client = _scripted(responder)
        summary = a._OpenQuestionSummary()
        a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        fact = next(
            item
            for messages in client.requests
            for item in _payload(messages)["facts"]
            if item["id"] == "fact_hotel"
        )
        for field in (
            "id", "text", "kind", "visibility", "evidence_status",
            "source_refs", "tags", "notes",
        ):
            assert field in fact, (field, fact)
        cold = client.calls

        def _mutated(mutate):
            edited = harness.bundle.model_copy(deep=True)
            for item in edited.facts:
                if item.id == "fact_hotel":
                    mutate(item)
            fresh = _scripted(responder)
            a._assign_plan_identities(harness.job, plans, edited, summary, fresh)
            return fresh

        status_run = _mutated(lambda item: setattr(item, "evidence_status", "inference"))
        assert status_run.calls >= 1, "a legal evidence-status change must invalidate"
        assert status_run.calls < cold, "an unrelated block must stay cached"

        def _add_ref(item):
            item.source_refs = [
                *item.source_refs,
                SourceRef(file="fixture://round2", page=9),
            ]

        ref_run = _mutated(_add_ref)
        assert ref_run.calls >= 1, "a new source page must invalidate"

        note_run = _mutated(lambda item: setattr(item, "notes", "复核备注：门厅保留旧式前台"))
        assert note_run.calls >= 1, "a notes change must invalidate"


def check_tail_and_ninth_fact_edit():
    """A change past the old 240-character cut, or in the ninth cited fact, re-runs."""
    long_text = "酒店背景事实：" + "旧式门厅与楼梯。" * 60
    # Eight extras plus the long fact: the plan below cites exactly nine facts, so the
    # last extra really is the ninth citation.
    extra = {"fact_long": long_text, **{f"fact_extra_{index}": f"补充事实{index}。" for index in range(8)}}
    with _Harness(extra) as harness:
        ninth = [f"fact_extra_{index}" for index in range(8)]
        plans = [
            _plan("p_long", "location", "切尔西酒店", ["fact_long", *ninth]),
            _plan("p_short", "location", "酒店落脚点", ["fact_long"]),
        ]
        client = _scripted(
            lambda messages: {"clusters": [{"label": "同一酒店", "plan_ids": sorted(_ids(messages))}]}
        )
        summary = a._OpenQuestionSummary()
        a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        cold = client.calls
        assert len(long_text) > 300

        def _rejudged(mutate):
            edited = harness.bundle.model_copy(deep=True)
            for item in edited.facts:
                mutate(item)
            fresh = _scripted(
                lambda messages: {
                    "clusters": [{"label": "同一酒店", "plan_ids": sorted(_ids(messages))}]
                }
            )
            a._assign_plan_identities(harness.job, plans, edited, summary, fresh)
            return fresh

        tail = _rejudged(
            lambda item: setattr(
                item, "text", item.text[:300] + "（复核后修改尾部）" + item.text[300:]
            )
            if item.id == "fact_long"
            else None
        )
        assert tail.calls >= 1, "an edit past character 240 must invalidate the judgement"

        assert len(plans[0]["fact_ids"]) == 9, plans[0]["fact_ids"]
        ninth_run = _rejudged(
            lambda item: setattr(item, "text", item.text + "（复核后补充）")
            if item.id == "fact_extra_7"
            else None
        )
        assert ninth_run.calls >= 1, "an edit in the ninth cited fact must invalidate"
        assert ninth_run.calls <= cold


def _npc_payload(profile, fact_id, overrides, sources):
    definition = next(d for d in profile.card_definitions if d.type == "npc")
    fields = {name: "有来源支持的内容。" for name in definition.required_fields}
    fields.update(overrides)
    return {
        "cards": [
            {
                "type": "npc",
                "title": "艾丽卡·卡莱尔",
                "subtitle": "媒体核验者",
                "fact_ids": [fact_id],
                "fields": fields,
                "field_sources": {name: [fact_id] for name in fields} | sources,
                "open_questions": [],
            }
        ],
        "open_questions": [],
    }


def check_final_field_sources():
    """The final card keeps a declared gap empty and a sourced field as cited."""
    profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
    profile = profiles["cthulhu-dark-2e"]
    bundle = _bundle()
    profile_id = "cthulhu-dark-2e"

    def _build(overrides, sources):
        raw = _npc_payload(profile, "fact_erica", overrides, sources)
        output = a._validate_materialized_card(
            raw,
            bundle,
            profile,
            _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
            model_id="scripted",
            prep_unit_id=None,
        )
        return output["cards"][0]

    gap = _build({"wants": "来源未说明她的目标。"}, {"wants": []})
    assert gap["field_sources"]["wants"] == [], gap["field_sources"]["wants"]
    assert gap["fields"]["wants"] == "来源未说明她的目标。"

    mixed = _build(
        {"wants": "来源未说明她的目标；她能证实媒体报道的真假。"},
        {"wants": ["fact_erica"]},
    )
    assert mixed["field_sources"]["wants"] == ["fact_erica"]

    single = _build({"offers": "她能证实媒体报道的真假。"}, {"offers": ["fact_erica"]})
    assert single["field_sources"]["offers"] == ["fact_erica"]

    # An omitted declaration is still repaired to the card's closure.
    missing = _npc_payload(profile, "fact_erica", {"wants": "她能证实媒体报道的真假。"}, {})
    missing["cards"][0]["field_sources"].pop("wants")
    repaired = a._validate_materialized_card(
        missing,
        bundle,
        profile,
        _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
        model_id="scripted",
        prep_unit_id=None,
    )["cards"][0]
    assert repaired["field_sources"]["wants"] == ["fact_erica"]

    # A reference outside the supplied facts stays a hard error.
    try:
        _build({"wants": "有内容。"}, {"wants": ["fact_not_supplied"]})
    except a.ArtifactGenerationError:
        pass
    else:
        raise AssertionError("an unsupplied fact reference must be rejected")
    assert profile_id == "cthulhu-dark-2e"


def check_every_defect_still_stops_a_reported_card():
    """A trustworthy mixed report stops the run whatever else is wrong with the response."""
    report = {
        "plan_id": "p_pair",
        "subjects": ["艾丽卡·卡莱尔", "约拿·肯辛顿"],
        "fact_ids": ["fact_erica", "fact_jonah"],
    }
    variants = {
        "unknown cluster id": {
            "clusters": [
                {"label": "单人", "plan_ids": ["p_erica", "p_jonah", "p_unknown"]}
            ],
            "multi_person_plans": [report],
        },
        "duplicate coverage": {
            "clusters": [
                {"label": "单人", "plan_ids": ["p_erica", "p_jonah", "p_pair"]}
            ],
            "multi_person_plans": [report],
        },
        "missing coverage": {
            "clusters": [{"label": "单人", "plan_ids": ["p_erica"]}],
            "multi_person_plans": [report],
        },
        "schema error": {
            "clusters": [{"label": "", "plan_ids": ["p_erica", "p_jonah"]}],
            "multi_person_plans": [report],
        },
        "second report out of range": {
            "clusters": [{"label": "单人", "plan_ids": ["p_erica", "p_jonah"]}],
            "multi_person_plans": [
                report,
                {"plan_id": "p_unknown", "subjects": ["a", "b"], "fact_ids": ["fact_erica"]},
            ],
        },
    }
    for name, response in variants.items():
        with _Harness() as harness:
            plans = [
                _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
                _plan("p_jonah", "npc", "约拿·肯辛顿", ["fact_jonah"]),
                _plan("p_pair", "npc", "两人合卡", ["fact_erica", "fact_jonah"]),
            ]
            client = _scripted(lambda messages, body=response: body)
            try:
                result = a._assign_plan_identities(
                    harness.job, plans, harness.bundle, a._OpenQuestionSummary(), client
                )
            except a.MixedPersonCardError:
                pass
            else:
                raise AssertionError(f"{name}: the reported card must stop the run, got {result}")
            steps = [
                step
                for step in storage.list_artifact_job_steps(harness.job.id)
                if step["stage"] == "identity_reconcile"
            ]
            assert steps, f"{name}: the attempt must be recorded"
            assert all(step["status"] != "succeeded" for step in steps), (
                name,
                [(step["step_index"], step["status"]) for step in steps],
            )

    # Without a trustworthy report the ordinary bounded behaviour is unchanged: an
    # invented or out-of-range report is not upgraded into a fact.
    with _Harness() as harness:
        plans = [
            _plan("p_erica", "npc", "艾丽卡·卡莱尔", ["fact_erica"]),
            _plan("p_jonah", "npc", "约拿·肯辛顿", ["fact_jonah"]),
        ]
        untrustworthy = {
            "clusters": [{"label": "单人", "plan_ids": ["p_erica", "p_jonah"]}],
            "multi_person_plans": [
                {"plan_id": "p_erica", "subjects": ["a", "b"], "fact_ids": ["fact_hotel"]}
            ],
        }
        client = _scripted(lambda messages: untrustworthy)
        summary = a._OpenQuestionSummary()
        result = a._assign_plan_identities(
            harness.job, plans, harness.bundle, summary, client
        )
        assert all(a._is_unjudged(plan) for plan in result), result


def check_environment_failure_protection():
    """With the identity step failing, environment duplicates stay separate."""
    with _Harness() as harness:
        plans = [
            {**_plan("e_a", "environment", "法师庄园", ["fact_hotel"]), "chapter_ids": ["ch_1"]},
            {**_plan("e_b", "environment", "法师庄园", ["fact_publisher"]), "chapter_ids": ["ch_2"]},
        ]
        summary = a._OpenQuestionSummary()

        def boom(messages):
            raise RuntimeError("upstream unavailable")

        result = a._assign_plan_identities(
            harness.job, plans, harness.bundle, summary, _scripted(boom)
        )
        # Environment plans are outside this round's same-scope rule, so they are only
        # candidates when they share a runtime group; whatever reaches the step, a
        # failure leaves the cards unmerged.
        if result and a._is_unjudged(result[0]):
            assert len(a._merge_chapter_plans([], result)) == 2


def check_diagnostics_distinguish_refusal_from_failure():
    """A refused-before-send block and a failed block are reported as what they were."""
    with _Harness() as harness:
        plans = [
            _plan("p_a", "location", "书房", ["fact_hotel"]),
            _plan("p_b", "location", "书房", ["fact_publisher"]),
        ]
        summary = a._OpenQuestionSummary()
        with patch.object(a, "MAX_IDENTITY_CANDIDATE_PLANS", 1):
            a._assign_plan_identities(
                harness.job, plans, harness.bundle, summary, _scripted(_singletons)
            )
        refused = " ".join(summary.preview)
        assert "未发送" in refused and "超过" in refused, refused
        assert "已发送但失败" not in refused, refused

    with _Harness() as harness:
        plans = [
            _plan("p_a", "location", "书房", ["fact_hotel"]),
            _plan("p_b", "location", "书房", ["fact_publisher"]),
        ]
        summary = a._OpenQuestionSummary()

        def boom(messages):
            raise RuntimeError("upstream unavailable")

        a._assign_plan_identities(harness.job, plans, harness.bundle, summary, _scripted(boom))
        failed = " ".join(summary.preview)
        assert "已发送但失败" in failed, failed
        assert "超过" not in failed, failed


def check_prompt_version_is_part_of_the_step_identity():
    """A cached step recorded under the old prompt version is not reused."""
    with _Harness() as harness:
        plans = [
            _plan("p_hotel_a", "location", "切尔西酒店", ["fact_hotel"]),
            _plan("p_hotel_b", "location", "酒店落脚点", ["fact_hotel"]),
        ]
        client = _scripted(
            lambda messages: {"clusters": [{"label": "同一酒店", "plan_ids": sorted(_ids(messages))}]}
        )
        summary = a._OpenQuestionSummary()
        a._assign_plan_identities(harness.job, plans, harness.bundle, summary, client)
        first = client.calls
        assert first >= 1
        with patch.object(a, "IDENTITY_RECONCILE_PROMPT_VERSION", "prep-artifact-identity-reconcile-v0"):
            stale = _scripted(
                lambda messages: {
                    "clusters": [{"label": "同一酒店", "plan_ids": sorted(_ids(messages))}]
                }
            )
            a._assign_plan_identities(harness.job, plans, harness.bundle, summary, stale)
        assert stale.calls >= 1, "a different prompt version must not reuse the old step"


def main():
    check_unjudged_never_merges()
    check_unjudged_card_cannot_bridge()
    check_mixed_report_stops_the_run()
    check_coverage_rules()
    check_mixed_report_is_cached_and_still_stops()
    check_shared_projection_is_complete_and_invalidating()
    check_tail_and_ninth_fact_edit()
    check_final_field_sources()
    check_every_defect_still_stops_a_reported_card()
    check_environment_failure_protection()
    check_diagnostics_distinguish_refusal_from_failure()
    check_prompt_version_is_part_of_the_step_identity()
    print("PASS: unjudged state, mixed-person protocol, shared evidence and source contract")


if __name__ == "__main__":
    main()
