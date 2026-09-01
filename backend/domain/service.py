from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .models import DerivedCard, DisplayMaterial, ExampleBundle, RuleProfile, SceneBeat, ScenePlan, SessionState, SourceFact


class DomainValidationError(ValueError):
    """Raised when facts, profiles, or derived cards fail cross-model rules."""


SCENE_PLAN_CARD_TYPES = frozenset({
    "scene",
    "investigation_site",
    "location",
    "chapter_overview",
    "environment",
    "scene_extract",
    "npc",
    "character",
    "character_function",
    "threat",
    "enemy",
    "anomaly",
    "clock",
    "operation_clock",
    "encounter_clock",
})
SCENE_PLAN_ANCHOR_TYPES = frozenset({"location", "environment"})


def is_handout_fact(fact: SourceFact) -> bool:
    """Return whether a fact was explicitly classified as display material."""
    return fact.kind == "handout"


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_profile(path: str | Path) -> RuleProfile:
    return RuleProfile.model_validate(load_json(path))


def load_profiles(directory: str | Path) -> dict[str, RuleProfile]:
    profiles: dict[str, RuleProfile] = {}
    for path in sorted(Path(directory).glob("*.json")):
        profile = load_profile(path)
        if profile.id in profiles:
            raise DomainValidationError(f"duplicate rule profile id: {profile.id}")
        profiles[profile.id] = profile
    if not profiles:
        raise DomainValidationError(f"no rule profiles found in {directory}")
    return profiles


_PROFILE_DISPLAY_NAMES = {
    "cthulhu-dark-2e": "现实恐怖",
    "daggerheart": "奇幻冒险",
    "module-prep": "通用备团",
}


def _profile_display_name(profile_id: str, profile: RuleProfile | None = None) -> str:
    """Return the neutral board name used in exports and user-facing copy."""
    return _PROFILE_DISPLAY_NAMES.get(profile_id, "备团板块")


def _fact_index(facts: list[SourceFact]) -> dict[str, SourceFact]:
    index: dict[str, SourceFact] = {}
    for fact in facts:
        if fact.id in index:
            raise DomainValidationError(f"duplicate fact id: {fact.id}")
        index[fact.id] = fact
    return index


def validate_fact_graph(facts: list[SourceFact]) -> dict[str, SourceFact]:
    index = _fact_index(facts)
    graph: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        for linked_id in fact.links:
            if linked_id == fact.id:
                raise DomainValidationError(f"fact {fact.id} links to itself")
            if linked_id not in index:
                raise DomainValidationError(
                    f"fact {fact.id} links to missing fact {linked_id}"
                )
            graph[fact.id].append(linked_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(fact_id: str, path: tuple[str, ...]) -> None:
        if fact_id in visiting:
            cycle = " -> ".join((*path, fact_id))
            raise DomainValidationError(f"fact link cycle: {cycle}")
        if fact_id in visited:
            return
        visiting.add(fact_id)
        for linked_id in graph[fact_id]:
            visit(linked_id, (*path, fact_id))
        visiting.remove(fact_id)
        visited.add(fact_id)

    for fact_id in list(graph):
        visit(fact_id, ())
    return index


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _model_candidate_fact_ids(
    fact_ids: list[str], fact_index: Mapping[str, SourceFact]
) -> list[str]:
    return [
        fact_id
        for fact_id in fact_ids
        if fact_index[fact_id].evidence_status == "model_candidate"
    ]


def validate_cards(
    cards: list[DerivedCard],
    profiles: Mapping[str, RuleProfile],
    facts: list[SourceFact],
) -> None:
    fact_index = {fact.id: fact for fact in facts}
    fact_ids = set(fact_index)
    card_ids: set[str] = set()
    for card in cards:
        if card.id in card_ids:
            raise DomainValidationError(f"duplicate card id: {card.id}")
        card_ids.add(card.id)

        profile = profiles.get(card.profile_id)
        if profile is None:
            raise DomainValidationError(
                f"card {card.id} refers to missing profile {card.profile_id}"
            )
        definition = profile.definition_for(card.type)

        missing = [key for key in definition.required_fields if not _nonempty(card.fields.get(key))]
        if missing:
            raise DomainValidationError(f"card {card.id} lacks required fields: {missing}")

        unknown_refs = [item for item in card.fact_ids if item not in fact_ids]
        if unknown_refs:
            raise DomainValidationError(
                f"card {card.id} refers to missing facts: {unknown_refs}"
            )
        unknown_source_fields = [key for key in card.field_sources if key not in card.fields]
        if unknown_source_fields:
            raise DomainValidationError(
                f"card {card.id} has sources for missing fields: {unknown_source_fields}"
            )
        field_source_refs = [
            fact_id
            for source_ids in card.field_sources.values()
            for fact_id in source_ids
        ]
        unknown_field_refs = [item for item in field_source_refs if item not in fact_ids]
        if unknown_field_refs:
            raise DomainValidationError(
                f"card {card.id} field sources refer to missing facts: {unknown_field_refs}"
            )
        outside_card_refs = [item for item in field_source_refs if item not in card.fact_ids]
        if outside_card_refs:
            raise DomainValidationError(
                f"card {card.id} field sources are outside its fact ids: {outside_card_refs}"
            )
        if card.edit_state == "approved":
            candidate_refs = _model_candidate_fact_ids(card.fact_ids, fact_index)
            if candidate_refs:
                raise DomainValidationError(
                    f"approved card {card.id} refers to model candidate facts: {candidate_refs}"
                )


def validate_bundle(
    bundle: ExampleBundle, profiles: Mapping[str, RuleProfile]
) -> dict[str, SourceFact]:
    missing_profiles = [item for item in bundle.profile_ids if item not in profiles]
    if missing_profiles:
        raise DomainValidationError(f"bundle refers to missing profiles: {missing_profiles}")

    fact_index = validate_fact_graph(bundle.facts)
    validate_cards(bundle.cards, profiles, bundle.facts)
    card_ids = {card.id for card in bundle.cards}
    cards_by_id = {card.id: card for card in bundle.cards}
    fact_ids = {fact.id for fact in bundle.facts}
    material_ids: set[str] = set()
    materials_by_id: dict[str, DisplayMaterial] = {}
    for material in bundle.display_materials:
        if material.id in material_ids:
            raise DomainValidationError(f"duplicate display material id: {material.id}")
        material_ids.add(material.id)
        materials_by_id[material.id] = material
        missing_material_facts = [
            fact_id for fact_id in material.source_fact_ids if fact_id not in fact_ids
        ]
        if missing_material_facts:
            raise DomainValidationError(
                f"display material {material.id} refers to missing facts: {missing_material_facts}"
            )
        candidate_material_facts = [
            fact_id
            for fact_id in material.source_fact_ids
            if fact_index[fact_id].evidence_status == "model_candidate"
        ]
        if candidate_material_facts:
            raise DomainValidationError(
                f"display material {material.id} refers to model candidate facts: {candidate_material_facts}"
            )
        non_material_facts = [
            fact_id
            for fact_id in material.source_fact_ids
            if not is_handout_fact(fact_index[fact_id])
        ]
        if non_material_facts:
            raise DomainValidationError(
                f"display material {material.id} must cite explicitly classified display-material facts: {non_material_facts}"
            )
    bundle_profile_ids = set(bundle.profile_ids)
    outside_card_profiles = sorted({
        card.profile_id for card in bundle.cards
        if card.profile_id not in bundle_profile_ids
    })
    if outside_card_profiles:
        raise DomainValidationError(
            f"cards refer to profiles outside the bundle: {outside_card_profiles}"
        )
    plan_ids: set[str] = set()
    plans_by_id: dict[str, ScenePlan] = {}
    for plan in bundle.plans:
        if plan.id in plan_ids:
            raise DomainValidationError(f"duplicate scene plan id: {plan.id}")
        plan_ids.add(plan.id)
        plans_by_id[plan.id] = plan
        if plan.profile_id not in bundle.profile_ids:
            raise DomainValidationError(f"plan {plan.id} refers to a profile outside the bundle")
        if profiles[plan.profile_id].profile_kind != "runtime":
            raise DomainValidationError(f"plan {plan.id} refers to a preparation-only profile")
        if len(plan.card_ids) != len(set(plan.card_ids)):
            raise DomainValidationError(f"plan {plan.id} contains duplicate card ids")
        missing_cards = [card_id for card_id in plan.card_ids if card_id not in card_ids]
        if missing_cards:
            raise DomainValidationError(f"plan {plan.id} refers to missing cards: {missing_cards}")
        plan_profile_mismatches = [
            card_id for card_id in plan.card_ids
            if cards_by_id[card_id].profile_id != plan.profile_id
        ]
        if plan_profile_mismatches:
            raise DomainValidationError(
                f"plan {plan.id} refers to cards from another profile: {plan_profile_mismatches}"
            )
        unapproved_cards = [
            card_id for card_id in plan.card_ids
            if cards_by_id[card_id].edit_state != "approved"
        ]
        if unapproved_cards:
            raise DomainValidationError(
                f"plan {plan.id} refers to unapproved cards: {unapproved_cards}"
            )
        if not any(
            cards_by_id[card_id].type in SCENE_PLAN_ANCHOR_TYPES
            for card_id in plan.card_ids
        ):
            raise DomainValidationError(f"plan {plan.id} has no runtime location card")
        expected_location_ids = [
            card_id for card_id in plan.card_ids
            if cards_by_id[card_id].type in SCENE_PLAN_ANCHOR_TYPES
        ]
        if plan.location_card_ids != expected_location_ids:
            raise DomainValidationError(
                f"plan {plan.id} location index does not match its runtime cards"
            )
        plan_fact_ids = [
            fact_id
            for card_id in plan.card_ids
            for fact_id in cards_by_id[card_id].fact_ids
        ]
        plan_candidate_refs = _model_candidate_fact_ids(plan_fact_ids, fact_index)
        if plan_candidate_refs:
            raise DomainValidationError(
                f"scene plan {plan.id} refers to model candidate facts through cards: {plan_candidate_refs}"
            )
        for beat in plan.beats:
            if len(beat.card_ids) != len(set(beat.card_ids)):
                raise DomainValidationError(f"beat {beat.id} contains duplicate card ids")
            if len(beat.display_material_ids) != len(set(beat.display_material_ids)):
                raise DomainValidationError(f"beat {beat.id} contains duplicate display material ids")
            missing_beat_cards = [card_id for card_id in beat.card_ids if card_id not in card_ids]
            if missing_beat_cards:
                raise DomainValidationError(f"beat {beat.id} refers to missing cards: {missing_beat_cards}")
            outside_plan_cards = [card_id for card_id in beat.card_ids if card_id not in plan.card_ids]
            if outside_plan_cards:
                raise DomainValidationError(
                    f"beat {beat.id} refers to cards outside plan {plan.id}: {outside_plan_cards}"
                )
            missing_materials = [
                material_id for material_id in beat.display_material_ids
                if material_id not in material_ids
            ]
            if missing_materials:
                raise DomainValidationError(
                    f"beat {beat.id} refers to missing display materials: {missing_materials}"
                )
            missing_confirmations = [
                material_id for material_id in beat.display_material_ids
                if (plan.id, beat.id) not in {
                    (link.plan_id, link.beat_id)
                    for link in materials_by_id[material_id].links
                }
            ]
            if missing_confirmations:
                raise DomainValidationError(
                    f"beat {beat.id} contains unconfirmed display material links: {missing_confirmations}"
                )
            missing_facts = [fact_id for fact_id in beat.reveal_fact_ids if fact_id not in fact_ids]
            if missing_facts:
                raise DomainValidationError(f"beat {beat.id} refers to missing facts: {missing_facts}")
            candidate_refs = _model_candidate_fact_ids(beat.reveal_fact_ids, fact_index)
            if candidate_refs:
                raise DomainValidationError(
                    f"beat {beat.id} refers to model candidate facts: {candidate_refs}"
                )
    for material in bundle.display_materials:
        for link in material.links:
            plan = plans_by_id.get(link.plan_id)
            if plan is None:
                raise DomainValidationError(
                    f"display material {material.id} refers to missing plan {link.plan_id}"
                )
            if link.card_id is not None:
                if link.card_id not in plan.location_card_ids:
                    raise DomainValidationError(
                        f"display material {material.id} refers to a location outside plan {plan.id}"
                    )
                continue
            if link.beat_id not in {beat.id for beat in plan.beats}:
                raise DomainValidationError(
                    f"display material {material.id} refers to missing beat {link.beat_id}"
                )
            beat = next(beat for beat in plan.beats if beat.id == link.beat_id)
            if material.id not in beat.display_material_ids:
                raise DomainValidationError(
                    f"display material {material.id} link is not listed on beat {link.beat_id}"
                )
    return fact_index


def validate_session(session: SessionState, bundle: ExampleBundle) -> None:
    """Validate runtime pointers without requiring a saved session to exist."""
    if session.example_id != bundle.id:
        raise DomainValidationError("session example id does not match bundle")
    card_by_id = {card.id: card for card in bundle.cards}
    plan_by_id = {plan.id: plan for plan in bundle.plans}
    if session.current_plan_id is not None and session.current_plan_id not in plan_by_id:
        raise DomainValidationError("session points to a missing scene plan")
    if session.current_beat_id is not None:
        if session.current_plan_id is None or session.current_plan_id not in plan_by_id:
            raise DomainValidationError("session beat has no valid current plan")
        beat_ids = {beat.id for beat in plan_by_id[session.current_plan_id].beats}
        if session.current_beat_id not in beat_ids:
            raise DomainValidationError("session points to a missing scene beat")
    if session.current_plan_id is not None:
        active_plan = plan_by_id[session.current_plan_id]
        if active_plan.navigation_mode == "location" and session.current_beat_id is not None:
            raise DomainValidationError("location-led runtime cannot point to a scene beat")
    if session.current_plan_id is not None and session.current_card_id is not None:
        if session.current_card_id not in plan_by_id[session.current_plan_id].card_ids:
            raise DomainValidationError("session current card is outside the current scene plan")
    if session.current_card_id is not None and session.current_card_id not in card_by_id:
        raise DomainValidationError("session points to a missing current card")
    for trigger_key, trigger_state in session.trigger_states.items():
        parts = trigger_key.split(":")
        if len(parts) != 3 or parts[1] != "first" or not parts[2].isdigit():
            raise DomainValidationError(f"invalid location trigger key: {trigger_key}")
        card = card_by_id.get(parts[0])
        if card is None or card.type != "location":
            raise DomainValidationError(f"location trigger points to a missing location card: {trigger_key}")
        triggers = card.fields.get("first_triggers", [])
        if not isinstance(triggers, list) or int(parts[2]) >= len(triggers):
            raise DomainValidationError(f"location trigger index is out of range: {trigger_key}")
        if trigger_state not in {"unhandled", "active", "resolved"}:
            raise DomainValidationError(f"invalid location trigger state: {trigger_state}")
    clock_cards = {
        card.id: card for card in bundle.cards
        if card.type in {"clock", "operation_clock", "encounter_clock"}
    }
    for card_id, stage in session.clock_stages.items():
        if card_id not in clock_cards:
            raise DomainValidationError(f"session points to a non-clock card: {card_id}")
        stages = clock_cards[card_id].fields.get("stages", [])
        if not isinstance(stage, int) or stage < 0 or (stages and stage >= len(stages)):
            raise DomainValidationError(f"invalid stage for clock {card_id}: {stage}")
    for entry in session.log:
        if entry.plan_id is not None and entry.plan_id not in plan_by_id:
            raise DomainValidationError(f"session log {entry.id} refers to a missing plan")
        if entry.card_id is not None and entry.card_id not in card_by_id:
            raise DomainValidationError(f"session log {entry.id} refers to a missing card")
        if entry.beat_id is not None:
            event_plan_id = entry.plan_id or session.current_plan_id
            if event_plan_id is None or event_plan_id not in plan_by_id:
                raise DomainValidationError(
                    f"session log {entry.id} beat has no valid plan context"
                )
            beat_ids = {beat.id for beat in plan_by_id[event_plan_id].beats}
            if entry.beat_id not in beat_ids:
                raise DomainValidationError(f"session log {entry.id} refers to a missing beat")
    if len(session.log) > 200:
        raise DomainValidationError("session log cannot contain more than 200 entries")


def _review_entry(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "text": entry.text,
        "created_at": entry.created_at,
        "subject_type": entry.subject_type,
        "subject_id": entry.subject_id,
        "plan_id": entry.plan_id,
        "card_id": entry.card_id,
        "beat_id": entry.beat_id,
        "metadata": dict(entry.metadata),
    }


def build_session_review(session: SessionState, bundle: ExampleBundle) -> dict[str, Any]:
    """Summarize GM workbench activity without inferring player or external data."""
    card_by_id = {card.id: card for card in bundle.cards}
    plan_by_id = {plan.id: plan for plan in bundle.plans}
    event_counts = Counter(entry.kind for entry in session.log)
    source_pages: dict[tuple[str, int], dict[str, Any]] = {}
    card_attention: dict[str, dict[str, Any]] = {}
    lookups: list[dict[str, Any]] = []
    lookup_gaps: list[dict[str, Any]] = []
    revealed_clues: list[dict[str, Any]] = []
    clock_changes: list[dict[str, Any]] = []
    scene_changes: list[dict[str, Any]] = []
    field_edits: list[dict[str, Any]] = []
    manual_notes: list[dict[str, Any]] = []

    for entry in session.log:
        record = _review_entry(entry)
        if entry.card_id is not None:
            attention = card_attention.setdefault(
                entry.card_id,
                {
                    "card_id": entry.card_id,
                    "card_title": card_by_id.get(entry.card_id).title
                    if entry.card_id in card_by_id
                    else entry.card_id,
                    "event_count": 0,
                    "lookup_count": 0,
                    "first_seen_at": entry.created_at,
                    "last_seen_at": entry.created_at,
                },
            )
            attention["event_count"] += 1
            attention["last_seen_at"] = entry.created_at
            if entry.kind == "lookup":
                attention["lookup_count"] += 1

        if entry.kind == "lookup":
            lookups.append(record)
        elif entry.kind == "lookup_missing":
            lookup_gaps.append(record)
        elif entry.kind == "source_page_opened":
            file_name = entry.metadata.get("file")
            page = entry.metadata.get("page")
            if isinstance(file_name, str) and isinstance(page, int):
                item = source_pages.setdefault(
                    (file_name, page),
                    {
                        "file": file_name,
                        "page": page,
                        "open_count": 0,
                        "first_opened_at": entry.created_at,
                        "last_opened_at": entry.created_at,
                    },
                )
                item["open_count"] += 1
                item["last_opened_at"] = entry.created_at
        elif entry.kind in {"clock_advanced", "clock_rewound"}:
            clock_changes.append(record)
        elif entry.kind == "clue_revealed":
            revealed_clues.append(record)
        elif entry.kind == "scene_changed":
            scene_changes.append(record)
        elif entry.kind == "field_edited":
            field_edits.append(record)
        elif entry.kind in {"manual_note", "note"}:
            manual_notes.append(record)

    active_plan = plan_by_id.get(session.current_plan_id) if session.current_plan_id else None
    return {
        "schema_version": "1.0",
        "example_id": session.example_id,
        "privacy_scope": (
            "仅汇总 GM 在工作台中的操作与手工备注；"
            "不自动记录玩家隐私或未经同意的外部数据。"
        ),
        "current_state": {
            "plan_id": session.current_plan_id,
            "plan_title": active_plan.title if active_plan else None,
            "beat_id": session.current_beat_id,
            "card_id": session.current_card_id,
            "revealed_clue_count": len(session.revealed_clue_keys),
            "clock_stages": dict(session.clock_stages),
        },
        "summary": {
            "event_count": len(session.log),
            "event_counts": dict(sorted(event_counts.items())),
            "lookup_count": len(lookups),
            "lookup_gap_count": len(lookup_gaps),
            "source_page_open_count": sum(
                item["open_count"] for item in source_pages.values()
            ),
            "revealed_clue_count": len(revealed_clues),
            "clock_change_count": len(clock_changes),
            "scene_change_count": len(scene_changes),
            "field_edit_count": len(field_edits),
            "manual_note_count": len(manual_notes),
        },
        "lookup_gaps": lookup_gaps,
        "lookups": lookups,
        "source_pages": sorted(
            source_pages.values(), key=lambda item: (item["file"], item["page"])
        ),
        "revealed_clues": revealed_clues,
        "clock_changes": clock_changes,
        "scene_changes": scene_changes,
        "card_attention": sorted(
            card_attention.values(),
            key=lambda item: (-item["event_count"], item["card_title"]),
        ),
        "field_edits": field_edits,
        "manual_notes": manual_notes,
    }


def _review_line(record: Mapping[str, Any]) -> str:
    created_at = record.get("created_at") or "无时间"
    text = record.get("text") or "无说明"
    return f"- {created_at}：{text}"


def export_session_review_markdown(session: SessionState, bundle: ExampleBundle) -> str:
    """Render a compact, GM-facing retrospective from structured session events."""
    review = build_session_review(session, bundle)
    summary = review["summary"]
    state = review["current_state"]
    lines = [
        "# 运行复盘",
        "",
        f"- 样例：{review['example_id']}",
        f"- 当前计划：{state['plan_title'] or state['plan_id'] or '尚未开始'}",
        f"- 事件：{summary['event_count']}；查找：{summary['lookup_count']}；未找到：{summary['lookup_gap_count']}；源页：{summary['source_page_open_count']}",
        f"- 线索揭示：{summary['revealed_clue_count']}；时钟变动：{summary['clock_change_count']}；字段改写：{summary['field_edit_count']}",
        "",
        "## 未找到的信息",
        "",
    ]
    if review["lookup_gaps"]:
        lines.extend(_review_line(item) for item in review["lookup_gaps"])
    else:
        lines.append("- 无")

    lines.extend(["", "## 查找与卡片停留", ""])
    if review["card_attention"]:
        for item in review["card_attention"]:
            lines.append(
                f"- {item['card_title']}：{item['event_count']} 次运行事件，"
                f"{item['lookup_count']} 次查找，最后记录于 {item['last_seen_at']}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## 打开的来源页", ""])
    if review["source_pages"]:
        for item in review["source_pages"]:
            lines.append(
                f"- {item['file']}，PDF p{item['page']}："
                f"{item['open_count']} 次"
            )
    else:
        lines.append("- 无")

    sections = [
        ("已揭示线索", review["revealed_clues"]),
        ("时钟推进", review["clock_changes"]),
        ("场景切换", review["scene_changes"]),
        ("手工补写与字段改动", [*review["field_edits"], *review["manual_notes"]]),
    ]
    for title, records in sections:
        lines.extend(["", f"## {title}", ""])
        if records:
            lines.extend(_review_line(record) for record in records)
        else:
            lines.append("- 无")

    lines.extend(["", "## 数据边界", "", review["privacy_scope"], ""])
    return "\n".join(lines)


def draft_scene_plan(
    bundle: ExampleBundle,
    profile_id: str,
    card_ids: list[str],
    title: str,
    source_file: str,
    source_pages: list[int],
    premise: str,
    profile: RuleProfile | None = None,
) -> ScenePlan:
    """Build a deterministic, editable scene skeleton from confirmed cards.

    This is orchestration, not interpretation: it never invents facts or player
    actions and leaves the GM to write the actual spoken framing.
    """
    if profile_id not in bundle.profile_ids:
        raise DomainValidationError("requested profile is not enabled for this bundle")
    if not title.strip() or not source_file.strip() or not premise.strip():
        raise DomainValidationError("scene plan title, source file, and premise cannot be empty")
    if any(page < 1 for page in source_pages):
        raise DomainValidationError("scene plan source pages must be positive")
    if len(card_ids) != len(set(card_ids)):
        raise DomainValidationError("scene plan card ids must be unique")
    cards_by_id = {card.id: card for card in bundle.cards}
    missing_cards = [card_id for card_id in card_ids if card_id not in cards_by_id]
    if missing_cards:
        raise DomainValidationError(f"scene plan refers to missing cards: {missing_cards}")
    selected = [cards_by_id[card_id] for card_id in card_ids]
    if not selected:
        raise DomainValidationError("a scene plan needs at least one selected card")
    if any(card.profile_id != profile_id for card in selected):
        raise DomainValidationError("all selected cards must use the requested profile")
    fact_index = {fact.id: fact for fact in bundle.facts}
    selected_candidate_refs = _model_candidate_fact_ids(
        [fact_id for card in selected for fact_id in card.fact_ids], fact_index
    )
    if selected_candidate_refs:
        raise DomainValidationError(
            "scene plans cannot be drafted from model candidate facts: "
            f"{selected_candidate_refs}"
        )
    if profile is not None and profile.id != profile_id:
        raise DomainValidationError("scene plan profile does not match the requested profile id")
    if profile is not None and profile.profile_kind != "runtime":
        raise DomainValidationError("scene plans require a runtime rule profile")

    def rule_focus(mode: str) -> str | None:
        return profile.scene_guidance.get(mode) if profile is not None else None

    scene_cards = [card for card in selected if card.type in SCENE_PLAN_ANCHOR_TYPES]
    npc_cards = [card for card in selected if card.type in {"npc", "character"}]
    clock_cards = [card for card in selected if card.type in {"clock", "operation_clock", "encounter_clock"}]
    threat_cards = [card for card in selected if card.type in {"threat", "enemy", "anomaly"}]
    if not scene_cards:
        raise DomainValidationError("a scene plan needs one location or environment card")
    facts_by_card = {card.id: list(card.fact_ids) for card in selected}

    def facts_for(cards: list[DerivedCard]) -> list[str]:
        return list(dict.fromkeys(
            fact_id for card in cards for fact_id in facts_by_card.get(card.id, [])
        ))

    # Overview/support cards do not change the navigation model. A plan with
    # location anchors remains location-led even when it also includes the
    # chapter overview, NPCs, threats, clocks, or display references.
    location_led = bool(scene_cards) and all(card.type == "location" for card in scene_cards)
    if location_led:
        slug = re.sub(r"[^a-z0-9_-]+", "_", title.lower()).strip("_") or "runtime"
        plan_id = "plan_" + bundle.id + "_" + slug
        existing_ids = {item.id for item in bundle.plans}
        suffix = 2
        candidate_id = plan_id
        while candidate_id in existing_ids:
            candidate_id = plan_id + "_" + str(suffix)
            suffix += 1
        overview = next((card for card in selected if card.type == "chapter_overview"), None)
        endings = overview.fields.get("endings", []) if overview is not None else []
        return ScenePlan(
            id=candidate_id,
            profile_id=profile_id,
            title=title,
            source_file=source_file,
            source_pages=source_pages,
            premise=premise,
            card_ids=[card.id for card in selected],
            navigation_mode="location",
            location_card_ids=[card.id for card in scene_cards],
            beats=[],
            exit_states=endings if isinstance(endings, list) else [],
        )

    beats: list[SceneBeat] = []

    if scene_cards:
        scene = scene_cards[0]
        arrival_cards = [scene, *npc_cards]
        npc_reference = "；".join(
            f"{card.title}：{card.fields.get('first_impression') or card.fields.get('role') or '按其动机行动'}"
            for card in npc_cards
        )
        arrival_framing = str(scene.fields.get("arrival_description") or scene.fields.get("opening_image") or scene.title)
        if npc_reference:
            arrival_framing += " 在场参考：" + npc_reference + "。不要一次把这些资料全说完，只在玩家注意到或询问时调用。"
        beats.append(SceneBeat(
            id="beat_arrival",
            title="抵达与定调",
            mode="arrival",
            source_pages=source_pages,
            framing=arrival_framing,
            situation=str(scene.fields.get("arrival_description") or scene.fields.get("opening_image") or scene.title),
            rule_focus=rule_focus("arrival"),
            card_ids=[card.id for card in arrival_cards],
            reveal_fact_ids=facts_for(arrival_cards),
            soft_cues=["描述可见的人、声音、光线和正在发生的日常，不宣布玩家应该调查什么。"],
            hard_cues=[],
            question_prompts=["玩家把注意力放在哪里？"],
            exit_when=["桌上已经形成第一个明确关注点，或玩家决定离开当前地点。"],
        ))
        for location_index, location_card in enumerate(scene_cards[1:], start=1):
            location_facts = facts_for([location_card])
            location_opening = location_card.fields.get("opening_image") or location_card.fields.get("situation") or location_card.title
            beats.append(SceneBeat(
                id=f"beat_location_{location_index}",
                title=f"探索：{location_card.title}",
                mode="investigation",
                source_pages=source_pages,
                framing=str(location_opening),
                situation=f"玩家可以主动前往并调查“{location_card.title}”。",
                rule_focus=rule_focus("investigation"),
                card_ids=[location_card.id],
                reveal_fact_ids=location_facts,
                soft_cues=["先描述地点中可见的变化，再等待玩家决定如何调查。"],
                hard_cues=[],
                question_prompts=["玩家想先看什么、询问谁，或带走什么线索？"],
                exit_when=["玩家离开该地点，或已获得足够线索转向下一个地点。"],
            ))
    if npc_cards or scene_cards:
        beats.append(SceneBeat(
            id="beat_investigation",
            title="人物与现场反应",
            mode="investigation",
            source_pages=source_pages,
            framing="描述在场人物和现场正在发生的事，然后停下来，把注意力交给玩家；不要替他们宣布调查目标。",
            situation="玩家的关注改变了现场；让在场人物依照自己的目的回应，并把已确认事实放回叙事。",
            rule_focus=rule_focus("investigation"),
            card_ids=[card.id for card in [*scene_cards[:1], *npc_cards]],
            reveal_fact_ids=facts_for([*scene_cards[:1], *npc_cards]),
            soft_cues=["先描述人物正在做什么，再等待玩家回应。", "玩家没有追问时，不替他们列出调查菜单。"],
            hard_cues=["玩家制造了明确风险或拖延已经改变局势时，推进对应威胁。"],
            question_prompts=["谁注意到了玩家？谁仍在假装一切正常？"],
            exit_when=["玩家获得足够线索并改变位置、目标或方法。"],
        ))
    if clock_cards:
        beats.append(SceneBeat(
            id="beat_pressure",
            title="压力开始显形",
            mode="pressure",
            source_pages=source_pages,
            framing="描述一个可被玩家注意到的变化，让他们感到时间、暴露或仪式正在靠近。",
            situation="时间、暴露或仪式不再只是背景；用现场变化让玩家感到它正在推进。",
            rule_focus=rule_focus("pressure"),
            card_ids=[card.id for card in clock_cards],
            reveal_fact_ids=facts_for(clock_cards),
            soft_cues=["先给出可回应的迹象：声音、来客、灯光、远处动静或 NPC 的异常反应。"],
            hard_cues=["玩家忽略已经明确的危险，或动作结果要求代价时，推进时钟并描述后果。"],
            question_prompts=["现在什么变化最先被玩家看见？"],
            exit_when=["玩家改变计划、进入下一层地点，或压力触发不可逆后果。"],
        ))
    if threat_cards:
        beats.append(SceneBeat(
            id="beat_confrontation",
            title="威胁成为现场事实",
            mode="confrontation",
            source_pages=source_pages,
            framing="先描述威胁的意图或前兆，再等待玩家回应；只有 fiction 已经越过阈值时才直接造成严重后果。",
            situation="威胁不必等待玩家选择战斗；它依照意图行动，玩家再决定如何回应。",
            rule_focus=rule_focus("confrontation"),
            card_ids=[card.id for card in threat_cards],
            reveal_fact_ids=facts_for(threat_cards),
            soft_cues=["先展示意图或危险前兆，给玩家一次改变计划的机会。"],
            hard_cues=["只有在 fiction 已经越过阈值时，才直接执行严重后果。"],
            question_prompts=["玩家想改变什么：位置、关系、证据，还是威胁本身？"],
            exit_when=["威胁被绕开、转移、暂时压制，或场景目标已经改变。"],
        ))
    beats.append(SceneBeat(
        id="beat_aftermath",
        title="收束与转场",
        mode="aftermath",
        source_pages=source_pages,
        framing="描述现场留下的变化，并把镜头交给玩家决定下一步去哪里或追谁。",
        situation="场景的直接变化已经发生；总结谁知道了什么、什么代价留下，以及镜头接下来落在哪里。",
        rule_focus=rule_focus("aftermath"),
        card_ids=[card.id for card in selected],
        reveal_fact_ids=facts_for(selected),
        soft_cues=["跳过已经没有戏剧价值的重复搜索或移动。"],
        hard_cues=[],
        question_prompts=["现在最值得跟随的下一拍是什么？"],
        exit_when=["玩家进入下一场景、休整，或明确结束当前调查。"],
    ))
    slug = re.sub(r"[^a-z0-9_-]+", "_", title.lower()).strip("_")
    if not slug:
        slug = "scene"
    plan_id = "plan_" + bundle.id + "_" + slug
    existing_ids = {item.id for item in bundle.plans}
    suffix = 2
    candidate_id = plan_id
    while candidate_id in existing_ids:
        candidate_id = plan_id + "_" + str(suffix)
        suffix += 1
    return ScenePlan(
        id=candidate_id,
        profile_id=profile_id,
        title=title,
        source_file=source_file,
        source_pages=source_pages,
        premise=premise,
        card_ids=[card.id for card in selected],
        navigation_mode="beat",
        location_card_ids=[card.id for card in scene_cards],
        beats=beats,
        exit_states=["带着新线索离开", "压力升级后转场", "目标改变", "暂时失败但故事继续"],
    )


def draft_scene_plan_from_workspace(
    bundle: ExampleBundle,
    profiles: Mapping[str, RuleProfile],
    *,
    profile_id: str | None = None,
    source_file: str | None = None,
    source_pages: list[int] | None = None,
) -> ScenePlan:
    """Draft from persisted workspace context without accepting GM-authored inputs.

    A prep-task workspace already fixes the source, page scope, target board and
    session length. Seed workspaces fall back to their approved card references.
    """
    runtime_profile_ids = [
        item
        for item in bundle.profile_ids
        if item in profiles and profiles[item].profile_kind == "runtime"
    ]
    if profile_id is None:
        profile_id = runtime_profile_ids[0] if runtime_profile_ids else None
    if profile_id is None or profile_id not in runtime_profile_ids:
        raise DomainValidationError(
            "当前板块只负责材料整理，不能直接组装运行场景"
        )

    trusted_source = (source_file or "").strip()
    trusted_pages = sorted({page for page in (source_pages or []) if page > 0})
    # A preparation task owns the selected source scope. When a caller passes
    # a source scope, only cards with matching provenance belong to this plan.
    # Standalone bundles without scope retain the historical all-approved-card
    # assembly path.
    fact_index = {fact.id: fact for fact in bundle.facts}

    def card_matches_scope(card: DerivedCard) -> bool:
        """Keep an assembled plan inside its task-owned source scope."""
        if not trusted_source or not trusted_pages:
            return True
        allowed_pages = set(trusted_pages)
        return any(
            ref.file == trusted_source and ref.page in allowed_pages
            for fact_id in card.fact_ids
            for ref in (fact_index.get(fact_id).source_refs if fact_index.get(fact_id) else [])
        )

    selected: list[DerivedCard] = []
    for card in bundle.cards:
        if card.profile_id != profile_id or card.type not in SCENE_PLAN_CARD_TYPES:
            continue
        if card.edit_state != "approved":
            continue
        if any(
            fact_index[fact_id].evidence_status == "model_candidate"
            for fact_id in card.fact_ids
            if fact_id in fact_index
        ):
            continue
        if card_matches_scope(card):
            selected.append(card)

    if not trusted_source:
        selected_fact_ids = {
            fact_id for card in selected for fact_id in card.fact_ids
        }
        refs_by_file: dict[str, set[int]] = defaultdict(set)
        for fact_id in selected_fact_ids:
            fact = fact_index.get(fact_id)
            if fact is None:
                continue
            for ref in fact.source_refs:
                refs_by_file[ref.file].add(ref.page)
        if not refs_by_file:
            raise DomainValidationError(
                "已批准产物缺少可追溯来源，不能组装运行场景"
            )
        if len(refs_by_file) != 1:
            raise DomainValidationError(
                "当前产物跨越多个来源文件；请按备团任务分别组装运行场景"
            )
        trusted_source, inferred_pages = next(iter(refs_by_file.items()))
        trusted_pages = sorted(inferred_pages)
    if not trusted_pages:
        raise DomainValidationError("当前备团范围没有可用页码，不能组装运行场景")
    selected = [card for card in selected if card_matches_scope(card)]
    if not selected:
        raise DomainValidationError(
            "当前备团任务范围内没有已批准的可编排产物；请先完成该范围的产物生成与复核"
        )
    scene_cards = [card for card in selected if card.type in SCENE_PLAN_ANCHOR_TYPES]
    if not scene_cards:
        raise DomainValidationError(
            "当前备团任务范围内没有已批准的场景或环境产物，不能组装运行场景"
        )

    anchor = scene_cards[0]
    opening = anchor.fields.get("opening_image") or anchor.fields.get("situation")
    if isinstance(opening, str) and opening.strip():
        premise = f"{anchor.title}：{opening.strip()}"
    else:
        premise = f"以「{anchor.title}」为当前情境，具体走向由桌边行动决定。"
    return draft_scene_plan(
        bundle,
        profile_id,
        [card.id for card in selected],
        f"{bundle.name} · 运行场景",
        trusted_source,
        trusted_pages,
        premise,
        profile=profiles[profile_id],
    )


def _field_label(key: str) -> str:
    labels = {
        "advance_condition": "推进条件",
        "anomaly_signs": "异常迹象",
        "available_procedures": "可用程序",
        "containment_options": "收容选项",
        "current_stage": "当前阶段",
        "danger_signs": "危险前兆",
        "damaged_behavior": "受创行为",
        "direct_clues": "直接线索",
        "escalation": "升级",
        "evidence_chain": "证据链",
        "exit_conditions": "退场条件",
        "final_consequence": "最终后果",
        "gm_moves": "GM 移动",
        "hidden_clues": "隐藏线索",
        "immediate_actions": "立刻能做的事",
        "intention": "意图",
        "manifestation": "表现",
        "name": "名称",
        "noncombat_exits": "非战斗解法",
        "opening_image": "开场画面",
        "pressure_point": "施压点",
        "refusal_consequence": "拒绝后果",
        "risk_if_pressed": "施压风险",
        "role": "身份",
        "signature_actions": "标志行动",
        "stages": "阶段",
        "wants": "想要",
    }
    return labels.get(key, key.replace("_", " ").capitalize())


def _format_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, dict):
        return [f"- {key}: {item}" for key, item in value.items()]
    if isinstance(value, list):
        formatted: list[str] = []
        for item in value:
            if isinstance(item, dict):
                rendered = ", ".join(f"{key}: {subitem}" for key, subitem in item.items())
                formatted.append(f"- {rendered}")
            else:
                formatted.append(f"- {str(item).strip()}")
        return formatted
    return [str(value)]


def export_cards_markdown(
    cards: list[DerivedCard],
    facts: list[SourceFact],
    profiles: Mapping[str, RuleProfile],
    display_materials: list[DisplayMaterial] | None = None,
) -> str:
    fact_by_id = {fact.id: fact for fact in facts}
    sections: list[str] = ["# 派生卡组", ""]

    for card in cards:
        profile = profiles.get(card.profile_id)
        profile_name = _profile_display_name(card.profile_id, profile)
        sections.extend([f"## {card.title}", "", f"- 备团板块：{profile_name}", f"- 卡型：{card.type}", ""])
        if card.subtitle:
            sections.extend([card.subtitle, ""])

        for key, value in card.fields.items():
            if not _nonempty(value):
                continue
            sections.extend([f"### {_field_label(key)}", "", *_format_value(value), ""])

        cited = [fact_by_id[item] for item in card.fact_ids if item in fact_by_id]
        if cited:
            sections.extend(["### 来源", ""])
            for fact in cited:
                if fact.source_refs:
                    refs = "；".join(
                        f"{source.file}，PDF p{source.page}"
                        + (f"，{source.locator}" if source.locator else "")
                        + (f"，{source.source_version}" if source.source_version else "")
                        for source in fact.source_refs
                    )
                else:
                    refs = "无原文来源"
                sections.append(
                    f"- [{fact.evidence_status}] {fact.text}（{refs}）"
                )
            sections.append("")

    materials = display_materials or []
    if materials:
        sections.extend(["# 展示材料", ""])
        for material in materials:
            sections.extend([f"## {material.title}", ""])
            if material.gm_notes:
                sections.extend(["### GM 备注", "", material.gm_notes, ""])
            sections.extend(["### 来源", ""])
            for source in material.source_refs:
                sections.append(
                    f"- {source.file}，PDF p{source.page}"
                    + (f"，{source.locator}" if source.locator else "")
                )
            if material.links:
                sections.extend(["", "### 已确认运行关联", ""])
                sections.extend(
                    f"- {link.plan_id} / {link.card_id or link.beat_id}"
                    for link in material.links
                )
            sections.append("")

    return "\n".join(sections).rstrip() + "\n"
