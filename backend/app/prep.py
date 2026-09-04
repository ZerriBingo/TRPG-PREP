"""R1 cross-page preparation jobs connected to the isolated shadow queue."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

import pymupdf as fitz
from pydantic import ValidationError

from ..domain import (
    ExampleBundle,
    CoreTextSlice,
    DisplayMaterial,
    ExtractionWindow,
    FactProvenance,
    PageSpan,
    PrepJob,
    PrepJobCreate,
    PrepScope,
    ShadowCandidate,
    ShadowTaskSpec,
    SourceFact,
    SourceRef,
    load_profiles,
    parse_page_spans,
    validate_bundle,
)
from . import shadow, storage
from .llm import make_client, parse_json


MAX_CORE_PAGE_COUNT = 3
WINDOW_CONTEXT_PAGE_COUNT = 1
MAX_REFERENCE_PAGE_COUNT = 8
MAX_WINDOW_INPUT_CHARS = 14000
MAX_SEMANTIC_PAGE_SIGNATURE_CHARS = 220
MAX_SEMANTIC_INPUT_CHARS = 18000
SEMANTIC_SLICE_MIN_BREAK_RATIO = 0.45
PROMPT_VERSION = "prep-fact-extract-v4"
SCHEMA_VERSION = "shadow-candidate-v1"
CONSOLIDATION_PROMPT_VERSION = "prep-fact-consolidate-v4"
DETERMINISTIC_CONSOLIDATION_VERSION = "prep-fact-consolidate-deterministic-v1"
MAX_CONSOLIDATION_INPUT_CHARS = 15000
MAX_CONSOLIDATION_CANDIDATES = 50
MAX_CONSOLIDATION_ROUNDS = 8
INTERRUPTED_CONSOLIDATION_ERROR = "segment consolidation was interrupted before completion"

PROFILE_OBJECTIVES = {
    "cthulhu-dark-2e": (
        "整理现实恐怖游戏所需的 GM 背景、调查场景、功能人物、线索、威胁、"
        "压力升级与多种收束；只整理原文支持的事实，不限定玩家路线。"
    ),
    "daggerheart": (
        "整理奇幻冒险所需的冒险框架、场景环境、人物与阵营意图、威胁、动态后果、"
        "非战斗解法与收束；只整理原文支持的事实，不预写玩家行动。"
    ),
    "module-prep": (
        "整理通用备团所需的章节背景、场景、人物功能、线索、威胁逻辑、时间线与"
        "开放问题；只整理原文支持的事实，不套用未选择的规则。"
    ),
}

KIND_ALIASES = {
    "线索": "clue",
    "人物": "npc",
    "角色": "npc",
    "地点": "location",
    "场景": "location",
    "展示材料": "handout",
    "玩家展示材料": "handout",
    "地图": "handout",
    "信件": "handout",
    "照片": "handout",
    "报纸": "handout",
    "事件": "event",
    "威胁": "threat",
    "利害": "stakes",
    "障碍": "obstacle",
    "时间线": "timeline",
    "资源": "resource",
}

_active_jobs: set[str] = set()
_active_jobs_lock = threading.Lock()
_rebuild_check_cache: dict[tuple[str, str, str, tuple], bool] = {}
_rebuild_check_cache_lock = threading.Lock()
_REBUILD_CHECK_CACHE_LIMIT = 256


class PrepError(ValueError):
    pass


class PrepJobNotFoundError(PrepError):
    pass


class PrepJobConflictError(PrepError):
    pass


class PrepSourceError(PrepError):
    pass


class PrepPromotionConflictError(PrepError):
    pass


def _prep_error_kind(error: object) -> str:
    """Map an extraction failure to a stable UI category."""
    text = str(error or "").casefold()
    if "cancel" in text or "取消" in text:
        return "cancelled"
    if "account_muted" in text or "账号访问被暂停" in text:
        return "account_access"
    if any(token in text for token in ("json", "validation", "schema", "字段", "格式", "candidate")):
        return "model_format"
    if any(token in text for token in ("api key", "配置", "密钥", "source pdf", "source file", "输入")):
        return "input_config"
    if any(token in text for token in ("worker", "子进程")):
        return "worker"
    return "upstream_unavailable"


def _source_path(file_name: str) -> Path:
    if not file_name or "://" in file_name:
        raise PrepSourceError("prep jobs require a local PDF source")
    root = storage.PROJECT_ROOT.resolve()
    candidate = (storage.PROJECT_ROOT / file_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise PrepSourceError("source file must be inside the project directory") from error
    if candidate.suffix.lower() != ".pdf" or not candidate.is_file():
        raise PrepSourceError("source PDF was not found")
    return candidate


def _source_version(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _page_count(path: Path) -> int:
    try:
        document = fitz.open(path)
    except Exception as error:  # noqa: BLE001
        raise PrepSourceError(f"could not open source PDF: {error}") from error
    try:
        return document.page_count
    finally:
        document.close()


def _page_texts(path: Path, spans: list[PageSpan]) -> dict[int, str]:
    try:
        document = fitz.open(path)
    except Exception as error:  # noqa: BLE001
        raise PrepSourceError(f"could not open source PDF: {error}") from error
    try:
        return {
            page_number: document[page_number - 1].get_text("text").strip()
            for span in spans
            for page_number in span.pages()
        }
    finally:
        document.close()


def _looks_like_page_heading(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    first = lines[0]
    if len(first) > 90:
        return False
    if re.match(
        r"(?i)^(?:chapter|part|scene|location|appendix)\b|^第\s*[\d一二三四五六七八九十百]+\s*[章节幕部]|^[\d一二三四五六七八九十]+[.、]\s*\S+",
        first,
    ):
        return True
    letters = [
        character for character in first if character.isascii() and character.isalpha()
    ]
    return bool(letters) and len(first) <= 60 and all(
        not character.islower() for character in letters
    )


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(("。", "！", "？", ".", "!", "?", "”", "’", "》", "】"))


def _looks_continued(current_text: str, next_text: str) -> bool:
    current = current_text.rstrip()
    next_lines = [line.strip() for line in next_text.splitlines() if line.strip()]
    if current.endswith((",", "，", ";", "；", ":", "：", "-", "—")):
        return True
    if not next_lines:
        return False
    first = next_lines[0]
    if first[:1].islower() or first.startswith(("，", "。", ",", ".", ")", "）")):
        return True
    return bool(re.match(r"^(?:and|but|or|then|而|并|以及|同时|随后|因此)\b", first, re.I))


def _estimated_window_chars(
    page_texts: dict[int, str], span: PageSpan, core_start: int, core_end: int
) -> int:
    transport_start = max(span.start, core_start - WINDOW_CONTEXT_PAGE_COUNT)
    transport_end = min(span.end, core_end + WINDOW_CONTEXT_PAGE_COUNT)
    return sum(len(page_texts.get(page, "")) + 32 for page in range(transport_start, transport_end + 1))


def _preferred_text_end(text: str, start: int, hard_end: int) -> int:
    """Choose a readable break without ever exceeding the requested range."""
    if hard_end >= len(text):
        return len(text)
    if hard_end <= start:
        return min(len(text), start + 1)
    minimum = start + max(1, int((hard_end - start) * SEMANTIC_SLICE_MIN_BREAK_RATIO))
    break_chars = "\n。！？!?；;，,、:：.!?"
    candidates = [text.rfind(character, start + 1, hard_end) + 1 for character in break_chars]
    candidates = [position for position in candidates if position >= minimum]
    if candidates:
        return max(candidates)
    whitespace = max(text.rfind(" ", start + 1, hard_end), text.rfind("\t", start + 1, hard_end))
    if whitespace >= minimum:
        return whitespace
    return hard_end


def _semantic_context_pages(span: PageSpan, core_start: int, core_end: int) -> list[int]:
    pages: list[int] = []
    if core_start > span.start:
        pages.append(core_start - 1)
    if core_end < span.end:
        pages.append(core_end + 1)
    return pages


def _build_lossless_windows(
    path: Path,
    spans: list[PageSpan],
    job_token: str,
    *,
    semantic_segments: bool,
) -> list[ExtractionWindow]:
    """Partition source ranges into lossless, auditable transport windows."""
    page_texts = _page_texts(path, spans)
    all_windows: list[ExtractionWindow] = []
    global_index = 1
    for segment_index, span in enumerate(spans, start=1):
        segment_id = (
            f"semantic_segment_{job_token}_{segment_index}"
            if semantic_segments
            else None
        )
        segment_windows: list[ExtractionWindow] = []
        cursor_page = span.start
        while cursor_page <= span.end:
            core_start = cursor_page
            feasible_ends = [
                end
                for end in range(core_start, span.end + 1)
                if _estimated_window_chars(page_texts, span, core_start, end)
                <= MAX_WINDOW_INPUT_CHARS
            ]
            slices: list[CoreTextSlice] = []
            split_due_to_budget = False
            if feasible_ends:
                core_end = max(feasible_ends)
                split_due_to_budget = core_end < span.end
                for page in range(core_start, core_end + 1):
                    page_text = page_texts.get(page, "")
                    if page_text:
                        slices.append(
                            CoreTextSlice(page=page, start_char=0, end_char=len(page_text))
                        )
                cursor_page = core_end + 1
            else:
                # A single page can exceed the whole request budget. In that
                # rare case, split only that page into contiguous source ranges;
                # no page text is discarded or silently overwritten.
                page_text = page_texts.get(core_start, "")
                marker_chars = len(f"--- PDF p{core_start} ---\n")
                available = max(1, MAX_WINDOW_INPUT_CHARS - marker_chars)
                end_offset = _preferred_text_end(page_text, 0, available)
                if not page_text:
                    cursor_page = core_start + 1
                else:
                    slices.append(
                        CoreTextSlice(
                            page=core_start,
                            start_char=0,
                            end_char=end_offset,
                        )
                    )
                    if end_offset < len(page_text):
                        # Store the continuation offset on a local attribute by
                        # advancing through a dedicated fragment loop below.
                        continuation_offset = end_offset
                        core_end = core_start
                        context_pages = []
                        transport = PageSpan(start=core_start, end=core_end, label=span.label)
                        core = PageSpan(start=core_start, end=core_end, label=span.label)
                        segment_windows.append(
                            ExtractionWindow(
                                id=f"prep_window_{job_token}_{global_index}",
                                page_span=transport,
                                core_span=core,
                                context_pages=context_pages,
                                boundary_pages=[core_start],
                                boundary_basis="transport_budget",
                                boundary_signals=[],
                                split_reason="transport_budget",
                                semantic_segment_id=segment_id,
                                core_text_slices=slices,
                            )
                        )
                        global_index += 1
                        while continuation_offset < len(page_text):
                            next_end = _preferred_text_end(
                                page_text,
                                continuation_offset,
                                continuation_offset + available,
                            )
                            final_fragment = next_end >= len(page_text)
                            segment_windows.append(
                                ExtractionWindow(
                                    id=f"prep_window_{job_token}_{global_index}",
                                    page_span=transport,
                                    core_span=core,
                                    context_pages=[],
                                    boundary_pages=[] if final_fragment else [core_start],
                                    boundary_basis=(
                                        "semantic"
                                        if semantic_segments and final_fragment
                                        else "scope_end"
                                        if final_fragment and core_start == span.end
                                        else "page_limit"
                                        if final_fragment
                                        else "transport_budget"
                                    ),
                                    boundary_signals=[],
                                    split_reason=(
                                        "none"
                                        if semantic_segments and final_fragment
                                        else "scope_end"
                                        if final_fragment and core_start == span.end
                                        else "page_limit"
                                        if final_fragment
                                        else "transport_budget"
                                    ),
                                    semantic_segment_id=segment_id,
                                    core_text_slices=[
                                        CoreTextSlice(
                                            page=core_start,
                                            start_char=continuation_offset,
                                            end_char=next_end,
                                        )
                                    ],
                                )
                            )
                            global_index += 1
                            continuation_offset = next_end
                        cursor_page = core_start + 1
                        continue
                    cursor_page = core_start + 1
                core_end = core_start

            context_pages = _semantic_context_pages(span, core_start, core_end)
            transport_start = min([core_start, *context_pages])
            transport_end = max([core_end, *context_pages])
            transport = PageSpan(start=transport_start, end=transport_end, label=span.label)
            core = PageSpan(start=core_start, end=core_end, label=span.label)
            boundary_pages: list[int] = []
            boundary_signals: list[str] = []
            has_remaining = cursor_page <= span.end
            if has_remaining:
                boundary_page = core_end
                next_page = cursor_page if cursor_page > core_end else core_end + 1
                boundary_pages = [boundary_page]
                if next_page <= span.end and next_page != boundary_page:
                    boundary_pages.append(next_page)
                current_text = page_texts.get(boundary_page, "")
                next_text = page_texts.get(next_page, "")
                if _looks_like_page_heading(next_text):
                    boundary_signals.append("possible_heading")
                if _looks_continued(current_text, next_text):
                    boundary_signals.append("possible_continuation")
                elif _ends_sentence(current_text):
                    boundary_signals.append("sentence_end")
                boundary_basis = "transport_budget" if split_due_to_budget else "page_limit"
                split_reason = "transport_budget" if split_due_to_budget else "page_limit"
            else:
                boundary_basis = "semantic" if semantic_segments else "scope_end"
                split_reason = "none" if semantic_segments else "scope_end"

            segment_windows.append(
                ExtractionWindow(
                    id=f"prep_window_{job_token}_{global_index}",
                    page_span=transport,
                    core_span=core,
                    context_pages=context_pages,
                    boundary_pages=boundary_pages,
                    boundary_basis=boundary_basis,
                    boundary_signals=boundary_signals,
                    split_reason=split_reason,
                    semantic_segment_id=segment_id,
                    core_text_slices=slices,
                )
            )
            global_index += 1

        if not segment_windows:
            label = "semantic segment" if semantic_segments else "source span"
            identifier = segment_id or f"p{span.start}-{span.end}"
            raise PrepSourceError(f"{label} {identifier} produced no windows")
        if not semantic_segments:
            all_windows.extend(segment_windows)
            continue
        count = len(segment_windows)
        for index, window in enumerate(segment_windows, start=1):
            data = window.model_copy(
                update={
                    "segment_window_index": index,
                    "segment_window_count": count,
                    "split_reason": (
                        "transport_budget" if count > 1 and index < count else window.split_reason
                    ),
                }
            )
            all_windows.append(data)
    return all_windows


def _build_windows(
    path: Path,
    spans: list[PageSpan],
    job_token: str,
    *,
    semantic_boundaries: bool = False,
    lossless_semantic: bool = False,
) -> list[ExtractionWindow]:
    """Build non-overlapping ownership cores with repeated boundary context."""
    if semantic_boundaries and lossless_semantic:
        return _build_lossless_windows(
            path, spans, job_token, semantic_segments=semantic_boundaries
        )
    page_texts = _page_texts(path, spans)
    windows: list[ExtractionWindow] = []
    window_index = 1
    for span in spans:
        core_start = span.start
        while core_start <= span.end:
            # A semantic unit owns as many adjacent pages as the transport
            # budget permits. Mechanical windows retain the conservative
            # three-page cap as their deterministic fallback.
            max_end = (
                span.end
                if semantic_boundaries
                else min(core_start + MAX_CORE_PAGE_COUNT - 1, span.end)
            )
            feasible_ends = [
                end
                for end in range(core_start, max_end + 1)
                if end == core_start
                or _estimated_window_chars(page_texts, span, core_start, end)
                <= MAX_WINDOW_INPUT_CHARS
            ]
            core_end = max(feasible_ends)

            transport_start = max(
                span.start, core_start - WINDOW_CONTEXT_PAGE_COUNT
            )
            transport_end = min(span.end, core_end + WINDOW_CONTEXT_PAGE_COUNT)
            transport = PageSpan(
                start=transport_start, end=transport_end, label=span.label
            )
            core = PageSpan(start=core_start, end=core_end, label=span.label)
            context_pages = [
                page for page in transport.pages() if page not in set(core.pages())
            ]

            if core_end == span.end:
                boundary_basis = "semantic" if semantic_boundaries else "scope_end"
                boundary_pages: list[int] = []
                boundary_signals: list[str] = []
            else:
                current_text = page_texts.get(core_end, "")
                next_text = page_texts.get(core_end + 1, "")
                boundary_pages = [core_end, core_end + 1]
                boundary_signals = []
                if _looks_like_page_heading(next_text):
                    boundary_signals.append("possible_heading")
                if _looks_continued(current_text, next_text):
                    boundary_signals.append("possible_continuation")
                elif _ends_sentence(current_text):
                    boundary_signals.append("sentence_end")
                boundary_basis = "char_budget" if core_end < max_end else "page_limit"

            windows.append(
                ExtractionWindow(
                    id=f"prep_window_{job_token}_{window_index}",
                    page_span=transport,
                    core_span=core,
                    context_pages=context_pages,
                    boundary_pages=boundary_pages,
                    boundary_basis=boundary_basis,
                    boundary_signals=boundary_signals,
                )
            )
            window_index += 1
            core_start = core_end + 1
    return windows


def _semantic_page_signatures(path: Path, spans: list[PageSpan]) -> list[dict[str, Any]]:
    """Build a bounded page map for the semantic planner, not the extractor."""
    page_texts = _page_texts(path, spans)
    signatures: list[dict[str, Any]] = []
    for span in spans:
        for page in span.pages():
            lines = [line.strip() for line in page_texts.get(page, "").splitlines() if line.strip()]
            snippet = " ".join(lines)
            if len(snippet) > MAX_SEMANTIC_PAGE_SIGNATURE_CHARS:
                snippet = snippet[:MAX_SEMANTIC_PAGE_SIGNATURE_CHARS - 1].rstrip() + "…"
            signatures.append({"page": page, "text": snippet})
    encoded = json.dumps(signatures, ensure_ascii=False)
    if len(encoded) > MAX_SEMANTIC_INPUT_CHARS:
        # Every page remains represented by its number; text is reduced in a
        # deterministic second pass before giving up to mechanical paging.
        compact = [
            {"page": item["page"], "text": str(item["text"])[:80]}
            for item in signatures
        ]
        signatures = compact
        if len(json.dumps(signatures, ensure_ascii=False)) > MAX_SEMANTIC_INPUT_CHARS:
            raise PrepSourceError("selected page scope is too large for semantic planning")
    return signatures


def _semantic_prompt_messages(job: PrepJob, signatures: list[dict[str, Any]]) -> list[dict[str, str]]:
    pages = [item["page"] for item in signatures]
    system = (
        "You are a page-boundary planner for a source-backed TRPG preparation task. "
        "Return exactly one JSON object with a segments array. Group adjacent selected "
        "PDF pages that belong to the same semantic unit such as a location, event, "
        "character, clue chain, or transition. Preserve every selected page exactly "
        "once; never include an unselected page; never overlap segments. A segment "
        "must be an object {start, end, label}. Labels are short GM-facing descriptions, "
        "not rules or invented content. This is only a planning hint: do not extract facts."
    )
    user = (
        "[TASK:prep:segment]\n"
        f"SOURCE_FILE_JSON={json.dumps(job.scope.source_file, ensure_ascii=False)}\n"
        f"SELECTED_PAGES_JSON={json.dumps(pages)}\n"
        f"PAGE_SIGNATURES_JSON={json.dumps(signatures, ensure_ascii=False)}\n"
        "Return {\"segments\":[{\"start\":1,\"end\":2,\"label\":\"...\"}]} and no markdown."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _normalize_semantic_segments(parsed: Any, scope_spans: list[PageSpan]) -> list[PageSpan]:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("segments"), list):
        raise PrepSourceError("semantic planner returned no segments array")
    raw_segments = parsed["segments"]
    if not raw_segments or len(raw_segments) > 120:
        raise PrepSourceError("semantic planner returned an invalid segment count")
    normalized: list[PageSpan] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise PrepSourceError("semantic planner returned an invalid segment")
        start, end = raw.get("start"), raw.get("end")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
            raise PrepSourceError("semantic segment bounds must be integers")
        label = raw.get("label")
        if label is not None and not isinstance(label, str):
            raise PrepSourceError("semantic segment label must be text")
        owner = next(
            (span for span in scope_spans if span.start <= start <= end <= span.end),
            None,
        )
        if owner is None or end < start:
            raise PrepSourceError("semantic segment falls outside the selected scope")
        normalized.append(PageSpan(start=start, end=end, label=(label or None)))
    normalized.sort(key=lambda span: (span.start, span.end))
    # The planner is a partitioner, not a filter. Require exact coverage of
    # every selected span so a bad response cannot silently drop source pages.
    cursor = 0
    for scope in sorted(scope_spans, key=lambda span: (span.start, span.end)):
        segment_group = [item for item in normalized if item.start >= scope.start and item.end <= scope.end]
        if not segment_group or segment_group[0].start != scope.start:
            raise PrepSourceError("semantic planner omitted the start of a selected span")
        expected = scope.start
        for segment in segment_group:
            if segment.start != expected:
                raise PrepSourceError("semantic planner left a page gap or overlap")
            expected = segment.end + 1
        if expected != scope.end + 1:
            raise PrepSourceError("semantic planner omitted the end of a selected span")
        cursor += len(segment_group)
    if cursor != len(normalized):
        raise PrepSourceError("semantic planner returned pages outside the selected spans")
    return normalized


def _prepare_semantic_windows(job: PrepJob, path: Path, client) -> PrepJob:
    """Plan semantic page units once, falling back to persisted mechanical windows."""
    if (
        job.segmentation_strategy not in {"semantic-v1", "semantic-v2"}
        or job.segmentation_status != "pending"
    ):
        return job
    try:
        signatures = _semantic_page_signatures(path, job.scope.page_spans)
        raw = client.chat(
            _semantic_prompt_messages(job, signatures),
            temperature=0.0,
            max_tokens=3000,
        )
        segments = _normalize_semantic_segments(
            parse_json(raw), job.scope.page_spans
        )
        windows = _build_windows(
            path,
            segments,
            job.id.removeprefix("prep_job_"),
            semantic_boundaries=True,
            lossless_semantic=job.segmentation_strategy == "semantic-v2",
        )
        return _save_job(
            job,
            segmentation_status="succeeded",
            semantic_segments=[item.model_dump(mode="json") for item in segments],
            segmentation_error=None,
            windows=[item.model_dump(mode="json") for item in windows],
        )
    except Exception as error:  # noqa: BLE001
        # Semantic planning is an enhancement to extraction, never a reason to
        # lose a whole job. New v2 jobs still use lossless mechanical transport
        # windows, so a planner failure cannot reintroduce core-text clipping.
        fallback_windows = None
        if job.segmentation_strategy == "semantic-v2":
            fallback_windows = _build_lossless_windows(
                path,
                job.scope.page_spans,
                job.id.removeprefix("prep_job_"),
                semantic_segments=False,
            )
        return _save_job(
            job,
            segmentation_status="fallback",
            segmentation_error=str(error)[:2000],
            **(
                {
                    "windows": [
                        item.model_dump(mode="json") for item in fallback_windows
                    ]
                }
                if fallback_windows is not None
                else {}
            ),
        )


def _clip_page_text(text: str, limit: int, mode: str) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    if mode == "tail":
        return "[page beginning omitted]\n" + text[-max(1, limit - 26) :].lstrip(), True
    if mode == "head":
        return text[: max(1, limit - 25)].rstrip() + "\n[page remainder omitted]", True
    head_size = max(1, (limit - 43) // 2)
    tail_size = max(1, limit - 43 - head_size)
    return (
        text[:head_size].rstrip()
        + "\n[page middle omitted]\n"
        + text[-tail_size:].lstrip(),
        True,
    )


def _window_excerpt(path: Path, window: ExtractionWindow) -> tuple[str, list[int]]:
    if window.core_text_slices:
        return _lossless_semantic_excerpt(path, window)

    page_texts = _page_texts(path, [window.page_span])
    core = window.core_span or window.page_span
    pages = window.page_span.pages()
    marker_chars = sum(len(f"--- PDF p{page} ---\n\n\n") for page in pages)
    available = max(1000, MAX_WINDOW_INPUT_CHARS - marker_chars)
    context_pages = set(window.context_pages)
    context_budget = min(1800 * len(context_pages), available // 3)
    core_budget = max(1, available - context_budget)
    core_count = max(1, core.page_count)
    context_count = max(1, len(context_pages))
    core_limit = max(900, core_budget // core_count)
    context_limit = max(500, context_budget // context_count) if context_pages else 0
    parts: list[str] = []
    truncated_pages: list[int] = []
    for page_number in pages:
        page_text = page_texts.get(page_number, "")
        if page_number < core.start:
            page_text, truncated = _clip_page_text(page_text, context_limit, "tail")
        elif page_number > core.end:
            page_text, truncated = _clip_page_text(page_text, context_limit, "head")
        else:
            page_text, truncated = _clip_page_text(page_text, core_limit, "core")
        if truncated:
            truncated_pages.append(page_number)
        parts.append(f"--- PDF p{page_number} ---\n{page_text or '[no extractable text]'}")
    excerpt = "\n\n".join(parts)[:MAX_WINDOW_INPUT_CHARS]
    if not any(
        line.strip() and not line.startswith(("--- PDF p", "[no extractable"))
        for line in excerpt.splitlines()
    ):
        raise PrepSourceError(
            f"pages {window.page_span.start}-{window.page_span.end} contain no extractable text"
        )
    return excerpt, truncated_pages


def _lossless_semantic_excerpt(
    path: Path, window: ExtractionWindow
) -> tuple[str, list[int]]:
    """Pack a semantic window while never clipping its owned source text."""
    page_texts = _page_texts(path, [window.page_span])
    core = window.core_span or window.page_span
    core_pages = set(core.pages())
    slice_map: dict[int, list[CoreTextSlice]] = {}
    for text_slice in window.core_text_slices:
        slice_map.setdefault(text_slice.page, []).append(text_slice)

    def core_text(page: int) -> str:
        text = page_texts.get(page, "")
        slices = sorted(slice_map.get(page, []), key=lambda item: item.start_char)
        if not slices:
            return text
        return "".join(text[item.start_char : item.end_char] for item in slices)

    def block(page: int, text: str) -> str:
        return f"--- PDF p{page} ---\n{text or '[no extractable text]'}"

    core_blocks = {
        page: block(page, core_text(page))
        for page in core.pages()
    }
    core_length = sum(len(value) for value in core_blocks.values())
    core_length += max(0, len(core_blocks) - 1) * 2
    if core_length > MAX_WINDOW_INPUT_CHARS:
        raise PrepSourceError(
            f"semantic core p{core.start}-{core.end} exceeds the transport budget"
        )

    context_pages = [page for page in window.page_span.pages() if page not in core_pages]
    context_blocks: dict[int, str] = {}
    truncated_pages: list[int] = []
    used = core_length
    for page in context_pages:
        full_block = block(page, page_texts.get(page, ""))
        separator = 2 if core_blocks or context_blocks else 0
        if used + separator + len(full_block) <= MAX_WINDOW_INPUT_CHARS:
            context_blocks[page] = full_block
            used += separator + len(full_block)
            continue
        available = MAX_WINDOW_INPUT_CHARS - used - separator
        if available <= len(f"--- PDF p{page} ---\n"):
            truncated_pages.append(page)
            continue
        text_limit = available - len(f"--- PDF p{page} ---\n")
        clipped, _ = _clip_page_text(page_texts.get(page, ""), text_limit, "head")
        context_blocks[page] = block(page, clipped)
        truncated_pages.append(page)
        used += separator + len(context_blocks[page])

    parts: list[str] = []
    for page in window.page_span.pages():
        if page in core_blocks:
            parts.append(core_blocks[page])
        elif page in context_blocks:
            parts.append(context_blocks[page])
    excerpt = "\n\n".join(parts)
    if len(excerpt) > MAX_WINDOW_INPUT_CHARS:
        raise PrepSourceError("semantic transport window exceeded its character budget")
    if not any(
        line.strip() and not line.startswith(("--- PDF p", "[no extractable"))
        for line in excerpt.splitlines()
    ):
        raise PrepSourceError(
            f"pages {window.page_span.start}-{window.page_span.end} contain no extractable text"
        )
    return excerpt, truncated_pages


def _prep_objective(profile_id: str) -> str:
    return PROFILE_OBJECTIVES[profile_id]


def _reference_pages(value: Any) -> list[int] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return [value]
    if not isinstance(value, str):
        return None
    text = value.strip()
    patterns = (
        r"(?i)^p(?:age)?\s*[:#-]?\s*(\d+)(?:\s*[-–—]\s*p?(\d+))?$",
        r"^第\s*(\d+)\s*页(?:\s*[-–—至到]\s*第?\s*(\d+)\s*页?)?$",
        r"^(\d+)(?:\s*[-–—]\s*(\d+))?$",
    )
    match = next((re.fullmatch(pattern, text) for pattern in patterns if re.fullmatch(pattern, text)), None)
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start or end - start >= MAX_REFERENCE_PAGE_COUNT:
        return None
    return list(range(start, end + 1))


def _normalize_confidence(value: Any) -> Any:
    if value is None or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    if not text:
        return None
    if text in {"high", "medium", "low", "高", "中", "低", "较高", "较低"}:
        return None
    if re.fullmatch(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", text):
        return float(text)
    percent = re.fullmatch(r"(\d{1,3})(?:\.\d+)?%", text)
    if percent and 0 <= float(text[:-1]) <= 100:
        return float(text[:-1]) / 100
    return value


def _normalize_prep_response(
    parsed: Any, task, *, core_pages: set[int] | None = None
) -> Any:
    """Repair only deterministic adapter differences before strict validation."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("candidates"), list):
        return parsed
    normalized = dict(parsed)
    normalized_candidates: list[Any] = []
    for raw_candidate in parsed["candidates"]:
        if not isinstance(raw_candidate, dict):
            normalized_candidates.append(raw_candidate)
            continue
        candidate = dict(raw_candidate)
        # The reducer input includes this internal observation id so the model
        # can distinguish source candidates. It is not part of the public
        # candidate schema and some models echo it back verbatim.
        candidate.pop("candidate_id", None)
        kind = candidate.get("kind")
        if isinstance(kind, str):
            cleaned_kind = kind.strip()
            candidate["kind"] = KIND_ALIASES.get(cleaned_kind, cleaned_kind.lower())

        raw_refs = candidate.get("source_refs")
        if isinstance(raw_refs, (str, int, dict)) and not isinstance(raw_refs, bool):
            raw_refs = [raw_refs]
        if isinstance(raw_refs, list):
            refs: list[Any] = []
            seen: set[tuple[str, int, str | None]] = set()
            for raw_ref in raw_refs:
                if isinstance(raw_ref, dict):
                    ref = dict(raw_ref)
                    # Some providers use source_file for the same field name
                    # used by the surrounding task payload. Accept only this
                    # exact alias; SourceRef remains strict for everything
                    # else.
                    if "file" not in ref and "source_file" in ref:
                        ref["file"] = ref.pop("source_file")
                    pages = _reference_pages(ref.get("page"))
                    file_name = ref.get("file") or task.source_file
                    locator = ref.get("locator")
                else:
                    ref = {}
                    pages = _reference_pages(raw_ref)
                    file_name = task.source_file
                    locator = None
                if pages is None:
                    refs.append(raw_ref)
                    continue
                for page in pages:
                    item = dict(ref)
                    item["file"] = file_name
                    item["page"] = page
                    key = (str(file_name), page, locator if isinstance(locator, str) else None)
                    if key not in seen:
                        refs.append(item)
                        seen.add(key)
            candidate["source_refs"] = refs

        candidate["confidence"] = _normalize_confidence(candidate.get("confidence"))
        for field in ("possible_links", "open_questions"):
            value = candidate.get(field)
            if isinstance(value, str):
                candidate[field] = [value.strip()] if value.strip() else []

        refs = candidate.get("source_refs")
        if core_pages and isinstance(refs, list) and refs:
            allowed_pages = set(task.source_pages)
            valid_owned_refs = all(
                isinstance(ref, dict)
                and isinstance(ref.get("page"), int)
                and not isinstance(ref.get("page"), bool)
                and ref.get("page") in allowed_pages
                and ref.get("file") == task.source_file
                for ref in refs
            )
            if valid_owned_refs and min(ref["page"] for ref in refs) not in core_pages:
                # The adjacent window owns this fact. The raw response remains in
                # the run summary, while the durable queue stays duplicate-free.
                continue
        normalized_candidates.append(candidate)
    normalized["candidates"] = normalized_candidates
    if normalized.get("open_questions") == []:
        # Consolidation questions belong to individual candidates. An empty
        # provider-level list carries no information and is harmless noise.
        normalized.pop("open_questions")
    return normalized


def _job_from_store(job_id: str) -> PrepJob:
    raw = storage.load_prep_job(job_id)
    if raw is None:
        raise PrepJobNotFoundError(f"unknown prep job: {job_id}")
    try:
        return PrepJob.model_validate(raw)
    except ValidationError as error:
        raise PrepError(f"stored prep job {job_id} is invalid: {error}") from error


def _save_job(job: PrepJob, **updates) -> PrepJob:
    data = job.model_dump(mode="json")
    data.update(updates)
    data["updated_at"] = storage.now()
    updated = PrepJob.model_validate(data)
    storage.save_prep_job(updated.model_dump(mode="json"))
    return updated


def _replace_window(job: PrepJob, window_id: str, **updates) -> PrepJob:
    windows: list[dict] = []
    found = False
    for window in job.windows:
        data = window.model_dump(mode="json")
        if window.id == window_id:
            data.update(updates)
            found = True
        windows.append(data)
    if not found:
        raise PrepError(f"unknown extraction window: {window_id}")
    return _save_job(job, windows=windows)


def _latest_consolidation_error(task_ids: set[str]) -> str | None:
    """Recover a durable reducer error without exposing model internals."""
    failures: list[tuple[str, int, str]] = []
    for task_id in task_ids:
        try:
            runs = shadow.list_shadow_runs(task_id)
        except (shadow.ShadowTaskNotFoundError, shadow.ShadowResultValidationError):
            continue
        for run in runs:
            if run.status != "failed":
                continue
            error = run.parse_error or run.transport_error
            if error:
                failures.append((run.started_at, run.attempt, error[:2000]))
    if not failures:
        return None
    failures.sort(key=lambda item: (item[0], item[1]))
    return failures[-1][2]


def _repair_stale_consolidation_diagnostics() -> None:
    """Repair only the generic marker written by the previous recovery code."""
    with _active_jobs_lock:
        active = set(_active_jobs)
    for raw in storage.list_prep_jobs():
        job = PrepJob.model_validate(raw)
        if (
            job.id in active
            or job.status not in {"partial", "failed"}
            or job.segmentation_strategy != "semantic-v2"
            or job.segmentation_status != "succeeded"
        ):
            continue
        changed = False
        updates_by_id: dict[str, dict] = {}
        for _segment_id, segment_windows in _semantic_window_groups(job):
            if not any(
                window.consolidation_error == INTERRUPTED_CONSOLIDATION_ERROR
                for window in segment_windows
            ):
                continue
            task_ids = {
                window.consolidation_task_id
                for window in segment_windows
                if window.consolidation_task_id
            }
            recovered_error = _latest_consolidation_error(task_ids)
            for window in segment_windows:
                data = window.model_dump(mode="json")
                if recovered_error:
                    data["consolidation_error"] = recovered_error
                elif not task_ids:
                    data.update(
                        consolidation_status=None,
                        consolidation_candidate_count=0,
                        consolidation_error=None,
                    )
                else:
                    continue
                updates_by_id[window.id] = data
                changed = True
        if changed:
            merged_windows = [
                updates_by_id.get(item.id, item.model_dump(mode="json"))
                for item in job.windows
            ]
            _save_job(job, windows=merged_windows)


def _recover_interrupted_jobs() -> None:
    with _active_jobs_lock:
        active = set(_active_jobs)
    for raw in storage.list_prep_jobs():
        job = PrepJob.model_validate(raw)
        if job.status != "running" or job.id in active:
            continue
        interrupted_segments: dict[str, str | None] = {}
        recovery_errors: dict[str, str] = {}
        semantic_groups: list[tuple[str, list[ExtractionWindow]]] = []
        completed_segment_tasks: dict[str, str] = {}
        if (
            job.segmentation_strategy == "semantic-v2"
            and job.segmentation_status == "succeeded"
        ):
            task_rows = storage.list_shadow_tasks()
            tasks_by_id = {
                str(task.get("id")): task
                for task in task_rows
                if isinstance(task, dict) and task.get("id")
            }
            semantic_groups = _semantic_window_groups(job)
            for segment_id, segment_windows in semantic_groups:
                if not all(window.status == "succeeded" for window in segment_windows):
                    continue
                linked_task_ids = {
                    window.consolidation_task_id
                    for window in segment_windows
                    if window.consolidation_task_id
                }
                if (
                    len(linked_task_ids) == 1
                    and all(
                        window.consolidation_status == "succeeded"
                        for window in segment_windows
                    )
                    and next(iter(linked_task_ids)) in tasks_by_id
                    and tasks_by_id[next(iter(linked_task_ids))].get("status")
                    == "completed"
                ):
                    completed_segment_tasks[segment_id] = next(iter(linked_task_ids))
                    continue

                has_consolidation_state = any(
                    window.consolidation_status is not None
                    for window in segment_windows
                )
                if not linked_task_ids and not has_consolidation_state:
                    # Extraction completed, but this segment had not reached
                    # the reducer seam when the process stopped. Leave it
                    # queued for the next run instead of inventing a failure.
                    continue

                existing_error = next(
                    (
                        window.consolidation_error
                        for window in segment_windows
                        if window.consolidation_error
                        and window.consolidation_error != INTERRUPTED_CONSOLIDATION_ERROR
                    ),
                    None,
                )

                for task_id in linked_task_ids:
                    try:
                        shadow.set_shadow_task_visibility(task_id, "internal")
                    except shadow.ShadowTaskNotFoundError:
                        pass
                active_tasks = [
                    task
                    for task in task_rows
                    if task.get("task_kind") == "semantic_consolidation"
                    and task.get("semantic_segment_id") == segment_id
                    and str(task.get("idempotency_key", "")).startswith(
                        job.id + ":consolidate:"
                    )
                    and task.get("status") in {"queued", "running"}
                ]
                for task in active_tasks:
                    try:
                        shadow.cancel_shadow_task(task["id"])
                    except (
                        shadow.ShadowTaskConflictError,
                        shadow.ShadowTaskNotFoundError,
                    ):
                        # A task that finished or was removed while recovery ran
                        # can be retried from the durable window observations.
                        pass
                recovery_errors[segment_id] = (
                    INTERRUPTED_CONSOLIDATION_ERROR
                    if active_tasks
                    else existing_error
                    or _latest_consolidation_error(linked_task_ids)
                    or INTERRUPTED_CONSOLIDATION_ERROR
                )
                interrupted_segments[segment_id] = (
                    active_tasks[0]["id"]
                    if active_tasks
                    else next(iter(linked_task_ids), None)
                )

            if (
                semantic_groups
                and len(completed_segment_tasks) == len(semantic_groups)
            ):
                exposed_task_ids: list[str] = []
                for segment_id, task_id in completed_segment_tasks.items():
                    try:
                        shadow.set_shadow_task_visibility(task_id, "review")
                        exposed_task_ids.append(task_id)
                    except shadow.ShadowTaskNotFoundError:
                        for exposed_task_id in exposed_task_ids:
                            try:
                                shadow.set_shadow_task_visibility(
                                    exposed_task_id, "internal"
                                )
                            except shadow.ShadowTaskNotFoundError:
                                pass
                        interrupted_segments[segment_id] = task_id
                        break

        windows = []
        for window in job.windows:
            data = window.model_dump(mode="json")
            if window.status == "running":
                data.update(
                    status="failed",
                    error="generation was interrupted before completion",
                    error_kind="worker",
                )
            if window.semantic_segment_id in interrupted_segments:
                consolidation_task_id = interrupted_segments[window.semantic_segment_id]
                data["consolidation_task_id"] = consolidation_task_id
                data.update(
                    consolidation_status="failed",
                    consolidation_candidate_count=0,
                    consolidation_error=recovery_errors[window.semantic_segment_id],
                )
            windows.append(data)
        if (
            semantic_groups
            and not interrupted_segments
            and len(completed_segment_tasks) == len(semantic_groups)
        ):
            candidate_count = sum(
                segment_windows[0].consolidation_candidate_count
                for _segment_id, segment_windows in semantic_groups
            )
            _save_job(
                job,
                status="completed",
                windows=windows,
                candidate_count=candidate_count,
            )
            continue
        succeeded = any(item["status"] == "succeeded" for item in windows)
        _save_job(job, status="partial" if succeeded else "failed", windows=windows)


def create_prep_job(
    spec: PrepJobCreate,
    *,
    workspace_id: str | None = None,
    analysis_version: int = 1,
    previous_job_id: str | None = None,
) -> PrepJob:
    try:
        spans = parse_page_spans(spec.page_range)
    except ValueError as error:
        raise PrepSourceError(str(error)) from error

    path = _source_path(spec.source_file)
    page_count = _page_count(path)
    if any(span.end > page_count for span in spans):
        raise PrepSourceError(f"source PDF has {page_count} pages")

    profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
    if spec.profile_id not in profiles:
        raise PrepSourceError(f"unknown target profile: {spec.profile_id}")

    config = storage.get_config()
    model_id = config["model"]
    if not config["fake"] and not config["api_key"]:
        raise PrepSourceError("an API key or FakeLLM mode is required")

    timestamp = storage.now()
    job_token = uuid.uuid4().hex
    scope = PrepScope(
        source_file=spec.source_file,
        source_version=_source_version(path),
        page_spans=spans,
        profile_id=spec.profile_id,
        objective=_prep_objective(spec.profile_id),
        notes=None,
    )
    windows = _build_windows(path, scope.page_spans, job_token)
    job_id = f"prep_job_{job_token}"
    job = PrepJob(
        id=job_id,
        scope=scope,
        model_id=model_id,
        fake_model=bool(config["fake"]),
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        window_strategy="core-context-v3",
        segmentation_strategy="semantic-v2",
        segmentation_status="pending",
        semantic_segments=[],
        workspace_id=workspace_id or job_id,
        analysis_version=analysis_version,
        previous_job_id=previous_job_id,
        windows=windows,
        created_at=timestamp,
        updated_at=timestamp,
    )
    storage.create_prep_job(job.model_dump(mode="json"))
    return job


def rebuild_prep_job(job_id: str) -> PrepJob:
    """Create a new analysis version inside the same bookshelf project."""
    job = _job_from_store(job_id)
    with _active_jobs_lock:
        if job.id in _active_jobs or job.status == "running":
            raise PrepJobConflictError("running prep jobs cannot be rebuilt")
    page_range = ", ".join(
        str(span.start) if span.start == span.end else f"{span.start}-{span.end}"
        for span in job.scope.page_spans
    )
    workspace_id = job.workspace_id or job.id
    next_version = max(
        (
            item.analysis_version
            for item in list_prep_jobs()
            if (item.workspace_id or item.id) == workspace_id
        ),
        default=job.analysis_version,
    ) + 1
    return create_prep_job(
        PrepJobCreate(
            source_file=job.scope.source_file,
            page_range=page_range,
            profile_id=job.scope.profile_id,
        ),
        workspace_id=workspace_id,
        analysis_version=next_version,
        previous_job_id=job.id,
    )


def job_needs_rebuild(job: PrepJob) -> bool:
    """Return whether a persisted task's windows differ from today's splitter."""
    if job.status == "running":
        return False

    def signature(window: ExtractionWindow) -> tuple:
        core = window.core_span or window.page_span
        return (
            window.page_span.start,
            window.page_span.end,
            core.start,
            core.end,
            tuple(window.context_pages),
            tuple(window.boundary_pages),
            window.boundary_basis,
            tuple(window.boundary_signals),
        )

    persisted_signature = tuple(signature(item) for item in job.windows)
    cache_key = (
        job.id,
        job.scope.source_version,
        job.window_strategy,
        persisted_signature,
    )
    with _rebuild_check_cache_lock:
        cached = _rebuild_check_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        path = _source_path(job.scope.source_file)
    except PrepError:
        # A missing or unreadable source cannot be rebuilt from the UI yet.
        return False

    if job.segmentation_strategy in {"semantic-v1", "semantic-v2"}:
        # Semantic boundaries depend on the model and page signatures. They
        # are durable for this job; a new explicit rebuild creates a fresh
        # planning request instead of silently re-slicing an existing queue.
        result = False
    elif job.window_strategy != "core-context-v3":
        result = True
    else:
        try:
            expected = _build_windows(path, job.scope.page_spans, "rebuild-check")
        except PrepError:
            # A source can disappear between the path check and extraction.
            return False
        result = persisted_signature != tuple(signature(item) for item in expected)

    with _rebuild_check_cache_lock:
        if len(_rebuild_check_cache) >= _REBUILD_CHECK_CACHE_LIMIT:
            _rebuild_check_cache.pop(next(iter(_rebuild_check_cache)))
        _rebuild_check_cache[cache_key] = result
    return result


def _job_with_promotion_count(job: PrepJob) -> PrepJob:
    candidate_ids: list[str] = []
    for candidate in list_prep_job_candidates(job.id, review_state=None):
        candidate_ids.append(candidate["id"])
    count = len(storage.list_candidate_promotions(candidate_ids))
    if count == job.promoted_count:
        return job
    return job.model_copy(update={"promoted_count": count})


def list_prep_jobs() -> list[PrepJob]:
    _recover_interrupted_jobs()
    _repair_stale_consolidation_diagnostics()
    return [
        _job_with_promotion_count(PrepJob.model_validate(item))
        for item in storage.list_prep_jobs()
    ]


def get_prep_job(job_id: str) -> PrepJob:
    _recover_interrupted_jobs()
    _repair_stale_consolidation_diagnostics()
    return _job_with_promotion_count(_job_from_store(job_id))


def find_prep_job_by_workspace(workspace_id: str) -> PrepJob | None:
    """Return the newest analysis version attached to a bookshelf project."""
    matches = [
        job
        for job in list_prep_jobs()
        if (job.workspace_id or job.id) == workspace_id
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (item.analysis_version, item.updated_at, item.id))


def list_prep_jobs_by_workspace(workspace_id: str) -> list[PrepJob]:
    """Return every persisted analysis version owned by one bookshelf project."""
    return sorted(
        (
            job
            for job in list_prep_jobs()
            if (job.workspace_id or job.id) == workspace_id
        ),
        key=lambda item: (item.analysis_version, item.created_at, item.id),
    )


def start_prep_job(
    job_id: str,
    *,
    model_id: str | None = None,
    fake_model: bool | None = None,
) -> PrepJob:
    """Queue a retry, optionally refreshing the model snapshot.

    A failed or cancelled task may be retried after the GM changes the model
    configuration.  Succeeded windows remain cached; only windows that need
    work use the refreshed snapshot.
    """
    job = _job_from_store(job_id)
    if job.status == "running":
        raise PrepJobConflictError("prep job is already running")
    if job.status == "completed":
        raise PrepJobConflictError("completed prep jobs do not need another run")

    updates: dict[str, Any] = {}
    if model_id is not None:
        model_id = model_id.strip()
        if not model_id:
            raise PrepError("model id cannot be empty")
        updates["model_id"] = model_id
    if fake_model is not None:
        updates["fake_model"] = bool(fake_model)

    retrying_segments = {
        segment_id
        for segment_id, segment_windows in _semantic_window_groups(job)
        if job.segmentation_strategy == "semantic-v2"
        and all(window.status == "succeeded" for window in segment_windows)
        and any(
            window.consolidation_status != "succeeded"
            for window in segment_windows
        )
    }
    windows = []
    for window in job.windows:
        data = window.model_dump(mode="json")
        if window.status in {"failed", "cancelled"}:
            data.update(status="queued", error=None, error_kind=None)
        if window.semantic_segment_id in retrying_segments:
            data.update(
                consolidation_status=None,
                consolidation_candidate_count=0,
                consolidation_error=None,
            )
        windows.append(data)
    job = _save_job(job, status="running", windows=windows, **updates)
    with _active_jobs_lock:
        _active_jobs.add(job.id)
    return job


def cancel_prep_job(job_id: str) -> PrepJob:
    job = _job_from_store(job_id)
    if job.status == "completed":
        raise PrepJobConflictError("completed prep jobs cannot be cancelled")
    if job.status == "cancelled":
        return job
    windows = []
    for window in job.windows:
        data = window.model_dump(mode="json")
        if window.status == "queued":
            data.update(status="cancelled", error="cancelled before generation")
        windows.append(data)
    return _save_job(job, status="cancelled", windows=windows)


def delete_prep_job(job_id: str) -> None:
    job = _job_from_store(job_id)
    with _active_jobs_lock:
        is_active = job.id in _active_jobs
    if job.status == "running" or is_active:
        raise PrepJobConflictError("running prep jobs must be cancelled before deletion")
    if not storage.delete_prep_job(job.id):
        raise PrepJobNotFoundError(f"unknown prep job: {job.id}")


def _job_for_shadow_task(task_id: str) -> PrepJob:
    for raw in storage.list_prep_jobs():
        job = PrepJob.model_validate(raw)
        if any(
            task_id in {window.shadow_task_id, window.consolidation_task_id}
            for window in job.windows
        ):
            return job
    raise PrepPromotionConflictError(
        "candidate is not attached to a preparation job workspace"
    )


def _workspace_name(job: PrepJob) -> str:
    source_name = Path(job.scope.source_file).stem
    source_name = re.sub(r"^[0-9a-f]{12}-", "", source_name)
    ranges = ", ".join(
        f"p{span.start}" if span.start == span.end else f"p{span.start}-{span.end}"
        for span in job.scope.page_spans
    )
    return f"{source_name} · {ranges}"


_DISPLAY_MATERIAL_LABEL = re.compile(
    r"(?:^|\b)(?:材料|展示材料|玩家材料|handout|document)\s*[：:#]\s*(.{1,120})",
    re.IGNORECASE,
)
def _page_visual_region(page: fitz.Page, label_rect: fitz.Rect) -> str | None:
    """Return nearest sizable embedded-image bounds as metadata, never a crop."""
    candidates: list[fitz.Rect] = []
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            if rect.width * rect.height >= 12_000:
                candidates.append(rect)
    if not candidates:
        return None
    nearest = min(
        candidates,
        key=lambda rect: abs(rect.x0 - label_rect.x0) + abs(rect.y0 - label_rect.y0),
    )
    return f"visual-candidate:{nearest.x0:.0f},{nearest.y0:.0f},{nearest.x1:.0f},{nearest.y1:.0f}"


def _ensure_labeled_display_materials(bundle: ExampleBundle, job: PrepJob) -> None:
    """Add only strong text-labelled player materials; unlabeled images stay metadata."""
    if any("auto-labelled" in fact.tags for fact in bundle.facts):
        return
    existing_titles = {item.title.casefold() for item in bundle.display_materials}
    try:
        document = fitz.open(_source_path(job.scope.source_file))
    except Exception:
        return
    try:
        for span in job.scope.page_spans:
            for page_number in span.pages():
                page = document[page_number - 1]
                for block in page.get_text("blocks"):
                    text = " ".join(str(block[4]).split())
                    if not text:
                        continue
                    match = _DISPLAY_MATERIAL_LABEL.search(text)
                    # Only an explicit source label can create a formal
                    # display material. Generic text blocks and nearby image
                    # captions are visual candidates, not handout facts.
                    title = (match.group(1) if match else "").strip()
                    if not title or title.casefold() in existing_titles:
                        continue
                    digest = hashlib.sha256(
                        f"{job.scope.source_file}:{page_number}:{title}".encode("utf-8")
                    ).hexdigest()[:16]
                    fact_id = f"fact_handout_{digest}"
                    if any(fact.id == fact_id for fact in bundle.facts):
                        continue
                    region = _page_visual_region(
                        page, fitz.Rect(float(block[0]), float(block[1]), float(block[2]), float(block[3]))
                    )
                    source_ref = SourceRef(
                        file=job.scope.source_file,
                        page=page_number,
                        excerpt=text[:1200],
                        region=region,
                        source_version=job.scope.source_version,
                    )
                    bundle.facts.append(SourceFact(
                        id=fact_id,
                        source_refs=[source_ref],
                        evidence_status="source_fact",
                        text=title,
                        kind="handout",
                        # "handout" is the fact kind, not a valid visibility
                        # value. Display-material facts are source-backed and
                        # therefore explicit like other extracted facts.
                        visibility="explicit",
                        tags=["display-material", "auto-labelled"],
                        notes="从 PDF 的展示材料标签自动识别。",
                    ))
                    bundle.display_materials.append(DisplayMaterial(
                        id=f"material_m_{digest}",
                        title=title,
                        source_fact_ids=[fact_id],
                        source_refs=[source_ref],
                        gm_notes="原文展示材料；请按来源页自行准备展示。",
                        links=[],
                    ))
                    existing_titles.add(title.casefold())
    finally:
        document.close()


def display_material_source_pages(job: PrepJob) -> list[int]:
    """Return source-page hints without creating facts or display materials."""
    try:
        document = fitz.open(_source_path(job.scope.source_file))
    except Exception:
        return []
    pages: set[int] = set()
    try:
        for span in job.scope.page_spans:
            for page_number in span.pages():
                page = document[page_number - 1]
                texts = [" ".join(str(block[4]).split()) for block in page.get_text("blocks")]
                if any(_DISPLAY_MATERIAL_LABEL.search(text) for text in texts):
                    pages.add(page_number)
                    continue
                page_area = max(float(page.rect.width * page.rect.height), 1.0)
                for image in page.get_images(full=True):
                    if any(0.03 <= (rect.width * rect.height) / page_area <= 0.85
                           for rect in page.get_image_rects(image[0])):
                        pages.add(page_number)
                        break
    finally:
        document.close()
    return sorted(pages)


def _load_or_create_workspace(job: PrepJob) -> ExampleBundle:
    workspace_id = job.workspace_id or job.id
    saved = storage.load_domain_bundle(workspace_id)
    profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
    if saved:
        bundle = ExampleBundle.model_validate(saved[0])
        validate_bundle(bundle, profiles)
        return bundle
    return ExampleBundle(
        id=workspace_id,
        name=_workspace_name(job),
        description="由备团任务中已复核并显式提升的事实组成。",
        profile_ids=[job.scope.profile_id],
        facts=[],
        cards=[],
        plans=[],
    )


def promote_shadow_candidate(
    candidate_id: str, *, evidence_status: str
) -> tuple[SourceFact, str, bool]:
    if evidence_status not in {"source_fact", "inference", "gm_authored"}:
        raise PrepPromotionConflictError("promotion evidence status is invalid")

    existing = storage.load_candidate_promotion(candidate_id)
    if existing:
        if existing["evidence_status"] != evidence_status:
            raise PrepPromotionConflictError(
                "candidate was already promoted with a different evidence status"
            )
        saved = storage.load_domain_bundle(existing["workspace_id"])
        if not saved:
            raise PrepPromotionConflictError("promoted candidate workspace is missing")
        bundle = ExampleBundle.model_validate(saved[0])
        fact = next(
            (item for item in bundle.facts if item.id == existing["fact_id"]), None
        )
        if fact is None:
            raise PrepPromotionConflictError("promoted candidate fact is missing")
        return fact, existing["workspace_id"], False

    raw_candidate = storage.load_shadow_candidate(candidate_id)
    if raw_candidate is None:
        raise shadow.ShadowCandidateNotFoundError(
            f"unknown shadow candidate: {candidate_id}"
        )
    candidate = ShadowCandidate.model_validate(raw_candidate)
    if candidate.review_state != "accepted" or not candidate.review_history:
        raise PrepPromotionConflictError(
            "only an accepted candidate with review history can be promoted"
        )
    review = candidate.review_history[-1]
    if review.review_state != "accepted":
        raise PrepPromotionConflictError("latest candidate review is not accepted")
    if evidence_status == "source_fact" and candidate.content_basis in {
        "inference",
        "gm_authored",
    }:
        raise PrepPromotionConflictError(
            "edited candidate content must be promoted as inference or GM-authored"
        )
    if evidence_status == "inference" and candidate.content_basis == "gm_authored":
        raise PrepPromotionConflictError(
            "GM-authored candidate content must be promoted as GM-authored"
        )

    # Normal analysis candidates are attached to a preparation job.  Keep the
    # standalone shadow seam usable for isolated review fixtures as well, but
    # only when an explicit domain bundle already exists under that task id;
    # never create a new bookshelf project implicitly from a loose candidate.
    try:
        job = _job_for_shadow_task(candidate.task_id)
    except PrepPromotionConflictError:
        saved = storage.load_domain_bundle(candidate.task_id)
        if not saved:
            raise
        workspace_id = candidate.task_id
        bundle = ExampleBundle.model_validate(saved[0])
        profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
        validate_bundle(bundle, profiles)
        job = None
    else:
        workspace_id = job.workspace_id or job.id
        if job.workspace_id != workspace_id:
            job = _save_job(job, workspace_id=workspace_id)
        bundle = _load_or_create_workspace(job)
    fact_id = f"fact_promoted_{hashlib.sha256(candidate.id.encode('utf-8')).hexdigest()[:16]}"
    promoted_at = storage.now()
    fact = SourceFact(
        id=fact_id,
        source_refs=candidate.source_refs,
        evidence_status=evidence_status,
        text=candidate.text,
        kind=candidate.kind,
        visibility=(
            "explicit"
            if evidence_status == "source_fact"
            else "inferred"
            if evidence_status == "inference"
            else "gm_suggestion"
        ),
        links=[],
        # Keep internal profile identifiers out of user-facing, copyable fact tags.
        tags=["reviewed-candidate"],
        notes=candidate.review_note,
        provenance=FactProvenance(
            candidate_id=candidate.id,
            task_id=candidate.task_id,
            run_id=candidate.run_id,
            review_id=review.id,
            promoted_at=promoted_at,
        ),
    )
    if any(item.id == fact.id for item in bundle.facts):
        raise PrepPromotionConflictError("workspace already contains the promoted fact id")
    bundle.facts.append(fact)
    profiles = load_profiles(storage.PROJECT_ROOT / "backend" / "domain" / "profiles")
    validate_bundle(bundle, profiles)
    promotion = {
        "candidate_id": candidate.id,
        "workspace_id": workspace_id,
        "fact_id": fact.id,
        "evidence_status": evidence_status,
        "review_id": review.id,
        "created_at": promoted_at,
        "candidate_snapshot": candidate.model_dump(mode="json"),
        "fact_snapshot": fact.model_dump(mode="json"),
    }
    storage.save_candidate_promotion_result(
        workspace_id,
        bundle.model_dump(mode="json", by_alias=True),
        promotion,
    )
    return fact, workspace_id, True


def _prompt_messages(job: PrepJob, window: ExtractionWindow, excerpt: str) -> list[dict]:
    pages = window.page_span.pages()
    core_pages = (window.core_span or window.page_span).pages()
    if job.prompt_version == "prep-fact-extract-v1":
        system = (
            "You extract source-bound TRPG preparation facts. Return only one JSON object "
            'with shape {"candidates": [...]}. Each candidate must contain text, kind, '
            "source_refs, confidence, and open_questions. kind must be one "
            "of clue, npc, location, handout, event, threat, stakes, obstacle, timeline, resource. "
            "Use only the supplied PDF pages. A fact that depends on more than one page "
            "must cite multiple source_refs. Do not invent rules, motives, links, or outcomes. "
            "Return an empty candidates array when the pages do not support a useful fact."
        )
    elif window.semantic_segment_id:
        system = (
            "You extract source-bound observations for one semantic segment from a "
            "bounded transport window. Return exactly one JSON object with a candidates "
            'array shaped as {"candidates":[{"text":"...","kind":"clue",'
            '"source_refs":[{"file":"exact source file","page":5,"locator":null}],'
            '"confidence":0.75,"open_questions":[]}]}. Every source_refs item must be '
            "an object; file must exactly equal SOURCE_FILE_JSON; page must be an integer "
            "from SOURCE_PAGES_JSON. Cite every supporting page visible in this window. "
            "Classify every observation with exactly one kind from clue, npc, location, "
            "handout, event, threat, stakes, obstacle, timeline, or resource. Use clue "
            "only for a discovered piece of information; use npc for a person or group's "
            "role or behavior, location for a playable place, event for something that "
            "happened or is happening, threat for an active danger or pressure, stakes "
            "for what can be lost, obstacle for a barrier, timeline for timing or change "
            "over time, resource for something usable, and handout for a map, letter, "
            "photo, newspaper, log, record, or other player-facing source asset. Do not "
            "default every observation to clue when another kind fits. "
            "This is a partial observation, not an independent scene or final fact: do "
            "not invent content, motives, links, or outcomes, and do not omit a supported "
            "detail just because it crosses a transport boundary. The segment-level "
            "reducer will merge duplicate observations. Return an empty candidates array "
            "when the supplied text supports no useful observation."
        )
    else:
        system = (
            "You extract source-bound TRPG preparation facts. Return only one JSON object "
            'with shape {"candidates":[{"text":"...","kind":"clue",'
            '"source_refs":[{"file":"exact source file","page":5,"locator":null}],'
            '"confidence":0.75,"open_questions":[]}]}. '
            "Every source_refs item must be an object; file must exactly equal SOURCE_FILE_JSON; "
            "page must be an integer from SOURCE_PAGES_JSON. confidence must be a JSON number "
            "from 0 to 1 or null, never words such as high or 高. kind must be one of clue, npc, "
            "location, handout, event, threat, stakes, obstacle, timeline, resource. Use only supplied "
            "pages. Cite every supporting page for a cross-page fact. Classify a map, letter, newspaper, "
            "photo, log, record, or other material intended to be shown/read by players as handout, "
            "even when it mentions a place. Only classify a place as location when it is a playable, "
            "returnable investigation site. Do not invent rules, "
            "motives, links, or outcomes. Context pages only complete facts crossing a window "
            "boundary. The earliest cited page is the candidate anchor: return a candidate only "
            "when that anchor is listed in CORE_PAGES_JSON. Return an empty candidates array "
            "when unsupported."
        )
    user = (
        "[TASK:prep:fact_extract]\n"
        f"SOURCE_FILE_JSON={json.dumps(job.scope.source_file, ensure_ascii=False)}\n"
        f"SOURCE_PAGES_JSON={json.dumps(pages)}\n"
        f"CORE_PAGES_JSON={json.dumps(core_pages)}\n"
        f"CONTEXT_PAGES_JSON={json.dumps(window.context_pages)}\n"
        f"BOUNDARY_PAGES_JSON={json.dumps(window.boundary_pages)}\n"
        f"SEMANTIC_SEGMENT_ID_JSON={json.dumps(window.semantic_segment_id)}\n"
        f"CORE_TEXT_SLICES_JSON={json.dumps([item.model_dump(mode='json') for item in window.core_text_slices])}\n"
        f"TARGET_PROFILE_JSON={json.dumps(job.scope.profile_id)}\n"
        f"PREP_DIRECTIVE_JSON={json.dumps(job.scope.objective, ensure_ascii=False)}\n"
        "SOURCE_TEXT_START\n"
        f"{excerpt}\n"
        "SOURCE_TEXT_END"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _semantic_window_groups(job: PrepJob) -> list[tuple[str, list[ExtractionWindow]]]:
    groups: dict[str, list[ExtractionWindow]] = {}
    order: list[str] = []
    for window in job.windows:
        segment_id = window.semantic_segment_id
        if not segment_id:
            continue
        if segment_id not in groups:
            groups[segment_id] = []
            order.append(segment_id)
        groups[segment_id].append(window)
    return [(segment_id, groups[segment_id]) for segment_id in order]


def _semantic_segment_span(
    segment_id: str, windows: list[ExtractionWindow]
) -> PageSpan:
    cores = [window.core_span or window.page_span for window in windows]
    label = next((span.label for span in cores if span.label), None)
    return PageSpan(
        start=min(span.start for span in cores),
        end=max(span.end for span in cores),
        label=label,
    )


def _raw_segment_candidates(windows: list[ExtractionWindow]) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()
    for window in windows:
        if not window.shadow_task_id:
            continue
        for raw in storage.list_shadow_candidates(
            window.shadow_task_id,
            review_state=None,
            queue_visibility=None,
        ):
            candidate_id = str(raw.get("id", ""))
            if candidate_id and candidate_id in seen:
                continue
            if candidate_id:
                seen.add(candidate_id)
            candidates.append(raw)
    return candidates


def _compact_segment_candidates(candidates: list[dict]) -> list[dict]:
    """Build the compact, source-complete wire representation for a reducer."""
    compact: list[dict] = []
    for index, raw in enumerate(candidates, start=1):
        refs = []
        for reference in raw.get("source_refs") or []:
            if not isinstance(reference, dict):
                continue
            item = {"page": reference.get("page")}
            if reference.get("locator") is not None:
                item["locator"] = reference["locator"]
            refs.append(item)
        item = {
            # This identifies an input observation only. A short ordinal is
            # enough for the reducer and avoids repeating storage UUIDs.
            "candidate_id": f"c{index}",
            "text": raw.get("text"),
            "kind": raw.get("kind"),
            "source_refs": refs,
        }
        for field in ("possible_links", "open_questions"):
            if raw.get(field):
                item[field] = raw[field]
        # The reducer assigns its own confidence; it is not source evidence.
        compact.append(item)
    return compact


def _consolidation_parent_task_ids(
    candidates: list[dict], fallback: list[str]
) -> list[str]:
    ids = [
        str(candidate.get("task_id"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("task_id")
    ]
    return list(dict.fromkeys(ids)) or list(dict.fromkeys(fallback))


def _format_consolidation_input(
    segment_id: str,
    segment_span: PageSpan,
    windows: list[ExtractionWindow],
    candidates: list[dict],
    source_task_ids: list[str] | None = None,
) -> str:
    parent_ids = source_task_ids
    if parent_ids is None:
        parent_ids = [
            window.shadow_task_id
            for window in windows
            if window.shadow_task_id
        ]
    payload = json.dumps(
        _compact_segment_candidates(candidates),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"SEMANTIC_SEGMENT_ID_JSON={json.dumps(segment_id)}\n"
        f"SEMANTIC_SEGMENT_PAGES_JSON={json.dumps(segment_span.pages())}\n"
        f"WINDOW_TASK_IDS_JSON={json.dumps(list(dict.fromkeys(parent_ids)))}\n"
        f"WINDOW_CANDIDATES_JSON={payload}"
    )


def _consolidation_candidate_batches(
    segment_id: str,
    segment_span: PageSpan,
    windows: list[ExtractionWindow],
    candidates: list[dict],
    parent_task_ids: list[str],
) -> list[tuple[list[dict], list[str]]]:
    """Partition reducer input without clipping any candidate evidence."""
    if not candidates:
        return [([], list(dict.fromkeys(parent_task_ids)))]

    batches: list[tuple[list[dict], list[str]]] = []
    current: list[dict] = []
    for candidate in candidates:
        proposed = [*current, candidate]
        proposed_parent_ids = _consolidation_parent_task_ids(
            proposed, parent_task_ids
        )
        proposed_input = _format_consolidation_input(
            segment_id,
            segment_span,
            windows,
            proposed,
            proposed_parent_ids,
        )
        over_budget = len(proposed_input) > MAX_CONSOLIDATION_INPUT_CHARS
        over_count = len(proposed) > MAX_CONSOLIDATION_CANDIDATES
        if current and (over_budget or over_count):
            batch_parent_ids = _consolidation_parent_task_ids(
                current, parent_task_ids
            )
            batches.append((current, batch_parent_ids))
            current = [candidate]
            single_input = _format_consolidation_input(
                segment_id,
                segment_span,
                windows,
                current,
                _consolidation_parent_task_ids(current, parent_task_ids),
            )
            if len(single_input) > MAX_CONSOLIDATION_INPUT_CHARS:
                raise PrepSourceError(
                    f"semantic segment {segment_id} contains one candidate too large for consolidation"
                )
            continue
        if not current and (over_budget or over_count):
            raise PrepSourceError(
                f"semantic segment {segment_id} contains one candidate too large for consolidation"
            )
        current = proposed

    if current:
        batches.append(
            (
                current,
                _consolidation_parent_task_ids(current, parent_task_ids),
            )
        )
    return batches


def _consolidation_input(
    segment_id: str,
    segment_span: PageSpan,
    windows: list[ExtractionWindow],
    candidates: list[dict],
    *,
    source_task_ids: list[str] | None = None,
) -> str:
    input_text = _format_consolidation_input(
        segment_id,
        segment_span,
        windows,
        candidates,
        source_task_ids,
    )
    if len(input_text) <= MAX_CONSOLIDATION_INPUT_CHARS:
        return input_text
    raise PrepSourceError(
        f"semantic segment {segment_id} has too many candidate details for consolidation"
    )


def _consolidation_prompt_messages(
    job: PrepJob, segment_id: str, segment_span: PageSpan, input_text: str
) -> list[dict[str, str]]:
    system = (
        "You consolidate source-bound TRPG fact candidates from transport windows. "
        "Return exactly one JSON object with a candidates array. Merge candidates only "
        "when they describe the same supported claim; combine partial cross-window "
        "claims when the cited source pages support one complete statement. Preserve "
        "distinct facts, all supporting source_refs, and unresolved open_questions. "
        "Do not invent content, links, motives, or outcomes. Input source_refs contain "
        "only page and optional locator because every input candidate inherits "
        "SOURCE_FILE_JSON. Every output source_ref must explicitly use that exact source "
        "file and a page in the semantic segment. An empty input may return an empty "
        "candidates array. Do not add top-level open_questions; attach any unresolved "
        "question to the relevant candidate instead."
    )
    user = (
        "[TASK:prep:consolidate]\n"
        f"SOURCE_FILE_JSON={json.dumps(job.scope.source_file, ensure_ascii=False)}\n"
        f"SOURCE_VERSION_JSON={json.dumps(job.scope.source_version)}\n"
        f"TARGET_PROFILE_JSON={json.dumps(job.scope.profile_id)}\n"
        f"SEMANTIC_SEGMENT_ID_JSON={json.dumps(segment_id)}\n"
        f"SEMANTIC_SEGMENT_PAGES_JSON={json.dumps(segment_span.pages())}\n"
        f"{input_text}\n"
        "Return {\"candidates\":[...]} and no markdown."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _fallback_segment_candidates(candidates: list[dict]) -> dict:
    """Deterministic no-loss fallback when a reducer returns no candidates."""
    merged: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}
    for raw in _compact_segment_candidates(candidates):
        text = " ".join(str(raw.get("text") or "").split())
        if not text:
            continue
        key = (str(raw.get("kind") or "clue"), text.casefold())
        existing = by_key.get(key)
        if existing is None:
            item = dict(raw)
            item.pop("candidate_id", None)
            by_key[key] = item
            merged.append(item)
            continue
        for field in ("source_refs", "possible_links", "open_questions"):
            values = [*existing.get(field, []), *raw.get(field, [])]
            if field == "source_refs":
                keys = [json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values]
                existing[field] = [json.loads(value) for value in dict.fromkeys(keys)]
            else:
                existing[field] = list(dict.fromkeys(values))
    if len(merged) > MAX_CONSOLIDATION_CANDIDATES:
        raise PrepSourceError(
            "deterministic consolidation would exceed the candidate limit"
        )
    return {"candidates": merged}


def _consolidation_response_transform(
    parsed: Any, raw_candidates: list[dict]
) -> Any:
    if isinstance(parsed, dict) and isinstance(parsed.get("candidates"), list):
        if parsed["candidates"]:
            return parsed
        if raw_candidates:
            return _fallback_segment_candidates(raw_candidates)
    return parsed


def _retry_shadow_task_spec(spec: ShadowTaskSpec) -> ShadowTaskSpec:
    """Give a cancelled task a fresh idempotency key for the next attempt."""
    retry_digest = hashlib.sha256(
        f"{spec.idempotency_key}:{uuid.uuid4().hex}".encode("utf-8")
    ).hexdigest()[:32]
    return spec.model_copy(
        update={
            "idempotency_key": f"{spec.idempotency_key[:110]}:retry:{retry_digest}"
        }
    )


def _retire_consolidation_tasks(
    windows: list[ExtractionWindow], replacement_task_id: str | None
) -> None:
    """Hide superseded segment reducers while retaining their audit records."""
    task_ids = {
        window.consolidation_task_id
        for window in windows
        if window.consolidation_task_id and window.consolidation_task_id != replacement_task_id
    }
    for task_id in task_ids:
        try:
            shadow.set_shadow_task_visibility(task_id, "internal")
        except shadow.ShadowTaskNotFoundError:
            # A manually cleaned-up historical task does not block the new run.
            pass


def _consolidation_progress_signature(candidates: list[dict]) -> str:
    comparable: list[dict] = []
    for item in _compact_segment_candidates(candidates):
        value = dict(item)
        value.pop("candidate_id", None)
        comparable.append(value)
    return json.dumps(comparable, ensure_ascii=False, sort_keys=True)


def _consolidation_task_key(
    job: PrepJob,
    segment_id: str,
    input_text: str,
    *,
    version: str = CONSOLIDATION_PROMPT_VERSION,
) -> str:
    digest = hashlib.sha256(
        "\x00".join(
            (
                job.id,
                segment_id,
                version,
                job.schema_version,
                job.model_id,
                str(job.fake_model),
                input_text,
            )
        ).encode("utf-8")
    ).hexdigest()
    # The job id stays readable; the digest makes each exact reducer input
    # reproducible while keeping the idempotency key well below the schema cap.
    return f"{job.id}:consolidate:{digest}"


def _deterministic_consolidation_input(
    segment_id: str,
    segment_span: PageSpan,
    candidates: list[dict],
    parent_task_ids: list[str],
) -> str:
    """Describe a no-loss aggregate through its durable parent results."""
    signature = _consolidation_progress_signature(candidates)
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return (
        'CONSOLIDATION_MODE_JSON="deterministic-no-loss-v1"\n'
        f"SEMANTIC_SEGMENT_ID_JSON={json.dumps(segment_id)}\n"
        f"SEMANTIC_SEGMENT_PAGES_JSON={json.dumps(segment_span.pages())}\n"
        f"PARENT_TASK_IDS_JSON={json.dumps(list(dict.fromkeys(parent_task_ids)))}\n"
        f"CANDIDATE_COUNT_JSON={json.dumps(len(candidates))}\n"
        f"CANDIDATE_SIGNATURE_SHA256_JSON={json.dumps(digest)}"
    )


def _find_completed_consolidation_task(
    job: PrepJob, segment_id: str, task_spec: ShadowTaskSpec
) -> Any | None:
    """Reuse an exact completed reducer result across retry-key variants."""
    expected = task_spec.model_dump(mode="json")
    for field_name in ("idempotency_key", "queue_visibility"):
        expected.pop(field_name, None)
    for task in shadow.list_shadow_tasks(include_internal=True):
        if task.status != "completed":
            continue
        if task.task_kind != "semantic_consolidation":
            continue
        if task.semantic_segment_id != segment_id:
            continue
        if not task.idempotency_key.startswith(job.id + ":consolidate:"):
            continue
        values = {
            field_name: getattr(task, field_name)
            for field_name in ShadowTaskSpec.model_fields
            if field_name not in {"idempotency_key", "queue_visibility"}
        }
        if values == expected:
            return task
    return None


def _run_consolidation_task(
    job: PrepJob,
    segment_id: str,
    segment_span: PageSpan,
    windows: list[ExtractionWindow],
    input_text: str,
    raw_candidates: list[dict],
    parent_task_ids: list[str],
    client,
) -> tuple[Any, list[Any], str | None]:
    task_spec = ShadowTaskSpec(
        idempotency_key=_consolidation_task_key(job, segment_id, input_text),
        source_file=job.scope.source_file,
        source_version=job.scope.source_version,
        source_pages=segment_span.pages(),
        profile_id=job.scope.profile_id,
        model_id=job.model_id,
        prompt_version=CONSOLIDATION_PROMPT_VERSION,
        schema_version=job.schema_version,
        input_excerpt=input_text,
        task_kind="semantic_consolidation",
        queue_visibility="internal",
        semantic_segment_id=segment_id,
        parent_task_ids=list(dict.fromkeys(parent_task_ids)),
    )
    task = _find_completed_consolidation_task(job, segment_id, task_spec)
    if task is None:
        task, _ = shadow.create_shadow_task(task_spec)
        if task.status == "cancelled":
            task, _ = shadow.create_shadow_task(_retry_shadow_task_spec(task_spec))
    # A reducer is never public while it is being built. This also hides a
    # completed task from an earlier attempt before its replacement is ready.
    shadow.set_shadow_task_visibility(task.id, "internal")

    current = _job_from_store(job.id)
    if current.status == "cancelled":
        try:
            shadow.cancel_shadow_task(task.id)
        except shadow.ShadowTaskConflictError:
            pass
        return task, [], "semantic segment consolidation was cancelled"

    current_windows = [
        window for window in current.windows if window.semantic_segment_id == segment_id
    ]
    _set_segment_consolidation_state(
        current,
        current_windows or windows,
        task_id=task.id,
        status="running",
        candidate_count=0,
        error=None,
    )

    if task.status == "completed":
        candidates = shadow.shadow_task_detail(task.id)["candidates"]
        return task, candidates, None

    try:
        raw_response = client.chat(
            _consolidation_prompt_messages(job, segment_id, segment_span, input_text),
            temperature=0.1,
            max_tokens=6000,
        )
    except Exception as error:  # noqa: BLE001
        _, run, _ = shadow.submit_shadow_result(
            task.id, transport_error=str(error)[:2000]
        )
        return task, [], run.transport_error or "semantic segment consolidation failed"

    current = _job_from_store(job.id)
    if current.status == "cancelled":
        try:
            shadow.cancel_shadow_task(task.id)
        except shadow.ShadowTaskConflictError:
            pass
        return task, [], "semantic segment consolidation was cancelled"

    completed_task, run, candidates = shadow.submit_shadow_result(
        task.id,
        raw_response=raw_response,
        response_transform=lambda parsed, active_task: _consolidation_response_transform(
            _normalize_prep_response(
                parsed,
                active_task,
                core_pages=set(segment_span.pages()),
            ),
            raw_candidates,
        ),
    )
    if run.status != "succeeded":
        return (
            completed_task,
            [],
            run.parse_error or "semantic segment consolidation failed",
        )
    return completed_task, candidates, None


def _run_deterministic_consolidation_task(
    job: PrepJob,
    segment_id: str,
    segment_span: PageSpan,
    windows: list[ExtractionWindow],
    candidates: list[dict],
    parent_task_ids: list[str],
) -> tuple[Any, list[Any], str | None]:
    """Publish a complete no-loss aggregate when model reduction has stalled."""
    source_task_ids = _consolidation_parent_task_ids(candidates, parent_task_ids)
    input_text = _deterministic_consolidation_input(
        segment_id,
        segment_span,
        candidates,
        source_task_ids,
    )
    task_spec = ShadowTaskSpec(
        idempotency_key=_consolidation_task_key(
            job,
            segment_id,
            input_text,
            version=DETERMINISTIC_CONSOLIDATION_VERSION,
        ),
        source_file=job.scope.source_file,
        source_version=job.scope.source_version,
        source_pages=segment_span.pages(),
        profile_id=job.scope.profile_id,
        model_id=job.model_id,
        prompt_version=DETERMINISTIC_CONSOLIDATION_VERSION,
        schema_version=job.schema_version,
        input_excerpt=input_text,
        task_kind="semantic_consolidation",
        queue_visibility="internal",
        semantic_segment_id=segment_id,
        parent_task_ids=source_task_ids,
    )
    task = _find_completed_consolidation_task(job, segment_id, task_spec)
    if task is None:
        task, _ = shadow.create_shadow_task(task_spec)
        if task.status == "cancelled":
            task, _ = shadow.create_shadow_task(_retry_shadow_task_spec(task_spec))
    shadow.set_shadow_task_visibility(task.id, "internal")

    current = _job_from_store(job.id)
    if current.status == "cancelled":
        try:
            shadow.cancel_shadow_task(task.id)
        except shadow.ShadowTaskConflictError:
            pass
        return task, [], "semantic segment consolidation was cancelled"

    current_windows = [
        window for window in current.windows if window.semantic_segment_id == segment_id
    ]
    _set_segment_consolidation_state(
        current,
        current_windows or windows,
        task_id=task.id,
        status="running",
        candidate_count=0,
        error=None,
    )

    if task.status == "completed":
        return task, shadow.shadow_task_detail(task.id)["candidates"], None

    completed_task, run, final_candidates = shadow.submit_shadow_result(
        task.id,
        raw_response=json.dumps({"candidates": candidates}, ensure_ascii=False),
        response_transform=lambda parsed, active_task: _normalize_prep_response(
            parsed,
            active_task,
            core_pages=set(segment_span.pages()),
        ),
    )
    if run.status != "succeeded":
        return (
            completed_task,
            [],
            run.parse_error or "deterministic semantic consolidation failed",
        )
    return completed_task, final_candidates, None


def _set_segment_consolidation_state(
    job: PrepJob,
    windows: list[ExtractionWindow],
    *,
    task_id: str | None,
    status: str,
    candidate_count: int = 0,
    error: str | None = None,
) -> PrepJob:
    window_ids = {window.id for window in windows}
    updated_windows: list[dict] = []
    for window in job.windows:
        data = window.model_dump(mode="json")
        if window.id in window_ids:
            data.update(
                consolidation_task_id=task_id,
                consolidation_status=status,
                consolidation_candidate_count=candidate_count,
                consolidation_error=error,
            )
        updated_windows.append(data)
    return _save_job(job, windows=updated_windows)


def _fail_semantic_segment(
    job: PrepJob,
    windows: list[ExtractionWindow],
    error: str,
    *,
    task_id: str | None = None,
) -> PrepJob:
    """Leave an attempted segment terminal and keep every result internal."""
    current = _job_from_store(job.id)
    current_windows = [
        window for window in current.windows
        if window.semantic_segment_id == windows[0].semantic_segment_id
    ] if windows else []
    linked_task_ids = {
        window.consolidation_task_id
        for window in current_windows
        if window.consolidation_task_id
    }
    selected_task_id = task_id or next(iter(linked_task_ids), None)
    if selected_task_id is None:
        # No reducer was created, so this segment has not started. Preserve
        # the unstarted state instead of inventing a failed task.
        return current
    for linked_task_id in linked_task_ids:
        try:
            shadow.set_shadow_task_visibility(linked_task_id, "internal")
        except shadow.ShadowTaskNotFoundError:
            pass
    return _set_segment_consolidation_state(
        current,
        current_windows or windows,
        task_id=selected_task_id,
        status="failed",
        candidate_count=0,
        error=error[:2000],
    )


def _fail_unfinished_semantic_segments(job: PrepJob, error: str) -> PrepJob:
    """Reconcile reducer states when an outer worker exception escapes."""
    current = _job_from_store(job.id)
    for _segment_id, windows in _semantic_window_groups(current):
        if all(window.consolidation_status == "succeeded" for window in windows):
            continue
        attempted = any(
            window.consolidation_status == "running"
            or window.consolidation_task_id is not None
            for window in windows
        )
        if attempted:
            current = _fail_semantic_segment(current, windows, error)
    return current


def _completed_segment_task_id(windows: list[ExtractionWindow]) -> str | None:
    """Return a durable final reducer id when a segment is already complete."""
    task_ids = {
        window.consolidation_task_id
        for window in windows
        if window.consolidation_task_id
    }
    if (
        len(task_ids) != 1
        or any(window.consolidation_status != "succeeded" for window in windows)
    ):
        return None
    task_id = next(iter(task_ids))
    try:
        task = shadow.shadow_task_detail(task_id)["task"]
    except (shadow.ShadowTaskNotFoundError, shadow.ShadowResultValidationError):
        return None
    return task_id if task.status == "completed" else None


def _consolidate_semantic_segment(
    job: PrepJob,
    segment_id: str,
    windows: list[ExtractionWindow],
    client,
) -> tuple[PrepJob, bool, int]:
    if any(window.status != "succeeded" for window in windows):
        return job, False, 0
    current = _job_from_store(job.id)
    current_windows = [
        window for window in current.windows if window.semantic_segment_id == segment_id
    ]
    raw_candidates = _raw_segment_candidates(current_windows)
    segment_span = _semantic_segment_span(segment_id, windows)
    _retire_consolidation_tasks(current_windows, None)
    pending_candidates = raw_candidates
    parent_task_ids = [
        window.shadow_task_id
        for window in current_windows
        if window.shadow_task_id
    ]
    last_task_id: str | None = None
    seen_signatures: set[str] = set()

    for _round in range(MAX_CONSOLIDATION_ROUNDS):
        try:
            batches = _consolidation_candidate_batches(
                segment_id,
                segment_span,
                current_windows,
                pending_candidates,
                parent_task_ids,
            )
        except Exception as error:  # noqa: BLE001
            updated = _set_segment_consolidation_state(
                _job_from_store(job.id),
                current_windows,
                task_id=last_task_id,
                status="failed",
                error=str(error)[:2000],
            )
            return updated, False, 0

        round_candidates: list[dict] = []
        round_task_ids: list[str] = []
        failed_error: str | None = None
        for batch, batch_parent_ids in batches:
            try:
                input_text = _consolidation_input(
                    segment_id,
                    segment_span,
                    current_windows,
                    batch,
                    source_task_ids=batch_parent_ids,
                )
                task, candidates, error = _run_consolidation_task(
                    job,
                    segment_id,
                    segment_span,
                    current_windows,
                    input_text,
                    batch,
                    batch_parent_ids,
                    client,
                )
            except Exception as error:  # noqa: BLE001
                failed_error = str(error)[:2000]
                break
            last_task_id = task.id
            round_task_ids.append(task.id)
            if error:
                failed_error = error
                break
            round_candidates.extend(
                item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                for item in candidates
            )

        if failed_error is not None:
            updated = _set_segment_consolidation_state(
                _job_from_store(job.id),
                current_windows,
                task_id=last_task_id,
                status="failed",
                error=failed_error[:2000],
            )
            return updated, False, 0

        if len(batches) == 1:
            _retire_consolidation_tasks(current_windows, last_task_id)
            updated = _set_segment_consolidation_state(
                _job_from_store(job.id),
                current_windows,
                task_id=last_task_id,
                status="succeeded",
                candidate_count=len(round_candidates),
                error=None,
            )
            return updated, True, len(round_candidates)

        if not round_candidates:
            error = "semantic consolidation produced no reducible candidates"
            updated = _set_segment_consolidation_state(
                _job_from_store(job.id),
                current_windows,
                task_id=last_task_id,
                status="failed",
                error=error,
            )
            return updated, False, 0

        current_signature = _consolidation_progress_signature(pending_candidates)
        next_signature = _consolidation_progress_signature(round_candidates)
        if next_signature in seen_signatures or next_signature == current_signature:
            # A non-reducing model pass cannot converge. Try the deterministic
            # exact-claim merge once; it preserves every distinct claim and all
            # source references without clipping text.
            deterministic = _fallback_segment_candidates(pending_candidates)[
                "candidates"
            ]
            next_signature = _consolidation_progress_signature(deterministic)
            if next_signature == current_signature:
                # The model has already validated every batch, but no exact
                # duplicate remains to merge. The final set is still within
                # the review response cap, so aggregate it deterministically
                # rather than treating complete source evidence as a failure.
                final_task, final_candidates, final_error = (
                    _run_deterministic_consolidation_task(
                        job,
                        segment_id,
                        segment_span,
                        current_windows,
                        deterministic,
                        round_task_ids,
                    )
                )
                if final_error is not None:
                    updated = _set_segment_consolidation_state(
                        _job_from_store(job.id),
                        current_windows,
                        task_id=final_task.id,
                        status="failed",
                        error=final_error[:2000],
                    )
                    return updated, False, 0
                _retire_consolidation_tasks(current_windows, final_task.id)
                updated = _set_segment_consolidation_state(
                    _job_from_store(job.id),
                    current_windows,
                    task_id=final_task.id,
                    status="succeeded",
                    candidate_count=len(final_candidates),
                    error=None,
                )
                return updated, True, len(final_candidates)
            round_candidates = deterministic

        seen_signatures.add(current_signature)
        pending_candidates = round_candidates
        parent_task_ids = round_task_ids

    error = f"semantic segment {segment_id} exceeded consolidation rounds"
    updated = _set_segment_consolidation_state(
        _job_from_store(job.id),
        current_windows,
        task_id=last_task_id,
        status="failed",
        error=error,
    )
    return updated, False, 0


def _consolidate_semantic_segments(job: PrepJob, client) -> tuple[PrepJob, bool, int]:
    total_candidates = 0
    all_succeeded = True
    current = job
    for segment_id, windows in _semantic_window_groups(current):
        current = _job_from_store(current.id)
        if current.status == "cancelled":
            return current, False, total_candidates
        segment_windows = [
            window for window in current.windows if window.semantic_segment_id == segment_id
        ]
        if _completed_segment_task_id(segment_windows) is not None:
            total_candidates += segment_windows[0].consolidation_candidate_count
            continue
        try:
            current, succeeded, count = _consolidate_semantic_segment(
                current, segment_id, segment_windows, client
            )
        except Exception as error:  # noqa: BLE001
            current = _fail_semantic_segment(
                current,
                segment_windows,
                str(error),
            )
            succeeded, count = False, 0
        total_candidates += count
        all_succeeded = all_succeeded and succeeded
    if all_succeeded:
        current = _job_from_store(current.id)
        if current.status == "cancelled":
            return current, False, total_candidates
        task_ids = list(
            dict.fromkeys(
                window.consolidation_task_id
                for window in current.windows
                if window.consolidation_task_id
            )
        )
        for task_id in task_ids:
            shadow.set_shadow_task_visibility(task_id, "review")
    return current, all_succeeded, total_candidates


def _execute_window(job: PrepJob, window: ExtractionWindow, path: Path, client) -> None:
    excerpt, truncated_pages = _window_excerpt(path, window)
    task_spec = ShadowTaskSpec(
        idempotency_key=(
            f"{job.id}:{window.id}:{job.prompt_version}:{job.schema_version}:"
            f"{hashlib.sha256(f'{job.model_id}:{job.fake_model}'.encode('utf-8')).hexdigest()[:16]}"
        ),
        source_file=job.scope.source_file,
        source_version=job.scope.source_version,
        source_pages=window.page_span.pages(),
        profile_id=job.scope.profile_id,
        model_id=job.model_id,
        prompt_version=job.prompt_version,
        schema_version=job.schema_version,
        input_excerpt=excerpt,
        task_kind=("prep_window" if window.semantic_segment_id else "standalone"),
        queue_visibility=("internal" if window.semantic_segment_id else "review"),
        semantic_segment_id=window.semantic_segment_id,
        segment_window_index=window.segment_window_index,
        segment_window_count=window.segment_window_count,
    )
    task, _ = shadow.create_shadow_task(task_spec)
    if task.status == "cancelled":
        task, _ = shadow.create_shadow_task(_retry_shadow_task_spec(task_spec))
    current = _job_from_store(job.id)
    current = _replace_window(
        current,
        window.id,
        status="running",
        shadow_task_id=task.id,
        input_chars=len(excerpt),
        truncated_pages=truncated_pages,
        error=None,
        error_kind=None,
    )

    if task.status == "completed":
        candidates = shadow.shadow_task_detail(task.id)["candidates"]
        _replace_window(
            current,
            window.id,
            status="succeeded",
            candidate_count=len(candidates),
            error=None,
            error_kind=None,
        )
        return

    try:
        raw_response = client.chat(
            _prompt_messages(job, window, excerpt),
            temperature=0.1,
            max_tokens=6000,
        )
    except Exception as error:  # noqa: BLE001
        _, run, _ = shadow.submit_shadow_result(
            task.id, transport_error=str(error)[:2000]
        )
        current = _job_from_store(job.id)
        _replace_window(
            current,
            window.id,
            status="failed",
            error=run.transport_error or "model transport failed",
            error_kind=run.error_kind or _prep_error_kind(run.transport_error),
        )
        return

    current = _job_from_store(job.id)
    if current.status == "cancelled":
        try:
            shadow.cancel_shadow_task(task.id)
        except shadow.ShadowTaskConflictError:
            pass
        _replace_window(
            current,
            window.id,
            status="cancelled",
            error="cancelled while the model request was running",
            error_kind="cancelled",
        )
        return

    _, run, candidates = shadow.submit_shadow_result(
        task.id,
        raw_response=raw_response,
        response_transform=lambda parsed, active_task: _normalize_prep_response(
            parsed,
            active_task,
            core_pages=(
                None
                if window.semantic_segment_id
                else set((window.core_span or window.page_span).pages())
            ),
        ),
    )
    current = _job_from_store(job.id)
    if run.status == "succeeded":
        _replace_window(
            current,
            window.id,
            status="succeeded",
            candidate_count=len(candidates),
            error=None,
            error_kind=None,
        )
    else:
        _replace_window(
            current,
            window.id,
            status="failed",
            error=run.parse_error or "model output validation failed",
            error_kind=run.error_kind or "model_format",
        )


def execute_prep_job(job_id: str) -> None:
    try:
        job = _job_from_store(job_id)
        path = _source_path(job.scope.source_file)
        if _source_version(path) != job.scope.source_version:
            raise PrepSourceError("source PDF changed after the prep job was created")
        client = make_client(model_id=job.model_id, force_fake=job.fake_model)
        job = _prepare_semantic_windows(job, path, client)

        for window in job.windows:
            current = _job_from_store(job_id)
            if current.status == "cancelled":
                break
            current_window = next(item for item in current.windows if item.id == window.id)
            if current_window.status == "succeeded":
                continue
            _execute_window(current, current_window, path, client)

        final = _job_from_store(job_id)
        if final.status == "cancelled":
            return
        succeeded = sum(window.status == "succeeded" for window in final.windows)
        if (
            final.segmentation_strategy == "semantic-v2"
            and final.segmentation_status == "succeeded"
        ):
            final, consolidated, candidate_count = _consolidate_semantic_segments(
                final, client
            )
            if final.status == "cancelled":
                return
            if not consolidated:
                # Segment results remain internal until every semantic segment
                # has a complete reducer result.
                candidate_count = 0
            if succeeded == len(final.windows) and consolidated:
                status = "completed"
            elif succeeded or candidate_count:
                status = "partial"
            else:
                status = "failed"
        else:
            candidate_count = sum(window.candidate_count for window in final.windows)
            if succeeded == len(final.windows):
                status = "completed"
            elif succeeded:
                status = "partial"
            else:
                status = "failed"
        _save_job(final, status=status, candidate_count=candidate_count)
    except Exception as error:  # noqa: BLE001
        try:
            job = _job_from_store(job_id)
            if job.status != "cancelled":
                if (
                    job.segmentation_strategy == "semantic-v2"
                    and job.segmentation_status == "succeeded"
                ):
                    job = _fail_unfinished_semantic_segments(job, str(error))
                windows = []
                for window in job.windows:
                    data = window.model_dump(mode="json")
                    if window.status in {"queued", "running"}:
                        data.update(
                            status="failed",
                            error=str(error)[:2000],
                            error_kind=_prep_error_kind(error),
                        )
                    windows.append(data)
                _save_job(job, status="failed", windows=windows)
        except Exception:
            pass
    finally:
        with _active_jobs_lock:
            _active_jobs.discard(job_id)


def list_prep_job_candidates(
    job_id: str, review_state: str | None = None
) -> list[dict]:
    job = _job_from_store(job_id)
    candidates: list[dict] = []
    task_ids: list[str] = []
    if (
        job.segmentation_strategy == "semantic-v2"
        and job.segmentation_status == "succeeded"
        and any(window.semantic_segment_id for window in job.windows)
    ):
        groups = _semantic_window_groups(job)
        task_ids = []
        for _segment_id, windows in groups:
            if any(window.status != "succeeded" for window in windows):
                return []
            segment_task_ids = {
                window.consolidation_task_id
                for window in windows
                if window.consolidation_task_id
            }
            if (
                len(segment_task_ids) != 1
                or any(window.consolidation_status != "succeeded" for window in windows)
            ):
                # A failed or cancelled consolidation has no user-facing
                # partial result. Keep the queue empty until every segment has
                # a complete result.
                return []
            task_ids.append(next(iter(segment_task_ids)))
    else:
        task_ids = list(
            dict.fromkeys(
                window.shadow_task_id for window in job.windows if window.shadow_task_id
            )
        )
    for task_id in task_ids:
        candidates.extend(
            storage.list_shadow_candidates(
                task_id,
                review_state,
                queue_visibility="review",
            )
        )
    return candidates
