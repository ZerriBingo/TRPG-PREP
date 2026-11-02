"""A handout's source format must not exclude its content from preparation.

A letter, log or newspaper is stored as a handout because that is what it *is*; the
same source can carry leads, motives and investigable places.  These checks drive real
planning input and the real coverage validator with synthetic facts on both boards and
confirm: a letter with a clue reaches planning, an explicitly classified display asset
does not, a handout with a chapter disposition is required by coverage, a model
candidate and an out-of-page-range fact never appear, and fact closure plus page
provenance survive.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

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
    load_profiles,
    validate_bundle,
)

PROFILES = load_profiles(ROOT / "backend" / "domain" / "profiles")

BOARDS = {
    "cthulhu-dark-2e": {"card_type": "location", "chapter": "chapter_lighthouse"},
    "daggerheart": {"card_type": "environment", "chapter": "chapter_gate"},
}

LETTER_TEXT = "值守日志末页写着：钥匙藏于北塔地板之下，只有守塔人知道位置。"
INVITE_TEXT = "邀请函写明 11 月 3 日夜在灯塔举行聚会，并点名了几位来客。"
MAP_TEXT = "航标岛全岛地图，纯绘图，没有可操作说明。"


def fact(fact_id, text, *, kind="handout", page=5, evidence="source_fact"):
    return SourceFact(
        id=fact_id,
        source_refs=[SourceRef(file="fixture://letter-module", page=page)],
        evidence_status=evidence,
        text=text,
        kind=kind,
        visibility="explicit",
    )


def build_bundle(board: str, *, chaptered: bool = True) -> ExampleBundle:
    chapter = BOARDS[board]["chapter"]
    letter = fact("fact_letter_clue", LETTER_TEXT, page=5)
    invite = fact("fact_invite_chapter", INVITE_TEXT, page=6)
    map_asset = fact("fact_map_asset", MAP_TEXT, page=7)
    candidate = fact("fact_letter_candidate", "模型提出的线索候选", page=5, evidence="model_candidate")
    distant = fact("fact_distant_page", "第 99 页的情节事实", kind="event", page=99)
    dispositions = [
        {"fact_id": invite.id, "kind": "chapter", "chapter_ids": [chapter]},
        # The map is the pure display asset of this fixture: an explicit display-only
        # decision, together with the display material record created for it.
        {"fact_id": map_asset.id, "kind": "display_material"},
    ]
    # The reality board reaches planning without an explicit disposition for the
    # letter; the fantasy board plans from confirmed chapter assignments, so the
    # structure stage has already dispositioned it there.
    if board == "daggerheart":
        dispositions.append(
            {"fact_id": letter.id, "kind": "chapter", "chapter_ids": [chapter]}
        )
    return ExampleBundle.model_validate({
        "id": f"handout_use_{board.replace('-', '_')}_{'chaptered' if chaptered else 'chapterless'}",
        "name": "Handout narrative use",
        "profile_ids": [board],
        "facts": [letter, invite, map_asset, candidate, distant],
        "cards": [],
        "plans": [],
        "adventure_chapters": [{
            "id": chapter,
            "title": "灯塔之夜",
            "summary": "来客与钥匙的夜晚。",
            "fact_ids": [letter.id, invite.id],
        }] if chaptered else [],
        # Without chapters only the display-only decision can exist, and it still
        # keeps the pure asset out of planning.
        "fact_dispositions": dispositions if chaptered else [
            item for item in dispositions if item["kind"] == "display_material"
        ],
        "display_materials": [{
            "id": "material_m_handout_use",
            "title": "航标岛全图",
            "source_fact_ids": [map_asset.id],
            "source_refs": [{"file": "fixture://letter-module", "page": 7}],
            "gm_notes": "需要辨认岛上位置时展示。",
            "links": [],
        }],
    })


def fact_letter() -> SourceFact:
    return fact("fact_review_letter", LETTER_TEXT, page=5)


def save_prep_scope(bundle: ExampleBundle, board: str) -> None:
    job = PrepJob(
        id=f"prep_job_{bundle.id}",
        status="completed",
        scope=PrepScope(
            source_file="fixture://letter-module",
            source_version="v1",
            page_spans=[PageSpan(start=5, end=7)],
            profile_id=board,
            objective="页面范围检查",
        ),
        model_id="fixture",
        prompt_version="test",
        schema_version="test",
        workspace_id=bundle.id,
        windows=[
            ExtractionWindow(
                id="prep_window_handout_use",
                page_span=PageSpan(start=5, end=7),
                core_span=PageSpan(start=5, end=7),
                status="succeeded",
                candidate_count=0,
                input_chars=10,
            )
        ],
        candidate_count=0,
        created_at=storage.now(),
        updated_at=storage.now(),
    )
    storage.create_prep_job(job.model_dump(mode="json"))


def plan_payload(board: str, fact_ids: list[str]) -> dict:
    chapter = BOARDS[board]["chapter"]
    cards = [{
        "type": BOARDS[board]["card_type"],
        "title": "灯塔现场",
        "purpose": "调查灯塔与来客的夜晚",
        "fact_ids": fact_ids,
        "chapter_ids": [chapter],
    }]
    if board == "cthulhu-dark-2e":
        # This board requires a chapter overview card in every plan.
        cards.append({
            "type": "chapter_overview",
            "title": "灯塔之夜总览",
            "purpose": "概述本夜的调查范围",
            "fact_ids": fact_ids,
            "chapter_ids": [chapter],
        })
    return {
        "cards": cards,
        "brief": {"premise": "灯塔重新点亮。", "current_situation": "来客陆续抵达。"},
        "chapter_briefs": [{
            "chapter_id": chapter,
            "brief": {"premise": "灯塔重新点亮。", "current_situation": "来客陆续抵达。"},
        }],
    }


def check_board(board: str) -> None:
    settings = BOARDS[board]
    chapter = settings["chapter"]
    bundle = build_bundle(board)
    validate_bundle(bundle, PROFILES)
    save_prep_scope(bundle, board)
    profile = PROFILES[board]

    # 1. Scope: the letter participates, the explicitly classified display asset does
    #    not, and neither does a model candidate or a fact outside the page range.
    scoped = artifacts._facts_for_workspace(bundle, bundle.id)
    scoped_ids = [item.id for item in scoped]
    assert scoped_ids == ["fact_letter_clue", "fact_invite_chapter"], scoped_ids
    assert artifacts.display_material_fact_ids(bundle) == {"fact_map_asset"}
    assert "fact_map_asset" not in scoped_ids
    assert "fact_letter_candidate" not in scoped_ids
    assert "fact_distant_page" not in scoped_ids

    # The fantasy stage narrows planning to confirmed chapter assignments.  A letter
    # the structure stage put in a chapter must survive that narrowing; only its
    # explicit disposition decides, never its handout kind.
    if board == "daggerheart":
        artifact_ids = {item.fact_id for item in bundle.fact_dispositions if item.kind == "chapter"}
        narrowed = [item for item in scoped if item.id in artifact_ids]
        assert [item.id for item in narrowed] == ["fact_letter_clue", "fact_invite_chapter"], narrowed
        scoped = narrowed

    # 2. Real planning input.  The scoped facts reach a digest batch and the display
    #    asset's text does not.
    batches = artifacts._artifact_batches(bundle, scoped)
    batched_ids = [item.id for batch in batches for item in batch]
    assert sorted(batched_ids) == sorted(scoped_ids), batched_ids
    payload = artifacts._fact_payload_from_facts(scoped)
    messages = artifacts._local_digest_messages(profile, payload)
    prompt_text = json.dumps(messages, ensure_ascii=False)
    assert "fact_letter_clue" in prompt_text and "北塔" in prompt_text, prompt_text[:400]
    assert "fact_map_asset" not in prompt_text and "纯绘图" not in prompt_text
    letter_payload = next(item for item in payload if item["id"] == "fact_letter_clue")
    assert letter_payload["source_refs"][0]["page"] == 5, letter_payload

    # 3. Real coverage validation.  A chapter-dispositioned handout must be covered;
    #    the letter may be covered too; the display asset is never required.
    units = [{"fact_ids": [item.id for item in scoped]}]
    validator_kwargs = (units, scoped, profile, bundle, {chapter})
    try:
        artifacts._validate_global_plan(
            plan_payload(board, ["fact_letter_clue"]), *validator_kwargs
        )
    except artifacts.PlanCoverageError as error:
        assert error.chapters == {chapter: ["fact_invite_chapter"]}, error.chapters
        assert error.global_ids == [], error.global_ids
        assert "fact_map_asset" not in json.dumps(error.chapters) + json.dumps(error.global_ids)
    else:  # pragma: no cover - defensive
        raise AssertionError("a chapter-dispositioned handout must be required by coverage")

    plan = artifacts._validate_global_plan(
        plan_payload(board, ["fact_letter_clue", "fact_invite_chapter"]), *validator_kwargs
    )
    card = plan["cards"][0]
    assert card["fact_ids"] == ["fact_letter_clue", "fact_invite_chapter"], card
    assert card["chapter_ids"] == [chapter], card
    assert plan["uncovered_fact_ids"] == [], plan["uncovered_fact_ids"]
    assert "fact_map_asset" not in json.dumps(plan), "a display asset must not be forced into a card"
    assert plan["chapter_briefs"][0]["chapter_id"] == chapter

    # 4. A synthetic letter fact that is display-only by explicit classification is
    #    dropped even though its text reads like a clue: only the decision counts.
    raw_bundle = build_bundle(board).model_dump(mode="json")
    not_letter = [
        item for item in raw_bundle["fact_dispositions"] if item["fact_id"] != "fact_letter_clue"
    ]
    display_bundle = ExampleBundle.model_validate({
        **raw_bundle,
        "fact_dispositions": [
            *not_letter,
            {"fact_id": "fact_letter_clue", "kind": "display_material"},
        ],
    })
    display_only_ids = artifacts.display_material_fact_ids(display_bundle)
    assert "fact_letter_clue" in display_only_ids, display_only_ids
    assert [
        item.id for item in artifacts._facts_for_workspace(display_bundle, display_bundle.id)
    ] == ["fact_invite_chapter"]

    # A letter can be both shown to players and used to prepare a chapter: an explicit
    # narrative disposition beats the display classification instead of hiding it.
    shared_bundle = ExampleBundle.model_validate({
        **raw_bundle,
        "fact_dispositions": [
            *not_letter,
            {"fact_id": "fact_letter_clue", "kind": "display_material"},
            {"fact_id": "fact_letter_clue", "kind": "chapter", "chapter_ids": [chapter]},
        ],
    })
    assert "fact_letter_clue" not in artifacts.display_material_fact_ids(shared_bundle)
    assert [
        item.id for item in artifacts._facts_for_workspace(shared_bundle, shared_bundle.id)
    ] == ["fact_letter_clue", "fact_invite_chapter"]


def check_chapterless_board(board: str) -> None:
    """A letter must reach planning without the board inventing a chapter for it."""
    bundle = build_bundle(board, chaptered=False)
    validate_bundle(bundle, PROFILES)
    save_prep_scope(bundle, board)

    scoped = artifacts._facts_for_workspace(bundle, bundle.id)
    scoped_ids = [item.id for item in scoped]
    assert scoped_ids == ["fact_letter_clue", "fact_invite_chapter"], scoped_ids
    batches = artifacts._artifact_batches(bundle, scoped)
    assert sorted(item.id for batch in batches for item in batch) == sorted(scoped_ids)
    payload = artifacts._fact_payload_from_facts(scoped)
    prompt_text = json.dumps(
        artifacts._local_digest_messages(PROFILES[board], payload),
        ensure_ascii=False,
    )
    assert "fact_letter_clue" in prompt_text and "北塔" in prompt_text

    # With no chapters there is nothing to assign, so a plan needs no chapter ids and
    # coverage demands no card: that is what "no chapter required" has to mean.
    cards = [{
        "type": BOARDS[board]["card_type"],
        "title": "灯塔现场",
        "purpose": "调查灯塔",
        "fact_ids": ["fact_letter_clue"],
    }]
    if board == "cthulhu-dark-2e":
        cards.append({
            "type": "chapter_overview",
            "title": "灯塔之夜总览",
            "purpose": "概述调查范围",
            "fact_ids": ["fact_letter_clue"],
        })
    plan = artifacts._validate_global_plan(
        {
            "cards": cards,
            "brief": {"premise": "灯塔重新点亮。", "current_situation": "来客陆续抵达。"},
        },
        [{"fact_ids": scoped_ids}],
        scoped,
        PROFILES[board],
        bundle,
        set(),
    )
    assert plan["cards"][0]["fact_ids"] == ["fact_letter_clue"], plan["cards"][0]


def check_display_record_keeps_narrative_use() -> None:
    """Registering a source as showable must not remove it from preparation.

    The reviewer's probe: a letter on a board without chapters and without dispositions
    stopped participating in planning as soon as the GM created a display material for
    it.  A display record says the GM may show the source, not that the source lost its
    leads; only an explicit display-only disposition decides that.
    """
    fact = fact_letter()
    bundle = ExampleBundle.model_validate({
        "id": "handout_use_dual",
        "name": "Dual use",
        "profile_ids": ["cthulhu-dark-2e"],
        "facts": [fact],
        "cards": [],
        "plans": [],
    })
    before = [item.id for item in artifacts._facts_for_workspace(bundle, bundle.id)]
    assert before == [fact.id], before

    shown = ExampleBundle.model_validate({
        **bundle.model_dump(mode="json"),
        "display_materials": [{
            "id": "material_m_dual",
            "title": "合成信件",
            "source_fact_ids": [fact.id],
            "source_refs": [{"file": "fixture://letter-module", "page": 5}],
        }],
    })
    assert [item.id for item in artifacts._facts_for_workspace(shown, shown.id)] == [fact.id], (
        "a display record must not remove a fact from planning"
    )
    assert artifacts.display_material_fact_ids(shown) == set(), (
        "a display record is not a display-only decision"
    )

    # Only an explicit display-only disposition removes the fact, and a narrative
    # disposition still wins so the same source can be shown and prepared.
    display_only = ExampleBundle.model_validate({
        **bundle.model_dump(mode="json"),
        "fact_dispositions": [{"fact_id": fact.id, "kind": "display_material"}],
    })
    assert [item.id for item in artifacts._facts_for_workspace(display_only, display_only.id)] == []
    dual = ExampleBundle.model_validate({
        **bundle.model_dump(mode="json"),
        "fact_dispositions": [
            {"fact_id": fact.id, "kind": "display_material"},
            {"fact_id": fact.id, "kind": "global_reference"},
        ],
    })
    assert [item.id for item in artifacts._facts_for_workspace(dual, dual.id)] == [fact.id]


def main() -> None:
    original_db_path = storage.DB_PATH
    with tempfile.TemporaryDirectory(prefix="handout-narrative-use-") as temp_dir:
        storage.DB_PATH = Path(temp_dir) / "handout-use.db"
        try:
            storage.init_db()
            for board in BOARDS:
                check_board(board)
                check_chapterless_board(board)
            check_display_record_keeps_narrative_use()
        finally:
            storage.DB_PATH = original_db_path
    print(
        "PASS: handout content reaches planning and coverage by explicit use, "
        "not by source format, on both boards"
    )


if __name__ == "__main__":
    main()
