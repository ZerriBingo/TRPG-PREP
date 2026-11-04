"""Run one repository check with an isolated database path and no model transport.

Usage:
    python scripts/run_isolated_test.py scripts/test_something.py [args...]

Two guarantees, both set before the application package is imported:

* `storage.DB_PATH` points at a fresh temporary file, so a check — including an older one
  that re-initialises the database in a `finally` block — can never reach the developer's
  own `data/` directory.  On the way out the path is set back to that same temporary file,
  never to a previously recorded value.
* `LLMClient._worker_call`, the single place a provider request leaves the process, raises
  immediately.  A check that uses the fake client is unaffected (it never goes through the
  worker), and a check that wants its own stub simply patches the same method.

The target is resolved against this repository, so the same command works from a clean
checkout: no absolute path, no `output/` dependency, no test switch, no framework.
"""
from __future__ import annotations

import runpy
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/run_isolated_test.py <script> [args...]",
              file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    target = (root / sys.argv[1]).resolve()
    if not target.exists():
        print(f"target not found: {target}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(root))

    from backend.app import llm, storage

    def refuse(*args, **kwargs):  # pragma: no cover - the refusal is the evidence
        raise RuntimeError("isolated check: model requests are forbidden")

    llm.LLMClient._worker_call = refuse
    with tempfile.TemporaryDirectory(prefix="trpg-isolated-") as directory:
        storage.DB_PATH = Path(directory) / "app.db"
        sys.argv = [str(target), *sys.argv[2:]]
        try:
            runpy.run_path(str(target), run_name="__main__")
        finally:
            storage.DB_PATH = Path(directory) / "app.db"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
