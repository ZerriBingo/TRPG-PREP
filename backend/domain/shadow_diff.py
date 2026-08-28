"""Deterministic comparison of shadow candidates against a manual gold standard."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from .models import SourceFact
from .service import ExampleBundle, load_json, load_profiles, validate_bundle
from .shadow import ShadowCandidate


class ShadowDiffError(ValueError):
    """Raised when a candidate snapshot or gold-standard fixture is unusable."""


@dataclass(frozen=True)
class GoldFact:
    id: str
    kind: str
    text: str
    source_refs: tuple[tuple[str, int], ...]
    links: tuple[str, ...]


@dataclass(frozen=True)
class GoldStandard:
    unit_id: str
    source_file: str
    source_version: str
    source_pages: tuple[int, ...]
    facts: dict[str, GoldFact]
    relation_pairs: frozenset[tuple[str, str]]
    asset_ids: frozenset[str]
    narrative_field_count: int
    fingerprint: str


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ShadowDiffError(f"could not read JSON from {path}: {error}") from error


def _as_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShadowDiffError(f"{path} must be an object")
    return value


def _as_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowDiffError(f"{path} must be a non-empty string")
    return value.strip()


def _as_pages(value: Any, path: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ShadowDiffError(f"{path} must be a non-empty page list")
    if any(not isinstance(page, int) or page < 1 for page in value):
        raise ShadowDiffError(f"{path} must contain positive integer pages")
    return tuple(sorted(set(value)))


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _source_ref_payload(source_ref: Any) -> dict[str, Any]:
    return {
        "file": source_ref.file,
        "page": source_ref.page,
        "locator": source_ref.locator,
        "source_version": source_ref.source_version,
    }


def _walk_manual_unit(
    value: Any,
    asset_ids: set[str],
) -> int:
    """Collect stable IDs and count narrative fields from the manual unit."""
    narrative_count = 0
    if isinstance(value, Mapping):
        raw_id = value.get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            asset_ids.add(raw_id)

        raw_fact_ids = value.get("fact_ids")
        if isinstance(raw_fact_ids, list):
            if isinstance(value.get("text"), str):
                narrative_count += 1

        for key, child in value.items():
            if key != "source_refs":
                narrative_count += _walk_manual_unit(
                    child, asset_ids
                )
    elif isinstance(value, list):
        for child in value:
            narrative_count += _walk_manual_unit(
                child, asset_ids
            )
    return narrative_count


def load_gold_standard(
    gold_unit_path: Path,
    pilot_path: Path,
    profiles_path: Path,
) -> GoldStandard:
    """Load the P0.2 unit and its approved fact baseline without mutating either."""
    unit = _as_mapping(_read_json(gold_unit_path), str(gold_unit_path))
    scope = _as_mapping(unit.get("scope"), "gold unit scope")
    source_file = _as_string(scope.get("source_file"), "gold unit scope.source_file")
    source_version = _as_string(
        scope.get("source_version"), "gold unit scope.source_version"
    )
    source_pages = _as_pages(scope.get("source_pages"), "gold unit scope.source_pages")
    unit_id = _as_string(unit.get("id"), "gold unit id")

    try:
        bundle = ExampleBundle.model_validate(load_json(pilot_path))
    except (OSError, ValidationError, ValueError) as error:
        raise ShadowDiffError(f"could not load approved pilot {pilot_path}: {error}") from error
    try:
        approved_facts = validate_bundle(bundle, load_profiles(profiles_path))
    except (OSError, ValidationError, ValueError) as error:
        raise ShadowDiffError(f"could not validate approved pilot {pilot_path}: {error}") from error

    reference_assets = _as_mapping(unit.get("reference_assets"), "gold unit reference_assets")
    raw_fact_ids = reference_assets.get("fact_ids")
    if not isinstance(raw_fact_ids, list) or not raw_fact_ids:
        raise ShadowDiffError("gold unit reference_assets.fact_ids must be a non-empty list")
    if any(not isinstance(fact_id, str) for fact_id in raw_fact_ids):
        raise ShadowDiffError("gold unit reference_assets.fact_ids must contain strings")
    selected_ids = sorted(set(raw_fact_ids))
    if len(selected_ids) != len(raw_fact_ids):
        raise ShadowDiffError("gold unit reference_assets.fact_ids must be unique")
    missing = [fact_id for fact_id in selected_ids if fact_id not in approved_facts]
    if missing:
        raise ShadowDiffError(f"gold unit references missing approved facts: {missing}")

    facts: dict[str, GoldFact] = {}
    for fact_id in selected_ids:
        fact: SourceFact = approved_facts[fact_id]
        source_refs = tuple(
            sorted({(reference.file, reference.page) for reference in fact.source_refs})
        )
        if not source_refs:
            raise ShadowDiffError(f"approved gold fact {fact_id} has no source reference")
        invalid_refs = [
            ref
            for ref in source_refs
            if ref[0] != source_file or ref[1] not in source_pages
        ]
        if invalid_refs:
            raise ShadowDiffError(
                f"approved gold fact {fact_id} falls outside the manual unit scope: {invalid_refs}"
            )
        facts[fact_id] = GoldFact(
            id=fact.id,
            kind=fact.kind,
            text=fact.text,
            source_refs=source_refs,
            links=tuple(sorted(link for link in fact.links if link in selected_ids)),
        )

    relation_pairs: set[tuple[str, str]] = set()
    for fact in facts.values():
        for linked_fact_id in fact.links:
            relation_pairs.add(_pair(fact.id, linked_fact_id))

    asset_ids: set[str] = set()
    raw_card_ids = reference_assets.get("card_ids", [])
    if isinstance(raw_card_ids, list):
        asset_ids.update(
            card_id for card_id in raw_card_ids if isinstance(card_id, str) and card_id
        )
    narrative_field_count = _walk_manual_unit(unit, asset_ids)

    fingerprint_payload = {
        "unit_id": unit_id,
        "source_file": source_file,
        "source_version": source_version,
        "source_pages": source_pages,
        "facts": {
            fact_id: {
                "kind": fact.kind,
                "text": fact.text,
                "source_refs": fact.source_refs,
                "links": fact.links,
            }
            for fact_id, fact in sorted(facts.items())
        },
        "relation_pairs": sorted(relation_pairs),
        "asset_ids": sorted(asset_ids),
        "narrative_field_count": narrative_field_count,
    }
    return GoldStandard(
        unit_id=unit_id,
        source_file=source_file,
        source_version=source_version,
        source_pages=source_pages,
        facts=facts,
        relation_pairs=frozenset(relation_pairs),
        asset_ids=frozenset(asset_ids),
        narrative_field_count=narrative_field_count,
        fingerprint=_fingerprint(fingerprint_payload),
    )


def _snapshot_metadata(raw_task: Any) -> dict[str, Any]:
    if not isinstance(raw_task, Mapping):
        return {}
    metadata: dict[str, Any] = {}
    for key in (
        "id",
        "source_file",
        "source_version",
        "source_pages",
        "profile_id",
        "model_id",
        "prompt_version",
        "schema_version",
    ):
        value = raw_task.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value.strip()
        elif key == "source_pages" and isinstance(value, list) and all(
            isinstance(page, int) for page in value
        ):
            metadata[key] = sorted(set(value))
    return metadata


def load_candidate_snapshot(path: Path) -> tuple[list[ShadowCandidate], dict[str, Any]]:
    """Load a saved API-shaped candidate response without calling a model."""
    raw = _read_json(path)
    metadata: dict[str, Any] = {"snapshot_source": "file"}
    if isinstance(raw, list):
        raw_candidates = raw
    else:
        snapshot = _as_mapping(raw, str(path))
        raw_candidates = snapshot.get("candidates")
        metadata.update(_snapshot_metadata(snapshot.get("task")))
        schema_version = snapshot.get("schema_version")
        if isinstance(schema_version, str) and schema_version.strip():
            metadata["snapshot_schema_version"] = schema_version.strip()
    if not isinstance(raw_candidates, list):
        raise ShadowDiffError("candidate snapshot must contain a candidates list")

    try:
        candidates = [ShadowCandidate.model_validate(value) for value in raw_candidates]
    except ValidationError as error:
        raise ShadowDiffError(f"candidate snapshot has invalid candidates: {error}") from error
    ids = [candidate.id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise ShadowDiffError("candidate snapshot contains duplicate candidate ids")
    return sorted(candidates, key=lambda candidate: candidate.id), metadata


def _compact_text(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _text_match_score(candidate_text: str, fact_text: str) -> float:
    candidate_compact = _compact_text(candidate_text)
    fact_compact = _compact_text(fact_text)
    # Very short labels are too ambiguous to treat as fact coverage on their own.
    if len(candidate_compact) < 12 or len(fact_compact) < 12:
        return 0.0
    candidate_bigrams = _bigrams(candidate_compact)
    fact_bigrams = _bigrams(fact_compact)
    if not candidate_bigrams or not fact_bigrams:
        return 0.0
    shared = len(candidate_bigrams & fact_bigrams)
    candidate_coverage = shared / len(candidate_bigrams)
    fact_coverage = shared / len(fact_bigrams)
    return round((0.65 * candidate_coverage) + (0.35 * fact_coverage), 4)


def _candidate_matches(
    candidate: ShadowCandidate,
    gold: GoldStandard,
    text_match_threshold: float,
) -> list[dict[str, Any]]:
    explicit_ids = set(candidate.possible_links) & set(gold.facts)
    matches: list[dict[str, Any]] = [
        {"fact_id": fact_id, "method": "possible_link", "score": None}
        for fact_id in sorted(explicit_ids)
    ]
    for fact_id, fact in sorted(gold.facts.items()):
        if fact_id in explicit_ids:
            continue
        score = _text_match_score(candidate.text, fact.text)
        if score >= text_match_threshold:
            matches.append(
                {
                    "fact_id": fact_id,
                    "method": "text_overlap",
                    "score": score,
                }
            )
    return matches


def _candidate_source_refs(candidate: ShadowCandidate) -> list[dict[str, Any]]:
    return sorted(
        (_source_ref_payload(reference) for reference in candidate.source_refs),
        key=lambda reference: (
            reference["file"],
            reference["page"],
            reference["locator"] or "",
            reference["source_version"] or "",
        ),
    )


def _candidate_fingerprint_payload(candidate: ShadowCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "text": candidate.text,
        "kind": candidate.kind,
        "source_refs": _candidate_source_refs(candidate),
        "possible_links": sorted(candidate.possible_links),
    }


def _excerpt(value: str, limit: int = 160) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def build_diff_report(
    gold: GoldStandard,
    candidates: Sequence[ShadowCandidate],
    *,
    snapshot_metadata: Mapping[str, Any] | None = None,
    text_match_threshold: float = 0.55,
    field_length_ratio_limit: float = 0.55,
) -> dict[str, Any]:
    """Return a deterministic, candidate-only comparison report.

    A text overlap match is intentionally conservative. `possible_links` to approved
    fact IDs remain the precise way for a model worker to assert intended coverage.
    Every heuristic finding is a review signal; this function never approves or edits
    a candidate.
    """
    if not 0 < text_match_threshold <= 1:
        raise ShadowDiffError("text_match_threshold must be greater than 0 and at most 1")
    if not 0 < field_length_ratio_limit <= 1:
        raise ShadowDiffError(
            "field_length_ratio_limit must be greater than 0 and at most 1"
        )

    sorted_candidates = sorted(candidates, key=lambda candidate: candidate.id)
    candidate_ids = [candidate.id for candidate in sorted_candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ShadowDiffError("candidate ids must be unique for a stable diff")

    metadata = dict(snapshot_metadata or {})
    known_link_ids = set(gold.facts) | set(gold.asset_ids)
    covered_fact_ids: set[str] = set()
    unsupported_source_pages: list[dict[str, Any]] = []
    wrong_page_numbers: list[dict[str, Any]] = []
    wrong_merges: list[dict[str, Any]] = []
    over_summaries: list[dict[str, Any]] = []
    type_mismatches: list[dict[str, Any]] = []
    unknown_relations: list[dict[str, Any]] = []
    unmatched_candidates: list[dict[str, Any]] = []
    candidate_matches: list[dict[str, Any]] = []

    for candidate in sorted_candidates:
        source_refs = _candidate_source_refs(candidate)
        valid_source_refs: list[dict[str, Any]] = []
        for source_ref in source_refs:
            reasons: list[str] = []
            if source_ref["file"] != gold.source_file:
                reasons.append("source_file_mismatch")
            if source_ref["page"] not in gold.source_pages:
                reasons.append("page_outside_gold_scope")
            if source_ref["source_version"] != gold.source_version:
                reasons.append("source_version_mismatch")
            if reasons:
                unsupported_source_pages.append(
                    {
                        "candidate_id": candidate.id,
                        "source_ref": source_ref,
                        "reasons": reasons,
                        "text_excerpt": _excerpt(candidate.text),
                    }
                )
            else:
                valid_source_refs.append(source_ref)

        matches = _candidate_matches(candidate, gold, text_match_threshold)
        matched_fact_ids = [match["fact_id"] for match in matches]
        covered_fact_ids.update(matched_fact_ids)
        candidate_pages = sorted({reference["page"] for reference in valid_source_refs})
        explicit_fact_ids = sorted(set(candidate.possible_links) & set(gold.facts))
        unknown_link_ids = sorted(set(candidate.possible_links) - known_link_ids)
        if unknown_link_ids:
            unknown_relations.append(
                {
                    "candidate_id": candidate.id,
                    "unknown_link_ids": unknown_link_ids,
                    "text_excerpt": _excerpt(candidate.text),
                }
            )

        if not matched_fact_ids:
            unmatched_candidates.append(
                {
                    "candidate_id": candidate.id,
                    "kind": candidate.kind,
                    "source_pages": candidate_pages,
                    "text_excerpt": _excerpt(candidate.text),
                }
            )

        for fact_id in matched_fact_ids:
            fact = gold.facts[fact_id]
            expected_pages = sorted({reference[1] for reference in fact.source_refs})
            if valid_source_refs and not (set(candidate_pages) & set(expected_pages)):
                wrong_page_numbers.append(
                    {
                        "candidate_id": candidate.id,
                        "fact_id": fact_id,
                        "expected_pages": expected_pages,
                        "candidate_pages": candidate_pages,
                        "text_excerpt": _excerpt(candidate.text),
                    }
                )
            if candidate.kind != fact.kind:
                type_mismatches.append(
                    {
                        "candidate_id": candidate.id,
                        "fact_id": fact_id,
                        "expected_kind": fact.kind,
                        "candidate_kind": candidate.kind,
                        "text_excerpt": _excerpt(candidate.text),
                    }
                )

        unsupported_pairs = [
            pair
            for pair in combinations(explicit_fact_ids, 2)
            if _pair(*pair) not in gold.relation_pairs
        ]
        if unsupported_pairs:
            wrong_merges.append(
                {
                    "candidate_id": candidate.id,
                    "fact_ids": explicit_fact_ids,
                    "unrelated_pairs": [list(pair) for pair in unsupported_pairs],
                    "text_excerpt": _excerpt(candidate.text),
                }
            )

        if matched_fact_ids:
            expected_length = sum(
                len(gold.facts[fact_id].text.strip()) for fact_id in matched_fact_ids
            )
            candidate_length = len(candidate.text.strip())
            ratio = round(candidate_length / expected_length, 4) if expected_length else 1.0
            if ratio < field_length_ratio_limit:
                over_summaries.append(
                    {
                        "candidate_id": candidate.id,
                        "fact_ids": matched_fact_ids,
                        "candidate_length": candidate_length,
                        "expected_length": expected_length,
                        "length_ratio": ratio,
                        "text_excerpt": _excerpt(candidate.text),
                    }
                )

        candidate_matches.append(
            {
                "candidate_id": candidate.id,
                "kind": candidate.kind,
                "text_length": len(candidate.text.strip()),
                "source_pages": candidate_pages,
                "matched_facts": matches,
                "explicit_fact_ids": explicit_fact_ids,
                "unknown_link_ids": unknown_link_ids,
            }
        )

    missing_items = [
        {
            "fact_id": fact.id,
            "kind": fact.kind,
            "source_pages": sorted({reference[1] for reference in fact.source_refs}),
            "text_length": len(fact.text.strip()),
            "text_excerpt": _excerpt(fact.text),
        }
        for fact_id, fact in sorted(gold.facts.items())
        if fact_id not in covered_fact_ids
    ]
    candidate_snapshot = [_candidate_fingerprint_payload(candidate) for candidate in sorted_candidates]
    return {
        "schema_version": "shadow-candidate-diff-v1",
        "baseline": {
            "unit_id": gold.unit_id,
            "source_file": gold.source_file,
            "source_version": gold.source_version,
            "source_pages": list(gold.source_pages),
            "fact_count": len(gold.facts),
            "narrative_field_count": gold.narrative_field_count,
            "fingerprint": gold.fingerprint,
        },
        "candidate_snapshot": {
            "metadata": metadata,
            "candidate_count": len(sorted_candidates),
            "fingerprint": _fingerprint(candidate_snapshot),
        },
        "settings": {
            "text_match_threshold": text_match_threshold,
            "field_length_ratio_limit": field_length_ratio_limit,
        },
        "summary": {
            "covered_fact_count": len(covered_fact_ids),
            "missing_item_count": len(missing_items),
            "unsupported_source_page_count": len(unsupported_source_pages),
            "wrong_page_number_count": len(wrong_page_numbers),
            "wrong_merge_count": len(wrong_merges),
            "over_summary_count": len(over_summaries),
            "type_mismatch_count": len(type_mismatches),
            "unknown_relation_count": len(unknown_relations),
            "unmatched_candidate_count": len(unmatched_candidates),
        },
        "missing_items": missing_items,
        "unsupported_source_pages": unsupported_source_pages,
        "wrong_page_numbers": wrong_page_numbers,
        "wrong_merges": wrong_merges,
        "over_summaries": over_summaries,
        "type_mismatches": type_mismatches,
        "unknown_relations": unknown_relations,
        "unmatched_candidates": unmatched_candidates,
        "candidate_matches": candidate_matches,
    }


def render_diff_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact Chinese review report from a deterministic JSON report."""
    baseline = _as_mapping(report.get("baseline"), "report baseline")
    snapshot = _as_mapping(report.get("candidate_snapshot"), "report candidate_snapshot")
    summary = _as_mapping(report.get("summary"), "report summary")
    lines = [
        "# 影子候选与人工金标准差异报告",
        "",
        f"- 报告 schema：{report.get('schema_version')}",
        "- 人工包：{unit}；范围：{file}，PDF p{pages}；版本：{version}".format(
            unit=baseline["unit_id"],
            file=baseline["source_file"],
            pages="-".join(str(page) for page in baseline["source_pages"]),
            version=baseline["source_version"],
        ),
        f"- 人工基线指纹：`{baseline['fingerprint']}`",
        f"- 候选快照：{snapshot['candidate_count']} 条；指纹：`{snapshot['fingerprint']}`",
        "",
        "## 汇总",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
        f"| 覆盖的确认事实 | {summary['covered_fact_count']} |",
        f"| 漏项 | {summary['missing_item_count']} |",
        f"| 无依据页或版本 | {summary['unsupported_source_page_count']} |",
        f"| 错误页码 | {summary['wrong_page_number_count']} |",
        f"| 疑似错误合并 | {summary['wrong_merge_count']} |",
        f"| 疑似过度摘要 | {summary['over_summary_count']} |",
        f"| 类型不一致 | {summary['type_mismatch_count']} |",
        f"| 未识别关系 | {summary['unknown_relation_count']} |",
        f"| 未匹配金标准的候选 | {summary['unmatched_candidate_count']} |",
        "",
    ]

    def section(title: str, entries: Any, formatter: Any) -> None:
        lines.extend([f"## {title}", ""])
        if not entries:
            lines.extend(["无。", ""])
            return
        lines.extend(formatter(entry) for entry in entries)
        lines.append("")

    section(
        "漏项",
        report["missing_items"],
        lambda item: "- `{fact_id}`（{kind}，p{pages}，{length} 字）：{text}".format(
            fact_id=item["fact_id"],
            kind=item["kind"],
            pages=",".join(str(page) for page in item["source_pages"]),
            length=item["text_length"],
            text=item["text_excerpt"],
        ),
    )
    section(
        "无依据页或版本",
        report["unsupported_source_pages"],
        lambda item: "- `{candidate}` 引用 `{file}` p{page}（{reasons}）：{text}".format(
            candidate=item["candidate_id"],
            file=item["source_ref"]["file"],
            page=item["source_ref"]["page"],
            reasons=", ".join(item["reasons"]),
            text=item["text_excerpt"],
        ),
    )
    section(
        "错误页码",
        report["wrong_page_numbers"],
        lambda item: "- `{candidate}` -> `{fact}`：应引 p{expected}，实际引 p{actual}。{text}".format(
            candidate=item["candidate_id"],
            fact=item["fact_id"],
            expected=",".join(str(page) for page in item["expected_pages"]),
            actual=",".join(str(page) for page in item["candidate_pages"]),
            text=item["text_excerpt"],
        ),
    )
    section(
        "疑似错误合并",
        report["wrong_merges"],
        lambda item: "- `{candidate}` 同时合并 {facts}，但人工包未建立关系：{pairs}。{text}".format(
            candidate=item["candidate_id"],
            facts=", ".join(f"`{fact_id}`" for fact_id in item["fact_ids"]),
            pairs="；".join(" + ".join(pair) for pair in item["unrelated_pairs"]),
            text=item["text_excerpt"],
        ),
    )
    section(
        "疑似过度摘要",
        report["over_summaries"],
        lambda item: "- `{candidate}` 覆盖 {facts}：候选 {actual} 字 / 基线 {expected} 字（{ratio:.1%}）。{text}".format(
            candidate=item["candidate_id"],
            facts=", ".join(f"`{fact_id}`" for fact_id in item["fact_ids"]),
            actual=item["candidate_length"],
            expected=item["expected_length"],
            ratio=item["length_ratio"],
            text=item["text_excerpt"],
        ),
    )
    section(
        "类型不一致",
        report["type_mismatches"],
        lambda item: "- `{candidate}` -> `{fact}`：候选 `{candidate_kind}`，基线 `{expected_kind}`。{text}".format(
            candidate=item["candidate_id"],
            fact=item["fact_id"],
            candidate_kind=item["candidate_kind"],
            expected_kind=item["expected_kind"],
            text=item["text_excerpt"],
        ),
    )
    section(
        "未识别关系",
        report["unknown_relations"],
        lambda item: "- `{candidate}` 引用了不存在的目标：{links}。{text}".format(
            candidate=item["candidate_id"],
            links=", ".join(f"`{link_id}`" for link_id in item["unknown_link_ids"]),
            text=item["text_excerpt"],
        ),
    )
    section(
        "未匹配金标准的候选",
        report["unmatched_candidates"],
        lambda item: "- `{candidate}`（`{kind}`，有效来源页：{pages}）：{text}".format(
            candidate=item["candidate_id"],
            kind=item["kind"],
            pages=", ".join(str(page) for page in item["source_pages"]) or "无",
            text=item["text_excerpt"],
        ),
    )
    lines.extend(
        [
            "## 解释边界",
            "",
            "本报告只产生复核信号。它不会接受、拒绝、改写或写入任何候选；`text_overlap` 是保守的字符重叠启发式，精确覆盖应由候选的 `possible_links` 指向确认事实 ID。",
            "",
        ]
    )
    return "\n".join(lines)
