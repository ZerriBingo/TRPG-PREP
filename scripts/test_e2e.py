"""端到端验证：起服后用假 LLM 跑通 配置→战役→上传→摄入→分析→三部分生成→导出。

用法: python scripts/test_e2e.py [base_url]
"""
import json
import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PDF = next(Path(__file__).resolve().parent.parent.parent.glob("*.pdf"), None)
if PDF is None:
    sys.exit("未找到模组 PDF")

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    mark = "PASS" if cond else "FAIL"
    if cond:
        ok += 1
    else:
        fail += 1
    print(f"[{mark}] {name} {extra}")


def sse_events(resp):
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            yield json.loads(line[6:])


def main():
    client = httpx.Client(base_url=BASE, timeout=300)

    # 0. 首页（静态前端）
    r = client.get("/")
    check("GET / 返回前端", r.status_code == 200 and "TRPG 备团助手" in r.text)

    # 1. 配置：默认假模式
    r = client.get("/api/config")
    cfg = r.json()
    check("默认假数据模式", cfg.get("fake") is True, f"fake={cfg.get('fake')}")

    # 2. 新建战役
    r = client.post("/api/campaigns", json={"name": "致命玩笑（验证）"})
    cid = r.json()["id"]
    check("新建战役", r.status_code == 200 and cid, f"id={cid}")

    # 3. 上传真实 PDF
    with PDF.open("rb") as f:
        r = client.post(f"/api/campaigns/{cid}/upload",
                        files={"file": (PDF.name, f, "application/pdf")})
    check("上传 PDF", r.status_code == 200 and r.json().get("status") == "uploaded",
          f"pdf={PDF.name}")

    # 4. 摄入
    r = client.post(f"/api/campaigns/{cid}/ingest")
    rep = r.json()
    check("摄入报告", rep.get("total_pages") == 160, f"pages={rep.get('total_pages')}")
    check("章节检测", len(rep.get("structure", [])) >= 4,
          f"headings={[h['title'] for h in rep.get('structure', [])]}")
    check("分块", rep.get("chunk_count", 0) >= 4, f"chunks={rep.get('chunk_count')}")
    scan = rep.get("scan_pages", [])
    print(f"      (文本页 {rep.get('text_pages')}, 图片页 {len(scan)}: {scan[:10]}...)")

    # 5. 分析（SSE）
    r = client.post(f"/api/campaigns/{cid}/analyze")
    events = list(sse_events(r))
    done = next((e for e in events if e["type"] == "done"), None)
    progress = [e for e in events if e["type"] == "progress"]
    check("分析 SSE 完成", done is not None and r.status_code == 200,
          f"events={len(events)} progress={len(progress)}")
    if done:
        print(f"      知识库: {done.get('knowledge')}")
        check("知识库非空", sum(done.get("knowledge", {}).values()) > 0)

    # 6. 生成三部分（SSE）
    for part in ("overview", "locations", "encounters"):
        r = client.post(f"/api/campaigns/{cid}/generate/{part}", json={"instruction": None})
        events = list(sse_events(r))
        done = next((e for e in events if e["type"] == "done"), None)
        err = next((e for e in events if e["type"] == "error"), None)
        check(f"生成 {part}", done is not None and err is None,
              f"tokens={len(events)}" + (f" err={err}" if err else ""))

    # 7. 读取备团
    r = client.get(f"/api/campaigns/{cid}/prep")
    prep = r.json()
    check("三部分齐全", set(prep) == {"overview", "locations", "encounters"}, f"parts={list(prep)}")

    # 8. 导出 Markdown
    r = client.get(f"/api/campaigns/{cid}/export")
    check("导出 Markdown", r.status_code == 200 and "故事总览" in r.text
          and "临场遭遇" in r.text, f"len={len(r.text)}")

    # 9. 局部重生成（带指令）
    r = client.post(f"/api/campaigns/{cid}/generate/encounters",
                    json={"instruction": "把高潮阶段再细化一个追逐遭遇"})
    done = next((e for e in sse_events(r) if e["type"] == "done"), None)
    check("指令重生成", done is not None)

    print(f"\n结果: {ok} 通过, {fail} 失败")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
