"""Report what the source-management listing costs, split by component.

Read-only. The database is copied through SQLite's backup API into a temporary
file, so the working database is never opened for writing; PDFs are only read.

R1 of the takeover audit measured this listing at tens of seconds while
``/api/domain/workspaces`` answered in about a tenth of one, and the workbench
used to wait for both. This report keeps that measurement reproducible and splits
it per item, which is what decides whether a metadata cache or a reference index
is justified.

Usage:
    python scripts/report_source_metadata_cost.py
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import main as app_main  # noqa: E402
from backend.app import storage  # noqa: E402

DB = ROOT / "data" / "app.db"


def timed(function):
    started = time.perf_counter()
    value = function()
    return value, (time.perf_counter() - started) * 1000


def isolated_copy() -> Path:
    """Copy through SQLite so a live WAL database is read consistently."""
    directory = Path(tempfile.mkdtemp(prefix="source-cost-"))
    target = directory / "app.db"
    source = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def main() -> None:
    if not DB.exists():
        print(f"NO_DATABASE {DB}")
        return
    copy = isolated_copy()
    original = storage.DB_PATH
    storage.DB_PATH = copy
    try:
        print(f"database copy: {copy} ({copy.stat().st_size / 1024 / 1024:.1f} MB)")
        print(f"upload directory: {storage.UPLOAD_DIR}")
        print()

        workspaces, workspaces_ms = timed(app_main.get_domain_workspaces)
        print(f"GET /api/domain/workspaces        {workspaces_ms:8.0f} ms  ({len(workspaces['workspaces'])} projects)")

        if workspaces["workspaces"]:
            workspace_id = workspaces["workspaces"][0]["id"]
            _, workbench_ms = timed(lambda: app_main.get_domain_workbench(workspace_id))
            print(f"GET /api/domain/workbench         {workbench_ms:8.0f} ms  ({workspace_id})")
            _, session_ms = timed(lambda: app_main.get_domain_session(workspace_id))
            print(f"GET /api/domain/examples/…/session{session_ms:8.0f} ms")

        uploads = app_main._pdf_files_under(storage.UPLOAD_DIR)
        print()
        print("per upload")
        totals = {"references": 0.0, "sha256": 0.0, "page_count": 0.0}
        for relative in uploads:
            path = app_main._uploaded_pdf_path(relative)
            references, references_ms = timed(lambda: storage.source_file_references(relative))
            _, sha_ms = timed(lambda: storage.file_sha256(path))
            pages, pages_ms = timed(lambda: page_count_of(path))
            for key, value in (("references", references_ms), ("sha256", sha_ms), ("page_count", pages_ms)):
                totals[key] += value
            print(
                f"  {Path(relative).name[:44]:44} references {references_ms:8.0f} ms"
                f" · sha256 {sha_ms:6.0f} ms · pages {pages_ms:5.0f} ms"
                f" · {pages} pages · {len(references)} reference(s)"
            )

        _, listing_ms = timed(app_main.get_domain_source_files)
        print()
        print(f"GET /api/domain/source-files      {listing_ms:8.0f} ms  ({len(uploads)} uploads)")
        print(f"  reference scans                 {totals['references']:8.0f} ms")
        print(f"  sha256                          {totals['sha256']:8.0f} ms")
        print(f"  page counts                     {totals['page_count']:8.0f} ms")
    finally:
        storage.DB_PATH = original
        shutil.rmtree(copy.parent, ignore_errors=True)


def page_count_of(path: Path) -> int | None:
    import pymupdf as fitz

    try:
        document = fitz.open(path)
    except Exception:
        return None
    try:
        return document.page_count
    finally:
        document.close()


if __name__ == "__main__":
    main()
