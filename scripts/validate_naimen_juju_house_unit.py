"""Validate and render the P0.2 manual Juju House gold-standard unit."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "backend" / "domain"
DEFAULT_FIXTURE = ROOT / "data" / "fixtures" / "naimen_juju_house_unit.json"
DEFAULT_PILOT = DOMAIN / "examples" / "naimen_pilot.json"
STATUSES = {"source_fact", "inference", "gm_authored", "model_candidate"}
VISIBILITIES = {"gm_only", "player_safe", "handout", "mixed"}


class UnitValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise UnitValidationError(message)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        result = json.load(handle)
    require(isinstance(result, dict), f"{path} must contain a JSON object")
    return result


def as_dict(value: Any, path: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{path} must be an object")
    return value


def as_list(value: Any, path: str) -> list[Any]:
    require(isinstance(value, list), f"{path} must be a list")
    return value


def items(unit: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    return [as_dict(value, f"{key}[{index}]") for index, value in enumerate(as_list(unit.get(key), key))]


def content_nodes(value: Any, path: str = "$") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        if "text" in value:
            yield path, value
        for key, child in value.items():
            if key != "source_refs":
                yield from content_nodes(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from content_nodes(child, f"{path}[{index}]")


def unique_ids(entries: list[dict[str, Any]], label: str) -> set[str]:
    values: list[str] = []
    for index, entry in enumerate(entries):
        item_id = entry.get("id")
        require(isinstance(item_id, str) and item_id.strip(), f"{label}[{index}].id must be non-empty")
        values.append(item_id)
    duplicates = sorted({value for value in values if values.count(value) > 1})
    require(not duplicates, f"duplicate {label} ids: {duplicates}")
    return set(values)


def validate_source_ref(value: Any, path: str, scope: Mapping[str, Any]) -> None:
    ref = as_dict(value, path)
    require(ref.get("file") == scope["source_file"], f"{path}.file must use the scoped PDF")
    require(ref.get("page") in scope["source_pages"], f"{path}.page must be in PDF p159-165")
    require(ref.get("source_version") in (None, scope["source_version"]), f"{path}.source_version must match the scoped version")


def validate_content(
    node: Mapping[str, Any],
    path: str,
    facts: Mapping[str, Any],
    scope: Mapping[str, Any],
) -> None:
    required = {"text", "evidence_status", "visibility", "fact_ids", "source_refs"}
    require(not (set(node) - required), f"{path} has unknown narrative keys")
    require(not (required - set(node)), f"{path} is missing narrative keys")
    require(isinstance(node["text"], str) and node["text"].strip(), f"{path}.text must be non-empty")
    require(node["evidence_status"] in STATUSES, f"{path}.evidence_status is invalid")
    require(node["evidence_status"] != "model_candidate", f"{path} must not contain model_candidate content")
    require(node["visibility"] in VISIBILITIES, f"{path}.visibility is invalid")
    fact_ids = as_list(node["fact_ids"], f"{path}.fact_ids")
    refs = as_list(node["source_refs"], f"{path}.source_refs")
    require(all(isinstance(item, str) for item in fact_ids), f"{path}.fact_ids must contain strings")
    require(len(fact_ids) == len(set(fact_ids)), f"{path}.fact_ids contains duplicates")
    missing = [item for item in fact_ids if item not in facts]
    require(not missing, f"{path} references missing pilot facts: {missing}")
    candidates = [item for item in fact_ids if facts[item].evidence_status == "model_candidate"]
    require(not candidates, f"{path} references candidate facts: {candidates}")
    for index, ref in enumerate(refs):
        validate_source_ref(ref, f"{path}.source_refs[{index}]", scope)
    if node["evidence_status"] in {"source_fact", "inference"}:
        require(bool(fact_ids or refs), f"{path} must cite a pilot fact or source page")


def validate_shape(unit: Mapping[str, Any]) -> None:
    require(unit.get("schema_version") == "1.0", "schema_version must be 1.0")
    require(unit.get("id") == "naimen_juju_house_unit", "fixture id must remain stable")
    require(unit.get("status") == "manual_package_ready_for_gm_trial", "fixture must be ready for GM trial, not falsely complete")
    scope = as_dict(unit.get("scope"), "scope")
    require(scope.get("source_file") == "Resource/奈亚拉托提普的面具v1.0.pdf", "scope.source_file must be the real PDF")
    require(scope.get("source_version") == "v1.0", "scope.source_version must be v1.0")
    require(scope.get("source_pages") == [159, 160, 161, 162, 163, 164, 165], "scope must remain PDF p159-165")

    brief = as_dict(unit.get("mystery_brief"), "mystery_brief")
    for key in ("hook", "theme", "recurring_horror_images", "why_investigators_care", "final_horror", "power_relationships", "opening_state", "open_direction_ids"):
        require(key in brief, f"mystery_brief.{key} is required")
    require(items(unit, "gm_only_background"), "gm_only_background must not be empty")
    minima = {
        "scenes": 3,
        "functional_npcs": 2,
        "investigation_directions": 2,
        "clues": 4,
        "threats": 2,
        "clocks": 2,
        "outcomes": 3,
        "unresolved_leads": 2,
        "next_station_entries": 2,
    }
    for key, minimum in minima.items():
        require(len(items(unit, key)) >= minimum, f"{key} needs at least {minimum} entries")


def validate_reference_assets(unit: Mapping[str, Any], facts: Mapping[str, Any], cards: Mapping[str, Any]) -> None:
    assets = as_dict(unit.get("reference_assets"), "reference_assets")
    require(assets.get("bundle_id") == "naimen_pilot", "reference bundle must be naimen_pilot")
    require(set(as_list(assets.get("fact_ids"), "reference_assets.fact_ids")) == set(facts), "reference assets must retain all 20 pilot facts")
    card_ids = set(as_list(assets.get("card_ids"), "reference_assets.card_ids"))
    require(card_ids == set(cards), "reference assets must retain all seven pilot cards")
    unapproved = [card_id for card_id in card_ids if cards[card_id].edit_state != "approved"]
    require(not unapproved, f"reference assets include non-approved cards: {unapproved}")


def all_target_ids(unit: Mapping[str, Any]) -> set[str]:
    targets: set[str] = set()
    for group in ("scenes", "functional_npcs", "investigation_directions", "clues", "threats", "clocks", "outcomes", "unresolved_leads", "next_station_entries"):
        targets.update(unique_ids(items(unit, group), group))
    return targets


def validate_clues(unit: Mapping[str, Any], targets: set[str]) -> None:
    for clue in items(unit, "clues"):
        clue_id = clue["id"]
        require(isinstance(clue.get("core"), bool), f"{clue_id}.core must be boolean")
        routes = as_list(clue.get("routes"), f"{clue_id}.routes")
        require(isinstance(clue.get("fail_forward"), dict), f"{clue_id}.fail_forward is required")
        if clue["core"]:
            require(bool(routes) or clue["fail_forward"], f"core clue {clue_id} lacks continuation")
        for index, route_value in enumerate(routes):
            route = as_dict(route_value, f"{clue_id}.routes[{index}]")
            require(route.get("target_id") in targets, f"{clue_id} routes to an unknown target")
            require(route.get("target_kind") in {"scene", "outcome", "next_station"}, f"{clue_id} has invalid target_kind")
            require(isinstance(route.get("reason"), dict), f"{clue_id} route needs a reason")


def validate_clocks(unit: Mapping[str, Any]) -> None:
    for clock in items(unit, "clocks"):
        clock_id = clock["id"]
        stages = items(clock, "stages")
        require(len(stages) >= 2, f"{clock_id} needs at least two visible stages")
        stage_ids = unique_ids(stages, f"{clock_id}.stages")
        require(clock.get("start_stage_id") in stage_ids, f"{clock_id}.start_stage_id is invalid")
        require(as_list(clock.get("advance_conditions"), f"{clock_id}.advance_conditions"), f"{clock_id} needs advance conditions")
        require(as_list(clock.get("player_levers"), f"{clock_id}.player_levers"), f"{clock_id} needs player levers")
        for stage in stages:
            require(isinstance(stage.get("visible_change"), dict), f"{clock_id}.{stage['id']} needs visible_change")
            require(isinstance(stage.get("consequence"), dict), f"{clock_id}.{stage['id']} needs consequence")


def validate_references(unit: Mapping[str, Any], targets: set[str]) -> None:
    route_fields = {
        "scenes": ("clue_ids", "npc_ids", "threat_ids", "clock_ids", "exit_ids"),
        "investigation_directions": ("entry_scene_ids", "clue_ids", "continuation_ids"),
        "outcomes": ("next_station_ids",),
        "unresolved_leads": ("next_station_ids",),
    }
    for group, fields in route_fields.items():
        for entry in items(unit, group):
            for field in fields:
                values = as_list(entry.get(field), f"{group}.{entry['id']}.{field}")
                require(values, f"{group}.{entry['id']}.{field} must not be empty")
                for value in values:
                    require(isinstance(value, str) and value in targets, f"{group}.{entry['id']}.{field} has an unknown target")


def validate_unit(unit: Mapping[str, Any], facts: Mapping[str, Any], cards: Mapping[str, Any]) -> int:
    validate_shape(unit)
    validate_reference_assets(unit, facts, cards)
    targets = all_target_ids(unit)
    validate_clues(unit, targets)
    validate_clocks(unit)
    validate_references(unit, targets)
    scope = as_dict(unit["scope"], "scope")
    nodes = list(content_nodes(unit))
    require(nodes, "unit has no narrative nodes")
    for path, node in nodes:
        validate_content(node, path, facts, scope)
    return len(nodes)


def source_label(ref: Mapping[str, Any]) -> str:
    label = f"PDF p{ref['page']}"
    if ref.get("locator"):
        label += f"，{ref['locator']}"
    return label


def render_content(node: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    labels: list[str] = []
    for fact_id in node["fact_ids"]:
        labels.extend(source_label(ref.model_dump()) for ref in facts[fact_id].source_refs)
    labels.extend(source_label(ref) for ref in node["source_refs"])
    source = "；".join(dict.fromkeys(labels)) if labels else "GM 编排"
    return f"{node['text']} [{node['evidence_status']}；{source}]"


def render_markdown(unit: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    output = [
        f"# {unit['title']}：人工金标准运行包",
        "",
        f"- 状态：{unit['status']}",
        "- 范围：Resource/奈亚拉托提普的面具v1.0.pdf，PDF p159-165",
        "- 使用边界：GM-only 运行包；不替代原文，也不包含模型候选。",
        "",
        "## 开场",
        "",
        render_content(unit["mystery_brief"]["hook"], facts),
        "",
        "## 调查方向",
        "",
    ]
    for direction in items(unit, "investigation_directions"):
        output.extend([
            f"### {direction['title']}",
            "",
            render_content(direction["content"], facts),
            "",
            "- 入口场景：" + ", ".join(direction["entry_scene_ids"]),
            "- 关键线索：" + ", ".join(direction["clue_ids"]),
            "- 可继续：" + ", ".join(direction["continuation_ids"]),
            "",
        ])
    output.extend(["## 场景", ""])
    for scene in items(unit, "scenes"):
        output.extend([
            f"### {scene['title']}",
            "",
            render_content(scene["opening"], facts),
            "",
            "- 可用线索：" + ", ".join(scene["clue_ids"]),
            "- 压力：" + render_content(scene["pressure_response"][0], facts),
            "- 转场：" + ", ".join(scene["exit_ids"]),
            "",
        ])
    output.extend(["## 线索网", ""])
    for clue in items(unit, "clues"):
        output.extend([
            f"### {clue['title']}",
            "",
            render_content(clue["discoverable_content"], facts),
            "",
            "- 继续：" + ", ".join(route["target_id"] for route in clue["routes"]),
            "- 失败前推：" + render_content(clue["fail_forward"], facts),
            "",
        ])
    output.extend(["## 时钟", ""])
    for clock in items(unit, "clocks"):
        output.extend([f"### {clock['title']}", ""])
        for index, stage in enumerate(items(clock, "stages"), start=1):
            output.append(f"{index}. {stage['title']}：{render_content(stage['visible_change'], facts)}")
            output.append("   后果：" + render_content(stage["consequence"], facts))
        output.append("")
    output.extend(["## 三种收束", ""])
    for outcome in items(unit, "outcomes"):
        output.extend([
            f"### {outcome['title']}",
            "",
            render_content(outcome["resolution"], facts),
            "",
            "- 代价或妥协：" + render_content(outcome["cost_or_compromise"], facts),
            "- 后续入口：" + ", ".join(outcome["next_station_ids"]),
            "",
        ])
    output.extend(["## 未回收线索", ""])
    for lead in items(unit, "unresolved_leads"):
        output.append(f"- {lead['title']}：{render_content(lead['content'], facts)}")
    output.extend(["", "## 下一站入口", ""])
    for entry in items(unit, "next_station_entries"):
        output.append(f"- {entry['title']}：{render_content(entry['content'], facts)}")
    output.extend(["", "## GM-only 背景", ""])
    for background in items(unit, "gm_only_background"):
        output.extend([f"### {background['title']}", "", render_content(background["content"], facts), ""])
    output.extend([
        "## 试跑状态",
        "",
        "本包已通过自动来源、候选隔离、线索前推、时钟和结局数量校验；真实 GM 试跑尚未执行。试跑时应记录翻页、遗漏、重复与玩家偏航，作为 P0.3 之前的人工反馈。",
        "",
    ])
    return "\n".join(output)


def load_pilot(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.domain import ExampleBundle, load_profiles, validate_bundle

    profiles = load_profiles(DOMAIN / "profiles")
    bundle = ExampleBundle.model_validate(read_json(path))
    facts = validate_bundle(bundle, profiles)
    return facts, {card.id: card for card in bundle.cards}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--write-markdown", type=Path)
    args = parser.parse_args()

    unit = read_json(args.fixture)
    facts, cards = load_pilot(args.pilot)
    node_count = validate_unit(unit, facts, cards)
    print(
        "unit={id} facts={facts} cards={cards} content_nodes={nodes} "
        "directions={directions} clues={clues} clocks={clocks} outcomes={outcomes}".format(
            id=unit["id"],
            facts=len(facts),
            cards=len(cards),
            nodes=node_count,
            directions=len(unit["investigation_directions"]),
            clues=len(unit["clues"]),
            clocks=len(unit["clocks"]),
            outcomes=len(unit["outcomes"]),
        )
    )
    if args.write_markdown:
        args.write_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.write_markdown.write_text(render_markdown(unit, facts), encoding="utf-8")
        print(f"markdown={args.write_markdown}")


if __name__ == "__main__":
    main()
