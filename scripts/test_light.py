"""实测：无光的灯塔全流程（摄入→分析→三部分生成），逐块计时，真实 LLM。结果保留战役 + dump 产物。"""
import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT.parent / "无光的灯塔.pdf"
OUT = ROOT / "data" / "light_results"
OUT.mkdir(parents=True, exist_ok=True)
c = httpx.Client(base_url=BASE, timeout=600)

t0 = time.time()


def ts():
    return f"[{time.time() - t0:7.1f}s]"


# 1) 建战役 + 上传 + 摄入
r = c.post("/api/campaigns", json={"name": "无光的灯塔（实测）"})
cid = r.json()["id"]
print(ts(), "campaign", cid)
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
t_ing = time.time()
rep = c.post(f"/api/campaigns/{cid}/ingest").json()
print(ts(), f"摄入 {time.time()-t_ing:.1f}s: {rep['total_pages']}页, {rep['chunk_count']}块")
for ch in rep["chunks"]:
    print(f"  块{ch['idx']}: {ch['title']}（{ch['pages']}页, {ch['chars']}字符）")

# 2) 分析
print(ts(), "=== 开始分析 ===")
r = c.post(f"/api/campaigns/{cid}/analyze")
chunk_t = {}
for line in r.text.splitlines():
    if not line.startswith("data: "):
        continue
    ev = json.loads(line[6:])
    if ev["type"] == "progress":
        chunk_t[ev["chunk"]] = time.time()
        print(ts(), f"  块完成 {ev['current']}/{ev['total']}: {ev['chunk']}")
    elif ev["type"] == "warn":
        dt = time.time() - chunk_t.get(ev["chunk"], t0)
        print(ts(), f"  ✗ {ev['chunk']}（{dt:.0f}s）: {ev['message'][:100]}")
    elif ev["type"] == "done":
        print(ts(), f"分析完成: {ev['knowledge']} failed={ev['failed']}")
        (OUT / "knowledge.json").write_text(
            json.dumps(ev["data"], ensure_ascii=False, indent=2), encoding="utf-8")
print(ts(), f"分析阶段耗时: {time.time()-t0:.0f}s")

# 3) 生成三部分
for part in ("overview", "locations", "encounters"):
    t_p = time.time()
    print(ts(), f"=== 生成 {part} ===")
    r = c.post(f"/api/campaigns/{cid}/generate/{part}")
    ok = False
    for line in r.text.splitlines():
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        if ev["type"] == "progress":
            print(ts(), f"  章节 {ev['current']}/{ev['total']}: {ev['chunk']}")
        elif ev["type"] == "stage_done":
            print(ts(), f"  ✓ {ev['chunk']} 完成（{ev['count']} 项）")
        elif ev["type"] == "warn":
            print(ts(), f"  ✗ {ev['chunk']}: {ev['message'][:100]}")
        elif ev["type"] == "done":
            d = ev["data"]
            (OUT / f"{part}.json").write_text(
                json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            if part == "overview":
                print(ts(), f"  done: title={d.get('title')} acts={len(d.get('acts', []))} endings={len(d.get('endings', []))}")
            elif part == "locations":
                print(ts(), f"  done: locations={len(d.get('locations', []))}")
                for loc in d.get("locations", [])[:10]:
                    print(f"    - {loc.get('name')}（p{loc.get('page')}）read_aloud={bool(loc.get('read_aloud'))} info={len(loc.get('info', []))}")
            elif part == "encounters":
                print(ts(), f"  done: tools={len(d.get('tools', []))} loose={len(d.get('loose_threads', []))}")
            ok = True
    print(ts(), f"  {part} 耗时 {time.time()-t_p:.0f}s ok={ok}")

print(ts(), f"总耗时 {time.time()-t0:.0f}s（战役 {cid} 已保留，产物在 data/light_results/）")
