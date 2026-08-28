"""验证 _filter_kb：各部分的字段过滤 + 大小（应 < 30000 不截断）。"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.generate import _filter_kb  # noqa: E402
from app.analyze import knowledge_summary_text  # noqa: E402

conn = sqlite3.connect(str(ROOT / "data/app.db"))
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT data FROM knowledge WHERE campaign_id = 11").fetchone()
conn.close()
kb = json.loads(row["data"])

for part in ("overview", "locations", "encounters"):
    fk = _filter_kb(kb, part)
    s = knowledge_summary_text(fk)
    print(f"{part}: keys={list(fk.keys())} chars={len(s)} 截断={'是!!' if len(s) >= 30000 else '否'}")
    if part == "overview":
        print("  story 在 prompt 里:", "story" in s and kb.get("story")[:20] in s)
