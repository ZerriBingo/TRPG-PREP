"""摄入管线：PDF → 逐页文本 → 扫描页检测 → 章节检测 → 分块。

设计依据（探针结果）：模组 PDF 通常有清晰的章节标题页与目录，
正文存在文本层，但封面/插图页可能只有图片（需 OCR 回退或跳过）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF

TEXT_THRESHOLD = 300      # 字符数 >= 此值视为文本页
LIGHT_THRESHOLD = 50      # 字符数 < 此值视为扫描/空白页

HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百零〇]+[章卷节]|"
    r"序幕|序章|序言|引子|楔子|终章|尾声|后记|"
    r"附录[一二三四五六七八九十百]?|附[录则]|"
    r"CHAPTER\s+\w+|PROLOGUE|EPILOGUE|APPENDIX\s*\w*"
    r")\s*[:：]?\s*(\S.*)?$",
    re.IGNORECASE,
)


@dataclass
class PageText:
    page: int          # 1-based
    text: str
    chars: int
    kind: str          # "text" | "light" | "scan"


@dataclass
class Chunk:
    idx: int
    title: str
    pages: str         # "7-12"
    kind: str          # "front" | "main" | "appendix"
    text: str
    scan_pages: list[int] = field(default_factory=list)


def classify(chars: int) -> str:
    if chars >= TEXT_THRESHOLD:
        return "text"
    if chars >= LIGHT_THRESHOLD:
        return "light"
    return "scan"


def extract_pages(pdf_path: str) -> list[PageText]:
    doc = fitz.open(pdf_path)
    pages: list[PageText] = []
    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        pages.append(PageText(page=i + 1, text=text, chars=len(text), kind=classify(len(text))))
    doc.close()
    return pages


def detect_headings(pages: list[PageText]) -> list[tuple[int, str]]:
    """扫描每页首个非空行是否为章节标题；返回 [(页号, 标题), ...] 按页序。"""
    headings: list[tuple[int, str]] = []
    for p in pages:
        first = next((ln.strip() for ln in p.text.splitlines() if ln.strip()), "")
        if not first or len(first) > 40:
            continue
        m = HEADING_RE.match(first)
        if m:
            rest = (m.group(2) or "").strip()
            headings.append((p.page, f"{m.group(1)}{('：' + rest) if rest else ''}"))
    return headings


def parse_toc(pages: list[PageText]) -> list[tuple[str, int]]:
    """从目录页解析『章节名 .... 页码』；仅作参考信息，不驱动分块。"""
    toc: list[tuple[str, int]] = []
    for p in pages:
        for ln in p.text.splitlines():
            parts = re.split(r"\.{2,}", ln.strip())
            if len(parts) >= 2 and parts[-1].strip().isdigit():
                title = "：".join(pp.strip() for pp in parts[:-1] if pp.strip())
                if title:
                    toc.append((title, int(parts[-1].strip())))
    return toc


def build_chunks(pages: list[PageText], headings: list[tuple[int, str]]) -> list[Chunk]:
    """按章节标题页切块。标题页之前的页面并入前言块；附录块单独标记。"""
    if not headings:
        # 无章节结构时整册为一块（通常不会发生）
        return [Chunk(idx=1, title="全文", pages=f"1-{pages[-1].page if pages else 0}",
                      kind="main", text="\n".join(f"〔第{p.page}页〕\n{p.text}" for p in pages),
                      scan_pages=[p.page for p in pages if p.kind == "scan"])]

    bounds = headings + [(pages[-1].page + 1 if pages else 0, "")]
    chunks: list[Chunk] = []
    idx = 0

    # 前言块：第一章节页之前
    first_heading_page = bounds[0][0]
    front = [p for p in pages if p.page < first_heading_page]
    if front and any(p.text for p in front):
        idx += 1
        chunks.append(Chunk(
            idx=idx, title="前言/目录", pages=f"{front[0].page}-{front[-1].page}", kind="front",
            text="\n".join(f"〔第{p.page}页〕\n{p.text}" for p in front if p.text),
            scan_pages=[p.page for p in front if p.kind == "scan"],
        ))

    for i in range(len(headings)):
        start = headings[i][0]
        end = bounds[i + 1][0] - 1
        seg = [p for p in pages if start <= p.page <= end]
        idx += 1
        kind = "appendix" if re.match(r"^(附录|附)", headings[i][1]) else "main"
        chunks.append(Chunk(
            idx=idx, title=headings[i][1], pages=f"{start}-{end}", kind=kind,
            text="\n".join(f"〔第{p.page}页〕\n{p.text}" for p in seg if p.text),
            scan_pages=[p.page for p in seg if p.kind == "scan"],
        ))
    return chunks


SUB_CHUNK_LIMIT = 4000  # 单块文本上限（与分析 CHUNK_LIMIT 一致）


def _split_oversized_chunks(full: list[dict], limit: int = SUB_CHUNK_LIMIT) -> list[dict]:
    """把超限章节按段落切成多个子块（保证分析/生成覆盖全文，不因截断丢内容）。"""
    out: list[dict] = []
    for ch in full:
        if len(ch["text"]) <= limit:
            out.append(ch)
            continue
        text = ch["text"]
        paras = re.split(r"\n\s*\n", text)
        parts: list[str] = []
        cur = ""
        for para in paras:
            while len(para) > limit:  # 段落本身超限：先硬切
                parts.append(para[:limit])
                para = para[limit:]
            if len(cur) + len(para) + 2 > limit and cur:
                parts.append(cur)
                cur = para
            else:
                cur = f"{cur}\n\n{para}" if cur else para
        if cur:
            parts.append(cur)
        if len(parts) <= 1:
            parts = [text[i:i + limit] for i in range(0, len(text), limit)]
        for j, part in enumerate(parts):
            out.append({
                **ch,
                "idx": ch["idx"] * 100 + j + 1,
                "title": f"{ch['title']}（{j + 1}/{len(parts)}）",
                "text": part,
            })
    return out


def run_ingest(pdf_path: str) -> tuple[dict, list[dict]]:
    """完整摄入：提取 → 检测 → 分块。

    返回 (报告, 分块全文, 页级原文)，报告供 UI 展示，分块全文与页级原文写入数据库。
    """
    pages = extract_pages(pdf_path)
    headings = detect_headings(pages)
    toc = parse_toc(pages)
    chunks = build_chunks(pages, headings)

    text_pages = sum(1 for p in pages if p.kind == "text")
    scan_pages = [p.page for p in pages if p.kind == "scan"]
    light_pages = [p.page for p in pages if p.kind == "light"]

    full = [
        {"idx": c.idx, "title": c.title, "pages": c.pages, "kind": c.kind, "text": c.text}
        for c in chunks
    ]
    full = _split_oversized_chunks(full)
    report = {
        "total_pages": len(pages),
        "text_pages": text_pages,
        "scan_pages": scan_pages,
        "light_pages": light_pages,
        "structure": [{"page": p, "title": t} for p, t in headings],
        "toc": toc[:40],
        "chunks": [
            {"idx": c["idx"], "title": c["title"], "pages": c["pages"], "kind": c["kind"],
             "chars": len(c["text"]), "scan_pages": []}
            for c in full
        ],
        "chunk_count": len(full),
    }
    page_texts = [(pg.page, pg.text) for pg in pages]
    return report, full, page_texts
