// Source-management details must never gate an existing project.
//
// The takeover audit measured /api/domain/source-files at tens of seconds while
// /api/domain/workspaces answered in about a tenth of one, and the workbench
// waited for both. These checks run the real init() with a stubbed network and
// assert the order the product promises: project material first, source details
// reported on their own surface afterwards.
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
  // Skip a destructured parameter list before looking for the body brace.
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

const stateStart = source.indexOf('const state = {');
const stateEnd = source.indexOf('\nconst $ =');
assert(stateStart >= 0 && stateEnd > stateStart, 'state literal must stay isolated from DOM helpers');

// esc is a const arrow helper, so it is sliced up to the next declaration.
const escapeStart = source.indexOf('const esc =');
const escapeEnd = source.indexOf('\nfunction formatApiError(');
assert(escapeStart >= 0 && escapeEnd > escapeStart, 'escape helper must be isolatable');

const extracted = [
  'formatApiError', 'userFacingError', 'formatElapsed', 'updateWorkStatus', 'clearLoadFailure',
  'formatFileSize', 'sourceBasename',
  'renderSourceStatus', 'renderSourceFiles', 'renderSourceOptions', 'adoptUploadedSource',
  'loadSourceFiles', 'uploadPrepSource', 'loadPrepConfig',
  'loadWorkspaces', 'recordProjectReady', 'loadPrepConfigDeferred',
  'startDeferredLoads', 'init'
].map(extract).join('\n\n');

const workbenchPayload = {
  saved_at: null,
  saved_state: 'seed',
  bundle: {facts: [], cards: [], plans: []},
  artifact_job: null,
  structure_job: null
};

const uploadItems = [
  {file: 'data/uploads/kept.pdf', original_name: 'kept.pdf', page_count: 12, size_bytes: 2048,
    referenced: true, references: [{kind: 'prep_job', id: 'job-1', label: '备团任务'}]},
  {file: 'data/uploads/free.pdf', original_name: 'free.pdf', page_count: 3, size_bytes: 1024,
    referenced: false, references: []}
];

const uploadedFile = 'data/uploads/new-upload.pdf';
const uploadResponse = {
  ok: true,
  status: 200,
  json: async () => ({
    file: uploadedFile, original_name: 'new.pdf', page_count: 2, size_bytes: 900,
    sha256: 'digest', reused: false
  })
};

function element() {
  return {
    className: '', textContent: '', innerHTML: '', value: '', hidden: false,
    disabled: false, href: '', placeholder: '', checked: false, files: [],
    querySelector: () => null
  };
}

// A real <select> refuses a value with no matching option, which is exactly how a
// source missing from the option list became unselectable.
function selectElement() {
  const node = element();
  let stored = '';
  Object.defineProperty(node, 'value', {
    get: () => stored,
    set: (next) => {
      const options = [...String(node.innerHTML).matchAll(/<option value="([^"]*)"/g)].map((match) => match[1]);
      stored = options.includes(next) ? next : '';
    }
  });
  Object.defineProperty(node, 'files', {value: []});
  return node;
}

// sourceMode: 'hang' | 'fail' | 'ready'
function harness(sourceMode) {
  const elements = new Map([['global-error', {...element(), hidden: true}]]);
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, id === 'prep-job-source' ? selectElement() : element());
    return elements.get(id);
  };
  const calls = {renderAll: 0, bindEvents: 0, showView: [], loadSession: 0};
  let releaseSource = () => {};
  let uploaded = false;
  const pendingSource = new Promise((resolve) => { releaseSource = resolve; });
  const sourceResponse = () => {
    const items = uploaded
      ? [...uploadItems, {file: uploadedFile, original_name: 'new.pdf', page_count: 2,
        size_bytes: 900, referenced: false, references: []}]
      : uploadItems;
    return {
      ok: true,
      status: 200,
      json: async () => ({
        files: items.map((item) => item.file),
        uploads: items.map((item) => item.file),
        upload_items: items,
        resources: []
      })
    };
  };

  const route = (url, options) => {
    if (url.startsWith('/api/domain/source-files')) {
      if (options?.method === 'POST') {
        uploaded = true;
        return Promise.resolve(uploadResponse);
      }
      if (sourceMode === 'hang') return pendingSource.then(() => sourceResponse());
      if (sourceMode === 'fail') return Promise.reject(new Error('HTTP 504'));
      return Promise.resolve(sourceResponse());
    }
    const payload = url.startsWith('/api/domain/workspaces') ? {workspaces: [{id: 'ws1'}]}
      : url.startsWith('/api/domain/workbench') ? workbenchPayload
      : url.startsWith('/api/config') ? {base_url: '', model: 'fake', fake: true, has_key: false}
      : {session: null, updated_at: null, state: 'new'};
    return Promise.resolve({ok: true, status: 200, json: async () => payload, text: async () => ''});
  };

  const context = {
    URLSearchParams,
    Date,
    console,
    location: {search: '?example=ws1&view=runtime'},
    $: getElement,
    fetch: (url, options) => route(url, options),
    encodeURIComponent,
    // The browser accepts a File here; the harness only has to let the POST be built.
    FormData: class { append() {} },
    setTimeout,
    clearTimeout,
    bindEvents: () => { calls.bindEvents += 1; },
    fillSelectors: () => {},
    fillWorkspaceSelector: () => {},
    renderEmptyWorkspace: () => {},
    renderPrep: () => {},
    syncPrepSelectionFromBundle: () => {},
    renderAll: () => { calls.renderAll += 1; },
    showView: (view) => { calls.showView.push(view); },
    updateSessionReviewLinks: () => {},
    setArtifactJob: () => {},
    structureJobInFlight: () => false,
    scheduleStructurePoll: () => {},
    artifactJobInFlight: () => false,
    scheduleArtifactPoll: () => {},
    loadPrepJobs: () => Promise.resolve(),
    loadSession: async () => { calls.loadSession += 1; }
  };
  vm.createContext(context);
  vm.runInContext(
    source.slice(stateStart, stateEnd) + '\n' + source.slice(escapeStart, escapeEnd) + '\n' + extracted,
    context
  );
  return {context, calls, elements, getElement, releaseSource: () => { releaseSource(); }};
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const withTimeout = (promise, label) => Promise.race([
  promise,
  new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} was blocked`)), 1000))
]);

async function projectSurvivesHangingSource() {
  const {context, calls, getElement, releaseSource} = harness('hang');
  await withTimeout(vm.runInContext('init()', context), 'init() with a hanging source request');
  assert.equal(calls.renderAll, 1, 'the project must render before source details');
  assert.equal(calls.bindEvents, 1, 'events must be bound without waiting for source details');
  assert.deepEqual(calls.showView, ['runtime'], 'the requested view must be shown');
  assert.equal(calls.loadSession, 1, 'project play state loads with the project');
  assert.equal(vm.runInContext('state.prep.source.state', context), 'loading');
  assert.match(getElement('prep-source-file-list').innerHTML, /正在读取来源详情/);
  assert.equal(getElement('global-error').hidden, true, 'a slow source request is not a workbench failure');
  assert.notEqual(getElement('connection-status').className, 'status error');
  assert.match(getElement('connection-status').textContent, /项目可查/);
  const readyMs = vm.runInContext('state.timings.projectReadyMs', context);
  assert.ok(Number.isFinite(readyMs) && readyMs >= 0, 'project readiness time is reported');

  releaseSource();
  await flush();
  await flush();
  assert.equal(vm.runInContext('state.prep.source.state', context), 'ready');
  assert.ok(Number.isFinite(vm.runInContext('state.prep.source.elapsedMs', context)));
  assert.match(getElement('prep-source-status').textContent, /来源详情已加载/);
  return readyMs;
}

async function projectSurvivesFailedSource() {
  const {context, calls, getElement} = harness('fail');
  await withTimeout(vm.runInContext('init()', context), 'init() with a failing source request');
  assert.equal(calls.renderAll, 1);
  assert.deepEqual(calls.showView, ['runtime']);
  assert.equal(vm.runInContext('state.prep.source.state', context), 'error');
  assert.match(getElement('prep-source-status').textContent, /来源详情读取失败/);
  assert.match(getElement('prep-source-file-list').innerHTML, /来源详情读取失败[\s\S]*刷新/);
  assert.notEqual(getElement('connection-status').className, 'status error');
  assert.equal(getElement('global-error').hidden, true, 'a failed source request is not a workbench failure');
  assert.equal(getElement('global-error').textContent, '');
}

async function referencesStayProtected() {
  const {context, getElement} = harness('ready');
  await withTimeout(vm.runInContext('init()', context), 'init() with source details');
  await flush();
  await flush();
  const html = getElement('prep-source-file-list').innerHTML;
  assert.match(html, /正在使用/);
  const referenced = html.slice(html.indexOf('kept.pdf'), html.indexOf('free.pdf'));
  assert.match(referenced, /data-source-delete="data\/uploads\/kept\.pdf" disabled/);
  assert.match(html, /data-source-delete="data\/uploads\/free\.pdf"\s+title=/);
  assert.doesNotMatch(html.slice(html.indexOf('free.pdf')), /disabled/);
}

// A successful upload must not wait for the detail listing, and must stay usable
// when that listing hangs or fails.
async function uploadSurvives(sourceMode) {
  const {context, getElement} = harness(sourceMode);
  const input = getElement('prep-source-upload');
  input.files = [{name: 'new.pdf'}];
  await withTimeout(vm.runInContext('uploadPrepSource()', context), `upload with ${sourceMode} details`);
  const select = getElement('prep-job-source');
  assert.equal(vm.runInContext('state.prep.uploading', context), false, 'the upload UI must be released');
  assert.match(getElement('prep-source-upload-status').textContent, /已上传 · 2 页 · new\.pdf/);
  assert.match(select.innerHTML, /data\/uploads\/new-upload\.pdf/, 'the uploaded source must be selectable');
  assert.equal(select.value, uploadedFile, 'the uploaded source must be selected immediately');
  assert.equal(vm.runInContext('state.prep.error', context), '', 'a detail failure is not an upload failure');
  const html = getElement('prep-source-file-list').innerHTML;
  assert.match(html, /new\.pdf/);
  assert.match(html, /data-source-delete="data\/uploads\/new-upload\.pdf" disabled/, 'unknown references are not deletable');
  if (sourceMode === 'hang') {
    assert.equal(vm.runInContext('state.prep.source.state', context), 'loading');
    assert.match(html, /引用状态确认中/);
  } else if (sourceMode === 'fail') {
    assert.equal(vm.runInContext('state.prep.source.state', context), 'error');
    assert.match(html, /引用状态未知 · 详情读取失败/);
  } else {
    // A successful refresh reconciles the details but must keep the selection.
    await flush();
    await flush();
    assert.equal(vm.runInContext('state.prep.source.state', context), 'ready');
    assert.equal(select.value, uploadedFile, 'the refresh must keep the uploaded source selected');
    assert.match(getElement('prep-source-file-list').innerHTML, /未被任务或书架引用，可删除/);
  }
}

(async () => {
  const readyMs = await projectSurvivesHangingSource();
  await projectSurvivesFailedSource();
  await referencesStayProtected();
  await uploadSurvives('hang');
  await uploadSurvives('fail');
  await uploadSurvives('ready');
  console.log(`PASS: project rendered in ${readyMs}ms while source details stayed independent, upload finished and stayed selectable, and references stayed protected`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
