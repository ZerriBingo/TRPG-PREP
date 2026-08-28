"""三部分备团生成：故事总览 / 信息集合 / 临场遭遇。

只从知识库生成（不重读全文）；支持携带 GM 指令的局部重生成。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

from . import skill_loader, storage
from .analyze import knowledge_summary_text
from .llm import FakeLLM, LLMClient, parse_json

PART_NAMES = {
    "overview": "故事总览",
    "locations": "信息集合（场景卡式整理页）",
    "encounters": "临场素材（GM 即兴工具箱）",
}

# 生成某部分时只喂相关知识库字段（避免整库超 30000 字符被截断、丢掉关键字段）
PART_FIELDS = {
    "overview": ["story", "timeline", "ending"],
    "locations": ["locations", "npcs", "clues", "items"],
    "encounters": ["events", "locations", "npcs", "clues", "timeline"],
}


def _filter_kb(kb: dict, part: str) -> dict:
    fields = PART_FIELDS.get(part)
    if not fields:
        return kb
    return {k: kb.get(k) for k in fields if k in kb}


def _collect_pages(kb: dict, part: str, limit: int = 25) -> list[int]:
    """从知识库实体收集相关页码（用于回溯原文）。"""
    pages: set[int] = set()
    for table in PART_FIELDS.get(part, []):
        for item in kb.get(table, []) or []:
            if isinstance(item, dict) and isinstance(item.get("page"), int) and item["page"] > 0:
                pages.add(item["page"])
    return sorted(pages)[:limit]


def _page_snippets(campaign_id: int, kb: dict, part: str) -> str:
    pages = _collect_pages(kb, part)
    return storage.load_page_snippets(campaign_id, pages, max_chars=6000)


def build_messages(part: str, campaign: dict, kb: dict, instruction: str | None) -> list[dict]:
    system = (
        skill_loader.skill_md()
        + "\n\n你是 TRPG 备团助手。基于给定的模组知识库，生成「"
        + PART_NAMES[part] + "」部分。严格按 schema 输出 JSON（只输出 JSON 对象本身）。"
        "知识库中已含来源页码；不要把知识库没有的内容当作模组事实，推测必须标注「（GM 建议）」。"
    )
    kb = _filter_kb(kb, part)
    user = (
        f"[TASK:generate:{part}]\n"
        f"模组/战役：{campaign['name']}\n\n"
        f"--- 模组知识库 ---\n{knowledge_summary_text(kb)}\n\n"
        f"--- 输出 schema ---\n{skill_loader.schema_text(part)}\n"
    )
    if part == "overview":
        user += (
            "\n--- 输出约束（务必遵守，避免截断）---\n"
            "**总输出控制在 2500 字以内**。acts 最多 6 幕、每幕 summary ≤100 字；"
            "key_information 最多 8 条；possible_directions 最多 6 条；endings 最多 4 个、"
            "每个 ≤80 字；gm_pitfalls 最多 6 条。\n"
        )
    if part == "locations":
        user += (
            "\n--- 输出约束（务必遵守，避免截断）---\n"
            "**总输出控制在 3000 字以内**。locations 最多输出 10 个最重要的场景；每个场景："
            "read_aloud（可直接朗读，第二人称现在时，60-120 字）、description（GM 细节，≤120 字）、"
            "info 最多 3 条、disclosure 每项 ≤60 字、extras 最多 3 条。\n"
        )
    if part == "encounters":
        user += (
            "\n--- 输出要求（务必遵守，避免截断）---\n"
            "这是给 GM 的即兴素材工具箱。**总输出控制在 2500 字以内**。"
            "每个素材必须是一个真正的遭遇时刻，不是静态环境描写：scene（遭遇画面，30-60 字，"
            "必须包含冲突、异常或需要玩家立即反应的状况）、choice（玩家抉择，≤40 字）、"
            "consequence（后果，≤60 字）。从模组知识库取材，不要凭空发明；tools 输出 8 条，"
            "覆盖不同用途；loose_threads 最多 4 条。\n"
        )
    snippets = _page_snippets(campaign["id"], kb, part)
    if snippets:
        user += (
            "\n--- 可查阅原文片段（按页号） ---\n"
            + snippets
            + "\n知识库中信息不足时，参考上述原文片段补充细节（如房间布局、物品外观、现场描写等），"
              "并保留页码标注；原文片段没有的内容不要发明。\n"
        )
    if instruction:
        user += f"\n--- GM 指令 ---\n{instruction}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_pages(pages_str: str) -> tuple[int, int]:
    """解析 "7-12" -> (7, 12)；无界时返回 (0, 10**9)。"""
    try:
        a, b = pages_str.split("-")
        return int(a.strip()), int(b.strip())
    except (ValueError, AttributeError):
        return 0, 10**9


def _filter_by_page(kb: dict, part: str, lo: int, hi: int) -> dict:
    """只保留页号落在 [lo, hi] 的知识库实体。"""
    out: dict = {}
    for table in PART_FIELDS.get(part, []):
        kept = []
        for item in kb.get(table, []) or []:
            if isinstance(item, dict) and isinstance(item.get("page"), int) and lo <= item["page"] <= hi:
                kept.append(item)
        if kept:
            out[table] = kept
    return out


def _norm_name(name: str) -> str:
    return "".join(str(name).split())


def _merge_staged(part: str, results: list[dict]) -> dict:
    """合并各章节结果：场景按名去重合并；素材直接拼接。"""
    if part == "locations":
        merged_locs: dict = {}
        for r in results:
            for loc in r.get("locations", []) or []:
                if not isinstance(loc, dict) or not loc.get("name"):
                    continue
                key = _norm_name(loc["name"])
                if key in merged_locs:
                    cur = merged_locs[key]
                    for f in ("npcs",):
                        cur[f] = list(dict.fromkeys(cur.get(f, []) + loc.get(f, [])))
                    if not cur.get("description") and loc.get("description"):
                        cur["description"] = loc["description"]
                    if not cur.get("read_aloud") and loc.get("read_aloud"):
                        cur["read_aloud"] = loc["read_aloud"]
                    if not cur.get("page") and loc.get("page"):
                        cur["page"] = loc["page"]
                    # info 按标题去重拼接
                    seen = {_norm_name(x.get("title", "")) for x in cur.get("info", [])}
                    for it in loc.get("info", []) or []:
                        if _norm_name(it.get("title", "")) not in seen:
                            cur.setdefault("info", []).append(it)
                            seen.add(_norm_name(it.get("title", "")))
                    cur["extras"] = list(dict.fromkeys(cur.get("extras", []) + loc.get("extras", [])))
                else:
                    merged_locs[key] = dict(loc)
        locs = list(merged_locs.values())
        locs.sort(key=lambda x: (x.get("page") or 10**9))
        return {"locations": locs}
    if part == "encounters":
        tools: list = []
        threads: list = []
        for r in results:
            tools.extend(r.get("tools", []) or [])
            threads.extend(r.get("loose_threads", []) or [])
        return {"tools": tools, "loose_threads": list(dict.fromkeys(threads))}
    return {"locations": [], "tools": []}


def _stage_anchors(part: str, stage_kb: dict) -> str:
    """从知识库提炼本章节的场景/事件清单，作为生成锚点（防漏 + 命名一致）。"""
    if part == "locations":
        names = [f"- {x.get('name', '')}（p{x.get('page', '?')}）"
                 for x in (stage_kb.get("locations") or []) if isinstance(x, dict)]
        if not names:
            return ""
        return "\n--- 本章场景清单（知识库提炼；清单中在本章有信息的场景必须输出） ---\n" + "\n".join(names[:20]) + "\n"
    if part == "encounters":
        evs = [f"- 事件：{x.get('name', '')}（p{x.get('page', '?')}）"
               for x in (stage_kb.get("events") or []) if isinstance(x, dict)]
        cls = [f"- 线索：{str(x.get('clue', ''))[:50]}（p{x.get('page', '?')}）"
               for x in (stage_kb.get("clues") or []) if isinstance(x, dict)]
        if not evs and not cls:
            return ""
        return ("\n--- 本章素材锚点（模组事件/线索，可作素材来源，优先采用） ---\n"
                + "\n".join(evs[:12] + cls[:6]) + "\n")
    return ""


def build_stage_messages(part: str, campaign: dict, stage_kb: dict, snippet: str,
                         stage_title: str, stage_pages: str,
                         instruction: str | None) -> list[dict]:
    system = (
        skill_loader.skill_md()
        + "\n\n你是 TRPG 备团助手。按章节分阶段整理「" + PART_NAMES[part]
        + "」。严格按 schema 输出 JSON（只输出 JSON 对象本身）；"
        "细节以本章原文为准，不要把原文没有的内容当作模组事实，推测必须标注「（GM 建议）」。"
    )
    anchors = _stage_anchors(part, stage_kb)
    user = (
        f"[TASK:generate:{part}:stage]\n"
        f"模组/战役：{campaign['name']}\n"
        f"当前章节：{stage_title}（第 {stage_pages} 页）\n\n"
        + (anchors + "\n" if anchors else "")
        + (f"--- 本章完整原文（细节来源，务必通读） ---\n{snippet}\n\n" if snippet else "")
        + f"--- 输出 schema ---\n{skill_loader.schema_text(part)}\n"
    )
    if part == "locations":
        user += (
            "\n--- 输出约束 ---\n"
            "只整理当前章节（第 " + stage_pages + " 页）范围内出现的场景；"
            "场景清单中在本章有信息的场景必须全部输出，本章新出现的场景也补充；最多 10 个；"
            "场景名与模组原文一致；每个场景 read_aloud 60-150 字（可直接朗读，基于原文改写）、"
            "description ≤150 字（GM 细节）、info ≤4 条、disclosure 每项 ≤80 字；总输出 ≤2000 字。\n"
        )
    if part == "encounters":
        user += (
            "\n--- 输出要求 ---\n"
            "基于本章完整原文产出 3-5 个即兴素材（必须是遭遇时刻）+ 1-2 条松散线索；总输出 ≤1500 字。\n"
            "合格遭遇 = 冲突/异常/需要玩家立即反应的状况：有 scene（画面）、choice（抉择）、consequence（后果）。\n"
            "不合格（禁止） = 纯环境/氛围描写（如：『海风呼啸，灯塔的灯光熄灭』这种没有冲突、无需玩家反应的句子不能作为 scene）。\n"
            "示例合格 scene：『一名浑身湿透的男人猛地推开厨房门，手中握着染血的鱼叉，目光锁定你们。』\n"
            "即使本章冲突事件较少，也必须从线索/NPC/场景中提炼至少 3 个可行动的遭遇点；不得输出空数组。\n"
        )
    if instruction:
        user += f"\n--- GM 指令 ---\n{instruction}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def run_generate_staged(campaign_id: int, part: str, client: LLMClient | FakeLLM,
                        instruction: str | None = None) -> Iterator[dict]:
    """locations/encounters 按章节分阶段生成（每章一小批，合并输出）；overview 走单次。"""
    if part not in PART_NAMES:
        yield {"type": "error", "message": f"未知部分: {part}"}
        return
    campaign = storage.get_campaign(campaign_id)
    kb = storage.load_knowledge(campaign_id)
    if campaign is None:
        yield {"type": "error", "message": "战役不存在"}
        return
    if kb is None:
        yield {"type": "error", "message": "尚无知识库，请先执行分析"}
        return
    if part == "overview":
        yield from run_generate(campaign_id, part, client, instruction)
        return

    chunks = storage.load_chunks(campaign_id)
    if not chunks:
        yield {"type": "error", "message": "尚无分块，请先摄入 PDF"}
        return
    # 按页范围分组：子块（同页范围）合并为单阶段，避免重复喂同一地点清单
    groups: dict[str, list[dict]] = {}
    for ch in chunks:
        groups.setdefault(ch["pages"], []).append(ch)
    stages: list[dict] = []
    for pages, cs in groups.items():
        title0 = cs[0]["title"]
        head = title0.split("（")[0]
        stages.append({
            "pages": pages, "title": f"{head}（{pages}页）",
            "text": "\n".join(c["text"] for c in cs)[:24000],
        })
    total = len(stages)
    results: list[dict | None] = [None] * total
    failed: list[dict] = []
    done_count = 0

    def _stage_one(st: dict) -> tuple[dict, dict]:
        lo, hi = _parse_pages(st["pages"])
        stage_kb = _filter_by_page(kb, part, lo, hi)
        messages = build_stage_messages(part, campaign, stage_kb, st["text"],
                                        st["title"], st["pages"], instruction)
        return st, client.chat_json(messages, temperature=0.4, max_tokens=4000)

    ex = ThreadPoolExecutor(max_workers=1)  # 章节间独立，并发压缩总时长（curl_cffi 非流式稳定）
    try:
        futures = {ex.submit(_stage_one, st): i for i, st in enumerate(stages)}
        pending = set(futures)
        while pending:
            ready = {f for f in pending if f.done()}
            if not ready:
                time.sleep(0.2)
                continue
            for f in ready:
                pending.discard(f)
                done_count += 1
                i = futures[f]
                st = stages[i]
                yield {"type": "progress", "current": done_count, "total": total,
                       "chunk": st["title"], "pages": st["pages"], "kind": "stage"}
                try:
                    _, data = f.result()
                    results[i] = data
                    yield {"type": "stage_done", "current": done_count, "total": total,
                           "chunk": st["title"],
                           "count": len(data.get("locations", data.get("tools", [])) or [])}
                except Exception as e:  # noqa: BLE001
                    # 自动重试一次（中转站波动常见，重试常能成功）
                    try:
                        _, data = _stage_one(st)
                        results[i] = data
                        yield {"type": "stage_done", "current": done_count, "total": total,
                               "chunk": st["title"],
                               "count": len(data.get("locations", data.get("tools", [])) or [])}
                        continue
                    except Exception as e2:  # noqa: BLE001
                        failed.append({"title": st["title"], "message": str(e2)[:200]})
                        yield {"type": "warn", "current": done_count, "total": total,
                               "chunk": st["title"], "message": f"章节分析失败(重试后仍失败): {e2}"}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    merged = _merge_staged(part, [r for r in results if r is not None])
    # 遭遇全空时自动重试一轮（多数为瞬时连接/生成问题）
    if part == "encounters" and not merged.get("tools") and not merged.get("loose_threads"):
        # 分阶段未产出任何素材：用整体知识库+原文兜底重生成一次
        yield {"type": "warn", "current": total, "total": total,
               "chunk": "全部章节", "message": "分阶段未产出遭遇素材，改用整体重试…"}
        try:
            messages = build_messages(part, campaign, kb, instruction)
            buf = client.chat(messages, temperature=0.4, max_tokens=6000)
            merged = parse_json(buf)
        except Exception as e:  # noqa: BLE001
            yield {"type": "warn", "current": total, "total": total,
                   "chunk": "全部章节", "message": f"整体重试失败: {e}"}
    storage.save_prep(campaign_id, part, merged)
    yield {"type": "done", "part": part, "data": merged,
           "failed": [f["title"] for f in failed]}


def run_generate(campaign_id: int, part: str, client: LLMClient | FakeLLM,
                 instruction: str | None = None) -> Iterator[dict]:
    """生成某一部分，yield SSE 事件（流式 tokens + done）。"""
    if part not in PART_NAMES:
        yield {"type": "error", "message": f"未知部分: {part}"}
        return
    campaign = storage.get_campaign(campaign_id)
    kb = storage.load_knowledge(campaign_id)
    if campaign is None:
        yield {"type": "error", "message": "战役不存在"}
        return
    if kb is None:
        yield {"type": "error", "message": "尚无知识库，请先执行分析"}
        return

    messages = build_messages(part, campaign, kb, instruction)
    try:
        # 非流式：curl_cffi 流式在 uvicorn 线程里会卡，非流式稳定
        buf = client.chat(messages, temperature=0.4, max_tokens=6000)
        data = parse_json(buf)
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "message": f"生成失败: {e}\n（原始输出片段：{str(e)[:300]}）"}
        return
    storage.save_prep(campaign_id, part, data)
    yield {"type": "done", "part": part, "data": data}


def prep_to_markdown(prep: dict) -> str:
    """把三部分产物渲染为可打印的 Markdown。"""
    lines: list[str] = []
    overview = prep.get("overview")
    if overview:
        lines += ["# 故事总览", "", f"**{overview.get('title', '')}**", ""]
        for act in overview.get("acts", []):
            lines += [f"## {act.get('name', '')}（p{act.get('page', '?')}）",
                      act.get("summary", ""), ""]
        lines += ["## 关键信息", ""]
        for k in overview.get("key_information", []):
            lines += [f"- **{k.get('item', '')}**（p{k.get('page', '?')}）"
                      + (f"：{k.get('why', '')}" if k.get("why") else "")]
        lines += ["", "## 可能的走向", ""] + [f"- {d}" for d in overview.get("possible_directions", [])]
        lines += ["", "## 结局", ""]
        for e in overview.get("endings", []):
            lines += [f"- **{e.get('name', '')}**（{e.get('type', '')}）：{e.get('description', '')}"]
        lines += ["", "## GM 坑点提示", ""] + [f"- {p}" for p in overview.get("gm_pitfalls", [])]

    locations = prep.get("locations")
    if locations:
        lines += ["", "# 信息集合（场景卡）", ""]
        for loc in locations.get("locations", []):
            lines += [f"## {loc.get('name', '')}（p{loc.get('page', '?')}）"]
            if loc.get("read_aloud"):
                lines += ["> 🎙 可直接朗读", f"> {loc['read_aloud']}", ""]
            lines += [loc.get("description", ""), ""]
            if loc.get("npcs"):
                lines += [f"- 在场：{', '.join(loc['npcs'])}"]
            for it in loc.get("info", []):
                lines += [f"### {it.get('title', '')}",
                          it.get("description", ""), ""]
                for x in it.get("disclosure", []):
                    lines += [f"- 信息披露：{x}"]
                lines += [""]
            for x in loc.get("extras", []):
                lines += [f"- 补充：{x}"]
            lines += [""]

    encounters = prep.get("encounters")
    if encounters:
        lines += ["", "# 临场素材（GM 即兴工具箱）", ""]
        for tt in encounters.get("tools", []):
            lines += [f"### [{tt.get('category', '素材')}]"
                      + (f"（p{tt.get('page', '?')}）" if tt.get("page") else ""),
                      f"- 何时用：{tt.get('situation', '')}"]
            if tt.get("scene"):
                lines += [f"- 🎙 遭遇画面：{tt['scene']}"]
            if tt.get("choice"):
                lines += [f"- 抉择：{tt['choice']}"]
            if tt.get("consequence"):
                lines += [f"- 后果：{tt['consequence']}"]
            if tt.get("material"):
                lines += [f"- 素材：{tt['material']}"]
            lines += [""]
        if encounters.get("loose_threads"):
            lines += ["## 松散线索（随手抛出，不必回收）", ""]
            lines += [f"- {x}" for x in encounters["loose_threads"]]
    return "\n".join(lines)
