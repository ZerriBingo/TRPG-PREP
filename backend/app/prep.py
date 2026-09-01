"""R1 cross-page preparation jobs connected to the isolated shadow queue."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

import fitz
from pydantic import ValidationError

from ..domain import (
    ExampleBundle,
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
PROMPT_VERSION = "prep-fact-extract-v3"
SCHEMA_VERSION = "shadow-candidate-v1"

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


def _build_windows(
    path: Path,
    spans: list[PageSpan],
    job_token: str,
    *,
    semantic_boundaries: bool = False,
) -> list[ExtractionWindow]:
    """Build non-overlapping ownership cores with repeated boundary context."""
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
    if job.segmentation_strategy != "semantic-v1" or job.segmentation_status != "pending":
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
        # lose a whole job. The original deterministic windows remain intact.
        return _save_job(
            job,
            segmentation_status="fallback",
            segmentation_error=str(error)[:2000],
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


def _recover_interrupted_jobs() -> None:
    with _active_jobs_lock:
        active = set(_active_jobs)
    for raw in storage.list_prep_jobs():
        job = PrepJob.model_validate(raw)
        if job.status != "running" or job.id in active:
            continue
        windows = []
        for window in job.windows:
            data = window.model_dump(mode="json")
            if window.status == "running":
                data.update(
                    status="failed",
                    error="generation was interrupted before completion",
                    error_kind="worker",
                )
            windows.append(data)
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
        segmentation_strategy="semantic-v1",
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

    if job.segmentation_strategy == "semantic-v1":
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
    for window in job.windows:
        if window.shadow_task_id:
            candidate_ids.extend(
                item["id"]
                for item in storage.list_shadow_candidates(
                    window.shadow_task_id, review_state=None
                )
            )
    count = len(storage.list_candidate_promotions(candidate_ids))
    if count == job.promoted_count:
        return job
    return job.model_copy(update={"promoted_count": count})


def list_prep_jobs() -> list[PrepJob]:
    _recover_interrupted_jobs()
    return [
        _job_with_promotion_count(PrepJob.model_validate(item))
        for item in storage.list_prep_jobs()
    ]


def get_prep_job(job_id: str) -> PrepJob:
    _recover_interrupted_jobs()
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

    windows = []
    for window in job.windows:
        data = window.model_dump(mode="json")
        if window.status in {"failed", "cancelled"}:
            data.update(status="queued", error=None, error_kind=None)
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
    task_ids = [
        window.shadow_task_id for window in job.windows if window.shadow_task_id
    ]
    if not storage.delete_prep_job(job.id, task_ids):
        raise PrepJobNotFoundError(f"unknown prep job: {job.id}")


def _job_for_shadow_task(task_id: str) -> PrepJob:
    for raw in storage.list_prep_jobs():
        job = PrepJob.model_validate(raw)
        if any(window.shadow_task_id == task_id for window in job.windows):
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
    )
    task, _ = shadow.create_shadow_task(task_spec)
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
            core_pages=set((window.core_span or window.page_span).pages()),
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
        failed = sum(window.status == "failed" for window in final.windows)
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
    for window in job.windows:
        if window.shadow_task_id:
            candidates.extend(
                storage.list_shadow_candidates(window.shadow_task_id, review_state)
            )
    return candidates
