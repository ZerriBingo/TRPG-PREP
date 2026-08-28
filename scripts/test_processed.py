"""processed 持久化验证：FakeLLM 分析后，已成功分块标题应可从 storage 读出。"""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import analyze, storage  # noqa: E402
from app.llm import FakeLLM  # noqa: E402

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=60)
r = c.post("/api/campaigns", json={"name": "processed验证"})
cid = r.json()["id"]
PDF = next(Path(ROOT.parent).glob("*.pdf"), None)
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
c.post(f"/api/campaigns/{cid}/ingest")

fake = FakeLLM({"base_url": "http://x", "model": "fake", "fake": True, "api_key": ""})
list(analyze.run_analysis(cid, fake))

titles = storage.load_processed_titles(cid)
print("processed titles:", titles)
assert len(titles) >= 2, f"应有多个成功块，实际 {titles}"

# get_campaign 端点也应返回
d = c.get(f"/api/campaigns/{cid}").json()
print("api processed_titles:", d.get("processed_titles"))
assert d.get("processed_titles") == titles

c.delete(f"/api/campaigns/{cid}")
print("PASS: processed 持久化 + API 返回正常")
