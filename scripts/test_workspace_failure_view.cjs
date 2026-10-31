// A project the server refuses must fail with its real reason, without stranding the
// page, and returning to the shelf must not erase a reason that is still true.
//
// The failure-ordering check runs the real init/showView/refreshWorkbenchData/renderAll/
// renderEmptyWorkspace/renderLoadFailure functions and the real bindEvents handlers;
// only unrelated renderers are stubbed.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../frontend/workbench.js'), 'utf8').replace(/\r\n/g, '\n');

function extract(name) {
  const start = [source.indexOf(`async function ${name}(`), source.indexOf(`function ${name}(`)]
    .filter((index) => index >= 0)
    .sort((a, b) => a - b)[0];
  assert.equal(typeof start, 'number', `workbench.js must define ${name}`);
  let params = 0;
  let bodyStart = start;
  for (let index = source.indexOf('(', start); index < source.length; index += 1) {
    if (source[index] === '(') params += 1;
    else if (source[index] === ')') {
      params -= 1;
      if (params === 0) { bodyStart = index; break; }
    }
  }
  let depth = 0;
  for (let index = source.indexOf('{', bodyStart); index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    else if (source[index] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const escapeStart = source.indexOf('const esc =');
const escapeEnd = source.indexOf('\nfunction formatApiError(');
assert(escapeStart >= 0 && escapeEnd > escapeStart, 'escape helper must be isolatable');

// The real entry points involved in the defect, plus the two formatters.
const extracted = [
  'formatApiError', 'userFacingError',
  'showLoadFailure', 'clearLoadFailure', 'renderLoadFailure', 'renderEmptyWorkspace', 'renderAll',
  'fillWorkspaceSelector', 'refreshWorkbenchData', 'showView', 'init', 'saveSession'
].map(extract).join('\n\n');

// bindEvents is real; only its direct handler references are stubbed.
const bindSource = extract('bindEvents');
const extractedNames = ['formatApiError', 'userFacingError', 'showLoadFailure', 'renderLoadFailure',
  'renderEmptyWorkspace', 'renderAll', 'fillWorkspaceSelector', 'refreshWorkbenchData', 'showView',
  'init', 'bindEvents', 'clearLoadFailure', 'saveSession'];
const handlerStubs = [...new Set(
  [...bindSource.matchAll(/addEventListener\(\s*"[a-z]+"\s*,\s*([A-Za-z_$][\w$]*)/g)].map((m) => m[1])
)].filter((name) => !extractedNames.includes(name)).map((name) => `const ${name} = () => {};`).join('\n');

const CONTRACT_MESSAGE = "项目含有当前板块契约不再接受的卡型或内容，无法按当前版本打开；项目内容仍保留。";

function element() {
  let html = '';
  const node = {
    className: '', textContent: '', innerHTML: '', value: '', hidden: false, disabled: false,
    title: '', style: {}, dataset: {}, files: [], options: [], listeners: {},
    classList: {add() {}, remove() {}, toggle() {}},
    querySelector(selector) {
      if (selector === '[data-workspace-load-failure]' && this.innerHTML.includes('data-workspace-load-failure')) {
        return {remove: () => { this.innerHTML = ''; }};
      }
      return null;
    },
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
  };
  Object.defineProperties(node, {
    innerHTML: {get: () => html, set: (value) => { html = String(value); }},
    textContent: {
      get: () => html.replace(/<[^>]*>/g, ''),
      set: (value) => { html = String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
    }
  });
  return node;
}

function harness() {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const navButtons = ["prep", "shelf", "facts", "cards", "review", "runtime"].map((view) => {
    const button = element();
    button.dataset.view = view;
    return button;
  });
  const calls = {workbenchRequests: 0};
  let failWorkbench = true;

  const context = {
    console, Date, Intl, encodeURIComponent, JSON, URLSearchParams,
    location: {search: '?example=ws_legacy&view=shelf'},
    state: {
      exampleId: 'ws_legacy',
      initialView: 'shelf',
      eventsBound: false,
      loadFailure: null,
      data: {bundle: {id: 'ws_legacy', name: '上个项目'}, profiles: {}, plans: []},
      workspaces: [],
      timings: {projectReadyMs: null},
      prepSelection: {profileId: null, boards: {}},
      prep: {jobs: [], config: null, uploads: [], uploadItems: [], error: '', notice: ''},
      session: null,
      sessionState: 'new',
      artifacts: {job: null, generating: false, retryingJobId: null, error: ''},
      structure: {job: null, generating: false},
      runtimeProfileId: null,
      workbenchRefreshToken: 0,
      workbenchLoading: false
    },
    $: getElement,
    document: {
      addEventListener: () => {},
      querySelectorAll: (selector) => (selector.includes('.work-nav button') ? navButtons : [])
    },
    window: {addEventListener: () => {}},
    fetch: async (url) => {
      if (String(url).endsWith('/session')) {
        return {ok: false, status: 503, json: async () => ({detail: 'HTTP 503'})};
      }
      if (!String(url).includes('/api/domain/workbench')) {
        return {ok: true, status: 200, json: async () => ({})};
      }
      calls.workbenchRequests += 1;
      if (!failWorkbench) {
        return {ok: true, status: 200, json: async () => ({bundle: {id: 'ws_legacy', name: '可用项目', cards: []}, profiles: {}, saved_at: null, saved_state: 'saved'})};
      }
      return {
        ok: false,
        status: 422,
        json: async () => ({
          detail: {code: 'contract_incompatible', message: CONTRACT_MESSAGE, detail: "card card_legacy uses undefined type 'scene'"}
        })
      };
    },
    loadWorkspaces: async () => {
      context.state.workspaces = [
        {id: 'ws_ready', name: '可用项目', validation_error: null, validation_kind: null, kind: 'prep'},
        {id: 'ws_legacy', name: '旧卡型项目', validation_error: CONTRACT_MESSAGE, validation_kind: 'contract_incompatible', kind: 'saved'},
        {id: 'ws_broken', name: '结构损坏项目', validation_error: '项目数据结构无法按当前版本读取。', validation_kind: 'model_invalid', kind: 'saved'},
        {id: 'ws_odd', name: '未知问题项目', validation_error: '无法打开。', validation_kind: null, kind: 'saved'},
        {id: 'ws_missing_id', name: 'ws_missing_id', validation_error: CONTRACT_MESSAGE, validation_kind: 'model_invalid', kind: 'saved'}
      ];
      context.fillWorkspaceSelector();
    },
    renderPrep: () => {},
    renderShelf: () => {},
    renderFacts: () => {},
    renderPrepSelection: () => {},
    renderCards: () => {},
    renderReview: () => {},
    renderRuntime: () => {},
    fillSelectors: () => {},
    loadSession: async () => {},
    syncPrepSelectionFromBundle: () => {},
    setArtifactJob: () => {},
    structureJobInFlight: () => false,
    scheduleStructurePoll: () => {},
    artifactJobInFlight: () => false,
    scheduleArtifactPoll: () => {},
    updateSessionReviewLinks: () => {},
    updateWorkStatus: () => {},
    recordProjectReady: () => 0,
    formatElapsed: () => '0.0 秒',
    startDeferredLoads: () => {},
    loadPrepJobs: () => Promise.resolve(),
    loadPrepConfigDeferred: () => {},
    bundleProfiles: () => [],
    refreshCardTypes: () => {},
    prepareRuntime: () => {},
    setFailWorkbench: (value) => { failWorkbench = value; }
  };
  context.fetch = context.fetch;
  context.setFailWorkbench = (value) => { failWorkbench = value; };
  vm.createContext(context);
  vm.runInContext(source.slice(escapeStart, escapeEnd) + '\n' + handlerStubs + '\n' + extracted + '\n' + bindSource, context);
  return {context, getElement, navButtons, calls};
}

const evaluate = (context, code) => vm.runInContext(code, context);
const tick = () => new Promise((resolve) => setImmediate(resolve));

async function main() {
  // A structured server reason survives both formatters.
  const fmtHarness = harness();
  const fmt = fmtHarness.context;
  assert.equal(evaluate(fmt, `formatApiError({detail: {code: "contract_incompatible", message: ${JSON.stringify(CONTRACT_MESSAGE)}}}, "x")`), CONTRACT_MESSAGE);
  assert.equal(
    evaluate(fmt, `userFacingError({apiReason: ${JSON.stringify(CONTRACT_MESSAGE)}}, "工作台加载失败")`),
    CONTRACT_MESSAGE,
    'an accurate reason must not be compressed into a refresh hint'
  );

  // Opening a refused project: reason shown, navigation bound, stale data dropped.
  const refused = harness();
  await evaluate(refused.context, 'init()');
  const panel = refused.getElement('global-error');
  assert.equal(evaluate(refused.context, 'state.eventsBound'), true, 'navigation must be bound');
  assert.equal(evaluate(refused.context, 'state.data'), null);
  assert.equal(refused.getElement('connection-status').textContent, '项目无法打开');
  assert.match(panel.innerHTML, /当前板块契约不再接受/);
  assert.match(panel.innerHTML, /刷新不会改变这个结果/);
  assert.doesNotMatch(panel.innerHTML, /刷新后重试/);
  assert.match(panel.innerHTML, /contract_incompatible/);

  // Clicking 书架 runs the real showView refresh-failure path. The reason is still
  // true, so it must survive the empty-workspace render that follows.
  const shelfButton = refused.navButtons.find((button) => button.dataset.view === 'shelf');
  shelfButton.listeners.click[0]();
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert.equal(panel.hidden, false, 'the failure panel must stay visible after returning to the shelf');
  assert.match(panel.innerHTML, /当前板块契约不再接受/);
  assert.equal(refused.getElement('connection-status').textContent, '项目无法打开');
  assert.ok(refused.calls.workbenchRequests >= 2, 'the shelf view really refreshed');

  // Once a project loads, the stored failure is cleared.
  refused.context.setFailWorkbench(false);
  await evaluate(refused.context, 'refreshWorkbenchData()');
  assert.equal(evaluate(refused.context, 'state.loadFailure'), null);
  assert.equal(panel.hidden, true);

  // An ordinary save failure belongs to its operation, not to the load renderer.
  evaluate(refused.context, `state.session = {notes: ""};`);
  assert.equal(await evaluate(refused.context, 'saveSession()'), false);
  assert.equal(panel.hidden, false);
  assert.equal(panel.textContent, 'HTTP 503');

  evaluate(refused.context, `showLoadFailure({apiCode: 'contract_incompatible', apiReason: 'Load failed'}, 'x'); renderAll();`);
  await evaluate(refused.context, 'saveSession()');
  evaluate(refused.context, 'renderAll(); clearLoadFailure();');
  assert.equal(panel.hidden, false, 'clearing an old load failure must keep a newer save failure');
  assert.equal(panel.textContent, 'HTTP 503');
  evaluate(refused.context, 'renderAll()');
  assert.equal(panel.hidden, false, 'background redraw must preserve a save failure');
  assert.equal(panel.textContent, 'HTTP 503');

  // A temporary failure still invites a retry.
  const network = harness();
  evaluate(network.context, `state.exampleId = "ws_legacy"; showLoadFailure(new Error("HTTP 502"), "工作台加载失败"); renderEmptyWorkspace();`);
  assert.match(network.getElement('global-error').innerHTML, /稍后刷新重试/);
  assert.equal(network.getElement('connection-status').textContent, '项目无法打开');

  // Bookshelf labels name the category instead of a vague "needs repair".
  evaluate(fmt, `
    state.exampleId = "";
    state.workspaces = [
      {id: "ws_ready", name: "可用项目", validation_error: null, validation_kind: null, kind: "prep"},
      {id: "ws_legacy", name: "旧卡型项目", validation_error: ${JSON.stringify(CONTRACT_MESSAGE)}, validation_kind: "contract_incompatible", kind: "saved"},
      {id: "ws_broken", name: "结构损坏项目", validation_error: "x", validation_kind: "model_invalid", kind: "saved"},
      {id: "ws_odd", name: "未知问题项目", validation_error: "x", validation_kind: null, kind: "saved"},
      {id: "ws_missing_id", name: "ws_missing_id", validation_error: "x", validation_kind: "model_invalid", kind: "saved"}
    ];
    fillWorkspaceSelector();
  `);
  const html = fmtHarness.getElement('example-select').innerHTML;
  assert.match(html, /可用项目 · 备团/);
  assert.match(html, /旧卡型项目 · 当前契约无法打开/);
  assert.match(html, /结构损坏项目 · 数据无法读取/);
  assert.match(html, /未知问题项目 · 无法打开/);
  assert.match(html, /value="ws_missing_id"/, 'a stored project without a body id stays listed');
  assert.doesNotMatch(html, /数据需修复/);

  console.log('PASS: refused projects keep their real reason across shelf navigation and are labelled by category');
}

Promise.resolve(main()).catch((error) => {
  console.error(error);
  process.exit(1);
});
