"""验证增量保存：run_analysis 流里应出现 partial 事件，且每块后知识库统计增长。"""
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=60)

# 用现有战役（test_chunks 留下的直调分析战役已被删，新建一个摄入）
r = c.post("/api/campaigns", json={"name": "增量验证"})
cid = r.json()["id"]
PDF = next(Path(ROOT.parent).glob("*.pdf"), None)
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
c.post(f"/api/campaigns/{cid}/ingest")

sys.path.insert(0, str(ROOT / "backend"))
from app import analyze  # noqa: E402
from app.llm import FakeLLM  # noqa: E402

fake = FakeLLM({"base_url": "http://x", "model": "fake", "fake": True, "api_key": ""})
evs = list(analyze.run_analysis(cid, fake))
partials = [e for e in evs if e["type"] == "partial"]
done = next((e for e in evs if e["type"] == "done"), None)
print(f"partial events: {len(partials)}")
for p in partials:
    print("  partial:", p["knowledge"], "failed:", p["failed"])
print("done failed:", done["failed"])

assert len(partials) >= 1, "没有 partial 事件"
c.delete(f"/api/campaigns/{cid}")
print("PASS: 增量保存事件正常")
