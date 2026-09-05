"""Static contract for transport and semantic-stage progress wording."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")

assert '" · 传输窗口 "' in source
assert '" · 全部传输窗口已完成 · 语义段归并 "' in source
assert "语义单元窗口" not in source

print("PASS: prep progress separates completed transport from semantic reduction")
