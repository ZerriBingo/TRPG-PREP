"""The materialization loop must stop after repeated upstream failures.

The loop lives inline in _generate_hierarchical_artifacts, so this test inspects
its parsed structure instead of driving a full job through the pipeline.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import artifacts as a  # noqa: E402


def _materialize_loop(func: ast.FunctionDef) -> ast.For:
    for node in ast.walk(func):
        if not isinstance(node, ast.For):
            continue
        if any(
            isinstance(child, ast.Constant) and child.value == "materialize"
            for child in ast.walk(node)
        ):
            return node
    raise AssertionError("materialize loop not found")


def main():
    limit = a.MAX_CONSECUTIVE_MATERIALIZE_FAILURES
    assert limit == 5, limit

    source = (ROOT / "backend" / "app" / "artifacts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_generate_hierarchical_artifacts"
    )
    loop = _materialize_loop(func)
    body = ast.unparse(loop)

    assert "consecutive_failures += 1" in body, body
    assert "MAX_CONSECUTIVE_MATERIALIZE_FAILURES" in body, body
    assert "raise ArtifactGenerationError" in body, body
    # A success must clear the run so isolated failures never abort the job.
    assert "consecutive_failures = 0" in body, body

    before_loop = ast.unparse(func).split(ast.unparse(loop))[0]
    assert "consecutive_failures = 0" in before_loop, before_loop[-500:]

    handler = next(
        node
        for node in ast.walk(loop)
        if isinstance(node, ast.Try)
        and any(
            isinstance(item, ast.AugAssign)
            and isinstance(item.target, ast.Name)
            and item.target.id == "consecutive_failures"
            for item in node.handlers[0].body
        )
    )
    handler_body = ast.unparse(handler.handlers[0])
    assert "raise ArtifactGenerationError" in handler_body, handler_body
    assert "consecutive_failures >= MAX_CONSECUTIVE_MATERIALIZE_FAILURES" in handler_body, handler_body

    print("PASS: materialization aborts after five consecutive failures")


if __name__ == "__main__":
    main()
