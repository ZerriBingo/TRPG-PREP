"""分析管线：逐块抽取（map）→ 合并消歧（reduce）→ 模组知识库。

每个分块调用一次模型，输出符合 knowledge schema 的小 JSON；
再合并为全局知识库（实体按名称合并、页码叠加、构建时间线）。
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

from . import skill_loader, storage
from .llm import FakeLLM, LLMClient

CHUNK_LIMIT = 4000  # 单块送入模型的字符上限

# 取消机制：前端点「停止」→ cancel_analysis 置标志 → 循环在每块前检查
_CANCELLED: dict[int, bool] = {}


def cancel_analysis(campaign_id: int) -> None:
    _CANCELLED[campaign_id] = True


def clear_cancel(campaign_id: int) -> None:
    _CANCELLED.pop(campaign_id, None)


def analyze_chunk(client: LLMClient | FakeLLM, chunk: dict) -> dict:
    system = (
        skill_loader.skill_md()
        + "\n\n你是 TRPG 模组分析助手。从给定的模组分块文本中抽取结构化事实，"
          "严格按给定 schema 输出 JSON（只输出 JSON 对象本身，不要任何解释文字）。"
    )
    text = chunk["text"]
    truncated = len(text) > CHUNK_LIMIT
    if truncated:
        text = text[:CHUNK_LIMIT]
    user = (
        f"[TASK:analyze_chunk]\n"
        f"分块：{chunk['title']}（第 {chunk['pages']} 页，来源kind={chunk['kind']}）\n"
        + ("注意：此块文本过长已被截断。\n" if truncated else "")
        + f"\n--- 模组文本 ---\n{text}\n\n"
        f"--- 输出 schema ---\n{skill_loader.schema_text('knowledge') + '\n\n--- 输出约束 ---\n请精简输出以避免截断：每类实体最多 12 条，timeline 只保留主线事件最多 20 条，事件描述每条不超过 80 字。只输出 JSON，不要解释。'}\n"
    )
    return client.chat_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )


def _norm(name: str) -> str:
    return "".join(name.split()).lower()


def merge_knowledge(partials: list[dict]) -> dict:
    """合并各块结果：同名实体合并信息与页码；事件按页码排序；story 拼接；ending 按名合并。"""
    merged = {
        "locations": [], "npcs": [], "items": [], "events": [], "clues": [], "timeline": [],
        "story": "", "ending": [],
    }
    tables = {k: {} for k in merged if k != "story"}

    def push(table: str, item: dict):
        name = str(item.get("name") or item.get("clue") or "?").strip()
        key = _norm(name)
        if key in tables[table]:
            cur = tables[table][key]
            # 页码叠加、列表字段合并去重
            pages = {cur.get("page"), item.get("page")} - {None}
            cur["page"] = min(pages) if pages else cur.get("page")
            for f in ("intel", "knows"):
                cur[f] = list(dict.fromkeys(cur.get(f, []) + item.get(f, [])))
            if item.get("desc") and not cur.get("desc"):
                cur["desc"] = item["desc"]
        else:
            tables[table][key] = dict(item)

    for p in partials:
        if not isinstance(p, dict):
            continue
        s = str(p.get("story") or "").strip()
        if s:
            merged["story"] = f"{merged['story']}\n{s}".strip() if merged["story"] else s
        for table in tables:
            for item in p.get(table, []) or []:
                if isinstance(item, dict):
                    push(table, item)
        for t in p.get("timeline", []) or []:
            if isinstance(t, dict) and t.get("event"):
                merged["timeline"].append(t)

    merged["timeline"].sort(key=lambda t: (t.get("page") or 0, str(t.get("time") or "")))
    for k in merged:
        if k == "story":
            continue
        merged[k] = list(tables[k].values()) if k != "timeline" else merged["timeline"]
    return merged


def _save_partial(campaign_id: int, partials: list[dict]) -> dict:
    """把目前已成功的分块结果合并并立即落库（增量保存，前块即时释放）。"""
    kb = merge_knowledge(partials)
    storage.save_knowledge(campaign_id, kb)
    return kb


def _split_text(text: str, limit: int) -> list[str]:
    """按换行边界把长文本切成 <=limit 的子段（不硬切句子）。"""
    paras = re.split(r"\n{1,2}", text)
    subs: list[str] = []
    cur = ""
    for para in paras:
        if not para.strip():
            continue
        if len(cur) + len(para) + 1 > limit and cur:
            subs.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        subs.append(cur)
    return subs or [text[:limit]]


def analyze_chunk_degraded(client: LLMClient | FakeLLM, chunk: dict) -> tuple[list[dict], list[str]]:
    """整块分析失败时，按段落切成 <=2500 字的子块逐个分析；能救回多少算多少。

    返回 (成功的子块结果列表, 失败子块错误列表)。
    """
    sub_limit = min(2500, CHUNK_LIMIT // 2)
    parts = _split_text(chunk["text"], sub_limit)
    subs: list[dict] = []
    errs: list[str] = []
    for j, part in enumerate(parts):
        sub = dict(chunk, title=f"{chunk['title']}（子块{j + 1}）", text=part)
        try:
            subs.append(analyze_chunk(client, sub))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{sub['title']}: {e}")
    return subs, errs


def _analyze_one(client: LLMClient | FakeLLM, c: dict) -> tuple[dict, dict | None, str | None]:
    """单块分析（线程池 worker）：返回 (块, 结果, 错误)。"""
    try:
        return c, analyze_chunk(client, c), None
    except Exception as e:  # noqa: BLE001
        return c, None, str(e)[:200]


def run_analysis(campaign_id: int, client: LLMClient | FakeLLM) -> Iterator[dict]:
    """执行分析，yield SSE 事件。分块并发分析（2 workers），单块失败自动降级。"""
    chunks = storage.load_chunks(campaign_id)
    if not chunks:
        yield {"type": "error", "message": "尚无分块，请先摄入 PDF"}
        return
    total = len(chunks)
    partials: list[dict] = []
    failed: list[dict] = []
    processed: list[str] = []
    done_count = 0
    ex = ThreadPoolExecutor(max_workers=1)  # 中转站不支持并发，串行稳
    try:
        futures = {ex.submit(_analyze_one, client, c): c for c in chunks}
        pending = set(futures)
        while pending:
            if _CANCELLED.get(campaign_id):
                clear_cancel(campaign_id)
                yield {"type": "cancelled", "current": done_count, "total": total,
                       "message": "分析已停止（已保存的分块即时生效）"}
                return
            ready = {f for f in pending if f.done()}
            if not ready:
                time.sleep(0.2)
                continue
            for f in ready:
                pending.discard(f)
                done_count += 1
                c = futures[f]
                yield {"type": "progress", "current": done_count, "total": total,
                       "chunk": c["title"], "pages": c["pages"], "kind": c["kind"]}
                try:
                    _, partial, err = f.result()
                except Exception as e:  # noqa: BLE001
                    partial, err = None, str(e)[:200]
                if partial is not None:
                    partials.append(partial)
                    processed.append(c["title"])
                else:
                    subs, errs = analyze_chunk_degraded(client, c)
                    partials.extend(subs)
                    if subs:
                        processed.append(c["title"])
                    if errs:
                        failed.append({"title": c["title"], "message": err or str(errs[0])[:200]})
                        yield {"type": "warn", "current": done_count, "total": total,
                               "chunk": c["title"],
                               "message": f"分块分析失败: {err}（已按段落降级，{len(subs)}/{len(subs) + len(errs)} 子块成功）"}
                    else:
                        yield {"type": "info", "current": done_count, "total": total,
                               "chunk": c["title"],
                               "message": f"整块失败，已按段落降级成功（{len(subs)} 个子块）"}
                # 增量保存：每块完成立即落库
                if partials:
                    kb = _save_partial(campaign_id, partials)
                    storage.save_processed_titles(campaign_id, processed)
                    yield {"type": "partial",
                           "knowledge": {k: len(v) for k, v in kb.items()},
                           "failed": [f["title"] for f in failed]}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    kb = _save_partial(campaign_id, partials)
    summary = {k: len(v) for k, v in kb.items()}
    yield {"type": "done", "knowledge": summary, "data": kb, "failed": failed}
    clear_cancel(campaign_id)


def run_retry(campaign_id: int, client: LLMClient | FakeLLM, titles: list[str]) -> Iterator[dict]:
    """只重新分析指定的失败分段（标题匹配），与现有知识库合并。"""
    chunks = [c for c in storage.load_chunks(campaign_id) if c["title"] in set(titles)]
    if not chunks:
        yield {"type": "error", "message": "没有找到要重建的分段"}
        return
    existing = storage.load_knowledge(campaign_id) or merge_knowledge([])
    total = len(chunks)
    partials: list[dict] = []
    failed: list[dict] = []
    processed: list[str] = storage.load_processed_titles(campaign_id)
    for i, c in enumerate(chunks):
        if _CANCELLED.get(campaign_id):
            clear_cancel(campaign_id)
            yield {"type": "cancelled", "current": i, "total": total,
                   "message": "分析已停止（已保存的分块即时生效）"}
            return
        yield {"type": "progress", "current": i + 1, "total": total,
               "chunk": c["title"], "pages": c["pages"], "kind": c["kind"]}
        try:
            partials.append(analyze_chunk(client, c))
            processed.append(c["title"])
        except Exception as e:  # noqa: BLE001
            subs, errs = analyze_chunk_degraded(client, c)
            partials.extend(subs)
            if subs:
                processed.append(c["title"])
            if errs:
                failed.append({"title": c["title"], "message": str(e)[:200]})
                yield {"type": "warn", "current": i + 1, "total": total,
                       "chunk": c["title"],
                       "message": f"分块分析失败: {e}（已按段落降级，{len(subs)}/{len(subs) + len(errs)} 子块成功）"}
            else:
                yield {"type": "info", "current": i + 1, "total": total,
                       "chunk": c["title"],
                       "message": f"整块失败，已按段落降级成功（{len(subs)} 个子块）"}
        if partials:
            kb = _save_partial(campaign_id, [existing, *partials])
            storage.save_processed_titles(campaign_id, processed)
            yield {"type": "partial",
                   "knowledge": {k: len(v) for k, v in kb.items()},
                   "failed": [f["title"] for f in failed]}
    merged = _save_partial(campaign_id, [existing, *partials])
    summary = {k: len(v) for k, v in merged.items()}
    yield {"type": "done", "knowledge": summary, "data": merged, "failed": failed}
    clear_cancel(campaign_id)


def knowledge_summary_text(kb: dict) -> str:
    return json.dumps(kb, ensure_ascii=False, indent=2)[:30000]
