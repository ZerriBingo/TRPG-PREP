"use strict";

const state = {
  data: null,
  exampleId: new URLSearchParams(location.search).get("example") || "",
  initialView: new URLSearchParams(location.search).get("view") || "prep",
  workspaces: [],
  editMode: false,
  dirty: false,
  saving: false,
  savedAt: null,
  savedState: "seed",
  editingFactId: null,
  editingCardId: null,
  editingPlanId: null,
  factSearch: "",
  factKind: "all",
  factVisibility: "all",
  cardProfile: "all",
  cardType: "all",
  selectedFactId: null,
  selectedCardId: null,
  selectedCardIds: new Set(),
  artifacts: {
    generating: false,
    reviewing: false,
    error: "",
    job: null,
    pollTimer: null
  },
  runtimeProfileId: "cthulhu-dark-2e",
  session: null,
  sessionUpdatedAt: null,
  sessionState: "new",
  sessionSaving: false,
  sessionDirty: false,
  sourcePrep: {file: "", page: 1, start: 1, end: 1, pageCount: null, loadedFile: "", loadedPage: null},
  sourceFiles: [],
  prep: {
    jobs: [],
    config: null,
    models: [],
    uploads: [],
    loading: false,
    uploading: false,
    submitting: false,
    configSaving: false,
    error: "",
    notice: "",
    pollTimer: null
  },
  review: {
    tasks: [],
    candidates: [],
    taskId: "",
    reviewState: "needs_review",
    sourcePage: "",
    selectedCandidateId: null,
    selectedIds: new Set(),
    loading: false,
    saving: false,
    error: "",
    notice: ""
  }
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[char]));

function formatApiError(payload, fallback = "请求失败") {
  const detail = payload?.detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const location = Array.isArray(item.loc) ? item.loc.filter(Boolean).join(".") : "";
        const message = item.msg || item.message || JSON.stringify(item);
        return location ? `${location}: ${message}` : message;
      }
      return String(item);
    }).filter(Boolean);
    if (messages.length) return messages.join("；");
  }
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (payload?.message && String(payload.message).trim()) return String(payload.message).trim();
  return fallback;
}

const kindLabels = {
  clue: "线索", npc: "人物", location: "地点", event: "事件",
  threat: "威胁", stakes: "利害", obstacle: "障碍",
  timeline: "时间线", resource: "资源"
};
const visibilityLabels = {
  explicit: "明示", hidden: "隐藏", inferred: "推断", gm_suggestion: "GM 建议"
};
const profileKindLabels = { runtime: "桌边运行", prep: "材料整理" };
const evidenceStatusLabels = {
  source_fact: "原文事实",
  inference: "可验证推断",
  gm_authored: "GM 创作",
  model_candidate: "模型候选"
};
const reviewStateLabels = {
  needs_review: "待复核",
  accepted: "已接受",
  rejected: "已拒绝"
};
const prepJobStatusLabels = {
  queued: "等待运行",
  running: "生成中",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消"
};
const prepWindowStatusLabels = {
  queued: "等待",
  running: "生成中",
  succeeded: "完成",
  failed: "失败",
  cancelled: "取消"
};
const prepBoundaryLabels = {
  legacy: "旧窗口",
  scope_end: "范围末端",
  heading: "历史标题信号（非分段）",
  sentence_end: "历史句末信号（非分段）",
  continuation: "历史续写信号（非分段）",
  page_limit: "固定页数",
  char_budget: "字数上限"
};
const prepProfileLabels = {
  "cthulhu-dark-2e": "现实恐怖",
  daggerheart: "奇幻冒险",
  "module-prep": "通用备团"
};
const prepProfileDescriptions = {
  "cthulhu-dark-2e": "线索、调查压力、恐怖递进与不可逆代价",
  daggerheart: "场景、环境、意图、压力与动态后果",
  "module-prep": "章节地图、人物功能、线索、威胁与时间线"
};
const beatModeLabels = {
  arrival: "抵达", investigation: "调查", pressure: "压力",
  revelation: "揭示", confrontation: "对峙", aftermath: "收束", transition: "转场"
};
const sessionLogLabels = {
  move: "GM 移动",
  note: "备注",
  transition: "转场",
  run_started: "开始运行",
  lookup: "查找",
  lookup_missing: "未找到",
  source_page_opened: "打开源页",
  clue_revealed: "揭示线索",
  clock_advanced: "推进时钟",
  clock_rewound: "回退时钟",
  scene_changed: "切换场景",
  beat_changed: "切换节拍",
  gm_move: "GM 移动",
  manual_note: "GM 备注",
  field_edited: "字段改写"
};
const fieldLabels = {
  opening_image: "开场画面", immediate_actions: "现场入口（GM 内部）", direct_clues: "直接线索",
  hidden_clues: "隐藏线索", gm_moves: "GM 移动", risk_if_pressed: "施压风险",
  exit_conditions: "退场条件", role: "身份", wants: "想要", offers: "能给什么",
  pressure_point: "施压点", refusal_consequence: "拒绝后果", manifestation: "表现",
  intention: "意图", danger_signs: "危险前兆", escalation: "升级",
  noncombat_exits: "非战斗解法", usable_features: "可利用物", hazards: "危险物",
  positioning: "距离与位置", environment_changes: "环境变化", signature_actions: "标志行动",
  damaged_behavior: "受创行为", resolutions: "解决方式", official_framing: "官方说法",
  anomaly_signs: "异常迹象", direct_observations: "直接观察", evidence_chain: "证据链",
  available_procedures: "可用程序", escalation_condition: "升级条件",
  observed_behavior: "观察行为", working_hypothesis: "工作假设",
  verification_method: "验证方法", danger_threshold: "危险阈值",
  containment_options: "收容选项", name: "名称", stages: "阶段",
  advance_condition: "推进条件", current_stage: "当前阶段",
  final_consequence: "最终后果", final_disposition: "最终处置",
  defensive_limits: "防御边界", retreat_condition: "撤退条件",
  classification_guess: "分类推测", failure_consequence: "失效后果"
};
const runtimeLabels = {
  exposure: "暴露", insight: "洞察", pursuit: "追查", relationships: "关系",
  hp: "生命", stress: "压力", positioning: "站位", attention: "注意",
  containment_integrity: "收容完整性", operational_security: "行动保密",
  resources: "资源", timeline: "时间", cost_forward: "代价前移",
  reveal_pressure: "揭示压力", threat_advance: "威胁推进",
  worsen_position: "恶化站位", enemy_advances: "敌人推进",
  evidence_compromised: "证据受损", procedure_backfires: "程序反噬",
  escalate_classification: "提高分级", ask_a_question: "提出问题",
  expose_a_cost: "暴露代价", advance_the_clock: "推进时钟",
  complicate_the_scene: "复杂化场景", make_the_invisible_felt: "让无形被感到",
  telegraph_danger: "预告危险", change_the_environment: "改变环境",
  show_enemy_intention: "展示意图", offer_a_hard_choice: "给予硬选择",
  reveal_anomaly_logic: "揭示异常逻辑", consume_resources: "消耗资源",
  force_a_disposal_choice: "迫使处置选择", advance_containment_clock: "推进收容钟"
};

function label(key, fallback = key) {
  return fieldLabels[key] || runtimeLabels[key] || kindLabels[key] || visibilityLabels[key] || evidenceStatusLabels[key] || fallback;
}

function factSourceRefs(fact) {
  if (Array.isArray(fact?.source_refs) && fact.source_refs.length) return fact.source_refs;
  return fact?.source ? [fact.source] : [];
}

function primarySource(fact) {
  return factSourceRefs(fact)[0] || null;
}

function factEvidenceStatus(fact) {
  if (fact?.evidence_status) return fact.evidence_status;
  if (fact?.visibility === "inferred") return "inference";
  if (fact?.visibility === "gm_suggestion") return "gm_authored";
  return "source_fact";
}

function sourceRefLabel(source) {
  if (!source) return "无原文来源";
  const locator = source.locator ? ` · ${source.locator}` : "";
  return `${source.file} · p${source.page}${locator}`;
}

function factSourceLabel(fact) {
  const refs = factSourceRefs(fact);
  return refs.length ? refs.map(sourceRefLabel).join("; ") : "无原文来源";
}

function reviewSourceRefs(candidate) {
  return Array.isArray(candidate?.source_refs) ? candidate.source_refs : [];
}

function reviewDisplayText(candidate) {
  return candidate?.reviewed_text || candidate?.text || "";
}

function prepSpanLabel(span) {
  return span.start === span.end ? "p" + span.start : "p" + span.start + "-" + span.end;
}

function prepScopeLabel(job) {
  return (job.scope?.page_spans || []).map(prepSpanLabel).join(", ");
}

function sourceBasename(file) {
  const value = String(file || "");
  return value.split(/[\\/]/).pop() || value;
}

function prepReviewFilterValue(jobId) {
  return "prep:" + jobId;
}

function prepReviewJobId(value) {
  return String(value || "").startsWith("prep:")
    ? String(value).slice("prep:".length)
    : null;
}

function reviewTaskLabel(task) {
  const pages = Array.isArray(task.source_pages) ? task.source_pages.join(",") : "";
  return task.id + " · " + sourceBasename(task.source_file) + (pages ? " · p" + pages : "");
}

function reviewPrepJobLabel(job) {
  const profileName = prepProfileLabels[job.scope?.profile_id] || job.scope?.profile_id || "备团任务";
  return profileName + " · " + sourceBasename(job.scope?.source_file) +
    (prepScopeLabel(job) ? " · " + prepScopeLabel(job) : "");
}

function prepErrorSummary(value) {
  const error = String(value || "").trim();
  if (/validation errors for ShadowResponse|model output validation failed/i.test(error)) {
    return "模型返回格式不兼容；任务已保留，可重试此页段。";
  }
  if (/Model is unavailable/i.test(error)) return "当前模型暂时不可用，可稍后重试。";
  if (/HTTP 429|rate.?limit/i.test(error)) return "上游请求过于频繁，可稍后重试。";
  if (/cancelled/i.test(error)) return "该页段已取消。";
  const firstLine = error.split(/\r?\n/, 1)[0];
  return firstLine.length > 180 ? firstLine.slice(0, 177) + "..." : firstLine;
}

function updatePrepModelOptions() {
  const options = new Set(state.prep.models || []);
  if (state.prep.config?.model) options.add(state.prep.config.model);
  const datalist = $("prep-model-options");
  if (datalist) {
    datalist.innerHTML = [...options].sort().map((model) =>
      '<option value="' + esc(model) + '"></option>'
    ).join("");
  }
}

function renderPrep() {
  const config = state.prep.config;
  const llmStatus = $("prep-llm-status");
  if (!llmStatus) return;
  if (!config) {
    llmStatus.className = "status loading";
    llmStatus.textContent = "读取模型配置";
  } else if (config.fake) {
    llmStatus.className = "status ok";
    llmStatus.textContent = "FakeLLM · " + config.model;
  } else if (config.has_key) {
    llmStatus.className = "status ok";
    llmStatus.textContent = "模型已配置 · " + config.model;
  } else {
    llmStatus.className = "status error";
    llmStatus.textContent = "模型未配置";
  }

  const submitButton = $("prep-job-submit");
  submitButton.disabled = state.prep.submitting || state.prep.uploading || !config;
  submitButton.textContent = state.prep.submitting ? "创建中…" : "创建并开始";
  const uploadButton = $("prep-source-upload-button");
  uploadButton.disabled = state.prep.uploading;
  uploadButton.textContent = state.prep.uploading ? "上传中…" : "上传并选用";
  $("prep-job-error").textContent = state.prep.error || "";
  const prepNotice = $("prep-job-notice");
  if (prepNotice) prepNotice.textContent = state.prep.notice || "";

  const jobs = state.prep.jobs || [];
  const runningCount = jobs.filter((job) => job.status === "running").length;
  $("prep-job-summary").textContent = jobs.length
    ? jobs.length + " 个任务" + (runningCount ? " · " + runningCount + " 个生成中" : "")
    : "尚无任务";

  const jobsHtml = jobs.map((job) => {
    const windowsHtml = (job.windows || []).map((window) => {
      const coreSpan = window.core_span || window.page_span;
      const contextLabel = Array.isArray(window.context_pages) && window.context_pages.length
        ? " · 上下文 " + window.context_pages.map((page) => "p" + page).join(", ")
        : "";
      const truncationLabel = Array.isArray(window.truncated_pages) && window.truncated_pages.length
        ? " · 截断 " + window.truncated_pages.map((page) => "p" + page).join(", ")
        : "";
      const boundaryClass = ["heading", "sentence_end", "continuation", "char_budget"].includes(window.boundary_basis)
        ? "needs_review"
        : "neutral";
      const windowAction = window.shadow_task_id && window.candidate_count
        ? '<button class="edit-button" type="button" data-prep-review-task="' +
          esc(window.shadow_task_id) + '">复核</button>'
        : "";
      const error = window.error
        ? '<details class="prep-window-error"><summary>' + esc(prepErrorSummary(window.error)) +
          '</summary><pre>' + esc(window.error) + '</pre></details>'
        : "";
      return '<div class="prep-window-row">' +
        '<span class="page-ref">负责 ' + esc(prepSpanLabel(coreSpan)) + '</span>' +
        badge(prepWindowStatusLabels[window.status] || window.status, window.status) +
        '<span class="muted">读取 ' + esc(prepSpanLabel(window.page_span)) + contextLabel +
          ' · ' + window.candidate_count + " 条候选" + truncationLabel +
          ' ' + badge(prepBoundaryLabels[window.boundary_basis] || window.boundary_basis, boundaryClass) + '</span>' +
        windowAction + error +
        '</div>';
    }).join("");
    const actions = [];
    if (job.status === "running") {
      actions.push('<button class="edit-button danger" type="button" data-prep-action="cancel" data-prep-job-id="' +
        esc(job.id) + '">取消</button>');
    } else if (["queued", "failed", "partial", "cancelled"].includes(job.status)) {
      actions.push('<button class="edit-button" type="button" data-prep-action="run" data-prep-job-id="' +
        esc(job.id) + '">' + (job.status === "queued" ? "开始" : "重试失败页段") + '</button>');
    }
    if (job.promoted_count > 0 && job.workspace_id) {
      actions.push('<button class="edit-button" type="button" data-prep-action="workspace" data-prep-workspace-id="' +
        esc(job.workspace_id) + '">书架 · ' + job.promoted_count + '</button>');
    }
    if (job.rebuild_available && job.status !== "running") {
      actions.push('<button class="edit-button" type="button" data-prep-action="rebuild" data-prep-job-id="' +
        esc(job.id) + '" title="在同一书架中保留旧结果并创建新的分析版本">按当前切分重新分析</button>');
    }
    if (job.status !== "running") {
      actions.push('<button class="edit-button danger" type="button" data-prep-action="delete" data-prep-job-id="' +
        esc(job.id) + '">删除任务</button>');
    }
    return '<article class="prep-job-item">' +
      '<div class="prep-job-head"><div><strong>' + esc(prepProfileLabels[job.scope.profile_id] || job.scope.profile_id) +
      '</strong><p class="muted">' + esc(job.scope.source_file) + ' · ' + esc(prepScopeLabel(job)) +
      '</p></div><div class="tag-row">' +
      badge(prepJobStatusLabels[job.status] || job.status, job.status) +
      badge("分析 v" + (job.analysis_version || 1), "neutral") +
      badge(job.scope.session_minutes + " 分钟", "neutral") +
      badge(job.fake_model ? "FakeLLM" : job.model_id, job.fake_model ? "neutral" : "accent") +
      '</div></div>' +
      '<div class="prep-job-progress"><span>' + job.candidate_count + " 条候选" +
      '</span><span>' + job.windows.filter((item) => item.status === "succeeded").length +
      "/" + job.windows.length + " 个窗口" + '</span></div>' +
      '<div class="prep-window-list">' + windowsHtml + '</div>' +
      '<div class="row-actions">' + actions.join("") + '</div>' +
      '</article>';
  }).join("");
  $("prep-job-list").innerHTML = jobsHtml ||
    '<div class="prep-empty-state"><strong>尚无备团任务</strong><span>从上方选择 PDF 与跨页范围。</span></div>';
  updatePrepModelOptions();
}

async function loadPrepConfig() {
  const response = await fetch("/api/config", {cache: "no-store"});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "模型配置加载失败"));
  state.prep.config = payload;
  $("prep-config-base-url").value = payload.base_url || "";
  $("prep-config-model").value = payload.model || "";
  $("prep-config-fake").checked = Boolean(payload.fake);
  $("prep-config-api-key").value = "";
  $("prep-config-api-key").placeholder = payload.has_key
    ? "已保存密钥；留空保持不变"
    : "输入 API Key";
  renderPrep();
}

async function persistPrepConfig() {
  const payload = {
    base_url: $("prep-config-base-url").value.trim(),
    model: $("prep-config-model").value.trim(),
    fake: $("prep-config-fake").checked
  };
  const apiKey = $("prep-config-api-key").value.trim();
  if (apiKey) payload.api_key = apiKey;
  const response = await fetch("/api/config", {
    method: "PUT",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(result, "模型配置保存失败"));
  state.prep.config = result;
  $("prep-config-api-key").value = "";
  $("prep-config-api-key").placeholder = result.has_key
    ? "已保存密钥；留空保持不变"
    : "输入 API Key";
  renderPrep();
  return result;
}

async function savePrepConfig(event) {
  event.preventDefault();
  if (state.prep.configSaving) return;
  state.prep.configSaving = true;
  $("prep-config-status").textContent = "保存中…";
  try {
    await persistPrepConfig();
    $("prep-config-status").textContent = "已保存";
  } catch (error) {
    $("prep-config-status").textContent = String(error);
  } finally {
    state.prep.configSaving = false;
  }
}

async function testPrepConfig() {
  if (state.prep.configSaving) return;
  state.prep.configSaving = true;
  $("prep-config-status").textContent = "测试中…";
  try {
    const config = await persistPrepConfig();
    if (config.fake) {
      state.prep.models = [config.model];
      $("prep-config-status").textContent = "FakeLLM 可用";
    } else {
      const response = await fetch("/api/models", {cache: "no-store"});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(formatApiError(payload, "模型连接失败"));
      state.prep.models = Array.isArray(payload.models) ? payload.models : [];
      $("prep-config-status").textContent = "连接成功 · " + state.prep.models.length + " 个模型";
    }
    updatePrepModelOptions();
  } catch (error) {
    $("prep-config-status").textContent = String(error);
  } finally {
    state.prep.configSaving = false;
  }
}

function schedulePrepPoll() {
  if (state.prep.pollTimer) clearTimeout(state.prep.pollTimer);
  state.prep.pollTimer = null;
  if (state.prep.jobs.some((job) => job.status === "running")) {
    state.prep.pollTimer = setTimeout(() => loadPrepJobs(), 1400);
  }
}

async function loadPrepJobs() {
  if (state.prep.loading) return;
  state.prep.loading = true;
  try {
    const response = await fetch("/api/domain/prep/jobs", {cache: "no-store"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "备团任务加载失败"));
    state.prep.jobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    state.prep.error = "";
  } catch (error) {
    state.prep.error = String(error);
  } finally {
    state.prep.loading = false;
    renderPrep();
    schedulePrepPoll();
  }
}

async function runPrepJob(jobId) {
  state.prep.error = "";
  const response = await fetch(
    "/api/domain/prep/jobs/" + encodeURIComponent(jobId) + "/run",
    {method: "POST"}
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "备团任务启动失败"));
  await loadPrepJobs();
}

async function rebuildPrepJob(jobId) {
  const job = (state.prep.jobs || []).find((item) => item.id === jobId);
  if (!job || job.status === "running") return;
  if (!confirm("按当前切分重新分析？旧分析及其候选会保留，新版本仍归入同一个书架项目。")) return;
  state.prep.error = "";
  state.prep.notice = "正在同一项目中创建新的分析版本…";
  renderPrep();
  try {
    const response = await fetch(
      "/api/domain/prep/jobs/" + encodeURIComponent(jobId) + "/rebuild",
      {method: "POST"}
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "按当前切分重建失败"));
    const created = payload.job;
    state.prep.notice = created?.id
      ? `已创建分析 v${created.analysis_version || "?"}；旧分析仍保留，书架项目不变。`
      : "已创建新的分析版本；旧分析仍保留，书架项目不变。";
    await loadPrepJobs();
  } catch (error) {
    state.prep.notice = "";
    throw error;
  }
}

async function cancelPrepJob(jobId) {
  state.prep.error = "";
  const response = await fetch(
    "/api/domain/prep/jobs/" + encodeURIComponent(jobId) + "/cancel",
    {method: "POST"}
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "备团任务取消失败"));
  await loadPrepJobs();
}

async function deletePrepJob(jobId) {
  if (!confirm("删除该备团任务及其候选、运行记录？已经提升到书架的事实会保留。")) return;
  state.prep.error = "";
  const response = await fetch(
    "/api/domain/prep/jobs/" + encodeURIComponent(jobId),
    {method: "DELETE"}
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "备团任务删除失败"));
  await Promise.all([loadPrepJobs(), loadReviewQueue()]);
}

function openWorkspace(workspaceId, view = "shelf") {
  const params = new URLSearchParams();
  params.set("example", workspaceId);
  params.set("view", view);
  location.search = params.toString();
}

async function submitPrepJob(event) {
  event.preventDefault();
  if (state.prep.submitting) return;
  state.prep.submitting = true;
  state.prep.error = "";
  renderPrep();
  try {
    const response = await fetch("/api/domain/prep/jobs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        source_file: $("prep-job-source").value,
        page_range: $("prep-job-range").value.trim(),
        profile_id: $("prep-job-profile").value,
        session_minutes: Number($("prep-job-minutes").value)
      })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "备团任务创建失败"));
    await runPrepJob(payload.job.id);
  } catch (error) {
    state.prep.error = String(error);
  } finally {
    state.prep.submitting = false;
    renderPrep();
  }
}

function parseReviewPageRanges(value) {
  const text = String(value || "").trim();
  if (!text) return {valid: true, ranges: []};
  const tokens = text.replace(/[，、]/g, ",").split(/[\s,]+/).filter(Boolean);
  const ranges = [];
  for (const token of tokens) {
    const match = token.match(/^p?(\d+)(?:\s*[-~]\s*p?(\d+))?$/i);
    if (!match) return {valid: false, ranges: []};
    const start = Number(match[1]);
    const end = match[2] ? Number(match[2]) : start;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) {
      return {valid: false, ranges: []};
    }
    ranges.push([start, end]);
  }
  ranges.sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged = [];
  for (const range of ranges) {
    const previous = merged[merged.length - 1];
    if (previous && range[0] <= previous[1] + 1) previous[1] = Math.max(previous[1], range[1]);
    else merged.push(range);
  }
  return {valid: true, ranges: merged};
}

function formatReviewPageRanges(value) {
  const parsed = parseReviewPageRanges(value);
  if (!parsed.valid) return String(value || "").trim();
  return parsed.ranges.map(([start, end]) => start === end ? String(start) : `${start}-${end}`).join(", ");
}

function reviewVisibleCandidates() {
  const pageFilter = parseReviewPageRanges(state.review.sourcePage);
  if (!pageFilter.ranges.length) return pageFilter.valid ? state.review.candidates : [];
  return state.review.candidates.filter((candidate) =>
    reviewSourceRefs(candidate).some((source) => {
      const page = Number(source.page);
      return Number.isSafeInteger(page) && pageFilter.ranges.some(([start, end]) => page >= start && page <= end);
    })
  );
}

function selectedReviewCandidate() {
  return state.review.candidates.find(
    (candidate) => candidate.id === state.review.selectedCandidateId
  ) || null;
}

function nextReviewCandidateId(candidateId, excludedIds = new Set()) {
  const visible = reviewVisibleCandidates();
  const index = visible.findIndex((candidate) => candidate.id === candidateId);
  const ordered = index < 0
    ? visible
    : [...visible.slice(index + 1), ...visible.slice(0, index)];
  return ordered.find((candidate) => !excludedIds.has(candidate.id))?.id || null;
}

async function refreshReviewAfterMutation({previousFilter, preferredId, acceptedId = null}) {
  state.review.selectedCandidateId = preferredId;
  await loadReviewQueue();
  if (previousFilter !== "needs_review" || state.review.reviewState !== "needs_review") return;
  if (state.review.candidates.length) {
    if (!reviewVisibleCandidates().length && state.review.sourcePage) {
      state.review.sourcePage = "";
      state.review.selectedCandidateId = preferredId || state.review.candidates[0]?.id || null;
      state.review.notice = "这一来源页已复核完，已继续显示本任务其余待复核候选。";
      renderReview();
    }
    return;
  }
  state.review.reviewState = "accepted";
  state.review.sourcePage = "";
  state.review.selectedIds = new Set();
  state.review.selectedCandidateId = acceptedId;
  state.review.notice = "待复核候选已全部处理。现在可检查已接受内容并送入书架。";
  await loadReviewQueue();
}

async function loadReviewQueue() {
  if (state.review.loading) return;
  state.review.loading = true;
  state.review.error = "";
  renderReview();
  try {
    const query = new URLSearchParams();
    const prepJobId = prepReviewJobId(state.review.taskId);
    if (!prepJobId && state.review.taskId) query.set("task_id", state.review.taskId);
    query.set("review_state", state.review.reviewState);
    const candidateUrl = prepJobId
      ? "/api/domain/prep/jobs/" + encodeURIComponent(prepJobId) + "/candidates?" + query.toString()
      : "/api/domain/shadow/review-queue?" + query.toString();
    const [tasksResponse, candidatesResponse] = await Promise.all([
      fetch("/api/domain/shadow/tasks", {cache: "no-store"}),
      fetch(candidateUrl, {cache: "no-store"})
    ]);
    const tasksPayload = await tasksResponse.json().catch(() => ({}));
    const candidatesPayload = await candidatesResponse.json().catch(() => ({}));
    if (!tasksResponse.ok) throw new Error(formatApiError(tasksPayload, "影子任务列表加载失败"));
    if (!candidatesResponse.ok) throw new Error(formatApiError(candidatesPayload, "候选队列加载失败"));
    state.review.tasks = Array.isArray(tasksPayload.tasks) ? tasksPayload.tasks : [];
    state.review.candidates = Array.isArray(candidatesPayload.candidates)
      ? candidatesPayload.candidates
      : [];
    const selectedPrepJobId = prepReviewJobId(state.review.taskId);
    const selectedPrepJobExists = selectedPrepJobId
      && (state.prep.jobs || []).some((job) => job.id === selectedPrepJobId);
    if (state.review.taskId && !selectedPrepJobExists &&
        !state.review.tasks.some((task) => task.id === state.review.taskId)) {
      state.review.taskId = "";
    }
    const candidateIds = new Set(state.review.candidates.map((candidate) => candidate.id));
    state.review.selectedIds = new Set(
      [...state.review.selectedIds].filter((candidateId) => candidateIds.has(candidateId))
    );
    if (!candidateIds.has(state.review.selectedCandidateId)) {
      state.review.selectedCandidateId = state.review.candidates[0]?.id || null;
    }
  } catch (error) {
    state.review.error = String(error);
  } finally {
    state.review.loading = false;
    renderReview();
  }
}

async function submitReviewAction(candidateId, reviewState) {
  const candidate = state.review.candidates.find((item) => item.id === candidateId);
  if (!candidate || state.review.saving) return;
  const previousFilter = state.review.reviewState;
  const preferredId = nextReviewCandidateId(candidateId, new Set([candidateId]));
  const reviewedTextInput = $("review-edited-text");
  const reviewNoteInput = $("review-note");
  const reviewedText = reviewedTextInput ? reviewedTextInput.value.trim() : "";
  const reviewNote = reviewNoteInput ? reviewNoteInput.value.trim() : "";
  const payload = {review_state: reviewState};
  const currentText = candidate.reviewed_text || candidate.text;
  const currentNote = candidate.review_note || "";
  if (reviewedText !== currentText) payload.reviewed_text = reviewedText || null;
  if (reviewNote !== currentNote) payload.review_note = reviewNote || null;

  state.review.saving = true;
  state.review.error = "";
  renderReview();
  try {
    const response = await fetch(
      "/api/domain/shadow/candidates/" + encodeURIComponent(candidateId) + "/review",
      {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}
    );
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(result, "候选复核保存失败"));
    state.review.notice = reviewState === "accepted" ? "已接受，继续下一条。" : "已保存，继续下一条。";
    await refreshReviewAfterMutation({
      previousFilter,
      preferredId,
      acceptedId: reviewState === "accepted" ? (result.candidate?.id || candidateId) : null
    });
  } catch (error) {
    state.review.error = String(error);
  } finally {
    state.review.saving = false;
    renderReview();
  }
}

async function submitReviewBatch() {
  const candidateIds = [...state.review.selectedIds];
  if (!candidateIds.length || state.review.saving) return;
  const previousFilter = state.review.reviewState;
  const excludedIds = new Set(candidateIds);
  const preferredId = reviewVisibleCandidates().find((candidate) => !excludedIds.has(candidate.id))?.id || null;
  const note = $("review-batch-note").value.trim();
  const payload = {
    candidate_ids: candidateIds,
    review_state: $("review-batch-state").value
  };
  if (note) payload.review_note = note;

  state.review.saving = true;
  state.review.error = "";
  renderReview();
  try {
    const prepJobId = prepReviewJobId(state.review.taskId);
    const reviewUrl = prepJobId
      ? "/api/domain/prep/jobs/" + encodeURIComponent(prepJobId) + "/candidates/review"
      : "/api/domain/shadow/review/batch";
    const chunks = [];
    for (let index = 0; index < candidateIds.length; index += 100) {
      chunks.push(candidateIds.slice(index, index + 100));
    }
    let processed = 0;
    for (const chunk of chunks) {
      const response = await fetch(reviewUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({...payload, candidate_ids: chunk})
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(formatApiError(result, "批量复核保存失败"));
      processed += chunk.length;
      state.review.selectedIds = new Set(candidateIds.slice(processed));
      state.review.notice = `正在复核 ${processed} / ${candidateIds.length} 条候选。`;
      renderReview();
    }
    state.review.selectedIds = new Set();
    $("review-batch-note").value = "";
    state.review.notice = `已复核 ${candidateIds.length} 条候选，继续当前队列。`;
    await refreshReviewAfterMutation({
      previousFilter,
      preferredId,
      acceptedId: payload.review_state === "accepted" ? candidateIds[0] : null
    });
  } catch (error) {
    state.review.error = String(error);
  } finally {
    state.review.saving = false;
    renderReview();
  }
}

async function promoteReviewCandidate(candidateId, evidenceStatus) {
  if (state.review.saving) return;
  state.review.saving = true;
  state.review.error = "";
  renderReview();
  try {
    const response = await fetch(
      "/api/domain/shadow/candidates/" + encodeURIComponent(candidateId) + "/promote",
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({evidence_status: evidenceStatus})
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "候选提升失败"));
    await Promise.all([loadReviewQueue(), loadPrepJobs(), loadWorkspaces()]);
    const nextCandidate = reviewVisibleCandidates().find((candidate) =>
      candidate.id !== candidateId && candidate.review_state === "accepted" && !candidate.promotion
    );
    state.review.selectedCandidateId = nextCandidate?.id || candidateId;
    state.review.notice = nextCandidate
      ? "已送入书架，继续处理下一条已接受候选。"
      : "当前已接受候选均已送入书架，可打开对应书架继续生成备团产物。";
  } catch (error) {
    state.review.error = String(error);
  } finally {
    state.review.saving = false;
    renderReview();
  }
}

async function promoteReviewBatch() {
  const candidates = state.review.candidates.filter((candidate) =>
    state.review.selectedIds.has(candidate.id)
  );
  if (!candidates.length || state.review.saving) return;
  if (candidates.some((candidate) => candidate.review_state !== "accepted")) {
    state.review.error = "批量提升只接受已通过复核的候选。";
    renderReview();
    return;
  }
  if (candidates.some((candidate) => candidate.promotion)) {
    state.review.error = "已选内容中包含已经进入书架的候选，请取消选择后再试。";
    renderReview();
    return;
  }
  const evidenceStatus = $("review-batch-evidence").value;
  state.review.saving = true;
  state.review.error = "";
  renderReview();
  try {
    for (const candidate of candidates) {
      const response = await fetch(
        "/api/domain/shadow/candidates/" + encodeURIComponent(candidate.id) + "/promote",
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({evidence_status: evidenceStatus})
        }
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(formatApiError(payload, `候选 ${candidate.id} 提升失败`));
      }
    }
    state.review.selectedIds = new Set();
    await Promise.all([loadReviewQueue(), loadPrepJobs(), loadWorkspaces()]);
    const nextCandidate = reviewVisibleCandidates().find((candidate) =>
      candidate.review_state === "accepted" && !candidate.promotion
    );
    state.review.selectedCandidateId = nextCandidate?.id || candidates[0]?.id || null;
    state.review.notice = nextCandidate
      ? `已将 ${candidates.length} 条候选送入书架，继续处理其余已接受候选。`
      : "当前已接受候选均已送入书架，可打开书架继续生成备团产物。";
  } catch (error) {
    state.review.error = String(error);
  } finally {
    state.review.saving = false;
    renderReview();
  }
}

function cardCandidateFactIds(card) {
  const factsById = new Map(state.data.bundle.facts.map((fact) => [fact.id, fact]));
  return (card.fact_ids || []).filter((id) => factEvidenceStatus(factsById.get(id)) === "model_candidate");
}

function cardHasModelCandidate(card) {
  return cardCandidateFactIds(card).length > 0;
}

function artifactProfileId() {
  return state.data.prep_context?.profile_id || state.data.bundle.profile_ids[0] || null;
}

function artifactDraftAvailability() {
  const profileId = artifactProfileId();
  const cards = state.data.bundle.cards.filter((card) => card.profile_id === profileId);
  const usableFacts = state.data.bundle.facts.filter((fact) => factEvidenceStatus(fact) !== "model_candidate");
  const counts = {
    generated: cards.filter((card) => card.edit_state === "generated").length,
    edited: cards.filter((card) => card.edit_state === "edited").length,
    approved: cards.filter((card) => card.edit_state === "approved").length
  };
  if (!profileId || !profile(profileId)) {
    return {ready: false, profileId, cards, counts, guidance: "当前书架没有可用的备团板块。"};
  }
  if (!usableFacts.length) {
    return {ready: false, profileId, cards, counts, guidance: "请先把已接受候选送入书架，形成可追溯事实。"};
  }
  if (cards.length) {
    const pending = counts.generated + counts.edited;
    return {
      ready: false,
      profileId,
      cards,
      counts,
      guidance: pending
        ? `已有 ${cards.length} 项备团产物，其中 ${pending} 项等待批准。请在产物页完成复核。`
        : `已有 ${cards.length} 项已批准备团产物，可以继续组装运行场景。`
    };
  }
  return {
    ready: true,
    profileId,
    cards,
    counts,
    guidance: `将使用书架中的 ${usableFacts.length} 条已提升事实和${profileDisplayName(profileId)}契约生成草案；不会自动批准。`
  };
}

const artifactJobStatusLabels = {
  queued: "排队中",
  running: "生成中",
  completed: "已完成",
  failed: "失败"
};
const artifactJobPhaseLabels = {
  queued: "等待开始",
  direct_generation: "直接生成",
  local_digest: "局部整理",
  global_plan: "全局规划",
  materializing: "回读原始事实落卡",
  validating: "确定性校验",
  completed: "已完成"
};

function artifactJobInFlight(job = state.artifacts.job) {
  return Boolean(job && ["queued", "running"].includes(job.status));
}

function scheduleArtifactPoll() {
  if (state.artifacts.pollTimer) clearTimeout(state.artifacts.pollTimer);
  state.artifacts.pollTimer = null;
  if (artifactJobInFlight()) {
    state.artifacts.pollTimer = setTimeout(() => {
      pollArtifactJob().catch(() => {});
    }, 1400);
  }
}

async function pollArtifactJob({openOnComplete = false} = {}) {
  const job = state.artifacts.job;
  if (!job?.id) return;
  try {
    const response = await fetch(
      "/api/domain/examples/" + encodeURIComponent(state.exampleId) +
      "/cards/draft-jobs/" + encodeURIComponent(job.id),
      {cache: "no-store"}
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "产物生成任务状态读取失败"));
    state.artifacts.job = payload.job || job;
    const current = state.artifacts.job;
    state.artifacts.generating = artifactJobInFlight(current);
    if (current.status === "completed") {
      if (state.artifacts.pollTimer) clearTimeout(state.artifacts.pollTimer);
      state.artifacts.pollTimer = null;
      state.artifacts.error = "";
      await refreshWorkbenchData();
      if (openOnComplete) {
        state.cardProfile = current.profile_id || artifactProfileId();
        $("card-profile").value = state.cardProfile;
        refreshCardTypes();
        state.selectedCardIds = new Set();
        showView("cards");
        updateWorkStatus(`已生成 ${current.card_count || 0} 项备团产物草案`);
      }
    } else if (current.status === "failed") {
      if (state.artifacts.pollTimer) clearTimeout(state.artifacts.pollTimer);
      state.artifacts.pollTimer = null;
      state.artifacts.generating = false;
      state.artifacts.error = current.error
        ? `上次生成在${artifactJobPhaseLabels[current.phase] || "当前阶段"}失败：${current.error}。请点击“重试失败步骤”；已成功完成的步骤会复用。`
        : "备团产物生成失败。请点击“重试失败步骤”；已成功完成的步骤会复用。";
    } else {
      scheduleArtifactPoll();
    }
  } catch (error) {
    state.artifacts.error = String(error);
    state.artifacts.generating = artifactJobInFlight();
    scheduleArtifactPoll();
  }
  renderAll();
}

async function draftArtifacts() {
  const availability = artifactDraftAvailability();
  if (!availability.ready || state.artifacts.generating) return;
  state.artifacts.generating = true;
  state.artifacts.error = "";
  state.artifacts.job = null;
  renderAll();
  try {
    const response = await fetch(
      "/api/domain/examples/" + encodeURIComponent(state.exampleId) + "/cards/draft",
      {method: "POST"}
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "备团产物生成失败"));
    state.artifacts.job = payload.job || null;
    if (!state.artifacts.job) throw new Error("服务器没有返回产物生成任务");
    state.artifacts.generating = artifactJobInFlight();
    updateWorkStatus(
      state.artifacts.job.status === "completed"
        ? `已生成 ${state.artifacts.job.card_count || 0} 项备团产物草案`
        : "备团产物草案已排队，后台生成中"
    );
    renderAll();
    await pollArtifactJob({openOnComplete: true});
  } catch (error) {
    state.artifacts.error = String(error);
    state.artifacts.generating = false;
    renderAll();
  }
}

async function reviewCards(cardIds, action) {
  if (!cardIds.length || state.artifacts.reviewing) return;
  if (state.dirty && !(await saveBundle())) return;
  state.artifacts.reviewing = true;
  state.artifacts.error = "";
  renderCards();
  try {
    const response = await fetch(
      "/api/domain/examples/" + encodeURIComponent(state.exampleId) + "/cards/review",
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({card_ids: cardIds, action})
      }
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(formatApiError(payload, "备团产物复核失败"));
    state.selectedCardIds = new Set();
    await refreshWorkbenchData();
    showView("cards");
    updateWorkStatus(action === "approve" ? `已批准 ${cardIds.length} 项备团产物` : `已退回 ${cardIds.length} 项产物修改`);
  } catch (error) {
    state.artifacts.error = String(error);
  } finally {
    state.artifacts.reviewing = false;
    renderAll();
  }
}

function selectedCardIdsForReview(action) {
  const referencedCardIds = new Set(
    state.data.bundle.plans.flatMap((plan) => plan.card_ids || [])
  );
  return state.data.bundle.cards
    .filter((card) => state.selectedCardIds.has(card.id))
    .filter((card) => action === "approve"
      ? card.edit_state !== "approved"
      : card.edit_state === "approved" && !referencedCardIds.has(card.id))
    .map((card) => card.id);
}

function profile(profileId) {
  return state.data.profiles[profileId];
}

function profileDisplayName(profileId) {
  return prepProfileLabels[profileId] || "备团板块";
}

async function copyKeyword(value) {
  try {
    await navigator.clipboard.writeText(String(value));
    updateWorkStatus("关键词已复制");
  } catch {
    updateWorkStatus("复制失败，请手动选择文本");
  }
}

function profileDisplaySummary(profileId) {
  return prepProfileDescriptions[profileId] || "按当前工作区契约组织备团产物";
}

function enabledProfiles() {
  return state.data.bundle.profile_ids.map((id) => profile(id)).filter((item) => item?.profile_kind === "runtime");
}

function bundleProfiles() {
  return state.data.bundle.profile_ids.map((id) => profile(id)).filter(Boolean);
}

function profileDefinition(profileId, cardType) {
  const item = profile(profileId)?.card_definitions.find((def) => def.type === cardType);
  return item || { type: cardType, display_name: cardType, required_fields: [], optional_fields: [] };
}

function badge(text, cssClass = "") {
  return `<span class="badge ${cssClass}">${esc(text)}</span>`;
}

function csvValues(value) {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

function updateWorkStatus(message = "") {
  const status = $("connection-status");
  const suffix = message || (state.dirty ? "未保存" : "");
  const savedText = state.savedState === "saved" && state.savedAt
    ? `已保存 ${state.savedAt.replace("T", " ").slice(0, 16)}`
    : state.savedState === "invalid" ? "存档无效" : "种子包";
  status.className = `status ${state.dirty ? "loading" : "ok"}`;
  status.textContent = suffix ? `${savedText} · ${suffix}` : savedText;
}

function setDirty(value) {
  state.dirty = value;
  $("save-bundle").disabled = !value || state.saving;
  $("save-bundle").textContent = state.saving ? "保存中…" : "保存更改";
  updateWorkStatus();
}

function openModal(formName) {
  $("editor-modal").hidden = false;
  $("plan-draft-editor").hidden = formName !== "plan-draft";
  $("plan-editor").hidden = formName !== "plan";
  $("source-prep-editor").hidden = formName !== "source-prep";
  $("fact-editor").hidden = formName !== "fact";
  $("card-editor").hidden = formName !== "card";
}

function closeModal() {
  $("editor-modal").hidden = true;
  state.editingFactId = null;
  state.editingCardId = null;
  $("fact-editor-error").textContent = "";
  $("card-editor-error").textContent = "";
  $("plan-draft-error").textContent = "";
  $("plan-editor-error").textContent = "";
  $("source-prep-error").textContent = "";
}

const planCardTypes = [
  "scene", "investigation_site", "environment", "scene_extract", "npc", "character", "character_function",
  "threat", "enemy", "anomaly", "clock", "operation_clock", "encounter_clock"
];

function pageSpanLabel(span) {
  return span.start === span.end ? `p${span.start}` : `p${span.start}-${span.end}`;
}

function compactPageLabel(pages) {
  const ordered = [...new Set(pages)].filter((page) => Number.isInteger(page) && page > 0).sort((a, b) => a - b);
  const spans = [];
  for (const page of ordered) {
    const last = spans[spans.length - 1];
    if (last && page === last.end + 1) last.end = page;
    else spans.push({start: page, end: page});
  }
  return spans.map(pageSpanLabel).join(", ");
}

function sourceContextForCards(cards) {
  const factIds = new Set(cards.flatMap((card) => card.fact_ids));
  const refs = state.data.bundle.facts
    .filter((fact) => factIds.has(fact.id))
    .flatMap(factSourceRefs);
  const files = [...new Set(refs.map((ref) => ref.file).filter(Boolean))];
  return {
    sourceName: files.length === 1 ? files[0].split("/").pop() : `${files.length} 个来源文件`,
    pageRange: compactPageLabel(refs.map((ref) => ref.page)) || "无可用页码"
  };
}

function scenePlanAvailability() {
  const bundle = state.data.bundle;
  const context = state.data.prep_context;
  const profileId = context?.profile_id || bundle.profile_ids.find((id) => profile(id)?.profile_kind === "runtime");
  const targetProfile = profile(profileId);
  if (!targetProfile || targetProfile.profile_kind !== "runtime") {
    return {
      ready: false,
      profileId,
      cards: [],
      guidance: "通用备团用于材料整理，不直接进入运行模式。转换到现实恐怖或奇幻冒险后再组装运行场景。"
    };
  }
  const cards = bundle.cards.filter((card) =>
    card.profile_id === profileId &&
    planCardTypes.includes(card.type) &&
    card.edit_state === "approved" &&
    !cardHasModelCandidate(card)
  );
  if (!cards.length) {
    return {
      ready: false,
      profileId,
      cards,
      guidance: context
        ? `当前书架已有 ${bundle.facts.length} 条已提升事实，尚无已批准的可运行产物。请先生成并批准备团产物；无需重新选择 PDF、板块或页码。`
        : "当前书架尚无可编排产物。请先完成产物生成与复核。"
    };
  }
  const hasScene = cards.some((card) => ["scene", "investigation_site", "environment"].includes(card.type));
  if (!hasScene) {
    return {
      ready: false,
      profileId,
      cards,
      guidance: "当前已批准产物还缺少场景或环境内容，暂不能组装运行场景。"
    };
  }
  return {
    ready: true,
    profileId,
    cards,
    guidance: context
      ? `沿用当前备团任务的来源、页范围、${profileDisplayName(profileId)}板块与 ${context.session_minutes} 分钟时长，自动纳入 ${cards.length} 项已批准产物。`
      : `从当前书架的来源引用恢复范围，并自动纳入 ${cards.length} 项已确认产物。`
  };
}

function renderPlanCardPicker(cards) {
  $("plan-card-picker").innerHTML = cards.map((card) =>
    '<div class="plan-card-option locked"><span><strong>' + esc(card.title) + '</strong><small>' + esc(profileDefinition(card.profile_id, card.type).display_name) + ' · 自动纳入</small></span></div>'
  ).join('') || '<div class="empty-state">尚无可编排产物。</div>';
}

function renderPlanDraftAvailability() {
  const availability = scenePlanAvailability();
  const button = $("draft-plan");
  button.disabled = !availability.ready;
  button.title = availability.ready ? "使用当前书架上下文组装运行场景" : availability.guidance;
  $("plan-draft-guidance").textContent = availability.guidance;
}

function openPlanDraftEditor() {
  const availability = scenePlanAvailability();
  if (!availability.ready) return;
  const context = state.data.prep_context;
  const inferred = sourceContextForCards(availability.cards);
  const sourceName = context?.source_file?.split("/").pop() || inferred.sourceName;
  const pageRange = context?.page_spans?.length
    ? context.page_spans.map(pageSpanLabel).join(", ")
    : inferred.pageRange;
  $("plan-draft-context").innerHTML = `
    <div><span>来源</span><strong>${esc(sourceName)}</strong></div>
    <div><span>备团范围</span><strong>${esc(pageRange)}</strong></div>
    <div><span>目标板块</span><strong>${esc(profileDisplayName(availability.profileId))}</strong></div>
    <div><span>本次时长</span><strong>${context ? `${esc(context.session_minutes)} 分钟` : "沿用书架"}</strong></div>
  `;
  renderPlanCardPicker(availability.cards);
  $("plan-draft-error").textContent = "";
  $("plan-draft-submit").disabled = false;
  openModal('plan-draft');
}

function sourcePagesForFile(file) {
  return state.data.bundle.facts
    .flatMap((fact) => factSourceRefs(fact)
      .filter((source) => source.file === file)
      .map((source) => source.page))
    .filter((page) => Number.isInteger(page) && page > 0);
}

function openSourcePrep() {
  const knownFile = primarySource(state.data.bundle.facts[0])?.file;
  const file = state.sourceFiles.includes(knownFile) ? knownFile : "";
  const pages = sourcePagesForFile(file);
  const start = pages.length ? Math.min(...pages) : 1;
  const end = pages.length ? Math.max(...pages) : start;
  state.sourcePrep = {file, page: start, start, end, pageCount: null, loadedFile: "", loadedPage: null};
  $("prep-source-file").value = file;
  $("prep-start-page").value = start;
  $("prep-end-page").value = end;
  $("prep-page").value = start;
  $("prep-fact-text").value = "";
  $("prep-fact-kind").value = "clue";
  $("prep-fact-visibility").value = "explicit";
  $("prep-fact-locator").value = "";
  $("prep-fact-tags").value = "";
  $("prep-page-status").textContent = "正在读取原文页…";
  openModal("source-prep");
  loadPrepPage();
}

function openSourceReference(file, page) {
  if (!state.sourceFiles.includes(file)) return;
  const pages = sourcePagesForFile(file);
  const start = pages.length ? Math.min(...pages) : Math.max(1, Number(page) || 1);
  const end = pages.length ? Math.max(...pages) : start;
  const selectedPage = Math.max(start, Math.min(end, Number(page) || start));
  state.sourcePrep = {
    file,
    page: selectedPage,
    start,
    end,
    pageCount: null,
    loadedFile: "",
    loadedPage: null
  };
  $("prep-source-file").value = file;
  $("prep-start-page").value = start;
  $("prep-end-page").value = end;
  $("prep-page").value = selectedPage;
  $("prep-page-status").textContent = "正在读取原文页…";
  openModal("source-prep");
  loadPrepPage();
}

function resetSourcePrepPageRange(file) {
  const pages = sourcePagesForFile(file);
  const start = pages.length ? Math.min(...pages) : 1;
  const end = pages.length ? Math.max(...pages) : start;
  state.sourcePrep = {file, page: start, start, end, pageCount: null, loadedFile: "", loadedPage: null};
  $("prep-start-page").value = start;
  $("prep-end-page").value = end;
  $("prep-page").value = start;
  $("prep-source-image").removeAttribute("src");
  $("prep-source-text").textContent = "";
  $("prep-page-status").textContent = file ? "正在读取原文页…" : "请选择来源 PDF";
}

function syncSourcePrepFromForm() {
  const file = $("prep-source-file").value.trim();
  if (file !== state.sourcePrep.file) {
    state.sourcePrep.file = file;
    state.sourcePrep.pageCount = null;
    state.sourcePrep.loadedFile = "";
    state.sourcePrep.loadedPage = null;
  }
  state.sourcePrep.page = Math.max(1, Number($("prep-page").value) || 1);
  state.sourcePrep.start = Math.max(1, Number($("prep-start-page").value) || state.sourcePrep.page);
  state.sourcePrep.end = Math.max(state.sourcePrep.start, Number($("prep-end-page").value) || state.sourcePrep.page);
  $("prep-page").value = state.sourcePrep.page;
  $("prep-start-page").value = state.sourcePrep.start;
  $("prep-end-page").value = state.sourcePrep.end;
}

async function loadPrepPage() {
  syncSourcePrepFromForm();
  const {file, page} = state.sourcePrep;
  const requestedPage = page;
  const image = $("prep-source-image");
  const text = $("prep-source-text");
  if (!file) {
    state.sourcePrep.pageCount = null;
    state.sourcePrep.loadedFile = "";
    state.sourcePrep.loadedPage = null;
    image.removeAttribute("src");
    text.textContent = "请选择一个来源 PDF。";
    $("prep-page-status").textContent = "请选择来源 PDF";
    return;
  }
  $("prep-page-status").textContent = "正在读取原文页…";
  try {
    const response = await fetch("/api/domain/source-page?file=" + encodeURIComponent(file) + "&page=" + page, {cache: "no-store"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "HTTP " + response.status);
    state.sourcePrep.pageCount = payload.page_count;
    state.sourcePrep.start = Math.min(state.sourcePrep.start, payload.page_count);
    state.sourcePrep.end = Math.min(Math.max(state.sourcePrep.start, state.sourcePrep.end), payload.page_count);
    state.sourcePrep.page = Math.min(Math.max(state.sourcePrep.start, state.sourcePrep.page), state.sourcePrep.end);
    $("prep-page").value = state.sourcePrep.page;
    $("prep-start-page").value = state.sourcePrep.start;
    $("prep-end-page").value = state.sourcePrep.end;
    if (state.sourcePrep.page !== requestedPage) {
      return loadPrepPage();
    }
    state.sourcePrep.loadedFile = file;
    state.sourcePrep.loadedPage = state.sourcePrep.page;
    image.src = payload.image_data;
    text.textContent = payload.text || "该页没有可提取的文本层，请以图片为准。";
    const shownPage = state.sourcePrep.page;
    recordSourcePageOpen(file, shownPage, "source_prep");
    if (hasActiveRuntime()) renderRuntime();
    $("prep-page-status").textContent = "第 " + shownPage + " 页 / 共 " + payload.page_count + " 页";
    $("prep-prev-page").disabled = shownPage <= state.sourcePrep.start;
    $("prep-next-page").disabled = shownPage >= Math.min(state.sourcePrep.end, payload.page_count);
  } catch (error) {
    state.sourcePrep.pageCount = null;
    state.sourcePrep.loadedFile = "";
    state.sourcePrep.loadedPage = null;
    image.removeAttribute("src");
    text.textContent = String(error);
    $("prep-page-status").textContent = "原文页读取失败";
  }
}

async function loadSourceFiles() {
  const response = await fetch("/api/domain/source-files", {cache: "no-store"});
  if (!response.ok) throw new Error("来源 PDF 列表加载失败: HTTP " + response.status);
  const payload = await response.json();
  state.sourceFiles = Array.isArray(payload.files) ? payload.files : [];
  state.prep.uploads = Array.isArray(payload.uploads)
    ? payload.uploads
    : state.sourceFiles.filter((file) => file.startsWith("data/uploads/"));
  const resources = Array.isArray(payload.resources)
    ? payload.resources
    : state.sourceFiles.filter((file) => file.startsWith("Resource/"));
  const optionItems = (files) => files.map((file) =>
    '<option value="' + esc(file) + '">' + esc(file.split("/").pop()) + '</option>'
  ).join("");

  const prepSelect = $("prep-source-file");
  const prepSelected = prepSelect.value;
  prepSelect.innerHTML = '<option value="">选择来源 PDF</option>' +
    (state.prep.uploads.length ? '<optgroup label="已上传">' + optionItems(state.prep.uploads) + '</optgroup>' : '') +
    (resources.length ? '<optgroup label="内置参考">' + optionItems(resources) + '</optgroup>' : '');
  if (state.sourceFiles.includes(prepSelected)) prepSelect.value = prepSelected;

  const jobSelect = $("prep-job-source");
  const jobSelected = jobSelect.value;
  jobSelect.innerHTML = '<option value="">' +
    (state.prep.uploads.length ? '选择已上传 PDF' : '请先上传 PDF') + '</option>' +
    optionItems(state.prep.uploads);
  if (state.prep.uploads.includes(jobSelected)) jobSelect.value = jobSelected;
}

async function loadWorkspaces() {
  const response = await fetch("/api/domain/workspaces", {cache: "no-store"});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "书架列表加载失败"));
  state.workspaces = Array.isArray(payload.workspaces) ? payload.workspaces : [];
  fillWorkspaceSelector();
}

async function refreshWorkbenchData() {
  if (!state.exampleId) {
    state.data = null;
    state.artifacts.job = null;
    state.artifacts.generating = false;
    fillWorkspaceSelector();
    renderEmptyWorkspace();
    return;
  }
  const previousProfile = state.cardProfile;
  const previousType = state.cardType;
  const response = await fetch(
    `/api/domain/workbench?example=${encodeURIComponent(state.exampleId)}`,
    {cache: "no-store"}
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "书架刷新失败"));
  state.data = payload;
  state.savedAt = payload.saved_at;
  state.savedState = payload.saved_state;
  state.artifacts.job = payload.artifact_job || null;
  state.artifacts.generating = artifactJobInFlight(state.artifacts.job);
  scheduleArtifactPoll();
  fillSelectors();
  if (previousProfile === "all" || bundleProfiles().some((item) => item.id === previousProfile)) {
    state.cardProfile = previousProfile;
    $("card-profile").value = previousProfile;
    refreshCardTypes();
  }
  if ([...$("card-type").options].some((option) => option.value === previousType)) {
    state.cardType = previousType;
    $("card-type").value = previousType;
  }
  await loadSession();
  renderAll();
}

function renderEmptyWorkspace() {
  const status = $("connection-status");
  if (status) { status.className = "status"; status.textContent = "请选择书架工作区"; }
  const panel = $("global-error");
  if (panel) { panel.hidden = true; panel.textContent = ""; }
  ["shelf-summary", "bundle-detail", "coverage-audit", "fact-grid", "work-card-grid", "plan-list", "scene-holder", "beat-holder", "runtime-exploration-list", "profile-grid"].forEach((id) => {
    const element = $(id);
    if (element) element.innerHTML = '<div class="empty-state">请先从书架选择一个工作区。</div>';
  });
}

function fillWorkspaceSelector() {
  const select = $("example-select");
  if (!select) return;
  const workspaces = state.workspaces.slice();
  let current = workspaces.find((item) => item.id === state.exampleId) || null;
  if (!current && !state.exampleId) {
    select.value = "";
  }
  if (state.data?.bundle && !current && state.exampleId) {
    current = {
      id: state.exampleId,
      name: state.data.bundle.name,
      kind: state.data.has_seed ? "seed" : "saved",
      can_rename: !state.data.has_seed,
      can_delete: !state.data.has_seed
    };
    workspaces.unshift(current);
  }
  select.innerHTML = '<option value="">请选择书架工作区</option>' + workspaces.map((item) =>
    `<option value="${esc(item.id)}">${esc(item.name)}${item.kind === "prep" ? " · 备团" : ""}</option>`
  ).join("");
  select.value = state.exampleId || "";
  const renameButton = $("rename-workspace");
  const deleteButton = $("delete-workspace");
  if (renameButton) {
    renameButton.hidden = !current?.can_rename;
    renameButton.disabled = !current?.can_rename;
    renameButton.title = current?.can_rename ? "修改当前书架项目名称" : "内置种子项目不能重命名";
  }
  if (deleteButton) {
    deleteButton.hidden = !current?.can_delete;
    deleteButton.disabled = !current?.can_delete;
    deleteButton.title = current?.can_delete ? "删除当前书架项目及其关联任务" : "内置种子项目不能删除";
  }
}

function currentWorkspace() {
  return state.workspaces.find((item) => item.id === state.exampleId) || {
    id: state.exampleId,
    name: state.data?.bundle?.name || state.exampleId,
    can_rename: !state.data?.has_seed,
    can_delete: !state.data?.has_seed,
  };
}

async function renameWorkspace() {
  const workspace = currentWorkspace();
  if (!workspace.can_rename) return;
  if (state.dirty && !(await saveBundle())) return;
  const nextName = window.prompt("输入新的书架名称", workspace.name || state.data.bundle.name);
  if (nextName === null) return;
  const name = nextName.trim();
  if (!name) return;
  const response = await fetch(
    "/api/domain/workspaces/" + encodeURIComponent(state.exampleId),
    {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name})
    }
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "书架重命名失败"));
  await Promise.all([loadWorkspaces(), refreshWorkbenchData()]);
  updateWorkStatus("书架名称已更新");
}

async function deleteWorkspace() {
  const workspace = currentWorkspace();
  if (!workspace.can_delete) return;
  if (state.dirty && !(await saveBundle())) return;
  const confirmed = window.confirm(
    `删除“${workspace.name}”？这会同时删除该项目的运行状态、产物任务、提升记录和备团任务，不能撤销。`
  );
  if (!confirmed) return;
  const response = await fetch(
    "/api/domain/workspaces/" + encodeURIComponent(state.exampleId),
    {method: "DELETE"}
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "书架项目删除失败"));
  await loadWorkspaces();
  const next = state.workspaces.find((item) => item.id !== state.exampleId) || null;
  if (next) {
    openWorkspace(next.id, "shelf");
    return;
  }
  location.search = "?view=prep";
}

async function uploadPrepSource() {
  if (state.prep.uploading) return;
  const input = $("prep-source-upload");
  const file = input.files?.[0];
  if (!file) {
    $("prep-source-upload-status").textContent = "请选择一个 PDF 文件";
    return;
  }
  state.prep.uploading = true;
  state.prep.error = "";
  $("prep-source-upload-status").textContent = "正在上传并校验…";
  renderPrep();
  try {
    const body = new FormData();
    body.append("file", file, file.name);
    const response = await fetch("/api/domain/source-files", {method: "POST", body});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "PDF 上传失败");
    await loadSourceFiles();
    $("prep-job-source").value = payload.file;
    $("prep-source-upload-status").textContent =
      "已上传 · " + payload.page_count + " 页 · " + payload.original_name;
    input.value = "";
  } catch (error) {
    $("prep-source-upload-status").textContent = String(error);
  } finally {
    state.prep.uploading = false;
    renderPrep();
  }
}

function shiftPrepPage(delta) {
  syncSourcePrepFromForm();
  const max = state.sourcePrep.pageCount || state.sourcePrep.end;
  state.sourcePrep.page = Math.max(state.sourcePrep.start, Math.min(Math.min(state.sourcePrep.end, max), state.sourcePrep.page + delta));
  $("prep-page").value = state.sourcePrep.page;
  loadPrepPage();
}

function createFactFromPrep() {
  if (!state.editMode) {
    $("source-prep-error").textContent = "请先打开编辑模式，再把确认过的内容保存为事实。";
    return;
  }
  syncSourcePrepFromForm();
  if (!state.sourcePrep.file) {
    $("source-prep-error").textContent = "先指定一个真实 PDF 来源。";
    return;
  }
  if (!state.sourcePrep.pageCount || state.sourcePrep.loadedFile !== state.sourcePrep.file || state.sourcePrep.loadedPage !== state.sourcePrep.page) {
    $("source-prep-error").textContent = "请先成功读取当前页，再把事实加入事实网。";
    return;
  }
  const text = $("prep-fact-text").value.trim();
  if (!text) {
    $("source-prep-error").textContent = "先写下你确认过的短事实；原文预览不会自动变成事实。";
    return;
  }
  const source = {
    file: state.sourcePrep.file,
    page: state.sourcePrep.page,
    locator: $("prep-fact-locator").value.trim() || null
  };
  const fact = {
    id: uniqueId("fact_custom_", state.data.bundle.facts),
    source,
    source_refs: [source],
    evidence_status: "source_fact",
    text,
    kind: $("prep-fact-kind").value,
    visibility: $("prep-fact-visibility").value,
    links: [],
    tags: csvValues($("prep-fact-tags").value)
  };
  state.data.bundle.facts.push(fact);
  recordFieldChanges('fact', fact.id, {}, fact);
  setDirty(true);
  $("prep-fact-text").value = "";
  $("prep-fact-locator").value = "";
  $("prep-fact-tags").value = "";
  $("source-prep-error").textContent = "已加入未保存事实；可以继续浏览下一页。";
  renderFacts();
}

function openPlanEditor(planId) {
  const plan = state.data.bundle.plans.find((item) => item.id === planId);
  if (!plan) return;
  state.editingPlanId = planId;
  $("plan-json").value = JSON.stringify(plan, null, 2);
  openModal('plan');
}

async function submitPlanDraft(event) {
  event.preventDefault();
  const submitButton = $("plan-draft-submit");
  submitButton.disabled = true;
  try {
    const response = await fetch('/api/domain/examples/' + encodeURIComponent(state.exampleId) + '/plans/draft', {
      method: 'POST'
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'HTTP ' + response.status);
    state.data.bundle.plans = state.data.bundle.plans.filter((plan) => plan.id !== payload.plan.id);
    state.data.bundle.plans.push(payload.plan);
    state.savedState = 'saved';
    state.savedAt = payload.saved_at;
    if (!state.session?.current_plan_id) state.runtimeProfileId = payload.plan.profile_id;
    closeModal();
    await loadSession();
    renderAll();
    showView("shelf");
    updateWorkStatus('已保存草案；请检查节拍与开场描述，再点击“开始运行”');
  } catch (error) {
    $("plan-draft-error").textContent = String(error);
    submitButton.disabled = false;
  }
}

async function submitPlanEditor(event) {
  event.preventDefault();
  let edited;
  try { edited = JSON.parse($("plan-json").value); } catch (error) {
    $("plan-editor-error").textContent = 'JSON 错误：' + error.message;
    return;
  }
  const planIndex = state.data.bundle.plans.findIndex((plan) => plan.id === state.editingPlanId);
  if (planIndex < 0) return;
  const previousPlan = state.data.bundle.plans[planIndex];
  const plans = state.data.bundle.plans.slice();
  plans[planIndex] = edited;
  const candidate = {...state.data.bundle, plans};
  try {
    const response = await fetch('/api/domain/examples/' + encodeURIComponent(state.exampleId) + '/bundle', {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(candidate)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'HTTP ' + response.status);
    state.data.bundle.plans = plans;
    state.savedAt = payload.saved_at;
    state.savedState = 'saved';
    await loadSession();
    recordFieldChanges('plan', edited.id, previousPlan, edited);
    closeModal();
    renderAll();
    updateWorkStatus('场景计划已保存');
  } catch (error) {
    $("plan-editor-error").textContent = String(error);
  }
}

function openFactEditor(factId) {
  const fact = state.data.bundle.facts.find((item) => item.id === factId);
  if (!fact) return;
  const source = primarySource(fact);
  $("fact-page").required = false;
  $("fact-source-file").required = false;
  state.editingFactId = factId;
  $("fact-editor-title").textContent = `编辑事实 · ${fact.id}`;
  $("fact-text").value = fact.text;
  $("fact-kind-edit").value = fact.kind;
  $("fact-visibility-edit").value = fact.visibility;
  $("fact-evidence-status").value = factEvidenceStatus(fact);
  $("fact-page").value = source?.page || "";
  $("fact-locator").value = source?.locator || "";
  $("fact-source-file").value = source?.file || "";
  $("fact-source-preview").hidden = true;
  $("fact-tags").value = fact.tags.join(", ");
  $("fact-links").value = fact.links.join(", ");
  $("fact-notes").value = fact.notes || "";
  $("fact-editor-error").textContent = "";
  openModal("fact");
}

async function previewFactSource() {
  const fact = state.data.bundle.facts.find((item) => item.id === state.editingFactId);
  if (!fact) return;
  const preview = $("fact-source-preview");
  const text = $("fact-source-text");
  const image = $("fact-source-image");
  text.textContent = "正在读取原文页…";
  preview.hidden = false;
  try {
    const page = Number($("fact-page").value);
    const file = $("fact-source-file").value.trim();
    if (!file || !Number.isInteger(page) || page < 1) {
      throw new Error("A PDF source file and page are required for preview.");
    }
    const response = await fetch(
      "/api/domain/source-page?file=" + encodeURIComponent(file) + "&page=" + page,
      {cache: "no-store"}
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "HTTP " + response.status);
    image.src = payload.image_data;
    text.textContent = payload.text || "该页没有可提取的文本层，请以图片为准。";
    recordSourcePageOpen(file, page, "fact_preview");
    if (hasActiveRuntime()) renderRuntime();
  } catch (error) {
    image.removeAttribute("src");
    text.textContent = String(error);
  }
}

function submitFactEditor(event) {
  event.preventDefault();
  const fact = state.data.bundle.facts.find((item) => item.id === state.editingFactId);
  if (!fact) return;
  const before = JSON.parse(JSON.stringify(fact));
  const links = csvValues($("fact-links").value);
  const factIds = new Set(state.data.bundle.facts.map((item) => item.id));
  if (links.includes(fact.id)) {
    $("fact-editor-error").textContent = "事实不能关联自身。";
    return;
  }
  const missing = links.filter((id) => !factIds.has(id));
  if (missing.length) {
    $("fact-editor-error").textContent = `缺少关联事实：${missing.join(", ")}`;
    return;
  }
  const evidenceStatus = $("fact-evidence-status").value;
  const sourceFile = $("fact-source-file").value.trim();
  const sourcePageValue = $("fact-page").value.trim();
  const sourcePage = Number(sourcePageValue);
  const sourceLocator = $("fact-locator").value.trim() || null;
  const sourceRequired = evidenceStatus === "source_fact" || evidenceStatus === "inference";
  const sourceEntered = Boolean(sourceFile || sourcePageValue || sourceLocator);
  if ((sourceRequired || sourceEntered) && (!sourceFile || !Number.isInteger(sourcePage) || sourcePage < 1)) {
    $("fact-editor-error").textContent = "原文事实和可验证推断必须有完整的 PDF 来源和页码。";
    return;
  }
  fact.text = $("fact-text").value.trim();
  fact.kind = $("fact-kind-edit").value;
  fact.visibility = $("fact-visibility-edit").value;
  fact.evidence_status = evidenceStatus;
  const existingRefs = factSourceRefs(fact);
  if (sourceEntered) {
    const source = {...existingRefs[0], file: sourceFile, page: sourcePage, locator: sourceLocator};
    fact.source = source;
    fact.source_refs = [source, ...existingRefs.slice(1)];
  } else {
    fact.source = null;
    fact.source_refs = [];
  }
  fact.tags = csvValues($("fact-tags").value);
  fact.links = links;
  fact.notes = $("fact-notes").value.trim() || null;
  const citedCards = state.data.bundle.cards.filter((card) => card.fact_ids.includes(fact.id));
  citedCards.forEach((card) => { card.edit_state = "edited"; });
  recordFieldChanges('fact', fact.id, before, fact);
  closeModal();
  setDirty(true);
  renderAll();
  updateWorkStatus(`已更新事实；请检查 ${citedCards.length} 张引用卡`);
}

function openCardEditor(cardId) {
  const card = state.data.bundle.cards.find((item) => item.id === cardId);
  if (!card) return;
  state.editingCardId = cardId;
  $("card-profile-edit").innerHTML = bundleProfiles().map((item) =>
    `<option value="${esc(item.id)}">${esc(profileDisplayName(item.id))}</option>`
  ).join("");
  $("card-profile-edit").value = card.profile_id;
  refreshCardEditorTypes(card.profile_id, card.type);
  $("card-editor-title").textContent = `编辑备团产物 · ${card.title}`;
  $("card-title").value = card.title;
  $("card-subtitle").value = card.subtitle || "";
  $("card-fact-ids").value = card.fact_ids.join(", ");
  $("card-fields").value = JSON.stringify(card.fields, null, 2);
  $("card-editor-error").textContent = "";
  openModal("card");
}

function refreshCardEditorTypes(profileId, selectedType = "") {
  const definitions = profile(profileId)?.card_definitions || [];
  $("card-type-edit").innerHTML = definitions.map((item) =>
    `<option value="${esc(item.type)}">${esc(item.display_name)}</option>`
  ).join("");
  if (definitions.some((item) => item.type === selectedType)) {
    $("card-type-edit").value = selectedType;
  }
}

async function submitCardEditor(event) {
  event.preventDefault();
  const card = state.data.bundle.cards.find((item) => item.id === state.editingCardId);
  if (!card) return;
  const before = JSON.parse(JSON.stringify(card));
  const profileId = $("card-profile-edit").value;
  const cardType = $("card-type-edit").value;
  if (!profile(profileId)?.card_definitions.some((item) => item.type === cardType)) {
    $("card-editor-error").textContent = "所选板块没有这个卡型。";
    return;
  }
  const affectedPlans = state.data.bundle.plans.filter((plan) => plan.card_ids.includes(card.id));
  if (affectedPlans.some((plan) => plan.profile_id !== profileId)) {
    $("card-editor-error").textContent = "这张卡已被其他板块的场景计划引用，不能跨板块改写。";
    return;
  }
  const sceneTypes = ["scene", "investigation_site", "environment"];
  if (affectedPlans.some((plan) => sceneTypes.includes(card.type) && !sceneTypes.includes(cardType))) {
    $("card-editor-error").textContent = "这张卡是计划的场景卡，不能改成非场景卡型。";
    return;
  }
  let fields;
  try {
    fields = JSON.parse($("card-fields").value);
  } catch (error) {
    $("card-editor-error").textContent = `JSON 错误：${error.message}`;
    return;
  }
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) {
    $("card-editor-error").textContent = "字段必须是 JSON 对象。";
    return;
  }
  const factIds = new Set(state.data.bundle.facts.map((item) => item.id));
  const citedIds = csvValues($("card-fact-ids").value);
  const missing = citedIds.filter((id) => !factIds.has(id));
  if (missing.length) {
    $("card-editor-error").textContent = `缺少引用事实：${missing.join(", ")}`;
    return;
  }
  const definition = profileDefinition(profileId, cardType);
  const missingFields = definition.required_fields.filter((key) => {
    const value = fields[key];
    return value === undefined || value === null || value === "" ||
      (Array.isArray(value) && !value.length) ||
      (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length);
  });
  if (missingFields.length) {
    $("card-editor-error").textContent = `缺少必填字段：${missingFields.join(", ")}`;
    return;
  }
  card.title = $("card-title").value.trim();
  card.subtitle = $("card-subtitle").value.trim() || null;
  card.profile_id = profileId;
  card.type = cardType;
  card.fact_ids = citedIds;
  card.fields = fields;
  card.field_sources = Object.fromEntries(Object.keys(fields).map((key) => {
    const retained = (card.field_sources?.[key] || []).filter((factId) => citedIds.includes(factId));
    return [key, retained.length ? retained : [...citedIds]];
  }));
  card.edit_state = "edited";
  recordFieldChanges('card', card.id, before, card);
  setDirty(true);
  renderAll();
  if (await saveBundle()) {
    closeModal();
    updateWorkStatus("备团产物已保存，等待批准");
  } else {
    $("card-editor-error").textContent = "保存失败，修改仍保留在当前页面。";
  }
}

function uniqueId(prefix, collection) {
  const ids = new Set(collection.map((item) => item.id));
  let index = 1;
  while (ids.has(`${prefix}${index}`)) index += 1;
  return `${prefix}${index}`;
}

function createFact() {
  const facts = state.data.bundle.facts;
  const fact = {
    id: uniqueId("fact_custom_", facts),
    source: null,
    source_refs: [],
    evidence_status: "gm_authored",
    text: "新事实：写下这条场景事实。",
    kind: "clue",
    visibility: "gm_suggestion",
    links: [],
    tags: ["自定义"]
  };
  facts.push(fact);
  setDirty(true);
  showView("facts");
  renderAll();
  openFactEditor(fact.id);
  updateWorkStatus("已新建事实");
}

function deleteFact(factId) {
  const fact = state.data.bundle.facts.find((item) => item.id === factId);
  if (!fact || !confirm(`删除事实“${fact.text.slice(0, 24)}…”？引用它的备团产物会移除该来源。`)) return;
  state.data.bundle.facts = state.data.bundle.facts.filter((item) => item.id !== factId);
  state.data.bundle.facts.forEach((item) => {
    item.links = item.links.filter((id) => id !== factId);
  });
  state.data.bundle.plans.forEach((plan) => {
    plan.beats.forEach((beat) => {
      beat.reveal_fact_ids = beat.reveal_fact_ids.filter((id) => id !== factId);
    });
  });
  const affected = state.data.bundle.cards.filter((card) => card.fact_ids.includes(factId));
  state.data.bundle.cards.forEach((card) => {
    card.fact_ids = card.fact_ids.filter((id) => id !== factId);
  });
  affected.forEach((card) => { card.edit_state = "edited"; });
  setDirty(true);
  renderAll();
  updateWorkStatus(`已删除事实；更新了 ${affected.length} 张卡`);
}

function createCard() {
  const profiles = state.data.profiles;
  const enabledProfileIds = new Set(bundleProfiles().map((item) => item.id));
  const preferredProfileId = state.cardProfile !== "all"
    && enabledProfileIds.has(state.cardProfile)
    ? state.cardProfile
    : (enabledProfileIds.has("cthulhu-dark-2e")
      ? "cthulhu-dark-2e"
      : bundleProfiles()[0]?.id);
  const selectedProfile = profiles[preferredProfileId];
  if (!selectedProfile) return;
  const preferredType = state.cardType !== "all" &&
    selectedProfile.card_definitions.some((item) => item.type === state.cardType)
    ? state.cardType
    : selectedProfile.card_definitions[0]?.type;
  if (!preferredType) return;
  const definition = profileDefinition(preferredProfileId, preferredType);
  const cards = state.data.bundle.cards;
  const exemplar = cards.find((item) =>
    item.profile_id === preferredProfileId && item.type === preferredType
  );
  const fields = Object.fromEntries(definition.required_fields.map((key) => [
    key,
    exemplar?.fields[key] !== undefined
      ? JSON.parse(JSON.stringify(exemplar.fields[key]))
      : (key === "evidence_chain" ? {} : "")
  ]));
  const defaultFact = state.data.bundle.facts.find(
    (fact) => factEvidenceStatus(fact) !== "model_candidate"
  );
  const card = {
    id: uniqueId("card_custom_", cards),
    profile_id: preferredProfileId,
    type: preferredType,
    title: "新备团产物",
    subtitle: null,
    fact_ids: defaultFact ? [defaultFact.id] : [],
    fields,
    edit_state: "edited"
  };
  cards.push(card);
  state.cardProfile = preferredProfileId;
  state.cardType = preferredType;
  setDirty(true);
  showView("cards");
  fillSelectors();
  renderAll();
  openCardEditor(card.id);
  updateWorkStatus("已新建备团产物");
}

async function deleteCard(cardId) {
  const card = state.data.bundle.cards.find((item) => item.id === cardId);
  if (!card || !confirm(`删除备团产物“${card.title}”？`)) return;
  const affectedPlans = state.data.bundle.plans.filter((plan) => plan.card_ids.includes(cardId));
  if (affectedPlans.length) {
    updateWorkStatus("这项产物已被运行场景引用；请先删除对应运行场景");
    return;
  }
  if (state.dirty) {
    state.data.bundle.cards = state.data.bundle.cards.filter((item) => item.id !== cardId);
    state.selectedCardIds.delete(cardId);
    setDirty(true);
    renderAll();
    updateWorkStatus("已从当前未保存修改中删除备团产物");
    return;
  }
  state.artifacts.error = "";
  try {
    const response = await fetch(
      "/api/domain/examples/" + encodeURIComponent(state.exampleId) + "/cards/" + encodeURIComponent(cardId),
      {method: "DELETE"}
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || "备团产物删除失败");
    state.selectedCardIds.delete(cardId);
    await refreshWorkbenchData();
    updateWorkStatus("已删除备团产物草案");
  } catch (error) {
    state.artifacts.error = String(error);
    renderAll();
  }
}

function formatValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return `• ${item}`;
      if (item && typeof item === "object") {
        return Object.entries(item).map(([key, inner]) => `${label(key)}：${inner}`).join("；");
      }
      return `• ${String(item)}`;
    }).join("\n");
  }
  if (value && typeof value === "object") {
    return Object.entries(value).map(([key, inner]) => `${label(key)}：${inner}`).join("\n");
  }
  return String(value ?? "");
}

function fieldSourceSummary(card, fieldName) {
  const sourceIds = card.field_sources?.[fieldName] || [];
  if (!sourceIds.length) return "";
  const factsById = new Map(state.data.bundle.facts.map((fact) => [fact.id, fact]));
  const pages = sourceIds.flatMap((factId) =>
    factSourceRefs(factsById.get(factId)).map((source) => source.page)
  );
  const pageLabel = compactPageLabel(pages);
  return pageLabel ? `字段来源：${pageLabel}` : `字段来源：${sourceIds.join(", ")}`;
}

function fieldsHtml(card) {
  return Object.entries(card.fields).map(([key, value]) => `
    <div class="field">
      <div class="field-label">${esc(label(key))}</div>
      <div class="field-value">${esc(formatValue(value))}</div>
      ${fieldSourceSummary(card, key) ? `<span class="card-field-source">${esc(fieldSourceSummary(card, key))}</span>` : ""}
    </div>
  `).join("");
}

function cardOpenQuestionsHtml(card) {
  const questions = Array.isArray(card.open_questions) ? card.open_questions : [];
  if (!questions.length) return "";
  return `<section class="card-open-questions"><strong>待 GM 确认</strong><ul>${questions.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></section>`;
}

function cardGenerationHtml(card) {
  if (!card.generation) return "";
  const generatedAt = String(card.generation.generated_at || "").replace("T", " ").slice(0, 16);
  return `<div class="card-generation-note">模型草案 · ${esc(card.generation.model_id)} · ${esc(generatedAt)}</div>`;
}

function sourceHtml(factIds) {
  const factsById = new Map(state.data.bundle.facts.map((fact) => [fact.id, fact]));
  const cited = factIds.map((id) => factsById.get(id)).filter(Boolean);
  if (!cited.length) return "";
  const lines = cited.map((fact) => {
    const refs = factSourceRefs(fact);
    const source = refs.length ? refs.map(sourceRefLabel).join("; ") : "无原文来源";
    const sourceButtons = refs
      .filter((item) => state.sourceFiles.includes(item.file))
      .map((item) =>
        '<button class="edit-button" type="button" data-open-source-file="' + esc(item.file) +
        '" data-open-source-page="' + esc(item.page) + '">查看 p' + esc(item.page) + '</button>'
      )
      .join("");
    return '<li>' + esc(fact.text) + ' <button type="button" class="icon-button" title="复制事实关键词" data-copy-keyword="' + esc(fact.text) + '">复制</button> ' +
      badge(label(factEvidenceStatus(fact)), factEvidenceStatus(fact)) +
      ' <span class="page-ref">' + esc(source) + '</span>' +
      (sourceButtons ? '<div class="row-actions">' + sourceButtons + '</div>' : '') +
      '</li>';
  }).join("");
  return `<details class="field"><summary class="field-label">来源事实</summary><ul>${lines}</ul></details>`;
}

function runtimeCards(cardTypes) {
  const plan = currentPlan();
  if (!plan) return [];
  const planCardIds = plan ? new Set(plan.card_ids) : null;
  return state.data.bundle.cards.filter((card) =>
    card.profile_id === state.runtimeProfileId &&
    cardTypes.includes(card.type) &&
    (!planCardIds || planCardIds.has(card.id))
  );
}

function hasActiveRuntime() {
  return Boolean(state.session && currentPlan());
}

function sessionLog(kind, text, options = {}) {
  if (!state.session || !text.trim()) return;
  const plan = currentPlan();
  state.session.log.push({
    id: 'log_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7),
    kind,
    text: text.trim(),
    created_at: new Date().toISOString(),
    subject_type: options.subjectType || null,
    subject_id: options.subjectId || null,
    plan_id: options.planId || plan?.id || state.session.current_plan_id || null,
    card_id: options.cardId || state.session.current_card_id || null,
    beat_id: options.beatId || state.session.current_beat_id || null,
    metadata: options.metadata || {}
  });
  state.session.log = state.session.log.slice(-200);
  state.sessionDirty = true;
}

function recordLookup(subjectType, subjectId, text, cardId = null) {
  if (!hasActiveRuntime()) return;
  sessionLog('lookup', text, {
    subjectType,
    subjectId,
    cardId,
    metadata: {subject_type: subjectType}
  });
}

function recordSourcePageOpen(file, page, reason) {
  if (!hasActiveRuntime()) return;
  sessionLog('source_page_opened', '打开来源页：' + file + ' · p' + page, {
    subjectType: 'source_page',
    subjectId: file + ':p' + page,
    metadata: {file, page, reason}
  });
}

function changedFieldPaths(before, after, prefix = '', depth = 0) {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  const beforeObject = before && typeof before === 'object' && !Array.isArray(before);
  const afterObject = after && typeof after === 'object' && !Array.isArray(after);
  if (!beforeObject || !afterObject || depth >= 2) return [prefix || 'value'];
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  return [...keys].flatMap((key) => changedFieldPaths(
    before[key],
    after[key],
    prefix ? prefix + '.' + key : key,
    depth + 1
  ));
}

function recordFieldChanges(entityType, entityId, before, after) {
  if (!hasActiveRuntime()) return;
  const paths = changedFieldPaths(before, after);
  const visiblePaths = paths.slice(0, 12);
  visiblePaths.forEach((fieldPath) => {
    sessionLog('field_edited', '手工改写字段：' + entityType + '.' + fieldPath, {
      subjectType: 'field',
      subjectId: entityType + ':' + entityId,
      metadata: {entity_type: entityType, entity_id: entityId, field_path: fieldPath}
    });
  });
  if (paths.length > visiblePaths.length) {
    sessionLog('field_edited', '手工改写字段：' + entityType + ' 还有 ' + (paths.length - visiblePaths.length) + ' 项', {
      subjectType: 'field',
      subjectId: entityType + ':' + entityId,
      metadata: {entity_type: entityType, entity_id: entityId, field_path: 'additional_fields'}
    });
  }
}

function clueKey(card, group, index) {
  return card.id + ':' + group + ':' + index;
}

function isRevealed(key) {
  return state.session?.revealed_clue_keys.includes(key);
}

function clueHtml(card, group, values) {
  if (!Array.isArray(values) || !values.length) return "";
  const title = group === "direct" ? "可直接给出的线索" : "隐藏线索";
  const rows = values.map((value, index) => {
    const key = clueKey(card, group, index);
    const revealed = isRevealed(key);
    return '<li class="runtime-clue ' + (revealed ? 'revealed' : 'unrevealed') + '">' +
      '<div class="runtime-clue-copy"><span class="runtime-clue-status">' +
      (revealed ? '已揭示' : '未揭示') + '</span><span class="runtime-clue-text">' +
      esc(value) + '</span></div>' +
      (revealed
        ? '<span class="runtime-clue-recorded">已记录</span>'
        : '<button class="edit-button" type="button" data-reveal-clue="' + esc(key) +
          '" data-reveal-text="' + esc(value) + '">标记已揭示</button>') +
      '</li>';
  }).join("");
  return '<div class="runtime-section"><h3>' + title + '</h3><ul class="runtime-clues">' + rows + '</ul></div>';
}

function currentRuntimeCard() {
  const cards = runtimeCards(["scene", "investigation_site", "environment"]);
  return cards.find((card) => card.id === state.session?.current_card_id) || cards[0] || null;
}

function currentPlan() {
  if (!state.session?.current_plan_id) return null;
  return state.data.bundle.plans.find((plan) => plan.id === state.session.current_plan_id) || null;
}

function currentBeat() {
  const plan = currentPlan();
  return plan?.beats.find((beat) => beat.id === state.session?.current_beat_id) || plan?.beats[0] || null;
}

function previewList(title, values) {
  if (!Array.isArray(values) || !values.length) return "";
  return '<div class="plan-preview-block"><strong>' + esc(title) + '</strong><ul>' +
    values.map((value) => '<li>' + esc(value) + '</li>').join("") + '</ul></div>';
}

function planReviewHtml(plan) {
  const cardsById = new Map(state.data.bundle.cards.map((card) => [card.id, card]));
  const factsById = new Map(state.data.bundle.facts.map((fact) => [fact.id, fact]));
  return '<details class="plan-review"><summary>审阅 ' + plan.beats.length + ' 个节拍</summary><div class="plan-beat-list">' +
    plan.beats.map((beat, index) => {
      const sourcePages = beat.source_pages?.length ? beat.source_pages : (plan.source_pages || []);
      const cards = (beat.card_ids || []).map((id) => cardsById.get(id)).filter(Boolean);
      const facts = (beat.reveal_fact_ids || []).map((id) => factsById.get(id)).filter(Boolean);
      const cardTags = cards.length
        ? '<div class="tag-row">' + cards.map((card) => badge(card.title)).join("") + '</div>'
        : '<span class="muted">本拍未指定参考卡</span>';
      const factTags = facts.length
        ? '<div class="plan-preview-facts">可调用事实：' + facts.map((fact) => esc(fact.text)).join(' · ') + '</div>'
        : '';
      return '<article class="plan-beat-preview">' +
        '<div class="plan-beat-head"><div><span class="badge accent">' + (index + 1) + ' · ' + esc(beatModeLabels[beat.mode] || beat.mode) + '</span><strong>' + esc(beat.title) + '</strong></div>' +
          '<span class="page-ref">' + (sourcePages.length ? sourcePages.map((page) => 'p' + esc(page)).join(' ') : '无页码') + '</span></div>' +
        '<div class="plan-preview-grid"><div><strong>描述方向</strong><p>' + esc(beat.framing || beat.situation || '') + '</p></div><div><strong>局势理解</strong><p>' + esc(beat.situation || '') + '</p></div></div>' +
        (beat.rule_focus ? '<div class="plan-rule-focus"><strong>本板块的运行关注</strong><p>' + esc(beat.rule_focus) + '</p></div>' : '') +
        previewList('软提示', beat.soft_cues) + previewList('硬推进', beat.hard_cues) +
        previewList('可以问自己', beat.question_prompts) + previewList('何时离开', beat.exit_when) +
        '<div class="plan-preview-block"><strong>参考卡</strong>' + cardTags + factTags + '</div>' +
        '</article>';
    }).join("") + '</div></details>';
}

function renderPlans() {
  const plans = state.data.bundle.plans || [];
  $("plan-list").innerHTML = plans.map((plan) =>
    '<article class="plan-item ' + (plan.id === state.session?.current_plan_id ? 'selected' : '') + '">' +
      '<div class="plan-item-body"><div><strong>' + esc(plan.title) + '</strong><p class="muted">' + esc(plan.premise) + '</p><div class="plan-source-summary">来源：' + esc(plan.source_file) + (plan.source_pages?.length ? ' · ' + plan.source_pages.map((page) => 'p' + esc(page)).join(' ') : '') + '</div></div>' +
        planReviewHtml(plan) + '</div>' +
      '<div class="row-actions"><span class="badge accent">' + plan.beats.length + ' 节拍</span>' +
        (plan.id === state.session?.current_plan_id ? '<span class="badge">当前运行</span>' : '<span class="badge">待开始</span>') +
        '<button class="edit-button" data-edit-plan="' + esc(plan.id) + '">查看场景</button>' +
        '<button class="edit-button danger" data-delete-plan="' + esc(plan.id) + '">删除场景</button>' +
        (plan.id === state.session?.current_plan_id ? '<button class="edit-button" data-resume-plan="' + esc(plan.id) + '">回到运行</button><button class="edit-button" data-start-plan="' + esc(plan.id) + '">重新开始</button>' : '<button class="edit-button" data-start-plan="' + esc(plan.id) + '">开始运行</button>') +
        '</div>' +
    '</article>'
  ).join('') || '<div class="empty-state">还没有运行场景。已批准产物就绪后，可在这里组装草案。</div>';
}

async function deletePlan(planId) {
  const plan = state.data.bundle.plans.find((item) => item.id === planId);
  if (!plan || !window.confirm(`删除“${plan.title}”？这会同时清除该场景的运行状态与日志。`)) return;
  const response = await fetch(`/api/domain/examples/${encodeURIComponent(state.exampleId)}/plans/${encodeURIComponent(planId)}`, {method: "DELETE"});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatApiError(payload, "删除运行场景失败"));
  state.data.bundle.plans = state.data.bundle.plans.filter((item) => item.id !== planId);
  if (state.session?.current_plan_id === planId) {
    state.session.current_plan_id = null;
    state.session.current_beat_id = null;
    state.session.current_card_id = null;
  }
  renderAll();
}

function renderBeat() {
  const beat = currentBeat();
  const plan = currentPlan();
  if (!beat || !plan) {
    $("beat-holder").innerHTML = '<div class="empty-state">尚未开始场景计划。先在书架检查草案，再点击“开始运行”；这里不会替 GM 决定玩家行动。</div>';
    return;
  }
  const beatIndex = plan.beats.findIndex((item) => item.id === beat.id);
  const framing = beat.framing || beat.situation || '';
  const sourcePages = beat.source_pages?.length ? beat.source_pages : (plan.source_pages || []);
  const softCues = Array.isArray(beat.soft_cues) ? beat.soft_cues : [];
  const hardCues = Array.isArray(beat.hard_cues) ? beat.hard_cues : [];
  const questionPrompts = Array.isArray(beat.question_prompts) ? beat.question_prompts : [];
  const exitWhen = Array.isArray(beat.exit_when) ? beat.exit_when : [];
  const cardsById = new Map(state.data.bundle.cards.map((card) => [card.id, card]));
  const factsById = new Map(state.data.bundle.facts.map((fact) => [fact.id, fact]));
  const referenceCards = (beat.card_ids || []).map((id) => cardsById.get(id)).filter(Boolean);
  const referenceFacts = (beat.reveal_fact_ids || []).map((id) => factsById.get(id)).filter(Boolean);
  const cardReferences = referenceCards.length
    ? '<div class="beat-reference"><h3>本拍参考卡（GM 内部）</h3><div class="tag-row">' + referenceCards.map((card) => badge(card.title + ' · ' + profileDefinition(card.profile_id, card.type).display_name)).join('') + '</div></div>'
    : '';
  const factReferences = referenceFacts.length
    ? '<div class="beat-reference"><h3>本拍可调用事实（GM 内部）</h3><ul class="beat-facts">' + referenceFacts.map((fact) => '<li>' + esc(fact.text) + ' ' + badge(label(factEvidenceStatus(fact)), factEvidenceStatus(fact)) + ' <span class="page-ref">' + esc(factSourceLabel(fact)) + '</span></li>').join('') + '</ul></div>'
    : '';
  $("beat-holder").innerHTML =
    '<section class="panel beat-panel"><div class="panel-head"><div><span class="badge accent">节拍 ' + (beatIndex + 1) + ' / ' + plan.beats.length + '</span><h2>' + esc(beat.title) + '</h2></div>' +
    '<div class="row-actions"><button class="edit-button" data-beat-delta="-1" ' + (beatIndex <= 0 ? 'disabled' : '') + '>上一拍</button><button class="edit-button" data-beat-delta="1" ' + (beatIndex >= plan.beats.length - 1 ? 'disabled' : '') + '>下一拍</button></div></div>' +
    '<div class="beat-framing"><strong>描述方向（GM 内部）</strong><p>' + esc(framing) + '</p></div><p class="beat-situation"><strong>局势理解</strong><br>' + esc(beat.situation || '') + '</p>' +
    (beat.rule_focus ? '<div class="beat-rule-focus"><strong>本板块关注（GM 内部）</strong><p>' + esc(beat.rule_focus) + '</p></div>' : '') +
    '<div class="beat-source-row">来源页：' + sourcePages.map((page) => '<span class="page-ref">p' + esc(page) + '</span>').join(' ') + '</div>' +
    cardReferences + factReferences +
    '<div class="beat-columns"><div><h3>软提示</h3><ul>' + softCues.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul></div><div><h3>硬推进</h3><ul>' + hardCues.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul></div></div>' +
    '<div class="beat-columns"><div><h3>可以问自己</h3><ul>' + questionPrompts.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul></div><div><h3>何时离开</h3><ul>' + exitWhen.map((item) => '<li>' + esc(item) + '</li>').join('') + '</ul></div></div>' +
    '</section>';
}

function changeRuntimeScene(cardId) {
  const card = runtimeCards(["scene", "investigation_site", "environment"])
    .find((item) => item.id === cardId);
  if (!card || !state.session || state.session.current_card_id === card.id) return;
  state.session.current_card_id = card.id;
  sessionLog('scene_changed', '切换场景：' + card.title, {
    subjectType: 'scene',
    subjectId: card.id,
    cardId: card.id
  });
  renderRuntime();
}

async function startPlan(planId) {
  const plan = state.data.bundle.plans.find((item) => item.id === planId);
  if (!plan || !state.session) return false;
  const currentPlanId = state.session.current_plan_id;
  const hasRuntimeState = Boolean(
    currentPlanId ||
    state.session.revealed_clue_keys.length ||
    state.session.log.length ||
    state.session.notes.trim() ||
    Object.values(state.session.clock_stages).some((stage) => stage > 0)
  );
  if (hasRuntimeState) {
    const action = currentPlanId === plan.id ? '重新开始这个计划' : '切换到这个计划';
    if (!confirm('当前运行已有状态，' + action + '会重置本次运行的线索、时钟、备注和日志。继续吗？')) return false;
  }
  state.runtimeProfileId = plan.profile_id;
  if ($("runtime-profile")) $("runtime-profile").value = plan.profile_id;
  state.session.revealed_clue_keys = [];
  state.session.clock_stages = {};
  plan.card_ids.forEach((cardId) => {
    const card = state.data.bundle.cards.find((item) => item.id === cardId);
    if (card && ["clock", "operation_clock", "encounter_clock"].includes(card.type)) {
      state.session.clock_stages[card.id] = 0;
    }
  });
  state.session.log = [];
  state.session.notes = '';
  state.session.current_plan_id = plan.id;
  state.session.current_beat_id = plan.beats[0]?.id || null;
  const sceneCard = plan.card_ids
    .map((cardId) => state.data.bundle.cards.find((card) => card.id === cardId))
    .find((card) => card && ["scene", "investigation_site", "environment"].includes(card.type));
  state.session.current_card_id = sceneCard?.id || null;
  sessionLog('run_started', '开始场景计划：' + plan.title, {
    subjectType: 'session',
    subjectId: plan.id,
    planId: plan.id,
    cardId: sceneCard?.id || null,
    beatId: state.session.current_beat_id
  });
  state.sessionDirty = true;
  showView("runtime");
  renderRuntime();
  await saveSession();
  return true;
}

function resumePlan(planId) {
  if (!state.session || state.session.current_plan_id !== planId) return;
  showView("runtime");
  renderRuntime();
}

function changeBeat(delta) {
  const plan = currentPlan();
  const beat = currentBeat();
  if (!plan || !beat || !state.session) return;
  const index = plan.beats.findIndex((item) => item.id === beat.id);
  const next = plan.beats[index + delta];
  if (!next) return;
  state.session.current_beat_id = next.id;
  sessionLog('beat_changed', '切换节拍：' + next.title, {
    subjectType: 'beat',
    subjectId: next.id,
    beatId: next.id
  });
  renderRuntime();
}

function showView(viewName) {
  document.querySelectorAll(".work-nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewName);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${viewName}`);
  });
  if (viewName === "prep") {
    if (!state.prep.config) {
      loadPrepConfig().catch((error) => {
        state.prep.error = String(error);
        renderPrep();
      });
    }
    loadPrepJobs();
  }
  if (viewName === "review") loadReviewQueue();
}

function renderArtifactStage() {
  const availability = artifactDraftAvailability();
  const button = $("draft-artifacts");
  const openButton = $("open-artifacts");
  const job = state.artifacts.job;
  const jobInFlight = artifactJobInFlight(job);
  button.disabled = !availability.ready || state.artifacts.generating;
  const retryingFailedJob = job?.status === "failed" && availability.ready;
  button.textContent = state.artifacts.generating
    ? "正在生成草案…"
    : retryingFailedJob
      ? "重试失败步骤"
      : "生成备团产物草案";
  button.title = retryingFailedJob
    ? "重试上次失败的步骤；已成功完成的步骤会复用，不会从头生成"
    : availability.ready
      ? "调用当前模型生成可复核草案"
      : availability.guidance;
  openButton.disabled = !availability.cards.length;
  const phaseProgress = job?.phase === "local_digest"
    ? ` · ${job.completed_batches || 0}/${job.batch_count || 0} 个局部批次 · ${job.unit_count || 0} 个整理单元`
    : job?.phase === "global_plan"
      ? ` · ${job.unit_count || 0} 个整理单元`
      : job?.phase === "materializing"
        ? ` · ${job.completed_cards || 0}/${job.planned_card_count || 0} 张计划卡`
        : job?.phase === "direct_generation"
          ? ` · ${job.fact_count || 0} 条事实`
          : "";
  const jobSummary = jobInFlight
    ? `后台任务：${artifactJobPhaseLabels[job.phase] || artifactJobStatusLabels[job.status] || job.status}` + phaseProgress
    : job?.status === "failed"
      ? `上次任务在${artifactJobPhaseLabels[job.phase] || "生成阶段"}失败：${job.error || "可重新尝试；已完成步骤会复用"}`
      : "";
  $("artifact-draft-guidance").textContent =
    state.artifacts.error || jobSummary || availability.guidance;
  $("artifact-stage-summary").innerHTML = [
    `<span><strong>${availability.counts.generated}</strong> 待审批</span>`,
    `<span><strong>${availability.counts.edited}</strong> 已修改</span>`,
    `<span><strong>${availability.counts.approved}</strong> 已批准</span>`,
    `<span><strong>${state.data.bundle.facts.length}</strong> 书架事实</span>`,
    job ? `<span><strong>${artifactJobStatusLabels[job.status] || job.status}</strong> 生成任务` +
      (job.phase ? ` · ${artifactJobPhaseLabels[job.phase] || job.phase}` : "") +
      `</span>` : ""
  ].join("");
}

function renderShelf() {
  const bundle = state.data.bundle;
  const profiles = bundleProfiles();
  const cardTypes = new Set(profiles.flatMap((item) => item.card_definitions.map((def) => def.type)));
  $("shelf-summary").innerHTML = [
    ["事实", bundle.facts.length],
    ["备团产物", bundle.cards.length],
    ["备团板块", profiles.length],
    ["卡型定义", cardTypes.size]
  ].map(([name, value]) => `
    <article class="summary-card">
      <div class="summary-value">${value}</div>
      <div class="summary-label">${name}</div>
    </article>
  `).join("");

  $("bundle-detail").innerHTML = `
    <h3>${esc(bundle.name)}</h3>
    <p class="muted">${esc(bundle.description)}</p>
    <div class="tag-row">${bundleProfiles().map((item) => badge(profileDisplayName(item.id) + " · " + profileKindLabels[item.profile_kind || "runtime"], item.profile_kind === "runtime" ? "accent" : "")).join("")}</div>
  `;
  const coverage = state.data.coverage || {};
  $("coverage-audit").innerHTML = coverage.location_total
    ? `<div class="panel-head"><div><h3>地点覆盖审计</h3><p class="muted">已覆盖 ${coverage.location_covered || 0} / ${coverage.location_total} 个来源地点。</p></div></div>` +
      ((coverage.uncovered_location_titles || []).length
        ? `<p class="coverage-warning">尚未独立成卡：${coverage.uncovered_location_titles.map(esc).join("；")}</p><button class="edit-button" id="draft-missing-locations" type="button">补生成遗漏地点卡</button>`
        : `<p class="coverage-ok">当前来源地点均已被场景/地点卡覆盖。</p>`)
    : "";

  $("profile-grid").innerHTML = profiles.map((item) => {
    const cards = state.data.bundle.cards.filter((card) => card.profile_id === item.id).length;
    const definitions = item.card_definitions.map((def) => `
      <div class="definition-item">
        <span class="definition-key">${esc(def.display_name)}</span>
        <span>${esc(def.description || `${def.required_fields.length} 个必填字段`)}</span>
      </div>
    `).join("");
    return `
      <article class="domain-card">
        <div class="card-title-row">
          <div class="card-title">${esc(profileDisplayName(item.id))}</div>
          ${badge(`${cards} 卡`, "accent")} ${badge(profileKindLabels[item.profile_kind || "runtime"])}
        </div>
        <p class="fact-text muted">${esc(profileDisplaySummary(item.id))}</p>
        <div class="definition-list">${definitions}</div>
      </article>
    `;
  }).join("");
  renderArtifactStage();
  renderPlanDraftAvailability();
}

function renderFacts() {
  const query = state.factSearch.trim().toLowerCase();
  const matches = state.data.bundle.facts.filter((fact) => {
    const kindOk = state.factKind === "all" || fact.kind === state.factKind;
    const visibleOk = state.factVisibility === "all" || fact.visibility === state.factVisibility;
    const haystack = [fact.id, fact.text, fact.notes || "", factEvidenceStatus(fact), factSourceLabel(fact), ...fact.tags].join(" ").toLowerCase();
    return kindOk && visibleOk && (!query || haystack.includes(query));
  });

  renderFactRelationGraph();

  $("fact-grid").innerHTML = matches.map((fact) => {
    const links = fact.links.map((linkedId) => {
      const linked = state.data.bundle.facts.find((item) => item.id === linkedId);
      return `<button class="link-button" data-fact-link="${esc(linkedId)}">→ ${esc(linked?.text || linkedId)}</button>`;
    }).join("");
    return `
      <article class="domain-card fact-card ${state.selectedFactId === fact.id ? "selected" : ""}" data-fact-id="${esc(fact.id)}">
        <div class="card-title-row">
          <strong>${esc(label(fact.kind))}</strong> <button type="button" class="icon-button" title="复制事实关键词" data-copy-keyword="${esc(fact.id)}">复制 ID</button>
          <span class="row-actions">
            ${badge(label(fact.visibility), fact.visibility)}
            ${state.editMode ? `<button class="edit-button" data-edit-fact="${esc(fact.id)}">编辑</button>` : ""}
            ${state.editMode ? `<button class="edit-button danger" data-delete-fact="${esc(fact.id)}">删除</button>` : ""}
          </span>
        </div>
        <p class="fact-text">${esc(fact.text)}</p>
        <div class="tag-row">
          ${badge(label(factEvidenceStatus(fact)), factEvidenceStatus(fact))}
          ${factSourceRefs(fact).length
            ? factSourceRefs(fact).map((source) => badge(`p${source.page}`, "accent")).join("")
            : badge("无原文来源")}
          ${fact.tags.map((tag) => badge(tag)).join("")}
        </div>
        ${links ? `<div class="link-list">${links}</div>` : ""}
      </article>
    `;
  }).join("") || `<div class="empty-state">没有匹配的事实。</div>`;
}

function renderFactRelationGraph() {
  const host = $("fact-relation-graph");
  if (!host) return;
  const facts = state.data.bundle.facts;
  const center = facts.find((fact) => fact.id === state.selectedFactId) || facts[0];
  if (!center) { host.innerHTML = '<div class="empty-state">暂无事实可展示。</div>'; return; }
  const linkedIds = new Set([...(center.links || []), center.id]);
  facts.forEach((fact) => {
    if ((fact.links || []).includes(center.id)) linkedIds.add(fact.id);
  });
  const linked = facts.filter((fact) => linkedIds.has(fact.id));
  host.innerHTML = '<div class="relation-center"><strong>中心事实</strong><p>' + esc(center.text) + '</p><button type="button" class="icon-button" data-copy-keyword="' + esc(center.text) + '">复制</button></div>' +
    '<div class="relation-edges">' + linked.filter((fact) => fact.id !== center.id).map((fact) => '<button type="button" class="relation-node" data-fact-link="' + esc(fact.id) + '"><strong>' + esc(label(fact.kind)) + '</strong><span>' + esc(fact.text) + '</span></button>').join('') + '</div>';
}

function renderCards() {
  const allCardIds = new Set(state.data.bundle.cards.map((card) => card.id));
  state.selectedCardIds = new Set(
    [...state.selectedCardIds].filter((cardId) => allCardIds.has(cardId))
  );
  const cards = state.data.bundle.cards.filter((card) => {
    const profileOk = state.cardProfile === "all" || card.profile_id === state.cardProfile;
    const typeOk = state.cardType === "all" || card.type === state.cardType;
    return profileOk && typeOk;
  });
  const referencedCardIds = new Set(
    state.data.bundle.plans.flatMap((plan) => plan.card_ids || [])
  );
  const approvableCardIds = selectedCardIdsForReview("approve");
  const reopenableCardIds = selectedCardIdsForReview("reopen");
  const visibleSelectedCount = cards.filter((card) => state.selectedCardIds.has(card.id)).length;
  $("card-count").textContent = `${cards.length} / ${state.data.bundle.cards.length} 项 · 已选 ${state.selectedCardIds.size}`;
  $("card-select-all").checked = Boolean(cards.length) && visibleSelectedCount === cards.length;
  $("card-select-all").indeterminate = Boolean(visibleSelectedCount) && visibleSelectedCount < cards.length;
  $("card-approve-selected").disabled = !approvableCardIds.length || state.artifacts.reviewing;
  $("card-reopen-selected").disabled = !reopenableCardIds.length || state.artifacts.reviewing;
  $("card-approve-selected").textContent = approvableCardIds.length ? `批准所选 · ${approvableCardIds.length}` : "批准所选";
  $("card-reopen-selected").textContent = reopenableCardIds.length ? `退回修改 · ${reopenableCardIds.length}` : "退回修改";
  $("card-review-error").textContent = state.artifacts.error;
  $("work-card-grid").innerHTML = cards.map((card) => {
    const definition = profileDefinition(card.profile_id, card.type);
    const isReferenced = referencedCardIds.has(card.id);
    const statusBadge = card.edit_state === "approved"
      ? badge("已批准", "accepted")
      : card.edit_state === "edited"
        ? badge("已修改，待批准", "needs_review")
        : badge("模型草案，待批准", "needs_review");
    return `
      <article class="domain-card work-card ${state.selectedCardId === card.id ? "selected" : ""}" data-card-id="${esc(card.id)}">
        <div class="card-title-row work-card-head">
          <label class="work-card-select" aria-label="选择 ${esc(card.title)}">
            <input type="checkbox" data-card-select-id="${esc(card.id)}" ${state.selectedCardIds.has(card.id) ? "checked" : ""} ${state.artifacts.reviewing ? "disabled" : ""}>
          </label>
          <div>
            <div class="card-title">${esc(card.title)}</div>
            ${card.subtitle ? `<p class="card-subtitle">${esc(card.subtitle)}</p>` : ""}
          </div>
          <span class="row-actions">
            ${statusBadge}
            ${cardHasModelCandidate(card) ? badge(evidenceStatusLabels.model_candidate, "model_candidate") : ""}
            ${badge(definition.display_name, "accent")}
            ${card.edit_state !== "approved" || state.editMode ? `<button class="edit-button" data-edit-card="${esc(card.id)}">编辑</button>` : ""}
            ${card.edit_state !== "approved" ? `<button class="action-link" data-card-review-action="approve" data-card-review-id="${esc(card.id)}" ${state.artifacts.reviewing ? "disabled" : ""}>批准</button>` : `<button class="edit-button" data-card-review-action="reopen" data-card-review-id="${esc(card.id)}" ${state.artifacts.reviewing || isReferenced ? "disabled" : ""} ${isReferenced ? 'title="已被运行场景引用"' : ""}>退回修改</button>`}
            ${card.edit_state !== "approved" || state.editMode ? `<button class="edit-button danger" data-delete-card="${esc(card.id)}" ${isReferenced ? 'disabled title="已被运行场景引用"' : ""}>删除</button>` : ""}
          </span>
        </div>
        <div class="tag-row">${badge(profileDisplayName(card.profile_id))}</div>
        ${fieldsHtml(card)}
        ${cardOpenQuestionsHtml(card)}
        ${sourceHtml(card.fact_ids)}
        ${cardGenerationHtml(card)}
      </article>
    `;
  }).join("") || `<div class="empty-state">还没有匹配的备团产物。可从书架使用已提升事实生成草案。</div>`;
}

function renderReview() {
  const review = state.review;
  const taskFilter = $("review-task-filter");
  if (!taskFilter) return;
  const prepJobs = state.prep.jobs || [];
  const prepOptions = prepJobs.map((job) =>
    '<option value="' + esc(prepReviewFilterValue(job.id)) + '">' +
    esc(reviewPrepJobLabel(job)) + ' · 全部窗口</option>'
  );
  const shadowOptions = review.tasks.map((task) =>
    '<option value="' + esc(task.id) + '">' + esc(reviewTaskLabel(task)) + '</option>'
  );
  const taskOptions = [
    '<option value="">全部任务</option>',
    prepOptions.length ? '<optgroup label="备团任务（全部窗口）">' + prepOptions.join("") + '</optgroup>' : "",
    shadowOptions.length ? '<optgroup label="分析窗口">' + shadowOptions.join("") + '</optgroup>' : ""
  ];
  taskFilter.innerHTML = taskOptions.join("");
  taskFilter.value = review.taskId;
  $("review-state-filter").value = review.reviewState;
  $("review-page-filter").value = review.sourcePage;

  const visible = reviewVisibleCandidates();
  const visibleIds = new Set(visible.map((candidate) => candidate.id));
  review.selectedIds = new Set(
    [...review.selectedIds].filter((candidateId) => visibleIds.has(candidateId))
  );
  if (!visibleIds.has(review.selectedCandidateId)) {
    review.selectedCandidateId = visible[0]?.id || null;
  }
  const selected = selectedReviewCandidate();
  const selectionCount = review.selectedIds.size;
  const selectedCandidates = visible.filter((candidate) =>
    review.selectedIds.has(candidate.id)
  );
  const pageFilter = parseReviewPageRanges(review.sourcePage);
  const canPromoteSelection = Boolean(selectedCandidates.length) && selectedCandidates.every(
    (candidate) => candidate.review_state === "accepted" && !candidate.promotion
  );
  const pageFilterInput = $("review-page-filter");
  if (pageFilterInput) {
    pageFilterInput.setAttribute("aria-invalid", pageFilter.valid ? "false" : "true");
    pageFilterInput.title = pageFilter.valid ? "按来源页筛选，可输入单页、范围或离散页" : "页码范围格式无效，例如 4-6, 8";
  }
  $("review-summary").textContent = review.loading
    ? "正在加载候选队列…"
    : `${visible.length} 条候选 · ${reviewStateLabels[review.reviewState] || "全部状态"}` +
      (review.sourcePage ? ` · p${formatReviewPageRanges(review.sourcePage)}` : "") +
      (!pageFilter.valid ? " · 页码范围格式无效（如 4-6, 8）" : "") +
      (review.notice ? ` · ${review.notice}` : "");
  $("review-select-all").checked = Boolean(visible.length) && visible.every(
    (candidate) => review.selectedIds.has(candidate.id)
  );
  $("review-select-all").indeterminate = Boolean(selectionCount) && selectionCount < visible.length;
  $("review-batch-apply").disabled = !selectionCount || review.saving;
  $("review-batch-promote").disabled = !canPromoteSelection || review.saving;
  $("review-batch-apply").textContent = selectionCount
    ? `复核所选 · ${selectionCount}`
    : "复核所选";
  $("review-batch-promote").textContent = selectionCount
    ? `送入书架 · ${selectionCount}`
    : "送入书架";

  $("review-candidate-list").innerHTML = pageFilter.valid ? visible.map((candidate) => {
    const sourceBadges = reviewSourceRefs(candidate).map((source) =>
      badge(`p${source.page}`, "accent")
    ).join("");
    const isSelected = candidate.id === review.selectedCandidateId;
    return `
      <article class="review-candidate ${isSelected ? "selected" : ""}">
        <label class="review-candidate-check" aria-label="选择 ${esc(candidate.id)}">
          <input type="checkbox" data-review-select-id="${esc(candidate.id)}" ${review.selectedIds.has(candidate.id) ? "checked" : ""} ${review.saving ? "disabled" : ""}>
        </label>
        <div class="review-candidate-body">
          <div class="review-candidate-head">
            <strong>${esc(candidate.id)}</strong>
            <div class="tag-row">
              ${badge(evidenceStatusLabels.model_candidate, "model_candidate")}
              ${badge(reviewStateLabels[candidate.review_state] || candidate.review_state, candidate.review_state)}
              ${badge(label(candidate.kind))}
            </div>
          </div>
          <p class="review-candidate-text">${esc(reviewDisplayText(candidate))}</p>
          <div class="review-candidate-meta">${sourceBadges}${candidate.reviewed_text ? badge("有复核稿") : ""}</div>
        </div>
        <div class="review-candidate-actions">
          ${candidate.promotion ? badge("已入书架", "accepted") : ""}
          <button class="edit-button" type="button" data-review-select="${esc(candidate.id)}">查看</button>
        </div>
      </article>
    `;
  }).join("") || (review.loading
    ? '<div class="empty-state">正在读取候选队列…</div>'
    : review.reviewState === "needs_review"
      ? '<div class="empty-state review-empty-state"><span>当前范围的待复核候选已经处理完。</span>' +
        '<button class="action-link" type="button" data-review-stage="accepted">查看已接受并送入书架</button></div>'
      : '<div class="empty-state review-empty-state"><span>没有匹配的候选。</span>' +
        '<button class="edit-button" type="button" data-view-target="prep">返回备团任务</button></div>')
    : '<div class="empty-state review-empty-state"><span>页码范围格式无效，请输入如 4-6, 8。</span></div>';

  if (!selected) {
    $("review-candidate-detail").innerHTML = `
      <div class="empty-state">${review.error ? esc(review.error) : "选择一条候选以复核。"}</div>
    `;
    return;
  }

  const sourceRows = reviewSourceRefs(selected).map((source) => `
    <button class="link-button review-source-button" type="button"
      data-review-source-file="${esc(source.file)}" data-review-source-page="${source.page}">
      ${esc(sourceRefLabel(source))}
    </button>
  `).join("") || '<span class="muted">没有来源引用。</span>';
  const possibleLinks = Array.isArray(selected.possible_links) ? selected.possible_links : [];
  const openQuestions = Array.isArray(selected.open_questions) ? selected.open_questions : [];
  const history = Array.isArray(selected.review_history) ? selected.review_history.slice().reverse() : [];
  const historyHtml = history.map((event) => `
    <div class="review-history-item">
      <div class="tag-row">${badge(reviewStateLabels[event.review_state] || event.review_state, event.review_state)} <span class="muted">${esc(String(event.created_at || "").replace("T", " ").slice(0, 16))}</span></div>
      ${event.reviewed_text ? `<p><strong>复核稿：</strong>${esc(event.reviewed_text)}</p>` : ""}
      ${event.review_note ? `<p><strong>说明：</strong>${esc(event.review_note)}</p>` : ""}
    </div>
  `).join("") || '<p class="muted">尚无复核记录。</p>';
  const currentReviewText = selected.reviewed_text || selected.text;
  const promotion = selected.promotion || null;
  const promotionHtml = promotion
    ? `<section class="review-promotion">
        <div class="tag-row">${badge("已进入书架", "accepted")} ${badge(evidenceStatusLabels[promotion.evidence_status] || promotion.evidence_status)}</div>
        <button class="edit-button" type="button" data-review-workspace="${esc(promotion.workspace_id)}">打开对应书架</button>
      </section>`
    : selected.review_state === "accepted"
      ? `<section class="review-promotion">
          <div class="field-label">提升为书架事实</div>
          <div class="review-detail-actions">
            <button class="action-link" type="button" data-review-promote="source_fact" data-review-candidate="${esc(selected.id)}" ${review.saving ? "disabled" : ""}>提升为原文事实</button>
            <button class="edit-button" type="button" data-review-promote="inference" data-review-candidate="${esc(selected.id)}" ${review.saving ? "disabled" : ""}>提升为可验证推断</button>
          </div>
        </section>`
      : "";
  $("review-candidate-detail").innerHTML = `
    <div class="review-detail-head">
      <div>
        <h2>${esc(selected.id)}</h2>
        <div class="tag-row">
          ${badge(evidenceStatusLabels.model_candidate, "model_candidate")}
          ${badge(reviewStateLabels[selected.review_state] || selected.review_state, selected.review_state)}
          ${badge(label(selected.kind))}
          ${selected.confidence != null ? badge(`置信度 ${Number(selected.confidence).toFixed(2)}`) : ""}
        </div>
      </div>
      <span class="muted">${esc(String(selected.created_at || "").replace("T", " ").slice(0, 16))}</span>
    </div>
    <section class="review-text-block">
      <div class="field-label">模型原文</div>
      <p>${esc(selected.text)}</p>
    </section>
    <div class="review-edit-grid">
      <label>复核文本<textarea id="review-edited-text" rows="5" ${review.saving ? "disabled" : ""}>${esc(currentReviewText)}</textarea></label>
      <label>复核说明<textarea id="review-note" rows="3" ${review.saving ? "disabled" : ""}>${esc(selected.review_note || "")}</textarea></label>
    </div>
    <div class="review-detail-actions">
      <button class="edit-button" type="button" data-review-action="needs_review" data-review-candidate="${esc(selected.id)}" ${review.saving ? "disabled" : ""}>退回复核</button>
      <button class="edit-button danger" type="button" data-review-action="rejected" data-review-candidate="${esc(selected.id)}" ${review.saving ? "disabled" : ""}>拒绝</button>
      <button class="action-link" type="button" data-review-action="accepted" data-review-candidate="${esc(selected.id)}" ${review.saving ? "disabled" : ""}>接受</button>
    </div>
    ${promotionHtml}
    <div class="review-detail-error">${review.error ? esc(review.error) : ""}</div>
    <section class="review-reference-section">
      <h3>原文页</h3>
      <div class="review-reference-list">${sourceRows}</div>
    </section>
    <section class="review-reference-section">
      <h3>关联目标</h3>
      ${possibleLinks.length ? `<ul class="review-link-list">${possibleLinks.map((item) => `<li><code>${esc(item)}</code></li>`).join("")}</ul>` : '<p class="muted">未提供关联目标。</p>'}
    </section>
    <section class="review-reference-section">
      <h3>待确认问题</h3>
      ${openQuestions.length ? `<ul class="review-link-list">${openQuestions.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : '<p class="muted">无。</p>'}
    </section>
    <details class="review-history" open>
      <summary>复核历史 · ${history.length}</summary>
      <div class="review-history-list">${historyHtml}</div>
    </details>
  `;
}

function renderRuntimeLegacy() {
  const item = profile(state.runtimeProfileId);
  if (!item) return;
  const moves = [...item.risk_axes, ...item.failure_moves, ...item.gm_moves]
    .map((move) => `<li>${esc(label(move))}</li>`).join("");
  const prompts = Object.entries(item.prompts).map(([key, value]) => `
    <div class="definition-item"><span class="definition-key">${esc(runtimeLabels[key] || key)}</span><span>${esc(value)}</span></div>
  `).join("");

  $("profile-runtime").innerHTML = `
    <div class="runtime-grid">
      <article class="domain-card">
        <div class="card-title-row"><div class="card-title">${esc(profileDisplayName(item.id))}</div>${badge(profileKindLabels[item.profile_kind || "runtime"], "accent")}</div>
        <p class="muted">${esc(profileDisplaySummary(item.id))}</p>
        <h3>现场语义</h3><ul class="muted">${moves}</ul>
        ${prompts ? `<h3>即兴提问</h3><div class="definition-list">${prompts}</div>` : ""}
      </article>
      <div id="current-scene"></div>
    </div>
  `;

  const sceneCard = state.data.bundle.cards.find((card) =>
    card.profile_id === state.runtimeProfileId &&
    ["scene", "investigation_site", "environment"].includes(card.type)
  );
  $("current-scene").innerHTML = sceneCard ? `
    <article class="domain-card selected">
      <div class="card-title-row"><div class="card-title">${esc(sceneCard.title)}</div>${badge("当前场景", "accent")}</div>
      ${fieldsHtml(sceneCard)}
      ${sourceHtml(sceneCard.fact_ids)}
    </article>
  ` : `<div class="empty-state">当前板块暂无场景级卡。</div>`;
}

function runtimeCardFieldsHtml(card) {
  return Object.entries(card.fields)
    .filter(([key]) => !["direct_clues", "hidden_clues", "current_stage"].includes(key))
    .map(([key, value]) =>
      '<div class="field"><div class="field-label">' + esc(label(key)) + '</div><div class="field-value">' + esc(formatValue(value)) + '</div></div>'
    ).join("");
}

function renderRuntime() {
  const activePlan = currentPlan();
  if (activePlan) state.runtimeProfileId = activePlan.profile_id;
  const item = profile(state.runtimeProfileId);
  if (!item || !state.session) return;
  const runtimeProfileSelect = $("runtime-profile");
  if (runtimeProfileSelect) {
    runtimeProfileSelect.value = state.runtimeProfileId;
    runtimeProfileSelect.disabled = Boolean(activePlan);
    runtimeProfileSelect.title = activePlan ? "当前场景计划已固定运行板块" : "切换要查看的运行板块";
  }
  renderPlans();
  renderBeat();
  const planStatus = $("runtime-plan-status");
  if (activePlan) {
    const beat = currentBeat();
    const beatIndex = beat ? activePlan.beats.findIndex((item) => item.id === beat.id) + 1 : 0;
    planStatus.className = "runtime-plan-status active";
    planStatus.innerHTML = '<strong>运行中：</strong> ' + esc(activePlan.title) +
      (beatIndex ? ' <span class="muted">· 当前节拍 ' + beatIndex + ' / ' + activePlan.beats.length + '</span>' : '');
  } else {
    planStatus.className = "runtime-plan-status pending";
    planStatus.innerHTML = '<strong>尚未开始场景计划。</strong> 请在书架检查草案，再明确点击“开始运行”。当前内容仅供 GM 预览。';
  }
  const runtimeActionIds = [
    "gm-move-select",
    "apply-gm-move",
    "session-note-input",
    "add-session-note",
    "lookup-gap-input",
    "record-lookup-gap",
    "save-session",
    "session-notes"
  ];
  runtimeActionIds.forEach((id) => {
    const element = $(id);
    if (element) element.disabled = !activePlan;
  });
  const runtimeControls = document.querySelector(".runtime-controls");
  if (runtimeControls) runtimeControls.classList.toggle("runtime-disabled", !activePlan);
  const moves = item.gm_moves.map((move) =>
    '<option value="' + esc(move) + '">' + esc(label(move)) + '</option>'
  ).join("");
  const prompts = Object.entries(item.prompts).map(([key, value]) =>
    '<div class="definition-item"><span class="definition-key">' + esc(runtimeLabels[key] || key) + '</span><span>' + esc(value) + '</span></div>'
  ).join("");
  $("profile-runtime").innerHTML =
    '<div class="runtime-profile-summary"><div><strong>' + esc(profileDisplayName(item.id)) + '</strong> ' + badge(profileKindLabels[item.profile_kind || "runtime"], 'accent') + '</div>' +
    '<p class="muted">' + esc(profileDisplaySummary(item.id)) + '</p><div class="runtime-semantic-row">' +
    item.risk_axes.map((move) => badge(label(move))).join("") + '</div>' +
    (prompts ? '<details class="runtime-prompts"><summary>即兴提问</summary><div class="definition-list">' + prompts + '</div></details>' : '') +
    '</div>';
  $("gm-move-select").innerHTML = moves || '<option value="">当前板块暂无 GM 移动</option>';

  const sceneCards = runtimeCards(["scene", "investigation_site", "environment"]);
  const scene = currentRuntimeCard();
  $("runtime-exploration-list").innerHTML = sceneCards.length
    ? sceneCards.map((card) => '<button type="button" class="exploration-node ' + (card.id === scene?.id ? 'selected' : '') + '" data-runtime-scene="' + esc(card.id) + '"><strong>' + esc(card.title) + '</strong><span>' + esc(profileDefinition(card.profile_id, card.type).display_name) + '</span></button>').join("")
    : '<div class="empty-state">当前计划还没有可探索地点。</div>';
  $("scene-holder").innerHTML = scene ?
    '<article class="domain-card selected runtime-scene-card"><div class="card-title-row"><div><div class="card-title">' + esc(scene.title) + '</div><p class="card-subtitle">' + esc(scene.subtitle || '当前地点') + '</p></div>' + badge('当前地点', 'accent') + '</div>' +
    runtimeCardFieldsHtml(scene) + clueHtml(scene, 'direct', scene.fields.direct_clues) + clueHtml(scene, 'hidden', scene.fields.hidden_clues) + sourceHtml(scene.fact_ids) + '</article>' :
    '<div class="empty-state">当前板块暂无场景级卡。请先完成该板块的产物生成与复核。</div>';

  const clocks = runtimeCards(["clock", "operation_clock", "encounter_clock"]);
  $("runtime-clocks").innerHTML = clocks.map((clock) => {
    const stages = Array.isArray(clock.fields.stages) ? clock.fields.stages : [];
    const stage = state.session.clock_stages[clock.id] ?? 0;
    return '<article class="runtime-clock"><div class="card-title-row"><strong>' + esc(clock.title) + '</strong><span class="badge accent">' + (stage + 1) + ' / ' + Math.max(stages.length, 1) + '</span></div>' +
      '<p class="muted">' + esc(stages[stage] || '未定义阶段') + '</p><div class="runtime-clock-track">' +
      stages.map((value, index) => '<span class="clock-step ' + (index <= stage ? 'filled' : '') + '">' + (index + 1) + '</span>').join("") + '</div>' +
      '<div class="row-actions"><button class="edit-button" data-clock-action="' + esc(clock.id) + '" data-clock-delta="-1" ' + (stage <= 0 ? 'disabled' : '') + '>退回</button><button class="edit-button" data-clock-action="' + esc(clock.id) + '" data-clock-delta="1" ' + (stage >= stages.length - 1 ? 'disabled' : '') + '>推进</button></div></article>';
  }).join("") || '<div class="empty-state">当前板块暂无推进钟。</div>';

  const lookupGapCount = state.session.log.filter((entry) => entry.kind === 'lookup_missing').length;
  $("session-log-count").textContent = state.session.log.length + ' 条记录' + (lookupGapCount ? ' · ' + lookupGapCount + ' 个缺口' : '');
  $("session-log").innerHTML = state.session.log.slice().reverse().map((entry) => {
    const eventLabel = sessionLogLabels[entry.kind] || '运行记录';
    const accentEvents = ['run_started', 'gm_move', 'clue_revealed', 'clock_advanced'];
    return '<div class="session-log-entry">' +
      badge(eventLabel, accentEvents.includes(entry.kind) ? 'accent' : 'neutral') +
      '<span>' + esc(entry.text) + '</span><time>' +
      esc(entry.created_at.replace('T', ' ').slice(0, 16)) + '</time></div>';
  }).join("") || '<div class="muted">还没有运行记录。</div>';
  $("session-notes").value = state.session.notes || '';
  $("session-status").textContent = state.sessionState === 'saved' && state.sessionUpdatedAt
    ? (state.sessionDirty ? '有未保存修改' : '已保存 ' + state.sessionUpdatedAt.replace('T', ' ').slice(0, 16))
    : state.sessionState === 'invalid' ? '存档无效，使用新状态' : '未保存的本次运行';
}

function fillSelectors() {
  const profileEntries = bundleProfiles();
  $("card-profile").innerHTML = [
    '<option value="all">全部板块</option>',
    ...profileEntries.map((item) => `<option value="${esc(item.id)}">${esc(profileDisplayName(item.id))}</option>`)
  ].join("");
  refreshCardTypes();
  const runtimeProfiles = enabledProfiles();
  $("runtime-profile").innerHTML = runtimeProfiles.map((item) =>
    `<option value="${esc(item.id)}">${esc(profileDisplayName(item.id))}</option>`
  ).join("");
  if (!runtimeProfiles.some((item) => item.id === state.runtimeProfileId)) {
    state.runtimeProfileId = runtimeProfiles[0]?.id;
  }
  $("runtime-profile").value = state.runtimeProfileId;
  fillWorkspaceSelector();
}

function refreshCardTypes() {
  const item = state.cardProfile !== "all" && bundleProfiles().some((entry) => entry.id === state.cardProfile)
    ? profile(state.cardProfile)
    : null;
  const definitions = item
    ? item.card_definitions
    : bundleProfiles().flatMap((entry) => entry.card_definitions);
  const uniqueTypes = [...new Map(definitions.map((def) => [def.type, def])).values()];
  $("card-type").innerHTML = [
    '<option value="all">全部卡型</option>',
    ...uniqueTypes.map((def) => `<option value="${esc(def.type)}">${esc(def.display_name)}</option>`)
  ].join("");
  state.cardType = "all";
}

function renderAll() {
  renderPrep();
  renderShelf();
  renderFacts();
  renderCards();
  renderReview();
  renderRuntime();
}

function updateSessionReviewLinks() {
  const base = '/api/domain/examples/' + encodeURIComponent(state.exampleId) + '/session/review';
  $("export-session-review-markdown").href = base + '?format=markdown';
  $("export-session-review-json").href = base + '?format=json';
}

async function saveBundle() {
  if (!state.dirty) return true;
  if (state.saving) return false;
  state.saving = true;
  setDirty(true);
  try {
    const response = await fetch(`/api/domain/examples/${encodeURIComponent(state.exampleId)}/bundle`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(state.data.bundle)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    state.saving = false;
    state.savedAt = payload.saved_at;
    state.savedState = "saved";
    setDirty(false);
    await loadSession();
    updateWorkStatus("运行包已保存");
    return true;
  } catch (error) {
    state.saving = false;
    setDirty(true);
    updateWorkStatus("保存失败");
    const panel = $("global-error");
    panel.hidden = false;
    panel.textContent = String(error);
    return false;
  }
}

async function resetBundle() {
  if (state.dirty && !confirm("有未保存修改，仍要还原为种子包吗？")) return;
  try {
    const response = await fetch(`/api/domain/examples/${encodeURIComponent(state.exampleId)}/bundle`, {method: "DELETE"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    location.reload();
  } catch (error) {
    updateWorkStatus("还原失败");
    const panel = $("global-error");
    panel.hidden = false;
    panel.textContent = String(error);
  }
}

async function saveSession() {
  if (!state.session || state.sessionSaving) return false;
  state.sessionSaving = true;
  try {
    state.session.notes = $("session-notes").value.trim();
    const response = await fetch('/api/domain/examples/' + encodeURIComponent(state.exampleId) + '/session', {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state.session)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'HTTP ' + response.status);
    state.sessionUpdatedAt = payload.updated_at;
    state.sessionState = 'saved';
    state.sessionDirty = false;
    renderRuntime();
    return true;
  } catch (error) {
    const panel = $("global-error");
    panel.hidden = false;
    panel.textContent = String(error);
    return false;
  } finally {
    state.sessionSaving = false;
  }
}

async function resetSession() {
  if (!confirm('确定要清空本次运行的线索、时钟和日志吗？')) return;
  const response = await fetch('/api/domain/examples/' + encodeURIComponent(state.exampleId) + '/session', {method: 'DELETE'});
  if (!response.ok) throw new Error('HTTP ' + response.status);
  await loadSession();
  renderRuntime();
}

async function loadSession() {
  const response = await fetch('/api/domain/examples/' + encodeURIComponent(state.exampleId) + '/session', {cache: 'no-store'});
  if (!response.ok) throw new Error('运行状态加载失败: HTTP ' + response.status);
  const payload = await response.json();
  state.session = payload.session;
  state.sessionUpdatedAt = payload.updated_at;
  state.sessionState = payload.state;
  state.sessionDirty = false;
  const activePlan = state.data?.bundle?.plans?.find((plan) => plan.id === state.session?.current_plan_id);
  if (activePlan) state.runtimeProfileId = activePlan.profile_id;
}

function revealClue(key, text) {
  if (!state.session || isRevealed(key)) return;
  state.session.revealed_clue_keys.push(key);
  sessionLog('clue_revealed', '揭示线索：' + text, {
    subjectType: 'clue',
    subjectId: key,
    metadata: {clue_key: key}
  });
  renderRuntime();
}

function changeClock(cardId, delta) {
  const clock = state.data.bundle.cards.find((card) => card.id === cardId);
  const stages = Array.isArray(clock?.fields?.stages) ? clock.fields.stages : [];
  const current = state.session.clock_stages[cardId] ?? 0;
  const next = Math.max(0, Math.min(Math.max(stages.length - 1, 0), current + delta));
  if (next === current) return;
  state.session.clock_stages[cardId] = next;
  sessionLog(delta > 0 ? 'clock_advanced' : 'clock_rewound',
    (delta > 0 ? '推进时钟：' : '回退时钟：') + (clock?.title || cardId) + ' → ' + (stages[next] || '阶段 ' + (next + 1)),
    {
      subjectType: 'clock',
      subjectId: cardId,
      metadata: {
        from_stage: current,
        to_stage: next,
        stage_title: stages[next] || '阶段 ' + (next + 1)
      }
    }
  );
  renderRuntime();
}

function applyGmMove() {
  if (!currentPlan()) return;
  const move = $("gm-move-select").value;
  if (!move) return;
  sessionLog('gm_move', '执行 GM 移动：' + label(move), {
    subjectType: 'gm_move',
    subjectId: move,
    metadata: {move}
  });
  renderRuntime();
}

function addSessionNote() {
  if (!currentPlan()) return;
  const input = $("session-note-input");
  if (!input.value.trim()) return;
  sessionLog('manual_note', input.value, {subjectType: 'session'});
  input.value = '';
  renderRuntime();
}

function recordLookupGap() {
  if (!currentPlan()) return;
  const input = $("lookup-gap-input");
  const text = input.value.trim();
  if (!text) return;
  sessionLog('lookup_missing', '未找到：' + text, {
    subjectType: 'session',
    subjectId: 'lookup_gap'
  });
  input.value = '';
  renderRuntime();
}

function bindEvents() {
  document.querySelectorAll(".work-nav button").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });

  $("prep-job-form").addEventListener("submit", submitPrepJob);
  $("prep-source-upload-button").addEventListener("click", uploadPrepSource);
  $("prep-config-form").addEventListener("submit", savePrepConfig);
  $("prep-config-test").addEventListener("click", testPrepConfig);
  $("prep-job-refresh").addEventListener("click", loadPrepJobs);

  $("fact-search").addEventListener("input", (event) => {
    state.factSearch = event.target.value;
    renderFacts();
  });
  $("fact-kind").addEventListener("change", (event) => {
    state.factKind = event.target.value;
    renderFacts();
  });
  $("fact-visibility").addEventListener("change", (event) => {
    state.factVisibility = event.target.value;
    renderFacts();
  });
  $("source-prep").addEventListener("click", openSourcePrep);
  $("prep-source-file").addEventListener("change", (event) => {
    resetSourcePrepPageRange(event.target.value.trim());
    loadPrepPage();
  });
  $("card-profile").addEventListener("change", (event) => {
    state.cardProfile = event.target.value;
    refreshCardTypes();
    renderCards();
  });
  $("card-type").addEventListener("change", (event) => {
    state.cardType = event.target.value;
    renderCards();
  });
  $("card-select-all").addEventListener("change", (event) => {
    const visibleCards = state.data.bundle.cards.filter((card) =>
      (state.cardProfile === "all" || card.profile_id === state.cardProfile) &&
      (state.cardType === "all" || card.type === state.cardType)
    );
    if (event.target.checked) {
      visibleCards.forEach((card) => state.selectedCardIds.add(card.id));
    } else {
      visibleCards.forEach((card) => state.selectedCardIds.delete(card.id));
    }
    renderCards();
  });
  $("work-card-grid").addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-card-select-id]");
    if (!checkbox) return;
    if (checkbox.checked) state.selectedCardIds.add(checkbox.dataset.cardSelectId);
    else state.selectedCardIds.delete(checkbox.dataset.cardSelectId);
    renderCards();
  });
  $("card-approve-selected").addEventListener("click", () =>
    reviewCards(selectedCardIdsForReview("approve"), "approve")
  );
  $("card-reopen-selected").addEventListener("click", () =>
    reviewCards(selectedCardIdsForReview("reopen"), "reopen")
  );
  $("draft-artifacts").addEventListener("click", draftArtifacts);
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#draft-missing-locations")) return;
    if (!state.exampleId || state.artifacts.generating) return;
    state.artifacts.generating = true;
    state.artifacts.error = "";
    renderAll();
    fetch(`/api/domain/examples/${encodeURIComponent(state.exampleId)}/cards/draft-missing-locations`, {method: "POST"})
      .then(async (response) => { const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(formatApiError(payload, "补生成地点卡失败")); return payload; })
      .then((payload) => { state.artifacts.job = payload.job || null; updateWorkStatus("遗漏地点卡已排队生成"); renderAll(); scheduleArtifactPoll(); })
      .catch((error) => { $("global-error").hidden = false; $("global-error").textContent = String(error); });
  });
  $("open-artifacts").addEventListener("click", () => showView("cards"));
  $("review-refresh").addEventListener("click", () => {
    state.review.notice = "";
    loadReviewQueue();
  });
  $("review-task-filter").addEventListener("change", (event) => {
    state.review.taskId = event.target.value;
    state.review.selectedIds = new Set();
    state.review.selectedCandidateId = null;
    state.review.notice = "";
    loadReviewQueue();
  });
  $("review-state-filter").addEventListener("change", (event) => {
    state.review.reviewState = event.target.value;
    state.review.selectedIds = new Set();
    state.review.selectedCandidateId = null;
    state.review.notice = "";
    loadReviewQueue();
  });
  $("review-page-filter").addEventListener("input", (event) => {
    state.review.sourcePage = event.target.value.trim();
    renderReview();
  });
  $("review-select-all").addEventListener("change", (event) => {
    const visible = reviewVisibleCandidates();
    state.review.selectedIds = event.target.checked
      ? new Set(visible.map((candidate) => candidate.id))
      : new Set();
    renderReview();
  });
  $("review-candidate-list").addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-review-select-id]");
    if (!checkbox) return;
    const candidateId = checkbox.dataset.reviewSelectId;
    if (checkbox.checked) state.review.selectedIds.add(candidateId);
    else state.review.selectedIds.delete(candidateId);
    renderReview();
  });
  $("review-batch-apply").addEventListener("click", () => submitReviewBatch());
  $("review-batch-promote").addEventListener("click", () => promoteReviewBatch());
  $("runtime-profile").addEventListener("change", (event) => {
    state.runtimeProfileId = event.target.value;
    renderRuntime();
  });
  $("scene-holder").addEventListener("change", (event) => {
    if (event.target.id === "runtime-scene-select") changeRuntimeScene(event.target.value);
  });
  $("runtime-exploration-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-runtime-scene]");
    if (button) changeRuntimeScene(button.dataset.runtimeScene);
  });
  $("draft-plan").addEventListener("click", openPlanDraftEditor);
  $("plan-draft-editor").addEventListener("submit", submitPlanDraft);
  $("plan-editor").addEventListener("submit", submitPlanEditor);
  $("card-profile-edit").addEventListener("change", (event) => {
    refreshCardEditorTypes(event.target.value);
  });
  $("source-prep-editor").addEventListener("submit", (event) => {
    event.preventDefault();
    createFactFromPrep();
  });
  $("prep-load-page").addEventListener("click", loadPrepPage);
  $("prep-prev-page").addEventListener("click", () => shiftPrepPage(-1));
  $("prep-next-page").addEventListener("click", () => shiftPrepPage(1));
  $("save-session").addEventListener("click", saveSession);
  $("reset-session").addEventListener("click", () => resetSession().catch((error) => {
    $("global-error").hidden = false;
    $("global-error").textContent = String(error);
  }));
  $("apply-gm-move").addEventListener("click", applyGmMove);
  $("add-session-note").addEventListener("click", addSessionNote);
  $("record-lookup-gap").addEventListener("click", recordLookupGap);
  $("session-notes").addEventListener("input", () => {
    if (state.session) {
      state.session.notes = $("session-notes").value;
      state.sessionDirty = true;
      $("session-status").textContent = "有未保存修改";
    }
  });
  $("example-select").addEventListener("change", (event) => {
    if (state.dirty && !confirm("当前更改尚未保存，切换样例会丢失这些修改。")) {
      event.target.value = state.exampleId;
      return;
    }
    const params = new URLSearchParams();
    params.set("example", event.target.value);
    params.set("view", "shelf");
    location.search = params.toString();
  });
  $("rename-workspace").addEventListener("click", () => renameWorkspace().catch((error) => {
    $("global-error").hidden = false;
    $("global-error").textContent = error instanceof Error ? error.message : String(error);
  }));
  $("delete-workspace").addEventListener("click", () => deleteWorkspace().catch((error) => {
    $("global-error").hidden = false;
    $("global-error").textContent = error instanceof Error ? error.message : String(error);
  }));
  $("edit-toggle").addEventListener("click", () => {
    state.editMode = !state.editMode;
    $("edit-toggle").classList.toggle("active", state.editMode);
    $("save-bundle").hidden = !state.editMode;
    $("reset-bundle").hidden = !state.editMode || !state.data.has_seed;
    $("create-fact").hidden = !state.editMode;
    $("create-card").hidden = !state.editMode;
    renderAll();
  });
  $("save-bundle").addEventListener("click", saveBundle);
  $("reset-bundle").addEventListener("click", resetBundle);
  $("create-fact").addEventListener("click", createFact);
  $("create-card").addEventListener("click", createCard);
  $("fact-editor").addEventListener("submit", submitFactEditor);
  $("fact-preview-source").addEventListener("click", previewFactSource);
  $("card-editor").addEventListener("submit", submitCardEditor);

  document.addEventListener("click", (event) => {
    const viewTarget = event.target.closest("[data-view-target]");
    if (viewTarget) {
      showView(viewTarget.dataset.viewTarget);
      return;
    }
    const reviewStage = event.target.closest("[data-review-stage]");
    if (reviewStage) {
      state.review.reviewState = reviewStage.dataset.reviewStage;
      state.review.sourcePage = "";
      state.review.selectedIds = new Set();
      state.review.selectedCandidateId = null;
      state.review.notice = "";
      loadReviewQueue();
      return;
    }
    const prepAction = event.target.closest("[data-prep-action]");
    if (prepAction) {
      const action = prepAction.dataset.prepAction;
      const jobId = prepAction.dataset.prepJobId;
      if (action === "workspace") {
        openWorkspace(prepAction.dataset.prepWorkspaceId);
        return;
      }
      const request = action === "cancel"
        ? cancelPrepJob(jobId)
        : action === "delete"
          ? deletePrepJob(jobId)
          : action === "rebuild"
            ? rebuildPrepJob(jobId)
            : runPrepJob(jobId);
      request.catch((error) => {
        state.prep.error = String(error);
        renderPrep();
      });
      return;
    }
    const prepReview = event.target.closest("[data-prep-review-task]");
    if (prepReview) {
      state.review.taskId = prepReview.dataset.prepReviewTask;
      state.review.reviewState = "needs_review";
      state.review.sourcePage = "";
      state.review.selectedIds = new Set();
      state.review.selectedCandidateId = null;
      state.review.notice = "";
      showView("review");
      return;
    }
    const reviewSelect = event.target.closest("[data-review-select]");
    if (reviewSelect) {
      state.review.selectedCandidateId = reviewSelect.dataset.reviewSelect;
      state.review.error = "";
      renderReview();
      return;
    }
    const reviewPromote = event.target.closest("[data-review-promote]");
    if (reviewPromote) {
      promoteReviewCandidate(
        reviewPromote.dataset.reviewCandidate,
        reviewPromote.dataset.reviewPromote
      );
      return;
    }
    const reviewWorkspace = event.target.closest("[data-review-workspace]");
    if (reviewWorkspace) {
      openWorkspace(reviewWorkspace.dataset.reviewWorkspace);
      return;
    }
    const reviewAction = event.target.closest("[data-review-action]");
    if (reviewAction) {
      submitReviewAction(
        reviewAction.dataset.reviewCandidate,
        reviewAction.dataset.reviewAction
      );
      return;
    }
    const reviewSource = event.target.closest("[data-review-source-file]");
    if (reviewSource) {
      openSourceReference(
        reviewSource.dataset.reviewSourceFile,
        Number(reviewSource.dataset.reviewSourcePage)
      );
      return;
    }
    const resumeButton = event.target.closest("[data-resume-plan]");
    if (resumeButton) resumePlan(resumeButton.dataset.resumePlan);
    const planButton = event.target.closest("[data-start-plan]");
    if (planButton) {
      startPlan(planButton.dataset.startPlan).catch((error) => {
        $("global-error").hidden = false;
        $("global-error").textContent = String(error);
      });
    }
    const editPlanButton = event.target.closest("[data-edit-plan]");
    if (editPlanButton) openPlanEditor(editPlanButton.dataset.editPlan);
    const deletePlanButton = event.target.closest("[data-delete-plan]");
    if (deletePlanButton) deletePlan(deletePlanButton.dataset.deletePlan).catch((error) => {
      $("global-error").hidden = false;
      $("global-error").textContent = String(error);
    });
    const beatButton = event.target.closest("[data-beat-delta]");
    if (beatButton) changeBeat(Number(beatButton.dataset.beatDelta));
    const revealButton = event.target.closest("[data-reveal-clue]");
    if (revealButton) revealClue(revealButton.dataset.revealClue, revealButton.dataset.revealText || '未命名线索');
    const clockButton = event.target.closest("[data-clock-action]");
    if (clockButton) changeClock(clockButton.dataset.clockAction, Number(clockButton.dataset.clockDelta));
    const editFact = event.target.closest("[data-edit-fact]");
    if (editFact) openFactEditor(editFact.dataset.editFact);
    const cardReview = event.target.closest("[data-card-review-action]");
    if (cardReview) {
      reviewCards([cardReview.dataset.cardReviewId], cardReview.dataset.cardReviewAction);
      return;
    }
    const editCard = event.target.closest("[data-edit-card]");
    if (editCard) openCardEditor(editCard.dataset.editCard);
    const deleteFactButton = event.target.closest("[data-delete-fact]");
    if (deleteFactButton) deleteFact(deleteFactButton.dataset.deleteFact);
    const deleteCardButton = event.target.closest("[data-delete-card]");
    if (deleteCardButton) deleteCard(deleteCardButton.dataset.deleteCard);
    if (event.target.closest("[data-close-modal]") || event.target === $("editor-modal")) closeModal();
    const sourceButton = event.target.closest("[data-open-source-file]");
    if (sourceButton) {
      openSourceReference(
        sourceButton.dataset.openSourceFile,
        Number(sourceButton.dataset.openSourcePage)
      );
      return;
    }
    const link = event.target.closest("[data-fact-link]");
    if (link) {
      state.selectedFactId = link.dataset.factLink;
      const fact = state.data.bundle.facts.find((item) => item.id === state.selectedFactId);
      if (fact) recordLookup('fact', fact.id, '查找事实：' + fact.id);
      showView("facts");
      renderFacts();
      return;
    }
    const copyButton = event.target.closest("[data-copy-keyword]");
    if (copyButton) {
      copyKeyword(copyButton.dataset.copyKeyword);
      return;
    }
    if (!event.target.closest("button, input, textarea, select, a")) {
      const factCard = event.target.closest("[data-fact-id]");
      if (factCard) {
        state.selectedFactId = factCard.dataset.factId;
        const fact = state.data.bundle.facts.find((item) => item.id === state.selectedFactId);
        if (fact) recordLookup('fact', fact.id, '查找事实：' + fact.id);
        renderFacts();
        return;
      }
      const card = event.target.closest("[data-card-id]");
      if (card) {
        state.selectedCardId = card.dataset.cardId;
        const selected = state.data.bundle.cards.find((item) => item.id === state.selectedCardId);
        if (selected) recordLookup('card', selected.id, '查找卡片：' + selected.title, selected.id);
        renderCards();
      }
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) event.preventDefault();
  });
}

async function init() {
  try {
    await loadWorkspaces();
    if (!state.exampleId) {
      bindEvents();
      renderEmptyWorkspace();
      if (["prep", "shelf", "facts", "cards", "review", "runtime"].includes(state.initialView)) showView(state.initialView);
      return;
    }
    document.querySelector('.action-link[href^="/api/domain/export"]').href =
      `/api/domain/export?example=${encodeURIComponent(state.exampleId)}`;
    updateSessionReviewLinks();
    const response = await fetch(
      `/api/domain/workbench?example=${encodeURIComponent(state.exampleId)}`,
      {cache: "no-store"}
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    state.data = await response.json();
    state.savedAt = state.data.saved_at;
    state.savedState = state.data.saved_state;
    state.artifacts.job = state.data.artifact_job || null;
    state.artifacts.generating = artifactJobInFlight(state.artifacts.job);
    scheduleArtifactPoll();
    await Promise.all([
      loadSourceFiles(),
      Promise.resolve(),
      loadSession(),
      loadPrepConfig(),
      loadPrepJobs()
    ]);
    fillSelectors();
    bindEvents();
    renderAll();
    if (["prep", "shelf", "facts", "cards", "review", "runtime"].includes(state.initialView)) {
      showView(state.initialView);
    }
    updateWorkStatus("领域数据已加载");
  } catch (error) {
    const status = $("connection-status");
    status.className = "status error";
    status.textContent = "加载失败";
    const panel = $("global-error");
    panel.hidden = false;
    panel.textContent = String(error);
  }
}

init();
