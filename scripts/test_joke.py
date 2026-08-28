"""致命玩笑全流程实测（显式选致命玩笑 PDF）。"""
import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
pdfs = [p for p in Path(ROOT.parent).glob("*.pdf") if "La Broma" in p.name]
PDF = pdfs[0] if pdfs else None
assert PDF, "致命玩笑 PDF 未找到"
c = httpx.Client(base_url=BASE, timeout=1800)
t0 = time.time()


def ts():
    return f"[{time.time()-t0:7.0f}s]"


r = c.post("/api/campaigns", json={"name": "致命玩笑（实测2）"})
cid = r.json()["id"]
print(ts(), "campaign", cid, "pdf:", PDF.name, flush=True)
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
rep = c.post(f"/api/campaigns/{cid}/ingest").json()
print(ts(), f"摄入: {rep['total_pages']}页 {rep['chunk_count']}块", flush=True)
for ch in rep["chunks"][:40]:
    print(f"  块{ch['idx']}: {ch['title']}（{ch['pages']}页 {ch['chars']}字）", flush=True)

print(ts(), "=== 分析 ===", flush=True)
r = c.post(f"/api/campaigns/{cid}/analyze")
for line in r.text.splitlines():
    if not line.startswith("data: "):
        continue
    ev = json.loads(line[6:])
    if ev["type"] == "progress":
        print(ts(), f"块 {ev['current']}/{ev['total']}: {ev['chunk']}", flush=True)
    elif ev["type"] == "warn":
        print(ts(), f"[X] {ev['chunk']}: {ev['message'][:90]}", flush=True)
    elif ev["type"] == "done":
        print(ts(), f"分析完成: {ev['knowledge']} failed={ev['failed']}", flush=True)
print(ts(), "分析阶段结束", flush=True)

for part in ("overview", "locations", "encounters"):
    t_p = time.time()
    print(ts(), f"=== 生成 {part} ===", flush=True)
    r = c.post(f"/api/campaigns/{cid}/generate/{part}")
    for line in r.text.splitlines():
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        if ev["type"] == "progress":
            print(ts(), f"  章节 {ev['current']}/{ev['total']}: {ev['chunk']}", flush=True)
        elif ev["type"] == "stage_done":
            print(ts(), f"  OK {ev['chunk']} ({ev['count']}项)", flush=True)
        elif ev["type"] == "warn":
            print(ts(), f"  [X] {ev['chunk']}: {ev['message'][:90]}", flush=True)
        elif ev["type"] == "done":
            d = ev["data"]
            n = len(d.get("locations", d.get("tools", d.get("acts", []))))
            print(ts(), f"  done {part}: {n}项 耗时{time.time()-t_p:.0f}s", flush=True)
    print(ts(), f"{part} 结束", flush=True)

print(ts(), f"全部完成 总耗时{time.time()-t0:.0f}s 战役{cid}", flush=True)
