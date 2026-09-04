"""存储层：SQLite（配置/战役/分块/知识库/备团产物）+ 文件（上传的 PDF）。

目录布局（trpg-prep/ 下）：
  data/app.db      SQLite 数据库
  data/uploads/    上传的模组 PDF
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    pdf_name   TEXT,
    pdf_path   TEXT,
    status     TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    campaign_id INTEGER NOT NULL,
    idx         INTEGER NOT NULL,
    title       TEXT,
    pages       TEXT,
    kind        TEXT NOT NULL DEFAULT 'main',
    text        TEXT NOT NULL,
    PRIMARY KEY (campaign_id, idx)
);
CREATE TABLE IF NOT EXISTS reports (
    campaign_id INTEGER PRIMARY KEY,
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge (
    campaign_id INTEGER PRIMARY KEY,
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prep (
    campaign_id INTEGER NOT NULL,
    part        TEXT NOT NULL,
    data        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (campaign_id, part)
);
CREATE TABLE IF NOT EXISTS processed (
    campaign_id INTEGER PRIMARY KEY,
    titles      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS page_text (
    campaign_id INTEGER NOT NULL,
    page        INTEGER NOT NULL,
    text        TEXT NOT NULL,
    PRIMARY KEY (campaign_id, page)
);
CREATE TABLE IF NOT EXISTS domain_bundles (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_states (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prep_jobs (
    id         TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_tasks (
    id              TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    data            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_runs (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    attempt    INTEGER NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, attempt)
);
CREATE TABLE IF NOT EXISTS shadow_candidates (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    review_state TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_promotions (
    candidate_id    TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    fact_id         TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    data            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(workspace_id, fact_id)
);
CREATE TABLE IF NOT EXISTS artifact_jobs (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    status       TEXT NOT NULL,
    data         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_job_steps (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    stage      TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    status     TEXT NOT NULL,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, stage, step_index, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_shadow_runs_task_id ON shadow_runs(task_id, attempt);
CREATE INDEX IF NOT EXISTS idx_shadow_candidates_review ON shadow_candidates(review_state, created_at);
CREATE INDEX IF NOT EXISTS idx_shadow_candidates_task_id ON shadow_candidates(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_prep_jobs_status ON prep_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_candidate_promotions_workspace ON candidate_promotions(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifact_jobs_workspace ON artifact_jobs(workspace_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_artifact_job_steps_job ON artifact_job_steps(job_id, stage, step_index);
"""


def _conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_contains_source_file(value: object, source_file: str | set[str]) -> bool:
    """Inspect persisted JSON without coupling the storage layer to domain models."""
    raw_variants = {source_file} if isinstance(source_file, str) else source_file
    variants: set[str] = set()
    for item in raw_variants:
        if not isinstance(item, str):
            continue
        normalized = item.strip().replace("\\", "/")
        if not normalized:
            continue
        variants.add(normalized)
        try:
            variants.add(str((PROJECT_ROOT / normalized).resolve()).replace("\\", "/"))
        except (OSError, ValueError):
            pass

    def matches(item: object) -> bool:
        if not isinstance(item, str):
            return False
        normalized = item.strip().replace("\\", "/")
        if normalized in variants:
            return True
        try:
            absolute = str((PROJECT_ROOT / normalized).resolve()).replace("\\", "/")
        except (OSError, ValueError):
            return False
        return absolute in variants

    if isinstance(value, dict):
        values = {value.get("source_file"), value.get("file"), value.get("pdf_path")}
        if any(matches(item) for item in values):
            return True
        return any(_json_contains_source_file(item, variants) for item in value.values())
    if isinstance(value, list):
        return any(_json_contains_source_file(item, variants) for item in value)
    return False


def _json_load(value: object) -> object:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}


def source_file_references(source_file: str) -> list[dict[str, str]]:
    """Return durable user-facing reasons a local source cannot be deleted."""
    source_file = str(source_file or "").strip().replace("\\", "/")
    if not source_file:
        return []
    absolute = str((PROJECT_ROOT / source_file).resolve()).replace("\\", "/")
    variants = {source_file, absolute}
    references: list[dict[str, str]] = []

    def search_rows(table: str, columns: tuple[str, ...] = ("data",)) -> list[sqlite3.Row]:
        """Use SQLite text matching to avoid decoding every large JSON blob."""
        if not variants:
            return []
        clauses = " OR ".join(
            f"{column} LIKE ?" for column in columns for _ in variants
        )
        params = [f"%{variant}%" for column in columns for variant in variants]
        return conn.execute(
            f"SELECT * FROM {table} WHERE {clauses}", params
        ).fetchall()

    def add(kind: str, identifier: str, label: str) -> None:
        key = (kind, identifier)
        if any((item["kind"], item["id"]) == key for item in references):
            return
        references.append({"kind": kind, "id": identifier, "label": label})

    conn = _conn()
    try:
        for row in search_rows("prep_jobs"):
            data = _json_load(row["data"])
            if _json_contains_source_file(data, variants):
                add("prep_job", row["id"], "备团任务")
        for row in search_rows("shadow_tasks"):
            data = _json_load(row["data"])
            if _json_contains_source_file(data, variants):
                add("shadow_task", row["id"], "候选分析任务")
        for row in search_rows("shadow_candidates"):
            data = _json_load(row["data"])
            if _json_contains_source_file(data, variants):
                add("candidate", row["id"], "候选内容")
        for row in search_rows("candidate_promotions"):
            data = _json_load(row["data"])
            if _json_contains_source_file(data, variants):
                add("promotion", row["candidate_id"], "已提升事实")
        for row in search_rows("domain_bundles"):
            data = _json_load(row["data"])
            if _json_contains_source_file(data, variants):
                add("workspace", row["id"], "书架工作区")
        for row in search_rows("artifact_jobs"):
            data = _json_load(row["data"])
            workspace_id = data.get("workspace_id") if isinstance(data, dict) else None
            if _json_contains_source_file(data, variants):
                add("artifact_job", row["id"], "备团产物任务")
            elif workspace_id:
                bundle = conn.execute(
                    "SELECT data FROM domain_bundles WHERE id = ?", (workspace_id,)
                ).fetchone()
                if bundle and _json_contains_source_file(_json_load(bundle["data"]), variants):
                    add("artifact_job", row["id"], "备团产物任务")
        for row in search_rows("campaigns", ("pdf_path",)):
            if _json_contains_source_file({"pdf_path": row["pdf_path"]}, variants):
                add("campaign", str(row["id"]), "旧版项目")
    finally:
        conn.close()
    return references


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- 领域运行包 ----------

def save_domain_bundle(bundle_id: str, data: dict) -> str:
    updated_at = now()
    conn = _conn()
    conn.execute(
        "INSERT INTO domain_bundles (id, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (bundle_id, json.dumps(data, ensure_ascii=False), updated_at),
    )
    conn.commit()
    conn.close()
    return updated_at


def load_domain_bundle(bundle_id: str) -> tuple[dict, str] | None:
    conn = _conn()
    row = conn.execute(
        "SELECT data, updated_at FROM domain_bundles WHERE id = ?", (bundle_id,)
    ).fetchone()
    conn.close()
    return (json.loads(row["data"]), row["updated_at"]) if row else None


def list_domain_bundles() -> list[tuple[dict, str]]:
    conn = _conn()
    rows = conn.execute(
        "SELECT data, updated_at FROM domain_bundles ORDER BY updated_at DESC, id ASC"
    ).fetchall()
    conn.close()
    return [(json.loads(row["data"]), row["updated_at"]) for row in rows]


def delete_domain_bundle(bundle_id: str) -> bool:
    conn = _conn()
    cursor = conn.execute("DELETE FROM domain_bundles WHERE id = ?", (bundle_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def delete_domain_workspace(bundle_id: str) -> bool:
    """Delete a saved bookshelf workspace and its project-scoped runtime data."""
    conn = _conn()
    try:
        had_records = any(
            conn.execute(query, (bundle_id,)).fetchone() is not None
            for query in (
                "SELECT 1 FROM domain_bundles WHERE id = ?",
                "SELECT 1 FROM candidate_promotions WHERE workspace_id = ? LIMIT 1",
                "SELECT 1 FROM artifact_jobs WHERE workspace_id = ? LIMIT 1",
                "SELECT 1 FROM session_states WHERE id = ?",
                "SELECT 1 FROM prep_jobs WHERE id = ?",
            )
        )

        conn.execute("DELETE FROM candidate_promotions WHERE workspace_id = ?", (bundle_id,))
        conn.execute(
            "DELETE FROM artifact_job_steps WHERE job_id IN "
            "(SELECT id FROM artifact_jobs WHERE workspace_id = ?)",
            (bundle_id,),
        )
        conn.execute("DELETE FROM artifact_jobs WHERE workspace_id = ?", (bundle_id,))
        conn.execute("DELETE FROM session_states WHERE id = ?", (bundle_id,))
        conn.execute("DELETE FROM domain_bundles WHERE id = ?", (bundle_id,))
        conn.commit()
        return had_records
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_workspace_instances(workspace_ids: list[str]) -> int:
    """Atomically remove explicit development workspace instances and owned rows."""
    targets = list(
        dict.fromkeys(
            value.strip()
            for value in workspace_ids
            if isinstance(value, str) and value.strip()
        )
    )
    if not targets:
        return 0

    target_set = set(targets)
    placeholders = ",".join("?" for _ in targets)
    conn = _conn()
    try:
        matched_prep_ids: list[str] = []
        shadow_task_ids: list[str] = []
        matched_targets: set[str] = set()
        prep_rows = conn.execute("SELECT id, data FROM prep_jobs").fetchall()
        for row in prep_rows:
            try:
                raw = json.loads(row["data"])
            except (TypeError, json.JSONDecodeError):
                raw = {}
            nested_workspace_id = raw.get("workspace_id") if isinstance(raw, dict) else None
            if row["id"] not in target_set and nested_workspace_id not in target_set:
                continue
            matched_prep_ids.append(row["id"])
            matched_targets.add(row["id"] if row["id"] in target_set else nested_workspace_id)
            windows = raw.get("windows", []) if isinstance(raw, dict) else []
            for window in windows:
                if not isinstance(window, dict):
                    continue
                for field_name in ("shadow_task_id", "consolidation_task_id"):
                    if window.get(field_name):
                        shadow_task_ids.append(window[field_name])

        for target in targets:
            checks = (
                ("SELECT 1 FROM domain_bundles WHERE id = ?", (target,)),
                ("SELECT 1 FROM candidate_promotions WHERE workspace_id = ? LIMIT 1", (target,)),
                ("SELECT 1 FROM artifact_jobs WHERE workspace_id = ? LIMIT 1", (target,)),
                ("SELECT 1 FROM session_states WHERE id = ?", (target,)),
                ("SELECT 1 FROM prep_jobs WHERE id = ?", (target,)),
            )
            if any(conn.execute(query, params).fetchone() is not None for query, params in checks):
                matched_targets.add(target)

        _delete_shadow_tasks(
            conn,
            shadow_task_ids,
            prep_job_ids=matched_prep_ids,
        )

        conn.execute(
            f"DELETE FROM candidate_promotions WHERE workspace_id IN ({placeholders})",
            targets,
        )
        conn.execute(
            f"DELETE FROM artifact_job_steps WHERE job_id IN "
            f"(SELECT id FROM artifact_jobs WHERE workspace_id IN ({placeholders}))",
            targets,
        )
        conn.execute(
            f"DELETE FROM artifact_jobs WHERE workspace_id IN ({placeholders})",
            targets,
        )
        conn.execute(
            f"DELETE FROM session_states WHERE id IN ({placeholders})",
            targets,
        )
        conn.execute(
            f"DELETE FROM domain_bundles WHERE id IN ({placeholders})",
            targets,
        )
        if matched_prep_ids:
            prep_placeholders = ",".join("?" for _ in matched_prep_ids)
            conn.execute(
                f"DELETE FROM prep_jobs WHERE id IN ({prep_placeholders})",
                matched_prep_ids,
            )
        conn.commit()
        return len(matched_targets)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_session_state(session_id: str, data: dict) -> str:
    """Persist GM-only session state, including structured runtime review events."""
    updated_at = now()
    conn = _conn()
    conn.execute(
        "INSERT INTO session_states (id, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (session_id, json.dumps(data, ensure_ascii=False), updated_at),
    )
    conn.commit()
    conn.close()
    return updated_at


def load_session_state(session_id: str) -> tuple[dict, str] | None:
    """Load a saved GM session without joining it to player or external data."""
    conn = _conn()
    row = conn.execute(
        "SELECT data, updated_at FROM session_states WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return (json.loads(row["data"]), row["updated_at"]) if row else None


def delete_session_state(session_id: str) -> bool:
    conn = _conn()
    cursor = conn.execute("DELETE FROM session_states WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


# ---------- P1 影子模式（候选与复核队列，不接触运行包） ----------

def create_prep_job(data: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO prep_jobs (id, status, data, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            data["id"],
            data["status"],
            json.dumps(data, ensure_ascii=False),
            data["created_at"],
            data["updated_at"],
        ),
    )
    conn.commit()
    conn.close()


def save_prep_job(data: dict) -> None:
    conn = _conn()
    cursor = conn.execute(
        "UPDATE prep_jobs SET status = ?, data = ?, updated_at = ? WHERE id = ?",
        (
            data["status"],
            json.dumps(data, ensure_ascii=False),
            data["updated_at"],
            data["id"],
        ),
    )
    conn.commit()
    conn.close()
    if cursor.rowcount != 1:
        raise ValueError(f"unknown prep job: {data['id']}")


def load_prep_job(job_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT data FROM prep_jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def list_prep_jobs() -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT data FROM prep_jobs ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    conn.close()
    return [json.loads(row["data"]) for row in rows]


def _shadow_task_ids_for_prep_jobs(
    conn: sqlite3.Connection, prep_job_ids: list[str]
) -> list[str]:
    prefixes = tuple(
        f"{job_id}:" for job_id in dict.fromkeys(prep_job_ids) if job_id
    )
    if not prefixes:
        return []
    rows = conn.execute("SELECT id, idempotency_key FROM shadow_tasks").fetchall()
    return [
        row["id"]
        for row in rows
        if any(str(row["idempotency_key"]).startswith(prefix) for prefix in prefixes)
    ]


def _delete_shadow_tasks(
    conn: sqlite3.Connection,
    explicit_task_ids: list[str] | None = None,
    *,
    prep_job_ids: list[str] | None = None,
) -> None:
    task_ids = set(explicit_task_ids or [])
    task_ids.update(
        _shadow_task_ids_for_prep_jobs(conn, prep_job_ids or [])
    )
    normalized_ids = list(dict.fromkeys(task_id for task_id in task_ids if task_id))
    if not normalized_ids:
        return
    placeholders = ",".join("?" for _ in normalized_ids)
    conn.execute(
        f"DELETE FROM shadow_candidates WHERE task_id IN ({placeholders})",
        normalized_ids,
    )
    conn.execute(
        f"DELETE FROM shadow_runs WHERE task_id IN ({placeholders})",
        normalized_ids,
    )
    conn.execute(
        f"DELETE FROM shadow_tasks WHERE id IN ({placeholders})",
        normalized_ids,
    )


def delete_prep_job(
    job_id: str, shadow_task_ids: list[str] | None = None
) -> bool:
    """Delete one prep job and its isolated shadow rows in one transaction."""
    conn = _conn()
    try:
        _delete_shadow_tasks(
            conn,
            shadow_task_ids,
            prep_job_ids=[job_id],
        )
        cursor = conn.execute("DELETE FROM prep_jobs WHERE id = ?", (job_id,))
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_shadow_task(data: dict) -> bool:
    """Insert an idempotent shadow task without replacing an existing task."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO shadow_tasks (id, idempotency_key, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                data["id"],
                data["idempotency_key"],
                json.dumps(data, ensure_ascii=False),
                data["created_at"],
                data["updated_at"],
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()


def load_shadow_task(task_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT data FROM shadow_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def load_shadow_task_by_idempotency_key(idempotency_key: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT data FROM shadow_tasks WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def list_shadow_tasks() -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT data FROM shadow_tasks ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    conn.close()
    return [json.loads(row["data"]) for row in rows]


def delete_orphan_prep_shadow_tasks() -> int:
    """Remove analysis windows whose owning prep job no longer exists."""
    conn = _conn()
    try:
        prep_ids = {row["id"] for row in conn.execute("SELECT id FROM prep_jobs").fetchall()}
        orphan_ids: list[str] = []
        for row in conn.execute("SELECT id, data FROM shadow_tasks").fetchall():
            try:
                data = json.loads(row["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            key = str(data.get("idempotency_key", ""))
            owner = key.split(":", 1)[0] if key.startswith("prep_job_") else ""
            if owner and owner not in prep_ids:
                orphan_ids.append(row["id"])
        if not orphan_ids:
            return 0
        placeholders = ",".join("?" for _ in orphan_ids)
        conn.execute(f"DELETE FROM shadow_candidates WHERE task_id IN ({placeholders})", orphan_ids)
        conn.execute(f"DELETE FROM shadow_runs WHERE task_id IN ({placeholders})", orphan_ids)
        conn.execute(f"DELETE FROM shadow_tasks WHERE id IN ({placeholders})", orphan_ids)
        conn.commit()
        return len(orphan_ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_shadow_task(data: dict) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE shadow_tasks SET data = ?, updated_at = ? WHERE id = ?",
        (json.dumps(data, ensure_ascii=False), data["updated_at"], data["id"]),
    )
    conn.commit()
    conn.close()


def load_shadow_run(run_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT data FROM shadow_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def list_shadow_runs(task_id: str) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT data FROM shadow_runs WHERE task_id = ? ORDER BY attempt ASC", (task_id,)
    ).fetchall()
    conn.close()
    return [json.loads(row["data"]) for row in rows]


def list_shadow_candidates(
    task_id: str | None = None,
    review_state: str | None = None,
    queue_visibility: str | None = "review",
) -> list[dict]:
    conditions: list[str] = []
    params: list[str] = []
    if task_id is not None:
        conditions.append("task_id = ?")
        params.append(task_id)
    if review_state is not None:
        conditions.append("review_state = ?")
        params.append(review_state)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    conn = _conn()
    rows = conn.execute(
        "SELECT data FROM shadow_candidates" + where + " ORDER BY created_at ASC, id ASC",
        params,
    ).fetchall()
    conn.close()
    candidates = [json.loads(row["data"]) for row in rows]
    if queue_visibility is None:
        return candidates
    return [
        candidate
        for candidate in candidates
        if candidate.get("queue_visibility", "review") == queue_visibility
    ]


def load_shadow_candidate(candidate_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT data FROM shadow_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def save_shadow_candidate(data: dict) -> None:
    save_shadow_candidates([data])


def save_shadow_candidates(candidates: list[dict]) -> None:
    """Persist one atomic review update without touching tasks, runs, or runtime data."""
    if not candidates:
        return
    conn = _conn()
    try:
        for candidate in candidates:
            cursor = conn.execute(
                "UPDATE shadow_candidates SET data = ?, review_state = ? WHERE id = ?",
                (
                    json.dumps(candidate, ensure_ascii=False),
                    candidate["review_state"],
                    candidate["id"],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"unknown shadow candidate: {candidate['id']}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_shadow_task_visibility(task_id: str, queue_visibility: str) -> None:
    """Hide or expose a task and its candidates as one queue operation."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT data FROM shadow_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown shadow task: {task_id}")
        task = json.loads(row["data"])
        task["queue_visibility"] = queue_visibility
        task["updated_at"] = now()
        conn.execute(
            "UPDATE shadow_tasks SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(task, ensure_ascii=False), task["updated_at"], task_id),
        )
        rows = conn.execute(
            "SELECT id, data FROM shadow_candidates WHERE task_id = ?", (task_id,)
        ).fetchall()
        for candidate_row in rows:
            candidate = json.loads(candidate_row["data"])
            candidate["queue_visibility"] = queue_visibility
            conn.execute(
                "UPDATE shadow_candidates SET data = ? WHERE id = ?",
                (json.dumps(candidate, ensure_ascii=False), candidate_row["id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_shadow_candidates(
    task_id: str,
    removed_candidate_ids: list[str],
    replacements: list[dict],
) -> None:
    """Atomically replace current candidates for one shadow task.

    This is the persistence seam for candidate edit/split/merge. The removed
    rows must belong to the task; no other task's review queue can be touched.
    """
    removed = list(dict.fromkeys(item for item in removed_candidate_ids if item))
    if not removed or not replacements:
        raise ValueError("candidate replacement needs removed and replacement rows")
    conn = _conn()
    try:
        placeholders = ",".join("?" for _ in removed)
        rows = conn.execute(
            f"SELECT id FROM shadow_candidates WHERE task_id = ? AND id IN ({placeholders})",
            [task_id, *removed],
        ).fetchall()
        if {row["id"] for row in rows} != set(removed):
            raise ValueError("candidate replacement contains an unknown task candidate")
        conn.execute(
            f"DELETE FROM shadow_candidates WHERE task_id = ? AND id IN ({placeholders})",
            [task_id, *removed],
        )
        for candidate in replacements:
            if candidate.get("task_id") != task_id:
                raise ValueError("replacement candidate belongs to another task")
            conn.execute(
                "INSERT INTO shadow_candidates "
                "(id, task_id, run_id, review_state, data, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    candidate["id"],
                    candidate["task_id"],
                    candidate["run_id"],
                    candidate["review_state"],
                    json.dumps(candidate, ensure_ascii=False),
                    candidate["created_at"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_shadow_run_result(task: dict, run: dict, candidates: list[dict]) -> None:
    """Persist a completed/failed run and its candidates atomically."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE shadow_tasks SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(task, ensure_ascii=False), task["updated_at"], task["id"]),
        )
        conn.execute(
            "INSERT INTO shadow_runs (id, task_id, attempt, data, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (
                run["id"],
                run["task_id"],
                run["attempt"],
                json.dumps(run, ensure_ascii=False),
                run["started_at"],
                run["finished_at"] or run["started_at"],
            ),
        )
        for candidate in candidates:
            conn.execute(
                "INSERT INTO shadow_candidates "
                "(id, task_id, run_id, review_state, data, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data, review_state = excluded.review_state",
                (
                    candidate["id"],
                    candidate["task_id"],
                    candidate["run_id"],
                    candidate["review_state"],
                    json.dumps(candidate, ensure_ascii=False),
                    candidate["created_at"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_candidate_promotion(candidate_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT data FROM candidate_promotions WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def list_candidate_promotions(candidate_ids: list[str] | None = None) -> list[dict]:
    conn = _conn()
    if candidate_ids is None:
        rows = conn.execute(
            "SELECT data FROM candidate_promotions ORDER BY created_at ASC, candidate_id ASC"
        ).fetchall()
    elif not candidate_ids:
        rows = []
    else:
        cleaned_ids = list(dict.fromkeys(candidate_ids))
        placeholders = ",".join("?" for _ in cleaned_ids)
        rows = conn.execute(
            f"SELECT data FROM candidate_promotions WHERE candidate_id IN ({placeholders}) "
            "ORDER BY created_at ASC, candidate_id ASC",
            cleaned_ids,
        ).fetchall()
    conn.close()
    return [json.loads(row["data"]) for row in rows]


def save_candidate_promotion_result(
    workspace_id: str,
    bundle: dict,
    promotion: dict,
) -> str:
    """Atomically create a promotion audit row and update its bookshelf bundle."""
    updated_at = now()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO candidate_promotions "
            "(candidate_id, workspace_id, fact_id, evidence_status, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                promotion["candidate_id"],
                workspace_id,
                promotion["fact_id"],
                promotion["evidence_status"],
                json.dumps(promotion, ensure_ascii=False),
                promotion["created_at"],
            ),
        )
        conn.execute(
            "INSERT INTO domain_bundles (id, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (workspace_id, json.dumps(bundle, ensure_ascii=False), updated_at),
        )
        conn.commit()
        return updated_at
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- 产物草案任务 ----------

def create_artifact_job(data: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO artifact_jobs (id, workspace_id, status, data, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            data["id"],
            data["workspace_id"],
            data["status"],
            json.dumps(data, ensure_ascii=False),
            data["created_at"],
            data["updated_at"],
        ),
    )
    conn.commit()
    conn.close()


def save_artifact_job(data: dict) -> None:
    conn = _conn()
    cursor = conn.execute(
        "UPDATE artifact_jobs SET status = ?, data = ?, updated_at = ? WHERE id = ?",
        (
            data["status"],
            json.dumps(data, ensure_ascii=False),
            data["updated_at"],
            data["id"],
        ),
    )
    conn.commit()
    conn.close()
    if cursor.rowcount != 1:
        raise ValueError(f"unknown artifact job: {data['id']}")


def load_artifact_job(job_id: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT data FROM artifact_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def list_artifact_jobs(workspace_id: str | None = None) -> list[dict]:
    conn = _conn()
    if workspace_id is None:
        rows = conn.execute(
            "SELECT data FROM artifact_jobs ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT data FROM artifact_jobs WHERE workspace_id = ? "
            "ORDER BY updated_at DESC, id DESC",
            (workspace_id,),
        ).fetchall()
    conn.close()
    return [json.loads(row["data"]) for row in rows]


def save_artifact_job_step(data: dict) -> None:
    """Persist one resumable LLM subtask without discarding prior attempts."""
    conn = _conn()
    conn.execute(
        "INSERT INTO artifact_job_steps "
        "(id, job_id, stage, step_index, input_hash, status, data, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "status = excluded.status, data = excluded.data, updated_at = excluded.updated_at",
        (
            data["id"],
            data["job_id"],
            data["stage"],
            data["step_index"],
            data["input_hash"],
            data["status"],
            json.dumps(data, ensure_ascii=False),
            data["created_at"],
            data["updated_at"],
        ),
    )
    conn.commit()
    conn.close()


def load_artifact_job_step(
    job_id: str, stage: str, step_index: int, input_hash: str
) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT data FROM artifact_job_steps "
        "WHERE job_id = ? AND stage = ? AND step_index = ? AND input_hash = ?",
        (job_id, stage, step_index, input_hash),
    ).fetchone()
    conn.close()
    return json.loads(row["data"]) if row else None


def list_artifact_job_steps(job_id: str) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT data FROM artifact_job_steps WHERE job_id = ? "
        "ORDER BY stage ASC, step_index ASC, created_at ASC",
        (job_id,),
    ).fetchall()
    conn.close()
    return [json.loads(row["data"]) for row in rows]


# ---------- 配置 ----------

def get_config() -> dict:
    conn = _conn()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    conn.close()
    cfg = {r["key"]: r["value"] for r in rows}
    return {
        "base_url": cfg.get("base_url", "https://api.deepseek.com"),
        "api_key": cfg.get("api_key", ""),
        "model": cfg.get("model", "deepseek-chat"),
        "fake": cfg.get("fake", "1").strip().lower() in {"1", "true", "yes", "on"},
    }


def set_config(cfg: dict) -> dict:
    cur = get_config()
    cur.update({k: v for k, v in cfg.items() if k in cur})
    if cur.get("api_key") and "fake" not in cfg:
        cur["fake"] = False
    conn = _conn()
    conn.executemany(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [
            (
                key,
                "1" if key == "fake" and bool(value)
                else "0" if key == "fake"
                else str(value),
            )
            for key, value in cur.items()
        ],
    )
    conn.commit()
    conn.close()
    return cur


# ---------- 战役 ----------

def create_campaign(name: str, pdf_name: str | None = None, pdf_path: str | None = None) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO campaigns (name, pdf_name, pdf_path, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, pdf_name, pdf_path, "created", now()),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def list_campaigns() -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, name, pdf_name, status, created_at FROM campaigns ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_campaign(cid: int) -> dict | None:
    conn = _conn()
    r = conn.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def update_campaign(cid: int, **fields) -> None:
    if not fields:
        return
    conn = _conn()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE campaigns SET {cols} WHERE id = ?", (*fields.values(), cid))
    conn.commit()
    conn.close()


def delete_campaign(cid: int) -> bool:
    """删除战役及其全部数据与上传的 PDF；返回是否真的删了。"""
    conn = _conn()
    row = conn.execute("SELECT pdf_path FROM campaigns WHERE id = ?", (cid,)).fetchone()
    if row is None:
        conn.close()
        return False
    conn.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
    for table in ("chunks", "reports", "knowledge", "prep", "processed", "page_text"):
        conn.execute(f"DELETE FROM {table} WHERE campaign_id = ?", (cid,))
    conn.commit()
    conn.close()
    if row["pdf_path"]:
        try:
            Path(row["pdf_path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return True


# ---------- 分块 ----------

def save_chunks(cid: int, chunks: list[dict]) -> None:
    conn = _conn()
    conn.execute("DELETE FROM chunks WHERE campaign_id = ?", (cid,))
    conn.executemany(
        "INSERT INTO chunks (campaign_id, idx, title, pages, kind, text) VALUES (?, ?, ?, ?, ?, ?)",
        [(cid, c["idx"], c.get("title"), c.get("pages"), c.get("kind", "main"), c["text"]) for c in chunks],
    )
    conn.commit()
    conn.close()


def load_chunks(cid: int) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT idx, title, pages, kind, text FROM chunks WHERE campaign_id = ? ORDER BY idx", (cid,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- 摄入报告 ----------

def save_report(cid: int, data: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO reports (campaign_id, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(campaign_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (cid, json.dumps(data, ensure_ascii=False), now()),
    )
    conn.commit()
    conn.close()


def load_report(cid: int) -> dict | None:
    conn = _conn()
    r = conn.execute("SELECT data FROM reports WHERE campaign_id = ?", (cid,)).fetchone()
    conn.close()
    return json.loads(r["data"]) if r else None


# ---------- 知识库与备团产物 ----------

def save_page_texts(cid: int, pages: list[tuple[int, str]]) -> None:
    """写入页级原文（摄入时调用；生成备团产物时按页回溯）。"""
    conn = _conn()
    conn.executemany(
        "INSERT OR REPLACE INTO page_text (campaign_id, page, text) VALUES (?, ?, ?)",
        [(cid, pg, txt) for pg, txt in pages],
    )
    conn.commit()
    conn.close()


def load_page_snippets(cid: int, pages: list[int], max_chars: int = 8000) -> str:
    """按页号取原文片段（保持页序，超长截断），用于生成时补充细节。"""
    if not pages:
        return ""
    conn = _conn()
    q = ",".join("?" for _ in pages)
    rows = conn.execute(
        f"SELECT page, text FROM page_text WHERE campaign_id = ? AND page IN ({q}) ORDER BY page",
        [cid, *pages],
    ).fetchall()
    conn.close()
    parts: list[str] = []
    used = 0
    for r in rows:
        snippet = f"--- 第 {r['page']} 页 ---\n{r['text']}\n"
        if used + len(snippet) > max_chars:
            if parts:
                parts.append("（原文片段过长，已截断；其余内容请按页号在模组原文查看）")
            break
        parts.append(snippet)
        used += len(snippet)
    return "".join(parts)


def save_processed_titles(cid: int, titles: list[str]) -> None:
    """记录已成功分析的分块标题（刷新后前端据此显示绿勾）。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO processed (campaign_id, titles) VALUES (?, ?) "
        "ON CONFLICT(campaign_id) DO UPDATE SET titles = excluded.titles",
        (cid, json.dumps(titles, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def load_processed_titles(cid: int) -> list[str]:
    conn = _conn()
    row = conn.execute("SELECT titles FROM processed WHERE campaign_id = ?", (cid,)).fetchone()
    conn.close()
    if row is None:
        return []
    try:
        return json.loads(row["titles"])
    except (json.JSONDecodeError, TypeError):
        return []


def save_knowledge(cid: int, data: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO knowledge (campaign_id, data, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(campaign_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (cid, json.dumps(data, ensure_ascii=False), now()),
    )
    conn.commit()
    conn.close()


def load_knowledge(cid: int) -> dict | None:
    conn = _conn()
    r = conn.execute("SELECT data FROM knowledge WHERE campaign_id = ?", (cid,)).fetchone()
    conn.close()
    return json.loads(r["data"]) if r else None


def save_prep(cid: int, part: str, data: dict) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO prep (campaign_id, part, data, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(campaign_id, part) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
        (cid, part, json.dumps(data, ensure_ascii=False), now()),
    )
    conn.commit()
    conn.close()


def load_prep(cid: int) -> dict:
    conn = _conn()
    rows = conn.execute("SELECT part, data FROM prep WHERE campaign_id = ?", (cid,)).fetchall()
    conn.close()
    return {r["part"]: json.loads(r["data"]) for r in rows}
