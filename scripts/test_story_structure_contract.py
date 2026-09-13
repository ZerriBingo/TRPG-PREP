"""Domain contract for reviewable chapters, beats, and cross-chapter threads."""
from __future__ import annotations

import sys
from pathlib import Path
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain import (  # noqa: E402
    DomainValidationError,
    ExampleBundle,
    AdventureChapter,
    FactDisposition,
    SourceFact,
    SourceRef,
    StoryBeat,
    StoryThread,
    load_profiles,
    validate_bundle,
)
from backend.app.structure import (  # noqa: E402
    StructureGenerationError,
    _materialize_structure,
    _validate_local,
    _validate_skeleton,
)


def fact(fact_id: str, page: int) -> SourceFact:
    return SourceFact(
        id=fact_id,
        text=f"{fact_id} 的来源事实。",
        kind="event",
        visibility="explicit",
        source_refs=[SourceRef(file="fixture://heist", page=page)],
    )


def chapter(chapter_id: str, fact_ids: list[str]) -> AdventureChapter:
    return AdventureChapter(
        id=chapter_id,
        title=f"章节 {chapter_id}",
        summary="一个可审核的叙事准备范围。",
        fact_ids=fact_ids,
        review_state="approved",
    )


def thread(
    thread_id: str,
    roles: dict[str, str],
    fact_ids: list[str] | None = None,
) -> StoryThread:
    return StoryThread(
        id=thread_id,
        title=f"剧情线 {thread_id}",
        kind="faction",
        summary="贯穿多个章节的故事线。",
        chapter_roles=roles,
        fact_ids=fact_ids or [],
    )


def dispositions(facts, chapter_id="chapter_ch1") -> list[FactDisposition]:
    return [
        FactDisposition(fact_id=item.id, kind="chapter", chapter_ids=[chapter_id])
        for item in facts
    ]


def bundle(chapters, threads, facts, beats=None, fact_dispositions=None) -> ExampleBundle:
    return ExampleBundle(
        id="story_structure_contract",
        name="结构契约",
        profile_ids=["daggerheart"],
        facts=facts,
        cards=[],
        adventure_chapters=list(chapters),
        story_beats=list(beats or []),
        story_threads=list(threads),
        fact_dispositions=list(fact_dispositions or []),
        plans=[],
    )


def expect_error(broken: ExampleBundle, profiles, needle: str) -> None:
    try:
        validate_bundle(broken, profiles)
    except DomainValidationError as error:
        assert needle in str(error), f"expected {needle!r} in {error}"
    else:
        raise AssertionError(f"expected DomainValidationError containing {needle!r}")


def main() -> None:
    profiles = load_profiles(ROOT / "backend" / "domain" / "profiles")
    facts = [fact("fact_opening", 1), fact("fact_villa", 12), fact("fact_vault", 72)]

    chapter1 = chapter("chapter_ch1", ["fact_opening", "fact_villa"])
    chapter2 = chapter("chapter_ch2", ["fact_villa"])
    threads = [
        thread("thread_heist", {"chapter_ch1": "introduce", "chapter_ch2": "develop"}),
        thread(
            "thread_truth",
            {"chapter_ch2": "resolve"},
            fact_ids=["fact_vault"],
        ),
    ]
    healthy = bundle(
        [chapter1, chapter2],
        threads,
        facts,
        fact_dispositions=dispositions(facts),
    )
    validate_bundle(healthy, profiles)

    dangling_membership = bundle(
        [chapter("chapter_ch1", ["fact_opening", "fact_missing"])],
        threads,
        facts,
    )
    expect_error(dangling_membership, profiles, "missing facts")

    dangling_thread_chapter = bundle(
        [chapter1, chapter2],
        [thread("thread_bad", {"chapter_missing": "develop"})],
        facts,
    )
    expect_error(dangling_thread_chapter, profiles, "missing adventure chapters")

    dangling_thread_fact = bundle(
        [chapter1, chapter2],
        [thread("thread_bad", {"chapter_ch1": "develop"}, fact_ids=["fact_missing"])],
        facts,
    )
    expect_error(dangling_thread_fact, profiles, "missing facts")

    # A proposed thread may remain global until the GM approves chapter links.
    validate_bundle(
        bundle(
            [chapter1, chapter2],
            [thread("thread_global", {})],
            facts,
            fact_dispositions=dispositions(facts),
        ),
        profiles,
    )

    beat = StoryBeat(
        id="story_beat_reveal",
        title="揭示宝库线索",
        summary="线索改变众人对争夺目标的理解。",
        chapter_id="chapter_ch2",
        thread_ids=["thread_truth"],
        fact_ids=["fact_vault"],
    )
    legacy = beat.model_dump()
    legacy.pop("chapter_id")
    legacy["chapter_ids"] = ["chapter_ch1", "chapter_ch2"]
    try:
        StoryBeat.model_validate(legacy)
    except ValidationError:
        pass
    else:
        raise AssertionError("a beat must not span chapters")
    expect_error(
        bundle([chapter1, chapter2], threads, facts,
               [beat.model_copy(update={"chapter_id": "chapter_ch1"})], dispositions(facts)),
        profiles, "inapplicable story threads",
    )
    skeleton = {
        "chapters": [{"key": "one", "title": "One", "summary": "Opening"},
                     {"key": "two", "title": "Two", "summary": "Resolution"}],
        "threads": [{"key": "truth", "title": "Truth", "chapter_roles": {"two": "resolve"}}],
        "beats": [{"key": "reveal", "title": "Reveal", "summary": "Learn truth",
                   "chapter_key": "one", "thread_keys": ["truth"],
                   "thread_developments": {"truth": {"development": "Discover a contradiction", "carry_forward": "The motive remains unknown"}},
                   "interplay": "Questioning the contradiction may expose the motive"}],
    }
    try:
        _validate_skeleton(skeleton, {item.id for item in facts})
    except StructureGenerationError as error:
        assert "不适用于其章节" in str(error)
    else:
        raise AssertionError("beat threads must apply to its chapter")
    skeleton["threads"][0]["chapter_roles"]["one"] = "introduce"
    skeleton["threads"][0]["chapter_roles"]["two"] = "background"
    assert _validate_skeleton(skeleton, set())["beats"][0]["chapter_key"] == "one"
    validate_bundle(
        bundle(
            [chapter1, chapter2],
            threads,
            facts,
            [beat],
            dispositions(facts),
        ),
        profiles,
    )

    incomplete = bundle(
        [chapter1, chapter2],
        threads,
        facts,
        fact_dispositions=dispositions(facts[:-1]),
    )
    expect_error(incomplete, profiles, "without disposition")

    dangling_disposition = bundle(
        [chapter1, chapter2],
        threads,
        facts,
        fact_dispositions=[
            *dispositions(facts[:-1]),
            FactDisposition(
                fact_id="fact_vault",
                kind="chapter",
                chapter_ids=["chapter_missing"],
            ),
        ],
    )
    expect_error(dangling_disposition, profiles, "missing adventure chapters")

    local = _validate_local(
        {
            "observations": [{
                "title": "开场",
                "summary": "只覆盖一条事实。",
                "fact_ids": ["fact_opening"],
            }]
        },
        facts,
    )
    fallback = local["observations"][-1]
    assert fallback["fact_ids"] == ["fact_vault", "fact_villa"]
    assert len(local["observations"]) == 2

    try:
        _materialize_structure(
            {
                "chapters": [
                    {"key": "used", "title": "有事实", "summary": "会被使用。"},
                    {"key": "empty", "title": "空章节", "summary": "不应进入工作流。"},
                ],
                "threads": [],
                "beats": [],
            },
            [{
                "fact_id": "fact_opening",
                "kind": "chapter",
                "chapter_keys": ["used"],
                "thread_keys": [],
                "reason": "",
            }],
        )
    except StructureGenerationError as error:
        assert "空章节" in str(error)
    else:
        raise AssertionError("empty generated chapters must be rejected")

    print("PASS: reviewed structure keeps complete, bounded fact ownership")


if __name__ == "__main__":
    main()
