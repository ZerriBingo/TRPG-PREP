"""Importing the ASGI app must not touch persistence; starting it must initialise it."""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _lifespan_calls_init_db() -> bool:
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            body = ast.unparse(node)
            return "storage.init_db()" in body
    return False


def _module_level_init_db() -> bool:
    source = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if "init_db" in ast.unparse(node.value):
                return True
    return False


def main():
    # Importing the app previously ran CREATE TABLE against the real database.
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "fresh.db"
        marker = Path(directory) / "result.txt"
        code = (
            "import pathlib, sys\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from backend.app import storage\n"
            f"storage.DB_PATH = pathlib.Path(r'{target}')\n"
            "import backend.app.main  # noqa: F401\n"
            f"pathlib.Path(r'{marker}').write_text("
            "'EXISTS=' + str(storage.DB_PATH.exists()))\n"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert marker.read_text().strip() == "EXISTS=False", marker.read_text()

    assert not _module_level_init_db(), "main.py must not initialise at import time"
    assert _lifespan_calls_init_db(), "lifespan must initialise persistence on start"

    print("PASS: importing the ASGI app leaves the database untouched and startup initialises it")


if __name__ == "__main__":
    main()
