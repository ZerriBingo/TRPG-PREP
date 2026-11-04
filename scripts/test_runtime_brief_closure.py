"""RuntimeBrief ownership closure: declared references join the brief's own fact list.

The brief's `fact_ids` is the ownership list; `field_sources` names the facts a field
relies on.  A source that is in scope but missing from `fact_ids` is a bookkeeping gap,
not missing content, so it is merged in; an unknown, out-of-scope or over-limit reference
is a hard failure.  Nothing else changes: prose, field_sources and cards stay byte-equal,
and a cached success is consumed through the same rule without a new request.

Run under the external worker/network guard:
    PYTHONPATH=scripts/trial_offline_guard TRIAL_AUDIT_LOG=... \\
      output/r1/run_test.py scripts/test_runtime_brief_closure.py
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backend.app import artifacts, storage  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "brief_closure_split", ROOT / "scripts" / "test_global_plan_split_recovery.py")
split = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = split
SPEC.loader.exec_module(split)

# The frozen real windows are local evidence, not repository content; they are only read
# when a caller names them explicitly.
REAL_PROBE_ARG = "--replay-real"
JOB = "artifact_job_0ae938e942e341b8b0ed90084a12f747"
STAGES = {"6.2": "compact_probe_window_6_2", "7": "compact_probe_window_7",
          "8": "compact_probe_window_8"}


def probe_steps(path: Path) -> dict[str, dict]:
    uri = path.as_uri() + "?mode=ro&immutable=1"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        rows = [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM artifact_job_steps WHERE job_id=?", (JOB,))]
    by_stage = {row["stage"]: row for row in rows}
    return {label: by_stage[stage] for label, stage in STAGES.items()}


def brief_closure(brief: dict) -> bool:
    """Every explicit source id is in the brief's own ownership list."""
    if brief is None:
        return True
    owned = set(brief.get("fact_ids") or [])
    return all(set(ids or []) <= owned for ids in (brief.get("field_sources") or {}).values())


def check_real_replay(probe: Path) -> None:
    """The frozen sample 8 is closed in consumption while its stored output is untouched."""
    steps = probe_steps(probe)
    for label, step in steps.items():
        before = json.dumps(step["output"], ensure_ascii=False, sort_keys=True)
        allowed = set(step["input_refs"]["fact_ids"])
        closed = artifacts._close_plan_briefs(step["output"], allowed, where=f"样本 {label}")
        after = json.dumps(step["output"], ensure_ascii=False, sort_keys=True)
        assert before == after, "the frozen output must not be mutated"
        assert brief_closure(closed.get("brief")), label
        for item in closed.get("chapter_briefs") or []:
            assert brief_closure(item["brief"]), (label, item["chapter_id"])
        # Cards and the rest of the plan are untouched.
        assert closed["cards"] == step["output"]["cards"], label
        if label != "8":
            assert closed["brief"] == step["output"]["brief"], (
                f"sample {label} was already closed and must stay unchanged")

    sample8 = steps["8"]["output"]
    original = sample8["brief"]
    closed = artifacts._close_plan_briefs(sample8, set(steps["8"]["input_refs"]["fact_ids"]),
                                          where="样本 8")["brief"]
    assert set(original["fact_ids"]) <= set(closed["fact_ids"]), "original ownership is kept"
    assert closed["field_sources"] == original["field_sources"], "sources are never rewritten"
    for field, value in original.items():
        if field != "fact_ids":
            assert closed[field] == value, field
    added = [fid for fid in closed["fact_ids"] if fid not in set(original["fact_ids"])]
    assert added, "sample 8 really had sources missing from its own list"


def check_package_and_chapter_briefs() -> None:
    allowed = {"fact_a", "fact_b", "fact_c"}
    brief = {"premise": "p", "current_situation": "c", "fact_ids": ["fact_a"],
             "field_sources": {"premise": ["fact_b"], "current_situation": ["fact_a", "fact_b"]}}
    closed = artifacts._close_plan_briefs({"cards": [], "brief": brief}, allowed,
                                          where="包摘要")["brief"]
    assert closed["fact_ids"] == ["fact_a", "fact_b"], closed
    # Idempotent, stable, and the input object is unchanged.
    again = artifacts._close_plan_briefs({"cards": [], "brief": closed}, allowed,
                                         where="包摘要")["brief"]
    assert again == closed, again
    assert brief["fact_ids"] == ["fact_a"] and brief["field_sources"]["premise"] == ["fact_b"]

    def rejects(output, *, label):
        with contextlib.suppress(artifacts.ArtifactGenerationError):
            artifacts._close_plan_briefs(output, allowed, where=label)
            raise AssertionError(f"{label}: out-of-scope reference must fail")
    rejects({"brief": {"premise": "p", "current_situation": "c", "fact_ids": ["fact_unknown"],
                       "field_sources": {}}}, label="未知 fact_ids")
    rejects({"brief": {"premise": "p", "current_situation": "c", "fact_ids": ["fact_a"],
                       "field_sources": {"premise": ["fact_outside"]}}}, label="越范围引用")
    rejects({"brief": {"premise": "p", "current_situation": "c", "fact_ids": ["fact_a"],
                       "field_sources": {"premise": []}},
             "chapter_briefs": [{"chapter_id": "c1",
                                 "brief": {"premise": "p", "current_situation": "c",
                                           "fact_ids": ["fact_a"],
                                           "field_sources": {"premise": ["fact_elsewhere"]}},
                                 "reference_fact_ids": ["fact_b"]}]}, label="章节越范围")

    # An empty declared source stays empty; another brief's ids do not count as this one's.
    empty = artifacts._close_plan_briefs(
        {"brief": {"premise": "p", "current_situation": "c", "fact_ids": ["fact_a"],
                   "field_sources": {"premise": []}},
         "chapter_briefs": [{"chapter_id": "c1",
                             "brief": {"premise": "p", "current_situation": "c",
                                       "fact_ids": [], "field_sources": {}},
                             "reference_fact_ids": ["fact_b"]}]},
        allowed, where="空来源")["brief"]
    assert empty["fact_ids"] == ["fact_a"] and empty["field_sources"]["premise"] == []

    chapter = artifacts._close_plan_briefs(
        {"brief": None,
         "chapter_briefs": [{"chapter_id": "c1",
                             "brief": {"premise": "p", "current_situation": "c",
                                       "fact_ids": ["fact_a"],
                                       "field_sources": {"premise": ["fact_c"]}},
                             "reference_fact_ids": ["fact_b"]}]},
        allowed, where="章节摘要")["chapter_briefs"][0]
    assert chapter["brief"]["fact_ids"] == ["fact_a", "fact_c"]
    assert chapter["reference_fact_ids"] == ["fact_b"], "reference ids are not the brief's list"


def chapter_fixture():
    """Two confirmed chapters, each owning its own facts, plus one shared fact."""
    from backend.domain.models import ExampleBundle, SourceFact, SourceRef

    def fact(name: str) -> SourceFact:
        return SourceFact(id=name, text=f"{name} 的原文。", kind="clue", visibility="explicit",
                          source_refs=[SourceRef(file="fixture://chapters", page=1)])

    facts = [fact(name) for name in
             ("fact_ch_a", "fact_ch_b", "fact_ch_c", "fact_ch_d", "fact_ch_shared")]
    bundle = ExampleBundle.model_validate({
        "id": "chapter_scope_fixture", "name": "Chapter scope", "profile_ids": ["cthulhu-dark-2e"],
        "facts": [item.model_dump(mode="json") for item in facts], "cards": [], "plans": [],
        "adventure_chapters": [
            {"id": "chapter_a", "title": "甲章", "summary": "甲", "fact_ids":
             ["fact_ch_a", "fact_ch_b", "fact_ch_shared"]},
            {"id": "chapter_b", "title": "乙章", "summary": "乙", "fact_ids":
             ["fact_ch_c", "fact_ch_d", "fact_ch_shared"]},
        ],
        "fact_dispositions": [
            {"fact_id": "fact_ch_a", "kind": "chapter", "chapter_ids": ["chapter_a"]},
            {"fact_id": "fact_ch_b", "kind": "chapter", "chapter_ids": ["chapter_a"]},
            {"fact_id": "fact_ch_c", "kind": "chapter", "chapter_ids": ["chapter_b"]},
            {"fact_id": "fact_ch_d", "kind": "chapter", "chapter_ids": ["chapter_b"]},
            {"fact_id": "fact_ch_shared", "kind": "chapter",
             "chapter_ids": ["chapter_a", "chapter_b"]},
        ],
    })
    units = [{"id": f"unit_{item.id}", "kind": "scene", "title": item.id, "summary": "",
              "fact_ids": [item.id], "entity_keys": [], "relationship_hints": [],
              "open_questions": []} for item in facts]
    sizes = {item.id: len(item.text) + 40 for item in facts}
    tokens = {item.id: 12 for item in facts}
    context = {"chapters": [{"id": chapter.id, "title": chapter.title,
                             "summary": chapter.summary}
                            for chapter in bundle.adventure_chapters], "threads": [], "beats": []}
    return facts, units, sizes, tokens, bundle, context


def _chapter_brief(chapter_id: str, fact_ids: list[str], sources: dict[str, list[str]]) -> dict:
    return {"chapter_id": chapter_id,
            "brief": {"premise": "p", "current_situation": "c",
                      "fact_ids": list(fact_ids), "field_sources": sources},
            "reference_fact_ids": []}


def check_chapter_scope_helper() -> None:
    """A chapter brief may only cite facts its own confirmed chapter owns."""
    _facts, _units, _sizes, _tokens, bundle, _context = chapter_fixture()
    ownership = {chapter.id: set(chapter.fact_ids) for chapter in bundle.adventure_chapters}
    scope = {"fact_ch_a", "fact_ch_b", "fact_ch_c", "fact_ch_d", "fact_ch_shared"}

    def close(output, *, chapter_scope=None):
        return artifacts._close_plan_briefs(
            output, scope, where="两章", chapter_facts=ownership,
            chapter_scope=chapter_scope or {"chapter_a", "chapter_b"})

    try:
        close({"chapter_briefs": [_chapter_brief("chapter_a", ["fact_ch_c"], {})]})
    except artifacts.ArtifactGenerationError as error:
        assert "chapter_a" in str(error) and "fact_ch_c" in str(error), error
    else:
        raise AssertionError("a chapter must not cite another chapter's fact")

    try:
        close({"chapter_briefs": [
            _chapter_brief("chapter_a", ["fact_ch_a"], {"premise": ["fact_ch_d"]})]})
    except artifacts.ArtifactGenerationError as error:
        assert "chapter_a" in str(error) and "fact_ch_d" in str(error), error
    else:
        raise AssertionError("a chapter field source must stay inside its chapter")

    closed = close({"chapter_briefs": [
        _chapter_brief("chapter_a", ["fact_ch_a"], {"premise": ["fact_ch_b"]}),
        _chapter_brief("chapter_b", ["fact_ch_shared"], {"premise": ["fact_ch_shared"]}),
    ]})
    assert closed["chapter_briefs"][0]["brief"]["fact_ids"] == ["fact_ch_a", "fact_ch_b"]
    assert closed["chapter_briefs"][1]["brief"]["fact_ids"] == ["fact_ch_shared"]

    try:
        close({"chapter_briefs": [_chapter_brief("chapter_zzz", ["fact_ch_a"], {})]})
    except artifacts.ArtifactGenerationError as error:
        assert "chapter_zzz" in str(error), error
    else:
        raise AssertionError("an unknown chapter id must fail")
    try:
        close({"chapter_briefs": [_chapter_brief("chapter_b", ["fact_ch_c"], {})]},
              chapter_scope={"chapter_a"})
    except artifacts.ArtifactGenerationError as error:
        assert "chapter_b" in str(error), error
    else:
        raise AssertionError("a chapter outside the reviewed scope must fail")

    package = close({"brief": {"premise": "p", "current_situation": "c",
                               "fact_ids": ["fact_ch_c"], "field_sources": {}},
                     "chapter_briefs": []})
    assert package["brief"]["fact_ids"] == ["fact_ch_c"]


def check_chapter_scope_production() -> None:
    """The production validator rejects a cross-chapter brief and accepts lawful ones."""
    from backend.domain import load_profiles

    facts, units, _sizes, _tokens, bundle, _context = chapter_fixture()
    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    chapter_scope = {"chapter_a", "chapter_b"}
    base_cards = [
        {"type": "location", "title": "甲章地点", "purpose": "入口。",
         "fact_ids": ["fact_ch_a"], "chapter_ids": ["chapter_a"], "focus": [],
         "open_questions": [], "runtime_groups": [], "story_beat_ids": [],
         "situation": "", "case_scope": "current"},
        {"type": "chapter_overview", "title": "总览", "purpose": "本范围。",
         "fact_ids": ["fact_ch_a"], "chapter_ids": ["chapter_a"]},
    ]

    def run(brief):
        return artifacts._validate_global_plan(
            {"cards": base_cards, "chapter_briefs": [brief], "open_questions": []},
            units, facts, profile, bundle, chapter_scope, require_coverage=False)

    lawful = run(_chapter_brief("chapter_a", ["fact_ch_a"], {}))
    assert lawful["chapter_briefs"][0]["brief"]["fact_ids"] == ["fact_ch_a"]
    try:
        run(_chapter_brief("chapter_a", ["fact_ch_c"], {}))
    except artifacts.ArtifactGenerationError as error:
        assert "chapter_a" in str(error) and "fact_ch_c" in str(error), error
    else:
        raise AssertionError("production validation must reject a cross-chapter brief")
    run(_chapter_brief("chapter_a", ["fact_ch_a"], {"premise": ["fact_ch_shared"]}))
    run(_chapter_brief("chapter_b", ["fact_ch_shared"], {}))


def check_chapter_cached_consumption() -> None:
    """A cached chapter brief from another chapter fails locally with zero sends."""
    facts, units, sizes, tokens, bundle, context = chapter_fixture()
    from backend.domain import load_profiles

    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    job = split.SimpleNamespace(id="artifact_job_chapter_cache", model_id="probe",
                                fake_model=False)
    calls = {"n": 0}

    def worker(messages, temperature, max_tokens, **kwargs):
        calls["n"] += 1
        return split._frame({
            "cards": [
                {"type": "location", "title": "甲章地点", "purpose": "入口。",
                 "fact_ids": ["fact_ch_a"], "chapter_ids": ["chapter_a"], "focus": [],
                 "open_questions": [], "runtime_groups": [], "story_beat_ids": [],
                 "situation": "", "case_scope": "current"},
                {"type": "chapter_overview", "title": "总览", "purpose": "本范围。",
                 "fact_ids": ["fact_ch_a", "fact_ch_c"], "chapter_ids": ["chapter_a"]},
            ],
            "chapter_briefs": [
                {"chapter_id": "chapter_a",
                 "brief": {"premise": "p", "current_situation": "c",
                           "fact_ids": ["fact_ch_a", "fact_ch_b", "fact_ch_shared"],
                           "field_sources": {}},
                 "reference_fact_ids": []},
                {"chapter_id": "chapter_b",
                 "brief": {"premise": "p", "current_situation": "c",
                           "fact_ids": ["fact_ch_c", "fact_ch_d", "fact_ch_shared"],
                 "field_sources": {}},
                 "reference_fact_ids": []},
            ],
            "open_questions": [],
        })

    def scope_call():
        return artifacts._run_global_plan_scope(
            job, plan_index=1, profile=profile, bundle=bundle, plan_units=units,
            plan_facts=facts, plan_fact_sizes=sizes, plan_fact_tokens=tokens,
            plan_context=context, chapter_scope={"chapter_a", "chapter_b"},
            plan_client=split._client(worker))

    with tempfile.TemporaryDirectory(prefix="chapter-cache-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        scope_call()
        assert calls["n"] > 0
        root = next(row for row in storage.list_artifact_job_steps(job.id)
                    if row["stage"] == "global_plan")
        assert root["output"]["chapter_briefs"][0]["brief"]["fact_ids"] == [
            "fact_ch_a", "fact_ch_b", "fact_ch_shared"]

        calls["n"] = 0
        scope_call()
        assert calls["n"] == 0, "a lawful cached chapter brief must be reused"

        broken = json.loads(json.dumps(root))
        broken["output"]["chapter_briefs"][0]["brief"]["field_sources"] = {
            "premise": ["fact_ch_c"]}
        storage.save_artifact_job_step(broken)
        before = json.dumps(
            next(row for row in storage.list_artifact_job_steps(job.id)
                 if row["stage"] == "global_plan")["output"],
            ensure_ascii=False, sort_keys=True)
        calls["n"] = 0
        try:
            scope_call()
        except artifacts.ArtifactGenerationError as error:
            assert "chapter_a" in str(error) and "fact_ch_c" in str(error), error
        else:
            raise AssertionError("a cached cross-chapter source must fail")
        assert calls["n"] == 0, "a failing cached chapter brief must not trigger a request"
        after = next(row for row in storage.list_artifact_job_steps(job.id)
                     if row["stage"] == "global_plan")
        assert json.dumps(after["output"], ensure_ascii=False, sort_keys=True) == before
        assert after["attempts"] == broken["attempts"], "attempts must be untouched"


def check_cached_window_consumption() -> None:
    """A cached window brief that reaches outside its own window fails with zero sends."""
    from unittest import mock

    profile, facts, units, sizes, tokens = split._fixture(4)
    job = split.SimpleNamespace(id="artifact_job_window_cache", model_id="probe",
                                fake_model=False)
    calls = {"n": 0}

    def worker(messages, temperature, max_tokens, **kwargs):
        calls["n"] += 1
        if len(split._units_in(messages)) > 1:
            return split._length_frame()
        return split._plan_frame(messages, f"窗口 {calls['n']}")

    with tempfile.TemporaryDirectory(prefix="window-cache-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        with mock.patch.object(artifacts, "GLOBAL_PLAN_WINDOW_MAX_CHARS", 6000):
            split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))
        assert calls["n"] > 0
        windows = [row for row in storage.list_artifact_job_steps(job.id)
                   if str(row["stage"]).startswith("global_plan_window_")]
        assert len(windows) > 1, windows
        first = sorted(windows, key=lambda row: row["stage"])[0]
        own_ids = set(first["input_refs"]["fact_ids"])
        foreign = next(fact.id for fact in facts if fact.id not in own_ids)

        # Give the cached window a brief that cites a fact from another window, then run
        # the real window consumption path again: it must fail locally, with no request.
        broken = json.loads(json.dumps(first))
        # The cached window now carries a brief that names a fact of another window.
        broken["output"]["brief"] = {"premise": "p", "current_situation": "c",
                                     "fact_ids": [foreign], "field_sources": {}}
        storage.save_artifact_job_step(broken)
        before = json.dumps(broken["output"], ensure_ascii=False, sort_keys=True)

        calls["n"] = 0
        # Re-enter the production window path with the first two units: it chunks into
        # one window per unit, so window 1 is the cached record just corrupted.
        reentry_units = units[:2]
        reentry_ids = {fact_id for unit in reentry_units for fact_id in unit["fact_ids"]}
        with mock.patch.object(artifacts, "GLOBAL_PLAN_WINDOW_MAX_CHARS", 6000):
            try:
                artifacts._recover_global_plan_windows(
                    job, plan_index=1, profile=profile, bundle=None,
                    plan_units=reentry_units,
                    plan_facts=[fact for fact in facts if fact.id in reentry_ids],
                    plan_fact_sizes={key: value for key, value in sizes.items()
                                     if key in reentry_ids},
                    plan_fact_tokens={key: value for key, value in tokens.items()
                                      if key in reentry_ids},
                    plan_context=None, chapter_scope=None,
                    plan_client=split._client(worker),
                    step_budget=artifacts.RequestBudget(max_requests=3), lead_window=True)
            except artifacts.ArtifactGenerationError as error:
                assert foreign in str(error), error
            else:
                raise AssertionError("a cached window brief outside its window must fail")
        assert calls["n"] == 0, "a failing cached window brief must not trigger a request"
        after = next(row for row in storage.list_artifact_job_steps(job.id)
                     if row["id"] == first["id"])
        assert json.dumps(after["output"], ensure_ascii=False, sort_keys=True) == before
        assert after["attempts"] == broken["attempts"], "attempts must be untouched"


def check_merge() -> None:
    allowed = {f"fact_{index}" for index in range(200)}
    # Two lawful briefs whose union stays under the ceiling: 50 + 50 with 20 shared.
    first = {"premise": "a", "current_situation": "b", "fact_ids": [f"fact_{i}" for i in range(50)],
             "field_sources": {"premise": [f"fact_{i}" for i in range(50)]}}
    second = {"premise": "a", "current_situation": "b",
              "fact_ids": [f"fact_{i}" for i in range(30, 80)],
              "field_sources": {"premise": [f"fact_{i}" for i in range(30, 80)]}}
    merged = artifacts._merge_runtime_brief_dicts(first, second)
    assert len(merged["fact_ids"]) == 80, len(merged["fact_ids"])
    assert set(merged["field_sources"]["premise"]) <= set(merged["fact_ids"]), merged
    closed = artifacts._close_plan_briefs({"brief": merged}, allowed, where="合并")["brief"]
    assert brief_closure(closed), "a lawful merge stays closed"

    # Two lawful 60-item briefs taken through the *production* merge, not a hand-built
    # oversized object: the combined ownership list is 120, which must fail at the
    # consumption boundary instead of being truncated.
    heavy_a = {"premise": "a", "current_situation": "b",
               "fact_ids": [f"fact_{i}" for i in range(60)],
               "field_sources": {"premise": [f"fact_{i}" for i in range(60)]}}
    heavy_b = {"premise": "a", "current_situation": "b",
               "fact_ids": [f"fact_{i}" for i in range(60, 120)],
               "field_sources": {"premise": [f"fact_{i}" for i in range(60, 120)]}}
    merged_output = artifacts._merge_global_plan_outputs(
        [{"cards": [], "brief": heavy_a, "chapter_briefs": []},
         {"cards": [], "brief": heavy_b, "chapter_briefs": []}])
    assert len(merged_output["brief"]["fact_ids"]) == 120, merged_output["brief"]["fact_ids"]
    before = copy.deepcopy(merged_output)
    try:
        artifacts._close_plan_briefs(merged_output, allowed, where="合并溢出")
    except artifacts.ArtifactGenerationError as error:
        message = str(error)
        assert "120" in message and "100" in message and "运行包摘要" in message, message
    else:
        raise AssertionError("an over-limit ownership list must fail loudly")
    assert merged_output == before, "a refused merge must not be modified or truncated"

    # A single-sided merge and repeated references behave the same way.
    assert artifacts._merge_runtime_brief_dicts(None, first) == first
    repeated = {"premise": "a", "current_situation": "b", "fact_ids": ["fact_0", "fact_0"],
                "field_sources": {"premise": ["fact_0", "fact_1", "fact_1"]}}
    closed_repeated = artifacts._close_plan_briefs({"brief": repeated}, allowed,
                                                   where="重复")["brief"]
    assert closed_repeated["fact_ids"] == ["fact_0", "fact_1"], closed_repeated


def check_consumption_path() -> None:
    """The real planning consumption path closes briefs with zero sends, from cache too."""
    profile, facts, units, sizes, tokens, bundle, context, _chapter = split._board_fixture(
        "cthulhu-dark-2e")
    first_id, second_id = facts[0].id, facts[1].id
    job = split.SimpleNamespace(id="artifact_job_brief_closure", model_id="probe",
                                fake_model=False)
    calls = {"n": 0}

    def reply():
        return split._frame({
            "cards": [
                {"type": "location", "title": "窗口卡", "purpose": "入口。",
                 "fact_ids": [first_id], "chapter_ids": [], "focus": [],
                 "open_questions": [], "runtime_groups": [], "story_beat_ids": [],
                 "situation": "", "case_scope": "current"},
                {"type": "chapter_overview", "title": "概览", "purpose": "局部。",
                 "fact_ids": [second_id], "chapter_ids": []},
            ],
            "brief": {"premise": "p", "current_situation": "c",
                      "fact_ids": [first_id],
                      "field_sources": {"premise": [first_id, second_id]}},
            "open_questions": [],
        })

    def worker(messages, temperature, max_tokens, **kwargs):
        calls["n"] += 1
        return reply()

    with tempfile.TemporaryDirectory(prefix="brief-closure-") as directory:
        storage.DB_PATH = Path(directory) / "test.db"
        storage.init_db()
        split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))
        assert calls["n"] > 0
        root = next(row for row in storage.list_artifact_job_steps(job.id)
                    if row["stage"] == "global_plan")
        # A fresh reply is stored already closed.
        assert brief_closure(root["output"]["brief"]), root["output"]["brief"]
        assert first_id in root["output"]["brief"]["fact_ids"]
        assert second_id in root["output"]["brief"]["fact_ids"]

        # The same record, un-closed (an older cached success): consumption closes it with
        # zero sends and leaves the stored bytes and attempts untouched.
        unclosed = json.loads(json.dumps(root))
        unclosed["output"]["brief"]["fact_ids"] = [first_id]
        storage.save_artifact_job_step(unclosed)
        bytes_before = json.dumps(
            next(row for row in storage.list_artifact_job_steps(job.id)
                 if row["stage"] == "global_plan")["output"],
            ensure_ascii=False, sort_keys=True)
        calls["n"] = 0
        again = split._scope_call(job, profile, facts, units, sizes, tokens,
                                  split._client(worker))
        assert calls["n"] == 0, "a cached success must not be sent again"
        assert brief_closure(again["brief"]), again["brief"]
        assert second_id in again["brief"]["fact_ids"]
        stored = next(row for row in storage.list_artifact_job_steps(job.id)
                      if row["stage"] == "global_plan")
        assert json.dumps(stored["output"], ensure_ascii=False, sort_keys=True) == bytes_before,             "consumption must not rewrite the cached record"
        assert stored["attempts"] == unclosed["attempts"], "attempts must be untouched"

        # A cached record with an unknown reference fails locally, with no request.
        broken = json.loads(json.dumps(stored))
        broken["output"]["brief"]["field_sources"]["premise"] = ["fact_not_in_scope"]
        storage.save_artifact_job_step(broken)
        calls["n"] = 0
        try:
            split._scope_call(job, profile, facts, units, sizes, tokens, split._client(worker))
        except artifacts.ArtifactGenerationError as error:
            assert "fact_not_in_scope" in str(error), error
        else:
            raise AssertionError("a cached out-of-scope reference must fail")
        assert calls["n"] == 0, "a failing cache must not trigger a request"
        still = next(row for row in storage.list_artifact_job_steps(job.id)
                     if row["stage"] == "global_plan")
        assert still["status"] == "succeeded", "the cached success record is untouched"


def check_messages_unchanged(probe: Path) -> None:
    """Closing a brief never changes a card's materialization request."""
    from backend.domain import load_profiles
    from backend.domain.models import ExampleBundle

    uri = probe.as_uri() + "?mode=ro&immutable=1"
    with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
        row = db.execute("SELECT data FROM domain_bundles WHERE id=?",
                         ("prep_job_74521ada400743c0964a8311009093be",)).fetchone()
    bundle = ExampleBundle.model_validate(json.loads(row[0]))
    facts_by_id = {fact.id: fact for fact in bundle.facts}
    profile = load_profiles(ROOT / "backend" / "domain" / "profiles")["cthulhu-dark-2e"]
    step = probe_steps(probe)["8"]
    allowed = set(step["input_refs"]["fact_ids"])
    closed = artifacts._close_plan_briefs(step["output"], allowed, where="样本 8")
    assert closed["cards"] == step["output"]["cards"]

    def build(card):
        selected = [facts_by_id[fact_id] for fact_id in card["fact_ids"]]
        payload = artifacts._fact_payload_from_facts(selected)
        plan = artifacts._prompt_plan(dict(card, runtime_group_ids=[]))
        return artifacts._materialization_messages(profile, plan, payload), payload

    for original_card, closed_card in zip(step["output"]["cards"], closed["cards"],
                                          strict=True):
        # Two separate builds: one from the frozen output's card, one from the closed
        # output's card.  They must be byte-identical.
        before, before_payload = build(original_card)
        after, after_payload = build(closed_card)
        assert before == after
        assert before_payload == after_payload
        assert before_payload[0]["id"] == original_card["fact_ids"][0], "fact order preserved"


def main() -> None:
    probe: Path | None = None
    if REAL_PROBE_ARG in sys.argv:
        name = sys.argv[sys.argv.index(REAL_PROBE_ARG) + 1]
        candidate = (ROOT / name).resolve()
        if not candidate.exists():
            raise SystemExit(f"{REAL_PROBE_ARG} 指向的 probe 不存在: {candidate}")
        probe = candidate
    if probe is not None:
        check_real_replay(probe)
        check_messages_unchanged(probe)
    check_chapter_scope_helper()
    check_chapter_scope_production()
    check_chapter_cached_consumption()
    check_cached_window_consumption()
    check_package_and_chapter_briefs()
    check_merge()
    check_consumption_path()
    print("PASS: brief field sources close into their own fact_ids, out-of-scope or "
          "over-limit references fail loudly, merges no longer truncate, and the cached "
          "consumption path closes with zero sends")


if __name__ == "__main__":
    main()
