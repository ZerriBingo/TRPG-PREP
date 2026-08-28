"""skill 加载器：程序运行时读取 trpg-prep skill 的 SKILL.md 与 schemas/。

让「方法论」以文件形式存在并随程序分发，而不是散落在代码里的 prompt 字符串。
"""
from __future__ import annotations

import json
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "trpg-prep"
SCHEMA_DIR = SKILL_DIR / "schemas"

_MD_CACHE: str | None = None


def skill_md() -> str:
    global _MD_CACHE
    if _MD_CACHE is None:
        _MD_CACHE = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    return _MD_CACHE


def schema(name: str) -> dict:
    path = SCHEMA_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def schema_text(name: str) -> str:
    return json.dumps(schema(name), ensure_ascii=False, indent=2)


def knowledge_schema() -> dict:
    return schema("knowledge")


def parts() -> dict[str, dict]:
    """三部分 schema：overview / locations / encounters。"""
    return {n: schema(n) for n in ("overview", "locations", "encounters")}
