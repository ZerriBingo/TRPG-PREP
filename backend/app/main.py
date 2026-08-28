"""trpg-prep 后端入口：FastAPI 应用。

路由：
  /api/config                 GET/PUT  保留的旧接口配置（当前工作台不调用 LLM）
  /api/campaigns              GET/POST 战役列表 / 新建
  /api/campaigns/{id}/upload  POST     上传模组 PDF
  /api/campaigns/{id}/ingest  POST     摄入（提取→检测→分块）
  /api/campaigns/{id}/analyze POST     保留的旧 SSE 分析接口
  /api/campaigns/{id}/generate/{part}  POST 保留的旧生成接口
  /api/campaigns/{id}/prep    GET      保留的旧备团产物接口
  /api/campaigns/{id}/export  GET      保留的旧导出接口
  /api/domain/*               新工作台领域包、页预览、场景计划和运行状态接口
  /                            新工作台静态前端
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from pathlib import Path
from typing import Literal

import fitz
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import artifacts, analyze, extract, generate, llm, prep, shadow, skill_loader, storage
from ..domain import (
    DomainValidationError,
    ExampleBundle,
    PrepJobCreate,
    SessionLogEntry,
    SessionState,
    ScenePlan,
    ShadowTaskSpec,
    build_session_review,
    draft_scene_plan_from_workspace,
    export_cards_markdown,
    export_session_review_markdown,
    load_profiles,
    validate_bundle,
    validate_session,
)
from .llm import make_client

storage.init_db()
artifacts.recover_interrupted_artifact_jobs()

PROJECT_ROOT = storage.PROJECT_ROOT
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DOMAIN_DIR = PROJECT_ROOT / "backend" / "domain"

app = FastAPI(title="trpg-prep", description="TRPG 备团助手")
MAX_DOMAIN_PDF_BYTES = 512 * 1024 * 1024
SEED_DOMAIN_WORKSPACES = {"red_signal_fixture", "naimen_pilot"}


def load_domain_example(example_id: str) -> ExampleBundle:
    example_path = DOMAIN_DIR / "examples" / f"{example_id}.json"
    if not example_path.is_file():
        raise HTTPException(404, f"未知领域样例: {example_id}")
    return ExampleBundle.model_validate_json(example_path.read_text(encoding="utf-8"))


def load_runtime_domain_example(example_id: str) -> tuple[ExampleBundle, str | None, str]:
    saved = storage.load_domain_bundle(example_id)
    if saved:
        try:
            saved_bundle = ExampleBundle.model_validate(saved[0])
            validate_bundle(saved_bundle, load_profiles(DOMAIN_DIR / "profiles"))
            return saved_bundle, saved[1], "saved"
        except (DomainValidationError, ValidationError) as error:
            example_path = DOMAIN_DIR / "examples" / f"{example_id}.json"
            if not example_path.is_file():
                raise HTTPException(422, f"书架工作区数据无效: {error}") from error
            # 种子覆盖存档可能落后于 schema/profile 演进；回退到种子包。
            return load_domain_example(example_id), None, "invalid"
    return load_domain_example(example_id), None, "seed"


def new_runtime_session(example_id: str, bundle: ExampleBundle) -> SessionState:
    clocks = {
        card.id: 0 for card in bundle.cards
        if card.type in {"clock", "operation_clock", "encounter_clock"}
    }
    return SessionState(
        example_id=example_id,
        current_plan_id=None,
        current_beat_id=None,
        current_card_id=None,
        clock_stages=clocks,
    )


def load_runtime_session(example_id: str, bundle: ExampleBundle) -> tuple[SessionState, str | None, str]:
    saved = storage.load_session_state(example_id)
    if not saved:
        return new_runtime_session(example_id, bundle), None, "new"
    try:
        session = SessionState.model_validate(saved[0])
        validate_session(session, bundle)
    except (DomainValidationError, ValidationError):
        return new_runtime_session(example_id, bundle), None, "invalid"
    return session, saved[1], "saved"


# ---------- 请求模型 ----------

class RetryIn(BaseModel):
    chunks: list[str] = []


class ConfigIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    fake: bool | None = None


class WorkspaceRenameIn(BaseModel):
    """Only the user-facing bookshelf name may be changed."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("workspace name cannot be empty")
        return value


class CampaignIn(BaseModel):
    name: str


class GenerateIn(BaseModel):
    instruction: str | None = None


class ShadowRunIn(BaseModel):
    """A worker result is recorded separately from every approved asset."""

    model_config = ConfigDict(extra="forbid")

    raw_response: str | None = Field(default=None, max_length=30000)
    transport_error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def has_exactly_one_result(self) -> "ShadowRunIn":
        has_response = bool(self.raw_response and self.raw_response.strip())
        has_error = bool(self.transport_error and self.transport_error.strip())
        if has_response == has_error:
            raise ValueError("provide exactly one of raw_response or transport_error")
        return self


class ShadowReviewIn(BaseModel):
    """A GM review action that leaves the original model candidate unchanged."""

    model_config = ConfigDict(extra="forbid")

    review_state: Literal["needs_review", "accepted", "rejected"]
    reviewed_text: str | None = Field(default=None, max_length=2000)
    review_note: str | None = Field(default=None, max_length=2000)

    @field_validator("reviewed_text", "review_note")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ShadowReviewBatchIn(BaseModel):
    """One shared review state and optional explanation for multiple candidates."""

    model_config = ConfigDict(extra="forbid")

    # One chapter-level review commonly contains several hundred candidates.
    # The frontend still sends 100-item chunks for progress and retry isolation,
    # while this wider server guard keeps older cached clients from failing at 101.
    candidate_ids: list[str] = Field(min_length=1, max_length=1000)
    review_state: Literal["needs_review", "accepted", "rejected"]
    review_note: str | None = Field(default=None, max_length=2000)

    @field_validator("candidate_ids")
    @classmethod
    def normalize_candidate_ids(cls, value: list[str]) -> list[str]:
        ids = [candidate_id.strip() for candidate_id in value if candidate_id.strip()]
        if not ids:
            raise ValueError("batch review needs at least one candidate id")
        if len(ids) != len(set(ids)):
            raise ValueError("batch review candidate ids must be unique")
        return ids

    @field_validator("review_note")
    @classmethod
    def strip_optional_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ShadowPromotionIn(BaseModel):
    """An explicit second step after candidate acceptance."""

    model_config = ConfigDict(extra="forbid")

    evidence_status: Literal["source_fact", "inference"]


class CardReviewBatchIn(BaseModel):
    """Approve generated artifacts or return approved artifacts to editing."""

    model_config = ConfigDict(extra="forbid")

    card_ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["approve", "reopen"]

    @field_validator("card_ids")
    @classmethod
    def normalize_card_ids(cls, value: list[str]) -> list[str]:
        ids = [card_id.strip() for card_id in value if card_id.strip()]
        if not ids:
            raise ValueError("card review needs at least one card id")
        if len(ids) != len(set(ids)):
            raise ValueError("card review ids must be unique")
        return ids


# ---------- 工具 ----------

def sse(events, campaign_id: int | None = None):
    def gen():
        try:
            for ev in events:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except (GeneratorExit, asyncio.CancelledError, Exception):
            # 客户端断开：标记取消，避免残留分析继续烧 token
            if campaign_id is not None:
                analyze.cancel_analysis(campaign_id)
            raise
    return StreamingResponse(gen(), media_type="text/event-stream")


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-]", "_", name)
    return name[-80:]


def _resolve_domain_source(file_name: str) -> Path:
    if not file_name or "://" in file_name:
        raise HTTPException(400, "该事实没有可预览的本地 PDF 来源")
    root = PROJECT_ROOT.resolve()
    candidate = (PROJECT_ROOT / file_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise HTTPException(400, "来源文件必须位于项目目录内") from error
    if candidate.suffix.lower() != ".pdf" or not candidate.is_file():
        raise HTTPException(404, "找不到来源 PDF")
    return candidate


def _pdf_files_under(root: Path) -> list[str]:
    files: set[str] = set()
    if not root.is_dir():
        return []
    for path in root.rglob("*.pdf"):
        if not path.is_file() or any(part.startswith(".") for part in path.parts):
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            continue
        files.add(relative.as_posix())
    return sorted(files, key=str.casefold)


# ---------- 配置 ----------

@app.get("/api/config")
def get_config():
    cfg = storage.get_config()
    return {"base_url": cfg["base_url"], "model": cfg["model"], "fake": cfg["fake"],
            "has_key": bool(cfg["api_key"])}


@app.put("/api/config")
def put_config(body: ConfigIn):
    cfg = storage.set_config(body.model_dump(exclude_none=True))
    return {"base_url": cfg["base_url"], "model": cfg["model"], "fake": cfg["fake"],
            "has_key": bool(cfg["api_key"])}


@app.get("/api/models")
def get_models():
    """拉取已配置 base_url 下可用的模型列表（OpenAI 兼容 /models 端点）。"""
    cfg = storage.get_config()
    if not cfg["api_key"]:
        raise HTTPException(400, "尚未配置 API Key，请先保存 Key 再拉取模型")
    try:
        models = llm.list_models(cfg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"拉取模型列表失败: {e}") from e
    return {"models": models, "base_url": cfg["base_url"], "url": llm.models_url(cfg["base_url"])}


# ---------- 战役 ----------

@app.get("/api/campaigns")
def list_campaigns():
    return storage.list_campaigns()


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: int):
    if not storage.delete_campaign(cid):
        raise HTTPException(404, "战役不存在")
    return {"ok": True}


@app.post("/api/campaigns")
def create_campaign(body: CampaignIn):
    cid = storage.create_campaign(body.name.strip() or "未命名战役")
    return storage.get_campaign(cid)


@app.get("/api/campaigns/{cid}")
def get_campaign(cid: int):
    camp = storage.get_campaign(cid)
    if not camp:
        raise HTTPException(404, "战役不存在")
    camp["report"] = storage.load_report(cid)
    camp["knowledge_summary"] = (
        {k: len(v) for k, v in storage.load_knowledge(cid).items()}
        if storage.load_knowledge(cid) else None
    )
    camp["processed_titles"] = storage.load_processed_titles(cid)
    return camp


@app.post("/api/campaigns/{cid}/upload")
async def upload_pdf(cid: int, file: UploadFile = File(...)):
    camp = storage.get_campaign(cid)
    if not camp:
        raise HTTPException(404, "战役不存在")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")
    safe = _safe_filename(file.filename or "module.pdf")
    dest = storage.UPLOAD_DIR / f"{cid}-{uuid.uuid4().hex[:8]}-{safe}"
    dest.write_bytes(await file.read())
    storage.update_campaign(cid, pdf_name=file.filename, pdf_path=str(dest), status="uploaded")
    return {"ok": True, "pdf_name": file.filename, "status": "uploaded"}


@app.post("/api/campaigns/{cid}/ingest")
def ingest(cid: int):
    camp = storage.get_campaign(cid)
    if not camp:
        raise HTTPException(404, "战役不存在")
    if not camp.get("pdf_path"):
        raise HTTPException(400, "尚未上传 PDF")
    report, full_chunks, page_texts = extract.run_ingest(camp["pdf_path"])
    storage.save_chunks(cid, full_chunks)
    storage.save_page_texts(cid, page_texts)
    storage.save_report(cid, report)
    storage.update_campaign(cid, status="ingested")
    return report


@app.post("/api/campaigns/{cid}/analyze")
def analyze_endpoint(cid: int):
    return sse(analyze.run_analysis(cid, make_client()), campaign_id=cid)


@app.post("/api/campaigns/{cid}/analyze/retry")
def retry_analyze_endpoint(cid: int, body: RetryIn):
    return sse(analyze.run_retry(cid, make_client(), body.chunks), campaign_id=cid)


@app.post("/api/campaigns/{cid}/analyze/cancel")
def cancel_analyze_endpoint(cid: int):
    analyze.cancel_analysis(cid)
    return {"ok": True}


@app.post("/api/campaigns/{cid}/generate/{part}")
def generate_endpoint(cid: int, part: str, body: GenerateIn | None = None):
    if part not in generate.PART_NAMES:
        raise HTTPException(400, f"未知部分: {part}，可选 {list(generate.PART_NAMES)}")
    instruction = body.instruction if body else None
    return sse(generate.run_generate_staged(cid, part, make_client(), instruction), campaign_id=cid)


@app.get("/api/campaigns/{cid}/prep")
def get_prep(cid: int):
    return storage.load_prep(cid)


@app.get("/api/campaigns/{cid}/export")
def export_markdown(cid: int):
    prep = storage.load_prep(cid)
    if not prep:
        raise HTTPException(404, "尚无备团产物")
    md = generate.prep_to_markdown(prep)
    return StreamingResponse(
        iter([md.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="prep-{cid}.md"'},
    )


@app.get("/api/domain/workbench")
def get_domain_workbench(
    example: str = Query("red_signal_fixture", pattern=r"^[a-z][a-z0-9_-]*$"),
):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, saved_at, saved_state = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    prep_job = prep.find_prep_job_by_workspace(example)
    artifact_job = artifacts.latest_artifact_job(example)
    runtime_profile_ids = {
        profile_id for profile_id in bundle.profile_ids
        if profile_id in profiles and profiles[profile_id].profile_kind == "runtime"
    }
    location_facts = [fact for fact in bundle.facts if fact.kind == "location" and fact.evidence_status != "model_candidate"]
    covered_location_ids = {
        fact.id for fact in location_facts
        if any(card.profile_id in runtime_profile_ids and card.type in {"scene", "investigation_site", "environment"} and fact.id in card.fact_ids for card in bundle.cards)
    }
    prep_context = None
    if prep_job is not None:
        prep_context = {
            "job_id": prep_job.id,
            "source_file": prep_job.scope.source_file,
            "source_version": prep_job.scope.source_version,
            "page_spans": [
                span.model_dump(mode="json") for span in prep_job.scope.page_spans
            ],
            "profile_id": prep_job.scope.profile_id,
            "session_minutes": prep_job.scope.session_minutes,
            "status": prep_job.status,
        }
    return {
        "bundle": bundle.model_dump(mode="json", by_alias=True),
        "profiles": {key: value.model_dump(mode="json") for key, value in profiles.items()},
        "saved_at": saved_at,
        "saved_state": saved_state,
        "has_seed": (DOMAIN_DIR / "examples" / f"{example}.json").is_file(),
        "prep_context": prep_context,
        "artifact_job": artifact_job.model_dump(mode="json") if artifact_job else None,
        "coverage": {
            "location_total": len(location_facts),
            "location_covered": len(covered_location_ids),
            "uncovered_location_titles": [fact.text[:80] for fact in location_facts if fact.id not in covered_location_ids][:20],
        },
    }


@app.get("/api/domain/workspaces")
def get_domain_workspaces():
    workspaces: dict[str, dict] = {}
    # Seed bundles remain in the repository for automated fixtures, but are
    # intentionally absent from the user bookshelf.
    for raw_bundle, updated_at in storage.list_domain_bundles():
        try:
            bundle = ExampleBundle.model_validate(raw_bundle)
        except ValidationError:
            continue
        kind = (
            "seed"
            if bundle.id in SEED_DOMAIN_WORKSPACES
            else "prep"
            if bundle.id.startswith("prep_job_")
            else "saved"
        )
        workspaces[bundle.id] = {
            "id": bundle.id,
            "name": bundle.name,
            "description": bundle.description,
            "kind": kind,
            "updated_at": updated_at,
            "can_rename": kind != "seed",
            "can_delete": kind != "seed",
        }
    return {
        "workspaces": sorted(
            workspaces.values(),
            key=lambda item: (
                0 if item["kind"] == "prep" else 1,
                item["name"].casefold(),
                item["id"],
            ),
        )
    }


@app.delete("/api/domain/examples/{example}/plans/{plan_id}")
def delete_domain_plan(example: str, plan_id: str):
    """Delete a locked scene plan; its runtime session is reset first."""
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    if not any(plan.id == plan_id for plan in bundle.plans):
        raise HTTPException(404, "未找到该运行场景")
    bundle.plans = [plan for plan in bundle.plans if plan.id != plan_id]
    storage.delete_session_state(example)
    validate_bundle(bundle, profiles)
    updated_at = storage.save_domain_bundle(example, bundle.model_dump(mode="json", by_alias=True))
    return {"ok": True, "saved_at": updated_at}


@app.patch("/api/domain/workspaces/{workspace_id}")
def rename_domain_workspace(workspace_id: str, body: WorkspaceRenameIn):
    """Rename only a saved/prep bookshelf workspace; seed examples stay immutable."""
    if workspace_id in SEED_DOMAIN_WORKSPACES:
        raise HTTPException(409, "内置种子项目不能重命名")
    saved = storage.load_domain_bundle(workspace_id)
    if not saved:
        raise HTTPException(404, "未找到该书架项目")
    try:
        bundle = ExampleBundle.model_validate(saved[0])
    except ValidationError as error:
        raise HTTPException(422, f"书架项目数据无效: {error}") from error
    bundle.name = body.name
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    try:
        validate_bundle(bundle, profiles)
    except DomainValidationError as error:
        raise HTTPException(422, str(error)) from error
    updated_at = storage.save_domain_bundle(
        workspace_id, bundle.model_dump(mode="json", by_alias=True)
    )
    return {
        "ok": True,
        "workspace": {
            "id": bundle.id,
            "name": bundle.name,
            "description": bundle.description,
            "kind": "prep" if bundle.id.startswith("prep_job_") else "saved",
            "updated_at": updated_at,
            "can_rename": True,
            "can_delete": True,
        },
    }


@app.delete("/api/domain/workspaces/{workspace_id}")
def delete_domain_workspace(workspace_id: str):
    """Delete a saved bookshelf project and all of its project-owned records."""
    if workspace_id in SEED_DOMAIN_WORKSPACES:
        raise HTTPException(409, "内置种子项目不能删除")

    prep_jobs = prep.list_prep_jobs_by_workspace(workspace_id)
    if any(job.status == "running" for job in prep_jobs):
        raise HTTPException(409, "项目仍有运行中的分析版本，请先取消后再删除")
    removed_prep_job = False
    for prep_job in prep_jobs:
        try:
            # This validates running/active state and removes its shadow queue.
            prep.delete_prep_job(prep_job.id)
            removed_prep_job = True
        except prep.PrepJobNotFoundError:
            pass
        except prep.PrepJobConflictError as error:
            raise HTTPException(409, str(error)) from error

    if not storage.delete_domain_workspace(workspace_id) and not removed_prep_job:
        raise HTTPException(404, "未找到该书架项目")
    return {"ok": True}


@app.get("/api/domain/source-page")
def get_domain_source_page(
    file: str = Query(..., min_length=1),
    page: int = Query(..., ge=1),
):
    source_path = _resolve_domain_source(file)
    document = None
    try:
        document = fitz.open(source_path)
        page_count = document.page_count
        if page > page_count:
            raise HTTPException(404, f"PDF 只有 {page_count} 页")
        source_page = document[page - 1]
        text = source_page.get_text("text").strip()
        pixmap = source_page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
        image = base64.b64encode(pixmap.tobytes("jpeg", jpg_quality=78)).decode("ascii")
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        raise HTTPException(422, f"读取 PDF 页面失败: {error}") from error
    finally:
        if document is not None:
            document.close()
    return {
        "file": file,
        "page": page,
        "page_count": page_count,
        "text": text,
        "image_data": f"data:image/jpeg;base64,{image}",
    }


@app.get("/api/domain/source-files")
def get_domain_source_files():
    uploads = _pdf_files_under(storage.UPLOAD_DIR)
    resources = _pdf_files_under(PROJECT_ROOT / "Resource")
    return {
        "files": sorted(set(uploads + resources), key=str.casefold),
        "uploads": uploads,
        "resources": resources,
    }


@app.post("/api/domain/source-files")
async def upload_domain_source_file(file: UploadFile = File(...)):
    original_name = (file.filename or "").strip()
    if not original_name.lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")
    safe_name = _safe_filename(original_name) or "module.pdf"
    storage.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination = storage.UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}-{safe_name}"
    size = 0
    document = None
    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DOMAIN_PDF_BYTES:
                    raise HTTPException(413, "PDF 不能超过 512 MB")
                output.write(chunk)
        with destination.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise HTTPException(400, "文件内容不是有效 PDF")
        document = fitz.open(destination)
        if document.needs_pass:
            raise HTTPException(400, "暂不支持加密 PDF")
        page_count = document.page_count
        if page_count < 1:
            raise HTTPException(400, "PDF 没有可读取页面")
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception as error:  # noqa: BLE001
        destination.unlink(missing_ok=True)
        raise HTTPException(422, f"PDF 校验失败: {error}") from error
    finally:
        if document is not None:
            document.close()
        await file.close()
    relative = destination.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    return {
        "file": relative,
        "original_name": original_name,
        "page_count": page_count,
        "size_bytes": size,
    }


@app.post("/api/domain/prep/jobs")
def create_domain_prep_job(body: PrepJobCreate):
    try:
        job = prep.create_prep_job(body)
    except prep.PrepSourceError as error:
        raise HTTPException(422, str(error)) from error
    except prep.PrepError as error:
        raise HTTPException(400, str(error)) from error
    return {"job": job.model_dump(mode="json")}


@app.get("/api/domain/prep/jobs")
def get_domain_prep_jobs():
    return {
        "jobs": [
            {
                **job.model_dump(mode="json"),
                "rebuild_available": prep.job_needs_rebuild(job),
            }
            for job in prep.list_prep_jobs()
        ]
    }


@app.get("/api/domain/prep/jobs/{job_id}")
def get_domain_prep_job(job_id: str):
    try:
        job = prep.get_prep_job(job_id)
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    return {
        "job": {
            **job.model_dump(mode="json"),
            "rebuild_available": prep.job_needs_rebuild(job),
        }
    }


@app.post("/api/domain/prep/jobs/{job_id}/run", status_code=202)
def run_domain_prep_job(job_id: str, background_tasks: BackgroundTasks):
    try:
        job = prep.start_prep_job(job_id)
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except prep.PrepJobConflictError as error:
        raise HTTPException(409, str(error)) from error
    background_tasks.add_task(prep.execute_prep_job, job.id)
    return {"job": job.model_dump(mode="json")}


@app.post("/api/domain/prep/jobs/{job_id}/rebuild", status_code=202)
def rebuild_domain_prep_job(job_id: str, background_tasks: BackgroundTasks):
    """Create and start a new analysis version in the same bookshelf project."""
    try:
        rebuilt = prep.rebuild_prep_job(job_id)
        started = prep.start_prep_job(rebuilt.id)
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except prep.PrepSourceError as error:
        raise HTTPException(422, str(error)) from error
    except prep.PrepJobConflictError as error:
        raise HTTPException(409, str(error)) from error
    except prep.PrepError as error:
        raise HTTPException(400, str(error)) from error
    background_tasks.add_task(prep.execute_prep_job, started.id)
    return {
        "ok": True,
        "previous_job_id": job_id,
        "job": started.model_dump(mode="json"),
    }


@app.post("/api/domain/prep/jobs/{job_id}/cancel")
def cancel_domain_prep_job(job_id: str):
    try:
        job = prep.cancel_prep_job(job_id)
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except prep.PrepJobConflictError as error:
        raise HTTPException(409, str(error)) from error
    return {"job": job.model_dump(mode="json")}


@app.delete("/api/domain/prep/jobs/{job_id}")
def delete_domain_prep_job(job_id: str):
    try:
        prep.delete_prep_job(job_id)
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except prep.PrepJobConflictError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@app.get("/api/domain/prep/jobs/{job_id}/candidates")
def get_domain_prep_job_candidates(
    job_id: str,
    review_state: Literal["needs_review", "accepted", "rejected", "all"] = Query(
        default="all"
    ),
):
    try:
        candidates = prep.list_prep_job_candidates(
            job_id, None if review_state == "all" else review_state
        )
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    promotions = {
        item["candidate_id"]: item
        for item in storage.list_candidate_promotions(
            [candidate["id"] for candidate in candidates]
        )
    }
    return {
        "candidates": [
            {**candidate, "promotion": promotions.get(candidate["id"])}
            for candidate in candidates
        ]
    }


@app.post("/api/domain/prep/jobs/{job_id}/candidates/review")
def review_domain_prep_job_candidates(job_id: str, body: ShadowReviewBatchIn):
    """Apply one review action to candidates across every window in a prep job."""
    try:
        available = prep.list_prep_job_candidates(job_id, review_state=None)
    except prep.PrepJobNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    available_ids = {candidate["id"] for candidate in available}
    unknown = [candidate_id for candidate_id in body.candidate_ids if candidate_id not in available_ids]
    if unknown:
        raise HTTPException(404, f"候选不属于该备团任务: {unknown}")
    try:
        candidates = shadow.review_shadow_candidates(
            body.candidate_ids,
            review_state=body.review_state,
            review_note=body.review_note,
            update_review_note="review_note" in body.model_fields_set,
        )
    except shadow.ShadowCandidateNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except shadow.ShadowResultValidationError as error:
        raise HTTPException(422, str(error)) from error
    return {"candidates": [item.model_dump(mode="json") for item in candidates]}


@app.get("/api/domain/examples/{example}/session")
def get_domain_session(example: str):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    session, updated_at, state = load_runtime_session(example, bundle)
    return {
        "session": session.model_dump(mode="json"),
        "updated_at": updated_at,
        "state": state,
    }


@app.get("/api/domain/examples/{example}/session/review")
def export_domain_session_review(
    example: str,
    format: str = Query("markdown", pattern=r"^(json|markdown)$"),
):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    session, updated_at, state = load_runtime_session(example, bundle)
    review = build_session_review(session, bundle)
    review["saved_at"] = updated_at
    review["session_state"] = state
    if format == "json":
        return JSONResponse(
            review,
            headers={
                "Content-Disposition": 'attachment; filename="workbench-session-review.json"'
            },
        )
    markdown = export_session_review_markdown(session, bundle)
    return StreamingResponse(
        iter([markdown.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="workbench-session-review.md"'
        },
    )


@app.post("/api/domain/shadow/tasks")
def create_domain_shadow_task(spec: ShadowTaskSpec):
    """Create an idempotent candidate-only task for the P1 shadow queue."""
    try:
        task, created = shadow.create_shadow_task(spec)
    except shadow.ShadowTaskConflictError as error:
        raise HTTPException(409, str(error)) from error
    return {"created": created, "task": task.model_dump(mode="json")}


@app.get("/api/domain/shadow/tasks")
def get_domain_shadow_tasks():
    return {"tasks": [task.model_dump(mode="json") for task in shadow.list_shadow_tasks()]}


@app.get("/api/domain/shadow/review-queue")
def get_domain_shadow_review_queue(
    task_id: str | None = Query(default=None),
    review_state: Literal["needs_review", "accepted", "rejected", "all"] = Query(
        default="needs_review"
    ),
):
    try:
        candidates = shadow.list_shadow_candidates(
            task_id,
            None if review_state == "all" else review_state,
        )
    except shadow.ShadowTaskNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except shadow.ShadowResultValidationError as error:
        raise HTTPException(422, str(error)) from error
    promotions = {
        item["candidate_id"]: item
        for item in storage.list_candidate_promotions(
            [candidate.id for candidate in candidates]
        )
    }
    candidate_payloads = []
    for candidate in candidates:
        item = candidate.model_dump(mode="json")
        item["promotion"] = promotions.get(candidate.id)
        candidate_payloads.append(item)
    return {"candidates": candidate_payloads}


@app.post("/api/domain/shadow/candidates/{candidate_id}/review")
def review_domain_shadow_candidate(candidate_id: str, body: ShadowReviewIn):
    try:
        candidate = shadow.review_shadow_candidate(
            candidate_id,
            review_state=body.review_state,
            reviewed_text=body.reviewed_text,
            review_note=body.review_note,
            update_reviewed_text="reviewed_text" in body.model_fields_set,
            update_review_note="review_note" in body.model_fields_set,
        )
    except shadow.ShadowCandidateNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except shadow.ShadowResultValidationError as error:
        raise HTTPException(422, str(error)) from error
    return {"candidate": candidate.model_dump(mode="json")}


@app.post("/api/domain/shadow/review/batch")
def review_domain_shadow_candidates(body: ShadowReviewBatchIn):
    try:
        candidates = shadow.review_shadow_candidates(
            body.candidate_ids,
            review_state=body.review_state,
            review_note=body.review_note,
            update_review_note="review_note" in body.model_fields_set,
        )
    except shadow.ShadowCandidateNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except shadow.ShadowResultValidationError as error:
        raise HTTPException(422, str(error)) from error
    return {"candidates": [item.model_dump(mode="json") for item in candidates]}


@app.post("/api/domain/shadow/candidates/{candidate_id}/promote")
def promote_domain_shadow_candidate(candidate_id: str, body: ShadowPromotionIn):
    try:
        fact, workspace_id, created = prep.promote_shadow_candidate(
            candidate_id, evidence_status=body.evidence_status
        )
    except shadow.ShadowCandidateNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except prep.PrepPromotionConflictError as error:
        raise HTTPException(409, str(error)) from error
    except (DomainValidationError, ValidationError) as error:
        raise HTTPException(422, str(error)) from error
    promotion = storage.load_candidate_promotion(candidate_id)
    return {
        "created": created,
        "workspace_id": workspace_id,
        "fact": fact.model_dump(mode="json"),
        "promotion": promotion,
    }


@app.get("/api/domain/shadow/tasks/{task_id}")
def get_domain_shadow_task(task_id: str):
    try:
        detail = shadow.shadow_task_detail(task_id)
    except shadow.ShadowTaskNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    return {
        "task": detail["task"].model_dump(mode="json"),
        "runs": [item.model_dump(mode="json") for item in detail["runs"]],
        "candidates": [
            item.model_dump(mode="json") for item in detail["candidates"]
        ],
    }


@app.post("/api/domain/shadow/tasks/{task_id}/runs")
def submit_domain_shadow_result(task_id: str, body: ShadowRunIn):
    try:
        task, run, candidates = shadow.submit_shadow_result(
            task_id,
            raw_response=body.raw_response,
            transport_error=body.transport_error,
        )
    except shadow.ShadowTaskNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except shadow.ShadowTaskConflictError as error:
        raise HTTPException(409, str(error)) from error
    except shadow.ShadowResultValidationError as error:
        raise HTTPException(422, str(error)) from error
    return {
        "task": task.model_dump(mode="json"),
        "run": run.model_dump(mode="json"),
        "candidates": [item.model_dump(mode="json") for item in candidates],
    }


@app.post("/api/domain/shadow/tasks/{task_id}/cancel")
def cancel_domain_shadow_task(task_id: str):
    try:
        task = shadow.cancel_shadow_task(task_id)
    except shadow.ShadowTaskNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except shadow.ShadowTaskConflictError as error:
        raise HTTPException(409, str(error)) from error
    return {"task": task.model_dump(mode="json")}


@app.post("/api/domain/examples/{example}/cards/draft", status_code=202)
def draft_domain_cards(example: str, background_tasks: BackgroundTasks):
    """Queue source-bound card draft generation after an explicit GM request."""
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    prep_job = prep.find_prep_job_by_workspace(example)
    profile_id = (
        prep_job.scope.profile_id
        if prep_job is not None
        else next((item for item in bundle.profile_ids if item in profiles), None)
    )
    if profile_id is None or profile_id not in profiles:
        raise HTTPException(422, "当前书架没有可用的备团板块")
    if any(card.profile_id == profile_id for card in bundle.cards):
        raise HTTPException(
            409,
            "当前板块已经有备团产物；请先完成现有草案的复核，不重复生成一套平行卡片",
        )
    config = storage.get_config()
    job, created = artifacts.create_artifact_job(
        example,
        profile_id,
        model_id=config["model"],
        fake_model=config["fake"],
    )
    if created:
        background_tasks.add_task(
            artifacts.execute_artifact_job,
            job.id,
            workspace_id=example,
            profile_id=profile_id,
            session_minutes=(
                prep_job.scope.session_minutes if prep_job is not None else None
            ),
        )
    return {
        "ok": True,
        "created": created,
        "job": job.model_dump(mode="json"),
    }


@app.post("/api/domain/examples/{example}/cards/draft-missing-locations", status_code=202)
def draft_missing_location_cards(example: str, background_tasks: BackgroundTasks):
    """Queue a focused supplemental draft for uncovered location facts."""
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    prep_job = prep.find_prep_job_by_workspace(example)
    profile_id = prep_job.scope.profile_id if prep_job is not None else next(
        (item for item in bundle.profile_ids if item in profiles and profiles[item].profile_kind == "runtime"), None
    )
    profile = profiles.get(profile_id) if profile_id else None
    if profile is None:
        raise HTTPException(422, "当前书架没有可用的运行板块")
    runtime_card_ids = {card.id for card in bundle.cards if card.profile_id == profile.id and card.type in {"scene", "investigation_site", "environment"}}
    covered = {fact_id for card in bundle.cards if card.id in runtime_card_ids for fact_id in card.fact_ids}
    missing = [fact for fact in bundle.facts if fact.kind == "location" and fact.evidence_status != "model_candidate" and fact.id not in covered]
    if not missing:
        raise HTTPException(409, "当前没有发现尚未覆盖的来源地点")
    focused = bundle.model_copy(deep=True)
    focused.facts = missing
    config = storage.get_config()
    job, created = artifacts.create_artifact_job(example, profile.id, model_id=config["model"], fake_model=config["fake"], fact_ids=[fact.id for fact in missing])
    if created:
        background_tasks.add_task(
            artifacts.execute_artifact_job,
            job.id,
            workspace_id=example,
            profile_id=profile.id,
            session_minutes=prep_job.scope.session_minutes if prep_job else None,
            fact_ids=[fact.id for fact in missing],
        )
    return {"ok": True, "created": created, "job": job.model_dump(mode="json"), "focused_fact_count": len(missing)}


@app.get("/api/domain/examples/{example}/cards/draft-jobs/{job_id}")
def get_domain_card_draft_job(example: str, job_id: str):
    """Return a durable card-draft job only when it belongs to this workspace."""
    try:
        job = artifacts.get_artifact_job(job_id)
    except artifacts.ArtifactGenerationError as error:
        raise HTTPException(404, str(error)) from error
    if job.workspace_id != example:
        raise HTTPException(404, "未找到该工作区的产物生成任务")
    return {"job": job.model_dump(mode="json")}


@app.post("/api/domain/examples/{example}/cards/review")
def review_domain_cards(example: str, body: CardReviewBatchIn):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    cards_by_id = {card.id: card for card in bundle.cards}
    missing = [card_id for card_id in body.card_ids if card_id not in cards_by_id]
    if missing:
        raise HTTPException(404, f"找不到备团产物: {missing}")
    if body.action == "reopen":
        referenced = sorted({
            card_id
            for plan in bundle.plans
            for card_id in body.card_ids
            if card_id in plan.card_ids
        })
        if referenced:
            raise HTTPException(
                409,
                f"这些产物已被运行场景引用，不能退回修改: {referenced}",
            )
    target_state = "approved" if body.action == "approve" else "edited"
    updated_cards = []
    for card_id in body.card_ids:
        card = cards_by_id[card_id]
        updated = card.model_copy(update={"edit_state": target_state})
        bundle.cards[bundle.cards.index(card)] = updated
        cards_by_id[card_id] = updated
        updated_cards.append(updated)
    try:
        validate_bundle(bundle, profiles)
    except DomainValidationError as error:
        raise HTTPException(422, str(error)) from error
    updated_at = storage.save_domain_bundle(
        example, bundle.model_dump(mode="json", by_alias=True)
    )
    return {
        "ok": True,
        "cards": [card.model_dump(mode="json") for card in updated_cards],
        "saved_at": updated_at,
    }


@app.delete("/api/domain/examples/{example}/cards/{card_id}")
def delete_domain_card(example: str, card_id: str):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    card = next((item for item in bundle.cards if item.id == card_id), None)
    if card is None:
        raise HTTPException(404, f"找不到备团产物: {card_id}")
    if any(card_id in plan.card_ids for plan in bundle.plans):
        raise HTTPException(409, "该产物已被运行场景引用；请先删除对应运行场景")
    bundle.cards = [item for item in bundle.cards if item.id != card_id]
    validate_bundle(bundle, profiles)
    updated_at = storage.save_domain_bundle(
        example, bundle.model_dump(mode="json", by_alias=True)
    )
    return {"ok": True, "saved_at": updated_at}


@app.post("/api/domain/examples/{example}/plans/draft")
def draft_domain_plan(example: str):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, saved_at, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    prep_job = prep.find_prep_job_by_workspace(example)
    source_file = prep_job.scope.source_file if prep_job is not None else None
    source_pages = (
        [
            page
            for span in prep_job.scope.page_spans
            for page in span.pages()
        ]
        if prep_job is not None
        else None
    )
    try:
        plan = draft_scene_plan_from_workspace(
            bundle,
            profiles,
            profile_id=prep_job.scope.profile_id if prep_job is not None else None,
            source_file=source_file,
            source_pages=source_pages,
            session_minutes=(
                prep_job.scope.session_minutes if prep_job is not None else None
            ),
        )
    except DomainValidationError as error:
        raise HTTPException(422, str(error)) from error
    plan_content = plan.model_dump(mode="json", exclude={"id"})
    existing = next(
        (
            item
            for item in bundle.plans
            if item.model_dump(mode="json", exclude={"id"}) == plan_content
        ),
        None,
    )
    if existing is not None:
        return {
            "ok": True,
            "created": False,
            "plan": existing.model_dump(mode="json"),
            "saved_at": saved_at,
        }
    bundle.plans = [item for item in bundle.plans if item.id != plan.id]
    bundle.plans.append(plan)
    try:
        validate_bundle(bundle, profiles)
    except DomainValidationError as error:
        raise HTTPException(422, str(error)) from error
    updated_at = storage.save_domain_bundle(example, bundle.model_dump(mode="json"))
    return {
        "ok": True,
        "created": True,
        "plan": plan.model_dump(mode="json"),
        "saved_at": updated_at,
    }


@app.put("/api/domain/examples/{example}/session")
def save_domain_session(example: str, session: SessionState):
    if session.example_id != example:
        raise HTTPException(400, "运行状态 id 与 URL 不一致")
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    try:
        validate_session(session, bundle)
    except DomainValidationError as error:
        raise HTTPException(422, str(error)) from error
    updated_at = storage.save_session_state(example, session.model_dump(mode="json"))
    return {"ok": True, "updated_at": updated_at}


@app.delete("/api/domain/examples/{example}/session")
def reset_domain_session(example: str):
    bundle, _, _ = load_runtime_domain_example(example)
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    validate_bundle(bundle, profiles)
    storage.delete_session_state(example)
    return {"ok": True}


@app.put("/api/domain/examples/{example}/bundle")
def save_domain_bundle(example: str, bundle: ExampleBundle):
    if bundle.id != example:
        raise HTTPException(400, "运行包 id 与 URL 不一致")
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    try:
        validate_bundle(bundle, profiles)
    except DomainValidationError as error:
        raise HTTPException(422, str(error)) from error
    updated_at = storage.save_domain_bundle(
        example, bundle.model_dump(mode="json", by_alias=True)
    )
    saved_session = storage.load_session_state(example)
    if saved_session:
        try:
            session = SessionState.model_validate(saved_session[0])
            validate_session(session, bundle)
        except (DomainValidationError, ValidationError):
            storage.delete_session_state(example)
    return {"ok": True, "saved_at": updated_at}


@app.delete("/api/domain/examples/{example}/bundle")
def reset_domain_bundle(example: str):
    load_domain_example(example)
    storage.delete_domain_bundle(example)
    storage.delete_session_state(example)
    return {"ok": True}


@app.get("/api/domain/export")
def export_domain_markdown(
    example: str = Query("red_signal_fixture", pattern=r"^[a-z][a-z0-9_-]*$"),
):
    profiles = load_profiles(DOMAIN_DIR / "profiles")
    bundle, _, _ = load_runtime_domain_example(example)
    validate_bundle(bundle, profiles)
    markdown = export_cards_markdown(bundle.cards, bundle.facts, profiles)
    return StreamingResponse(
        iter([markdown.encode("utf-8")]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="workbench-cards.md"'},
    )


# ---------- 新工作台 ----------

@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/workbench.html", status_code=307)

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
