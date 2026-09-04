# Maintenance: modern Python project migration

## Decision

The project is now managed as an application with `pyproject.toml` and a committed
`uv.lock`. Runtime dependencies live in the project dependency list. Maintainer
tools live in the `dev` dependency group.

The project keeps its existing direct `scripts/test_*.py` regression checks. The
migration does not introduce a package layout, a pytest collection convention, or a
pre-commit hook solely for tooling symmetry.

## User path

- `start.bat` downloads a pinned, SHA-256-verified uv release into the per-user
  `%LOCALAPPDATA%\TRPG-Prep\uv` cache.
- uv manages a Python 3.11 runtime and a user-local application environment.
- `uv sync --locked --no-dev` installs only application dependencies.
- `start.vbs` keeps the hidden daily-use path and opens the browser after the local
  server responds, rather than after a fixed two-second delay.

The first run needs network access to GitHub and PyPI. Later runs reuse uv, Python,
package, and virtual-environment caches. A user-installed Python remains supported
for development, but is not required by the launcher.

## Maintainer path

```powershell
uv sync --all-groups
uv run ruff check .
uv run ty check backend
uv run python scripts/test_new_project_contract.py
uv run python -m compileall -q backend scripts
node --check frontend/workbench.js
```

Use `uv add` and `uv remove` for dependency changes. Do not recreate
`backend/requirements.txt`; the lock file is the reproducibility record for this
application. The initial `ty` configuration is intentionally gradual: the existing
backend has several broad legacy annotations, so noisy type categories are ignored
until they can be tightened at stable public seams.

## Verification target

The migration is complete when the locked environment imports all runtime modules,
the focused regression checks pass, both JavaScript syntax checks pass, and the
launcher scripts pass static/smoke checks on Windows.
