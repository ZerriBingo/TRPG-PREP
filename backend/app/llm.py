"""LLM 网关：OpenAI 兼容接口（流式/JSON），未配置 key 时使用假客户端。

假客户端（FakeLLM）按提示中的 [TASK:...] 标记返回确定性的示例 JSON，
用于在无 API key 时跑通全流程验证。
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterator

import subprocess
import sys
from pathlib import Path

import curl_cffi.requests as creq

from . import storage

OPENAI_CHAT_PATH = "/v1/chat/completions"

# 部分中转站（new-api 类）按 User-Agent 拦截脚本/自动化客户端（ua_blocked_script_client），
# 因此请求需携带浏览器 UA。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def auth_headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['api_key']}", "User-Agent": BROWSER_UA}

class _Retry(Exception):
    """内部信号：本次请求可重试（限流/上游过载）。"""


RETRY_STATUSES = {429, 502, 503, 524}
RETRY_ATTEMPTS = 1


def _retry_delay(attempt: int, status: int = 0) -> float:
    # 524 是网关上游长请求超时（常因模型首 token 慢），多等一会再重试。
    base = 3.0 if status == 524 else 2.0
    return base * (attempt + 1)  # base*1..base*4

# ---------------- 工具函数 ----------------

def chat_url(base_url: str) -> str:
    """OpenAI 兼容 chat/completions 端点，健壮地处理各种 base_url 写法。

    - https://api.deepseek.com            → …/v1/chat/completions
    - https://api.deepseek.com/v1         → …/v1/chat/completions
    - https://open.bigmodel.cn/api/paas/v4 → …/paas/v4/chat/completions（不再多加 /v1）
    - …/chat/completions（用户误填完整端点）→ 原样使用
    """
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if re.search(r"/v\d+$", base):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def models_url(base_url: str) -> str:
    """对应 /models 列表端点，规则与 chat_url 一致。"""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if re.search(r"/v\d+$", base):
        return base + "/models"
    return base + "/v1/models"


def list_models(cfg: dict) -> list[str]:
    """拉取该 base_url 下可用的模型 id 列表。"""
    url = models_url(cfg["base_url"])
    r = creq.get(url, impersonate="chrome", timeout=(10, 30), headers=auth_headers(cfg))
    try:
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        raise RuntimeError(
            f"HTTP {r.status_code}\nURL: {url}\n响应: {r.text[:300]}"
        ) from None
    data = r.json()
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


def parse_json(text: str) -> dict:
    """从模型输出中稳健地解析 JSON：容忍 ```json 围栏、前后缀文字，以及截断（自动修复失败则抛原始错误）。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"响应里没有 JSON 对象: {text[:200]!r}")
    candidate = text[start:]
    try:
        return json.loads(candidate)
    except ValueError as first:
        repaired = _repair_truncated(candidate)
        if repaired is not None:
            try:
                return json.loads(repaired)
            except ValueError:
                pass
        raise first


def _repair_truncated(s: str) -> str | None:
    """尽力修复被截断的 JSON：从最近的完整值处截短，补上缺失的闭合括号。

    只处理"流中断在结构中间"这一常见情况（如 timeline 数组未闭合）；
    结构严重错乱时返回 None，由上层走重发/报错。
    """
    n = len(s)
    if n < 2:
        return None
    # A response ending immediately after an opening container is not a
    # recoverable truncation: closing it would silently turn missing model
    # content into an empty result.
    if re.search(r"[\[{]\s*$", s):
        return None
    cuts = [n]
    for m in re.finditer(r"[,\{\[\]}]", s):
        cuts.append(m.end())
    for cut in sorted(set(cuts), reverse=True)[:40]:
        piece = s[:cut]
        stack: list[str] = []
        in_str = False
        i = 0
        ok = True
        while i < len(piece):
            ch = piece[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
                i += 1
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if not stack:
                    ok = False
                    break
                top = stack.pop()
                if (top == "{" and ch != "}") or (top == "[" and ch != "]"):
                    ok = False
                    break
            i += 1
        if not ok or in_str:
            continue
        closer = "".join("}" if c == "{" else "]" for c in reversed(stack))
        try:
            json.loads(piece + closer)  # 验证可解析
            return piece + closer  # 返回修复后的字符串
        except ValueError:
            continue
    return None


def find_task(messages: list[dict]) -> str:
    """在消息中查找 [TASK:xxx] 标记，供假客户端使用。"""
    for m in messages:
        for hit in re.findall(r"\[TASK:([\w:.-]+)\]", str(m.get("content", ""))):
            return hit
    return ""

# ---------------- 真实客户端 ----------------

class LLMClient:
    """OpenAI 兼容客户端。用 curl_cffi 模拟 Chrome TLS 指纹，规避 Cloudflare 反脚本检测。"""

    def __init__(self, cfg: dict):
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]

    def _url(self) -> str:
        return chat_url(self.base_url)

    def _headers(self) -> dict:
        return auth_headers({"api_key": self.api_key})

    def _worker_call(self, messages: list[dict], temperature: float,
                     max_tokens: int, stream: bool = False,
                     request_timeout: int | float | None = None) -> dict:
        """在独立子进程里用 curl_cffi 发请求（主线程可靠 + subprocess 硬超时）。"""
        payload = {
            "base_url": self.base_url, "api_key": self.api_key, "model": self.model,
            "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "stream": stream,
        }
        if request_timeout is not None:
            payload["request_timeout"] = request_timeout
        script = Path(__file__).with_name("llm_worker.py")
        subprocess_timeout = max(float(request_timeout or 120), 1.0) + 10.0
        proc = subprocess.run(
            [sys.executable, str(script)], input=json.dumps(payload),
            capture_output=True, text=True, timeout=subprocess_timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = (proc.stdout or "").strip()
        try:
            result = json.loads(raw or "{}")
        except ValueError:
            detail = raw[:300] or (proc.stderr or "").strip()[:300] or "没有可解析的 worker 输出"
            return {
                "error": f"worker 输出异常: {detail}",
                "kind": "worker",
                "status": None,
                "body": detail,
                "error_type": "WorkerOutputError",
            }
        if proc.returncode != 0 and not result.get("error"):
            detail = (proc.stderr or raw or "worker 子进程异常").strip()[:500]
            return {
                "error": f"worker 子进程退出码 {proc.returncode}: {detail}",
                "kind": "worker",
                "status": None,
                "body": detail,
                "error_type": "WorkerProcessError",
            }
        return result

    def _format_worker_error(self, out: dict) -> str:
        """Turn a worker failure into an actionable message without fake HTTP codes."""
        kind = out.get("kind")
        status = out.get("status")
        body = str(out.get("body") or "").strip()
        detail = body or str(out.get("error") or "未知错误").strip()
        # Older workers persisted a missing status as ``0``. Treat it as a
        # transport failure instead of presenting a fictitious HTTP response.
        if kind == "http" and status in (None, 0, "0", ""):
            kind = "network"
        if kind == "network":
            return f"API 网络连接失败：未收到 HTTP 响应\nURL: {self._url()}\n详情: {detail}"
        if kind == "timeout":
            timeout_label = out.get("timeout") or "请求"
            return f"API 请求超时（{timeout_label}）\nURL: {self._url()}\n详情: {detail}"
        if kind == "worker":
            return f"API worker 执行失败：{detail}\nURL: {self._url()}"
        if kind == "response":
            return f"API 响应解析失败\nURL: {self._url()}\n详情: {detail}"
        if kind == "http" and status == 403:
            account_muted = "account_muted" in detail.casefold()
            label = "API 账号访问被暂停（account_muted）" if account_muted else "API 访问被拒绝（HTTP 403）"
            return (
                f"{label}\nURL: {self._url()}\n"
                "请检查账号、密钥、代理或供应商状态；该错误不会自动重试。\n"
                f"响应: {detail}"
            )
        status_label = str(status) if status is not None else "未知"
        return f"API 请求失败 HTTP {status_label}\nURL: {self._url()}\n响应: {detail}"

    def chat(self, messages: list[dict], temperature: float = 0.3,
             max_tokens: int = 8000,
             request_timeout: int | float | None = None) -> str:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                out = self._worker_call(
                    messages,
                    temperature,
                    max_tokens,
                    stream=False,
                    request_timeout=request_timeout,
                )
                if out.get("error"):
                    status = out.get("status")
                    if status in RETRY_STATUSES and attempt < RETRY_ATTEMPTS - 1:
                        time.sleep(_retry_delay(attempt, status or 0))
                        continue
                    raise RuntimeError(self._format_worker_error(out))
                return out["content"]
            except subprocess.TimeoutExpired:
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(_retry_delay(attempt))
                    continue
                timeout_label = request_timeout if request_timeout is not None else 120
                raise RuntimeError(
                    f"API 请求超时（{timeout_label}s）\nURL: {self._url()}"
                )
        raise RuntimeError("chat: unreachable")

    def chat_streamed(self, messages: list[dict], **kw) -> str:
        """用流式接口接收完整文本再返回（规避 524：只要持续有 token，网关不会判超时）。"""
        return "".join(self.stream(messages, **kw))

    def chat_json(self, messages: list[dict], **kw) -> dict:
        """调用模型（流式）并解析 JSON；空响应/解析失败各重发一次。"""
        for _ in range(2):
            # 非流式：curl_cffi 流式在 uvicorn 线程里会卡，非流式稳定
            text = self.chat(messages, **kw)
            if not text.strip():
                if _ == 0:
                    continue
                raise RuntimeError(
                    "模型连续两次返回空响应（请检查 base_url / 模型名 / 中转站是否可用）"
                )
            try:
                return parse_json(text)
            except (json.JSONDecodeError, ValueError) as e:
                last = "JSONDecodeError" if isinstance(e, json.JSONDecodeError) else "ValueError"
                tail = text[-300:] if text else "(empty)"
                msg = f"JSON 解析失败（{last}: {e}）。可能输出被截断。\n末尾 {len(tail)} 字符: ...{tail!r}"
                if _ == 0:
                    msg += "\n将重发一次请求。"
                    print(msg, flush=True)
                    continue
                raise RuntimeError(msg) from e
        raise RuntimeError("chat_json: unreachable")

    def stream(self, messages: list[dict], temperature: float = 0.4,
               max_tokens: int = 8000,
               request_timeout: int | float | None = None) -> Iterator[str]:
        """兼容接口：子进程一次性返回全文（token 级流式由前端进度替代）。"""
        yield self.chat(
            messages,
            temperature,
            max_tokens,
            request_timeout=request_timeout,
        )



FAKE_OUTPUTS: dict[str, dict] = {}


def _fake_segment_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    match = re.search(r"^SELECTED_PAGES_JSON=(.+)$", content, re.MULTILINE)
    pages = json.loads(match.group(1)) if match else [1]
    if not pages:
        return {"segments": []}
    segments = []
    start = previous = int(pages[0])
    for raw_page in pages[1:]:
        page = int(raw_page)
        if page != previous + 1:
            segments.append({"start": start, "end": previous, "label": "离线语义段"})
            start = page
        previous = page
    segments.append({"start": start, "end": previous, "label": "离线语义段"})
    return {"segments": segments}


def _fake_prep_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    file_match = re.search(r"^SOURCE_FILE_JSON=(.+)$", content, re.MULTILINE)
    pages_match = re.search(r"^SOURCE_PAGES_JSON=(.+)$", content, re.MULTILINE)
    core_pages_match = re.search(r"^CORE_PAGES_JSON=(.+)$", content, re.MULTILINE)
    source_match = re.search(
        r"SOURCE_TEXT_START\n(.*?)\nSOURCE_TEXT_END", content, re.DOTALL
    )
    source_file = json.loads(file_match.group(1)) if file_match else "fixture://fake-prep"
    pages = json.loads(pages_match.group(1)) if pages_match else [1]
    core_pages = json.loads(core_pages_match.group(1)) if core_pages_match else pages
    source_text = source_match.group(1) if source_match else ""
    page_blocks = {
        int(page): text.strip()
        for page, text in re.findall(
            r"--- PDF p(\d+) ---\n(.*?)(?=\n\n--- PDF p\d+ ---|\Z)",
            source_text,
            re.DOTALL,
        )
    }
    cited_pages = [core_pages[0]]
    if pages[-1] != core_pages[0]:
        cited_pages.append(pages[-1])
    source_refs = []
    text_parts = []
    for page in cited_pages:
        block = page_blocks.get(page, "")
        excerpt = " ".join(line.strip() for line in block.splitlines() if line.strip())
        excerpt = excerpt[:240] or f"PDF p{page}"
        source_refs.append(
            {
                "file": source_file,
                "page": page,
                "excerpt": excerpt,
                "locator": "FakeLLM workflow fixture",
            }
        )
        text_parts.append(excerpt)
    return {
        "candidates": [
            {
                "text": " / ".join(text_parts)[:1200],
                "kind": "event",
                "source_refs": source_refs,
                "confidence": 0.5,
                "possible_links": [],
                "open_questions": [
                    "FakeLLM only verifies the local workflow; review the cited pages."
                ],
            }
        ]
    }


def _fake_consolidation_output(messages: list[dict]) -> dict:
    """Keep the offline workflow lossless while exercising segment reduction."""
    content = "\n".join(str(message.get("content", "")) for message in messages)
    candidates_match = re.search(
        r"^WINDOW_CANDIDATES_JSON=(.+)$", content, re.MULTILINE
    )
    raw_candidates = json.loads(candidates_match.group(1)) if candidates_match else []
    if not isinstance(raw_candidates, list):
        return {"candidates": []}

    merged: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        candidate.pop("candidate_id", None)
        key = (
            str(candidate.get("kind", "clue")),
            " ".join(str(candidate.get("text", "")).split()).casefold(),
        )
        if not key[1]:
            continue
        existing = by_key.get(key)
        if existing is None:
            candidate["source_refs"] = list(candidate.get("source_refs") or [])
            candidate["possible_links"] = list(candidate.get("possible_links") or [])
            candidate["open_questions"] = list(candidate.get("open_questions") or [])
            by_key[key] = candidate
            merged.append(candidate)
            continue
        for field in ("source_refs", "possible_links", "open_questions"):
            values = [*existing.get(field, []), *candidate.get(field, [])]
            existing[field] = list(
                dict.fromkeys(json.dumps(item, sort_keys=True, ensure_ascii=False) if field == "source_refs" else str(item) for item in values)
            )
            if field == "source_refs":
                existing[field] = [json.loads(item) for item in existing[field]]
    return {"candidates": merged[:50]}


def _fake_artifact_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    profile_match = re.search(r"^TARGET_PROFILE_JSON=(.+)$", content, re.MULTILINE)
    facts_match = re.search(r"^REVIEWED_FACTS_JSON=(.+)$", content, re.MULTILINE)
    profile = json.loads(profile_match.group(1)) if profile_match else {}
    facts = json.loads(facts_match.group(1)) if facts_match else []
    if not facts:
        return {"cards": [], "open_questions": ["FakeLLM found no reviewed facts."]}
    list_fields = {
        "immediate_actions", "direct_clues", "hidden_clues", "gm_moves",
        "exit_conditions", "npc_hooks", "clock_links", "noncombat_exits",
        "stages", "usable_features", "hazards", "positioning",
        "environment_changes", "signature_actions", "resolutions",
        "entry_points", "discoveries", "relevant_characters",
    }
    fact_ids = [item["id"] for item in facts[:3]]
    source_text = " / ".join(item["text"] for item in facts[:3])[:600]
    cards = []
    for index, definition in enumerate(profile.get("card_definitions", []), start=1):
        fields = {}
        field_sources = {}
        for field_name in definition.get("required_fields", []):
            fields[field_name] = [source_text] if field_name in list_fields else source_text
            field_sources[field_name] = list(fact_ids)
        cards.append(
            {
                "type": definition["type"],
                "title": f"FakeLLM {definition.get('display_name') or definition['type']} {index}",
                "subtitle": "离线工作流验证草案",
                "fact_ids": list(fact_ids),
                "fields": fields,
                "field_sources": field_sources,
                "open_questions": ["仅用于验证生成、审批与场景解锁流程。"],
            }
        )
    return {"cards": cards, "open_questions": []}


def _fake_local_digest_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    facts_match = re.search(r"^LOCAL_FACTS_JSON=(.+)$", content, re.MULTILINE)
    facts = json.loads(facts_match.group(1)) if facts_match else []
    kind_map = {
        "clue": "clue_cluster",
        "npc": "npc",
        "location": "location",
        "event": "scene",
        "threat": "threat",
        "stakes": "background",
        "obstacle": "scene",
        "timeline": "timeline",
        "resource": "resource",
    }
    grouped: dict[str, list[dict]] = {}
    location_items: list[dict] = []
    for fact in facts:
        kind = kind_map.get(fact.get("kind"), "background")
        if kind == "location":
            location_items.append(fact)
        else:
            grouped.setdefault(kind, []).append(fact)
    units = []
    for fact in location_items:
        title = str(fact.get("text") or "离线地点").strip()[:80]
        units.append(
            {
                "kind": "location",
                "title": title,
                "summary": str(fact.get("text") or "离线地点索引")[:700],
                "fact_ids": [fact["id"]],
                "entity_keys": [title],
                "relationship_hints": [],
                "open_questions": [],
            }
        )
    for kind, items in grouped.items():
        sample = " / ".join(str(item.get("text", "")) for item in items[:2])[:700]
        units.append(
            {
                "kind": kind,
                "title": f"离线局部整理 {len(units) + 1}",
                "summary": sample or "离线局部整理索引",
                "fact_ids": [item["id"] for item in items],
                "entity_keys": [],
                "relationship_hints": [],
                "open_questions": [],
            }
        )
    return {"units": units, "open_questions": []}


def _fake_global_plan_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    profile_match = re.search(r"^TARGET_PROFILE_JSON=(.+)$", content, re.MULTILINE)
    units_match = re.search(r"^GLOBAL_UNITS_JSON=(.+)$", content, re.MULTILINE)
    sizes_match = re.search(r"^FACT_INPUT_CHARS_JSON=(.+)$", content, re.MULTILINE)
    tokens_match = re.search(r"^FACT_INPUT_TOKENS_JSON=(.+)$", content, re.MULTILINE)
    budget_match = re.search(r"^MAX_CARD_FACT_INPUT_CHARS_JSON=(\d+)$", content, re.MULTILINE)
    token_budget_match = re.search(r"^MAX_CARD_FACT_INPUT_TOKENS_JSON=(\d+)$", content, re.MULTILINE)
    profile = json.loads(profile_match.group(1)) if profile_match else {}
    units = json.loads(units_match.group(1)) if units_match else []
    sizes = json.loads(sizes_match.group(1)) if sizes_match else {}
    tokens = json.loads(tokens_match.group(1)) if tokens_match else {}
    budget = int(budget_match.group(1)) if budget_match else 72_000
    token_budget = int(token_budget_match.group(1)) if token_budget_match else 22_000
    fact_ids = list(
        dict.fromkeys(
            fact_id for unit in units for fact_id in unit.get("fact_ids", [])
        )
    )
    chunks: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    current_tokens = 0
    for fact_id in fact_ids:
        fact_size = max(int(sizes.get(fact_id, 1)), 1)
        fact_tokens = max(int(tokens.get(fact_id, 1)), 1)
        if current and (
            len(current) >= 90
            or current_size + fact_size > budget - 1000
            or current_tokens + fact_tokens > token_budget - 500
        ):
            chunks.append(current)
            current = []
            current_size = 0
            current_tokens = 0
        current.append(fact_id)
        current_size += fact_size
        current_tokens += fact_tokens
    if current:
        chunks.append(current)

    definitions = profile.get("card_definitions", [])
    cards = []
    if profile.get("profile_kind") == "runtime" and len(fact_ids) <= 90:
        unit_facts: dict[str, list[str]] = {}
        for unit in units:
            unit_facts.setdefault(str(unit.get("kind") or "background"), []).extend(unit.get("fact_ids", []))
        type_units = {
            "location": ("location", "scene"),
            "chapter_overview": tuple(unit_facts),
            "npc": ("npc",),
            "threat": ("threat",),
            "clock": ("timeline", "outcome"),
        }
        for index, definition in enumerate(definitions, start=1):
            if definition["type"] == "location":
                location_units = [unit for unit in units if unit.get("kind") == "location"]
                if location_units:
                    for location_index, unit in enumerate(location_units, start=1):
                        cards.append({
                            "type": "location",
                            "title": str(unit.get("title") or f"FakeLLM 地点 {location_index}"),
                            "purpose": "独立验证一个可进入、调查或折返的地点。",
                            "fact_ids": list(dict.fromkeys(unit.get("fact_ids", []))),
                            "focus": list(dict.fromkeys(unit.get("entity_keys", []))),
                            "open_questions": [],
                        })
                    continue
            relevant = list(dict.fromkeys(
                fact_id
                for kind in type_units.get(definition["type"], ())
                for fact_id in unit_facts.get(kind, [])
            ))
            if not relevant:
                relevant = fact_ids[:1]
            cards.append({
                "type": definition["type"],
                "title": f"FakeLLM {definition.get('display_name') or definition['type']} {index}",
                "purpose": "在离线流程中验证完整运行卡组与原始事实回读。",
                "fact_ids": relevant,
                "focus": ["来源覆盖", "运行资料"],
                "open_questions": [],
            })
        return {"cards": cards, "open_questions": []}
    for index, chunk in enumerate(chunks, start=1):
        definition = definitions[(index - 1) % len(definitions)]
        display_name = definition.get("display_name") or definition["type"]
        cards.append(
            {
                "type": definition["type"],
                "title": f"FakeLLM {display_name} {index}",
                "purpose": "在离线流程中验证全局规划与原始事实回读。",
                "fact_ids": chunk,
                "focus": ["来源覆盖", "跨批次规划"],
                "open_questions": [],
            }
        )
    return {"cards": cards, "open_questions": []}


def _fake_materialize_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    plan_match = re.search(r"^CARD_PLAN_JSON=(.+)$", content, re.MULTILINE)
    facts_match = re.search(r"^ORIGINAL_FACTS_JSON=(.+)$", content, re.MULTILINE)
    definition_match = re.search(r"^CARD_DEFINITION_JSON=(.+)$", content, re.MULTILINE)
    plan = json.loads(plan_match.group(1)) if plan_match else {}
    facts = json.loads(facts_match.group(1)) if facts_match else []
    definition = json.loads(definition_match.group(1)) if definition_match else {}
    available = {item["id"]: item for item in facts}
    fact_ids = [fact_id for fact_id in plan.get("fact_ids", []) if fact_id in available]
    source_text = " / ".join(available[fact_id]["text"] for fact_id in fact_ids[:3])[:700]
    list_fields = {
        "immediate_actions", "direct_clues", "hidden_clues", "gm_moves",
        "exit_conditions", "npc_hooks", "clock_links", "noncombat_exits",
        "stages", "usable_features", "hazards", "positioning",
        "environment_changes", "signature_actions", "resolutions",
        "entry_points", "discoveries", "relevant_characters",
    }
    fields = {}
    field_sources = {}
    for field_name in definition.get("required_fields", []):
        fields[field_name] = [source_text] if field_name in list_fields else source_text
        field_sources[field_name] = list(fact_ids)
    card = {
        "type": plan.get("type", definition.get("type", "scene")),
        "title": plan.get("title", "FakeLLM 备团产物"),
        "subtitle": "离线分层生成验证草案",
        "fact_ids": fact_ids,
        "fields": fields,
        "field_sources": field_sources,
        "open_questions": [],
    }
    return {"cards": [card], "open_questions": []}


class FakeLLM:
    """确定性的假 LLM：按 [TASK:...] 返回示例 JSON，用于无 key 验证。"""

    def __init__(self, cfg: dict):
        self.model = cfg.get("model", "fake")

    def _output(self, messages: list[dict]) -> str:
        task = find_task(messages)
        if task == "prep:segment":
            return json.dumps(_fake_segment_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:fact_extract":
            return json.dumps(_fake_prep_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:consolidate":
            return json.dumps(
                _fake_consolidation_output(messages), ensure_ascii=False, indent=2
            )
        if task == "prep:artifact_draft":
            return json.dumps(_fake_artifact_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:artifact_local_digest":
            return json.dumps(_fake_local_digest_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:artifact_global_plan":
            return json.dumps(_fake_global_plan_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:artifact_materialize":
            return json.dumps(_fake_materialize_output(messages), ensure_ascii=False, indent=2)
        data = FAKE_OUTPUTS.get(task)
        if data is None:
            data = {"note": f"假客户端未实现任务: {task!r}", "task": task}
        return json.dumps(data, ensure_ascii=False, indent=2)

    def chat(self, messages: list[dict], **kw) -> str:
        return self._output(messages)

    def chat_json(self, messages: list[dict], **kw) -> dict:
        return json.loads(self._output(messages))

    def stream(self, messages: list[dict], **kw) -> Iterator[str]:
        text = self._output(messages)
        for i in range(0, len(text), 24):
            yield text[i:i + 24]


def make_client(
    model_id: str | None = None, force_fake: bool | None = None
) -> LLMClient | FakeLLM:
    cfg = dict(storage.get_config())
    if model_id:
        cfg["model"] = model_id
    use_fake = cfg["fake"] if force_fake is None else force_fake
    return FakeLLM(cfg) if use_fake else LLMClient(cfg)
