// Preparation selection in the workbench: draft/saved state, per-project and per-board
// isolation, stale references, in-flight saves, the runtime quick area, and the reset
// confirmations that tell the GM what they actually lose.
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

const labelsStart = source.indexOf('const prepProfileLabels = {');
const labelsEnd = source.indexOf('\n};', labelsStart) + 3;
assert(labelsStart >= 0 && labelsEnd > labelsStart + 3, 'board label map must be isolatable');

const extractedNames = [
  'label', 'badge', 'formatValue', 'profileDefinition', 'profileDisplayName', 'enabledProfiles',
  'localDateTime', 'formatApiError', 'userFacingError',
  'prepSelectionProfiles', 'prepSelectionProfileId', 'prepSelectionKey', 'prepSelectionBoard',
  'syncPrepSelectionFromBundle', 'prepSelectionCardEntries', 'prepSelectionDirty',
  'prepSelectionScopeLabel', 'renderPrepSelection', 'togglePrepSelection',
  'clearPrepSelection', 'savePrepSelection', 'renderRuntimePrepSelection',
  'startPlan', 'resetSession'
];
const extracted = extractedNames.map(extract).join('\n\n');

const cards = [
  {id: 'card_ct_one', profile_id: 'cthulhu-dark-2e', type: 'location', title: '旧渡口仓库',
    edit_state: 'approved', chapter_ids: [], fields: {normal_state: '常态'}},
  {id: 'card_ct_two', profile_id: 'cthulhu-dark-2e', type: 'npc', title: '伊莲·福斯',
    edit_state: 'approved', chapter_ids: [], fields: {role: '记录员'}},
  {id: 'card_ct_draft', profile_id: 'cthulhu-dark-2e', type: 'npc', title: '未批准人物',
    edit_state: 'generated', chapter_ids: [], fields: {role: '草案'}},
  {id: 'card_ct_clock', profile_id: 'cthulhu-dark-2e', type: 'clock', title: '潮汐推进钟',
    edit_state: 'approved', chapter_ids: [], fields: {name: '潮汐', stages: ['平静', '涨潮']}},
  {id: 'card_dh_one', profile_id: 'daggerheart', type: 'environment', title: '峡谷伏击点',
    edit_state: 'approved', chapter_ids: ['ch1'], fields: {narrative_function: '穿越受阻'}}
];

const definitions = {
  'cthulhu-dark-2e': [
    {type: 'location', display_name: '地点卡', required_fields: [], optional_fields: []},
    {type: 'npc', display_name: '功能 NPC 卡', required_fields: [], optional_fields: []},
    {type: 'clock', display_name: '推进钟', required_fields: [], optional_fields: []}
  ],
  daggerheart: [{type: 'environment', display_name: '环境卡', required_fields: [], optional_fields: []}]
};

function element() {
  return {
    className: '', textContent: '', innerHTML: '', value: '', hidden: false, disabled: false,
    listeners: {}, addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
  };
}

function harness({failSave = false, delaySave = null, confirmText = null, clockCards = []} = {}) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const requests = [];
  const confirmations = [];
  const context = {
    console, Date, Intl, encodeURIComponent, JSON, Math,
    state: {
      exampleId: 'prep_test_project',
      runtimeProfileId: 'cthulhu-dark-2e',
      prepSelection: {profileId: null, boards: {}},
      sessionDirty: false,
      session: {
        current_plan_id: 'plan_one', current_beat_id: null, current_card_id: 'card_ct_one',
        trigger_states: {'card_ct_one:0': 'active'}, revealed_clue_keys: ['clue'], clock_stages: {card_ct_clock: 1},
        log: ['run_started'], notes: '备注'
      },
      data: {
        bundle: {
          id: 'prep_test_project',
          profile_ids: ['cthulhu-dark-2e', 'daggerheart'],
          cards,
          adventure_chapters: [{id: 'ch1', title: '第一章'}],
          plans: [{id: 'plan_one', card_ids: ['card_ct_one']}],
          prep_selections: {'cthulhu-dark-2e': ['card_ct_one', 'card_removed'], daggerheart: []}
        },
        profiles: {
          'cthulhu-dark-2e': {id: 'cthulhu-dark-2e', name: '现实恐怖', profile_kind: 'runtime', card_definitions: definitions['cthulhu-dark-2e']},
          daggerheart: {id: 'daggerheart', name: '奇幻冒险', profile_kind: 'runtime', card_definitions: definitions.daggerheart}
        }
      }
    },
    $: getElement,
    document: {
      querySelectorAll: (selector) => (selector.includes('[data-clock-card]')
        ? clockCards.map((id) => ({dataset: {clockCard: id}})) : []),
      querySelector: () => null
    },
    renderCards: () => {},
    showView: () => {},
    renderRuntime: () => {},
    renderAll: () => {},
    loadSession: async () => {},
    sessionLog: () => {},
    localDateTime: () => '',
    confirm: (message) => {
      confirmations.push(message);
      return confirmText === null ? false : confirmText;
    },
    fetch: async (url, options) => {
      requests.push({url, method: options?.method || 'GET', body: options?.body ? JSON.parse(options.body) : null});
      if (delaySave) await delaySave;
      if (failSave) return {ok: false, status: 500, json: async () => ({detail: '存储不可写'})};
      if (!options?.method || options.method === 'GET') return {ok: true, status: 200, json: async () => ({})};
      const body = JSON.parse(options.body || '{}');
      return {
        ok: true, status: 200,
        json: async () => ({ok: true, saved_at: '2026-10-31T12:00:00+00:00', profile_id: body.profile_id, card_ids: body.card_ids})
      };
    }
  };
  context.profile = (id) => context.state.data.profiles[id] || null;
  vm.createContext(context);
  vm.runInContext(source.slice(labelsStart, labelsEnd) + '\n' +
    'const runtimeClockTypes = ["clock", "operation_clock", "encounter_clock"];\n' +
    source.slice(escapeStart, escapeEnd) + '\n' + extracted, context);
  return {context, getElement, requests, confirmations};
}

const evaluate = (context, code) => vm.runInContext(code, context);
const draftOf = (context, profile = 'cthulhu-dark-2e') =>
  [...evaluate(context, `prepSelectionBoard(${JSON.stringify(profile)}).draft`)];
const boardOf = (context, field, profile = 'cthulhu-dark-2e') =>
  evaluate(context, `prepSelectionBoard(${JSON.stringify(profile)}).${field}`);

async function main() {
  const {context, getElement, requests, confirmations} = harness();
  evaluate(context, 'syncPrepSelectionFromBundle()');

  // Saved selection loads into the draft; the unknown id stays as a stale reference.
  assert.deepEqual(draftOf(context), ['card_ct_one', 'card_removed']);
  assert.equal(evaluate(context, 'prepSelectionDirty()'), false);

  evaluate(context, 'renderPrepSelection()');
  const html = getElement('prep-selection-list').innerHTML;
  assert.match(html, /已不可用的资料/);
  assert.match(html, /card_removed/);
  assert.doesNotMatch(html, /未批准人物/);

  // A stale reference must still be removable, otherwise it could never be cleared.
  evaluate(context, 'togglePrepSelection("card_removed")');
  assert.deepEqual(draftOf(context), ['card_ct_one']);
  evaluate(context, 'state.prepSelection.boards[prepSelectionKey("cthulhu-dark-2e")].draft = ["card_ct_one", "card_removed"]; state.prepSelection.boards[prepSelectionKey("cthulhu-dark-2e")].dirty = false');

  // Only approved cards of the active board can be added.
  evaluate(context, 'togglePrepSelection("card_ct_draft")');
  assert.equal(draftOf(context).length, 2, 'an unapproved card cannot be added');
  evaluate(context, 'togglePrepSelection("card_dh_one")');
  assert.equal(draftOf(context).length, 2, 'another board\'s card cannot be added to this board');

  // In-flight save: edits made while the request is pending are kept, not swallowed.
  let releaseSave;
  const gate = new Promise((resolve) => { releaseSave = resolve; });
  const slow = harness({delaySave: gate});
  evaluate(slow.context, 'syncPrepSelectionFromBundle()');
  evaluate(slow.context, 'togglePrepSelection("card_ct_two")');
  const pending = evaluate(slow.context, 'savePrepSelection()');
  // The GM keeps working while the request is in flight.
  evaluate(slow.context, 'togglePrepSelection("card_ct_clock")');
  const duringSave = {draft: [...evaluate(slow.context, 'prepSelectionBoard().draft')], dirty: evaluate(slow.context, 'prepSelectionBoard().dirty')};
  releaseSave();
  await pending;
  assert.deepEqual(duringSave.draft, ['card_ct_one', 'card_removed', 'card_ct_two', 'card_ct_clock']);
  assert.deepEqual([...evaluate(slow.context, 'prepSelectionBoard().draft')],
    ['card_ct_one', 'card_removed', 'card_ct_two', 'card_ct_clock'],
    'edits made during the save must survive the response');
  assert.equal(evaluate(slow.context, 'prepSelectionBoard().dirty'), true,
    'the newer edits are still unsaved');
  assert.deepEqual([...evaluate(slow.context, 'prepSelectionBoard().saved')],
    ['card_ct_one', 'card_removed', 'card_ct_two'],
    'the request snapshot is what the server stored');
  assert.match(evaluate(slow.context, 'prepSelectionBoard().notice'), /保存期间的改动尚未保存/);

  // A failed save keeps the draft and reports the reason.
  const failing = harness({failSave: true});
  evaluate(failing.context, 'syncPrepSelectionFromBundle()');
  evaluate(failing.context, 'togglePrepSelection("card_ct_two")');
  await evaluate(failing.context, 'savePrepSelection()');
  assert.equal(evaluate(failing.context, 'prepSelectionBoard().dirty'), true);
  assert.match(evaluate(failing.context, 'prepSelectionBoard().error'), /存储不可写/);
  assert.doesNotMatch(failing.getElement('prep-selection-status').textContent, /已保存/);

  // A successful save sends exactly the draft and keeps other boards untouched.
  evaluate(context, 'togglePrepSelection("card_ct_two"); renderPrepSelection()');
  await evaluate(context, 'savePrepSelection()');
  assert.deepEqual(requests.at(-1).body, {
    profile_id: 'cthulhu-dark-2e', card_ids: ['card_ct_one', 'card_removed', 'card_ct_two']
  });
  assert.equal(evaluate(context, 'prepSelectionBoard().dirty'), false);
  assert.deepEqual([...evaluate(context, 'prepSelectionBoard("daggerheart").saved')], []);

  // Drafts are isolated by project as well as board: adopting another project in the
  // same page must not show the old project's unsaved draft.
  evaluate(context, 'togglePrepSelection("card_ct_two")');
  const projectADraft = draftOf(context);
  evaluate(context, `
    state.exampleId = "other_project";
    state.data.bundle.prep_selections = {"cthulhu-dark-2e": []};
    syncPrepSelectionFromBundle();
  `);
  assert.deepEqual(draftOf(context), [], 'a newly adopted project starts from its own selection');
  assert.equal(evaluate(context, 'prepSelectionDirty()'), false, 'the other project has no unsaved draft');
  evaluate(context, 'state.exampleId = "prep_test_project"; syncPrepSelectionFromBundle()');
  assert.deepEqual(draftOf(context), projectADraft, 'returning to the first project restores its draft');

  // A save that lands while another project is open updates only its own project.
  let releaseCross;
  const crossGate = new Promise((resolve) => { releaseCross = resolve; });
  const cross = harness({delaySave: crossGate});
  evaluate(cross.context, 'syncPrepSelectionFromBundle(); togglePrepSelection("card_ct_two")');
  const crossPending = evaluate(cross.context, 'savePrepSelection()');
  evaluate(cross.context, `
    state.exampleId = "other_project";
    state.data.bundle.id = "other_project";
    state.data.bundle.cards = [];
    state.data.bundle.prep_selections = {};
    syncPrepSelectionFromBundle();
  `);
  releaseCross();
  await crossPending;
  assert.deepEqual([...evaluate(cross.context, 'prepSelectionBoard().draft')], [],
    'the other project keeps its own empty draft');
  assert.equal(evaluate(cross.context, 'prepSelectionDirty()'), false);
  assert.deepEqual(
    [...evaluate(cross.context, 'state.prepSelection.boards["prep_test_project::cthulhu-dark-2e"].saved')],
    ['card_ct_one', 'card_removed', 'card_ct_two'],
    'the response updates the project it belonged to'
  );
  assert.equal(evaluate(cross.context, 'state.data.bundle.prep_selections["cthulhu-dark-2e"]'),
    undefined, 'the open project\'s bundle is not touched by another project\'s response');

  // Runtime quick area: clocks are not disguised as card details.
  const quick = harness();
  evaluate(quick.context, `
    state.data.bundle.prep_selections = {"cthulhu-dark-2e": ["card_ct_one", "card_ct_clock", "card_ct_draft"]};
    state.runtimeProfileId = "cthulhu-dark-2e";
    renderRuntimePrepSelection();
  `);
  const quickHtml = quick.getElement('runtime-prep-selection-list').innerHTML;
  assert.match(quickHtml, /旧渡口仓库/);
  assert.match(quickHtml, /潮汐推进钟/);
  assert.doesNotMatch(quickHtml, /未批准人物/, 'a draft is never shown as saved material');
  assert.doesNotMatch(quickHtml, /data-retrieval-card="card_ct_clock"/,
    'a clock must not produce a normal card-detail button');
  assert.match(quickHtml, /data-retrieval-card="card_ct_one"/);
  assert.match(quickHtml, /当前运行范围未显示此钟/,
    'a clock outside the current situation is explained, not silently missing');
  assert.doesNotMatch(quickHtml, /切换局势后可见/,
    'an excluded clock may also be outside the plan or chapter');

  // A clock the runtime is showing gets a locate action and its stage, still no card detail.
  const quickShown = harness({clockCards: ['card_ct_clock']});
  evaluate(quickShown.context, `
    state.data.bundle.prep_selections = {"cthulhu-dark-2e": ["card_ct_clock"]};
    state.runtimeProfileId = "cthulhu-dark-2e";
    renderRuntimePrepSelection();
  `);
  const shownHtml = quickShown.getElement('runtime-prep-selection-list').innerHTML;
  assert.match(shownHtml, /阶段 2\/2 · 在下方“推进钟”区域查看与推进/);
  assert.match(shownHtml, /data-clock-locate="card_ct_clock"/);
  assert.doesNotMatch(shownHtml, /data-retrieval-card/);
  assert.match(quick.getElement('runtime-prep-selection-count').textContent, /2 张/);
  assert.match(quick.getElement('runtime-prep-selection-count').textContent, /1 条引用已不可用/);

  // Run the real renderRuntime, not just the quick-area renderer. The DOM starts
  // empty, then contains the preceding situation's clock on the next render.
  const ordered = harness();
  const clockOne = {...cards[3], runtime_group_ids: ['one']};
  const clockTwo = {...cards[3], id: 'card_clock_two', runtime_group_ids: ['two']};
  let scene = {...cards[0], runtime_group_ids: ['one']};
  Object.assign(ordered.context, {
    currentPlan: () => ({profile_id: 'cthulhu-dark-2e', navigation_mode: 'environment'}),
    currentBeat: () => null,
    anchorLed: () => true,
    renderRuntimeOverview: () => {}, renderRuntimeReferenceCards: () => {},
    renderGlobalDisplayMaterials: () => {}, renderPlans: () => {}, renderBeat: () => {},
    profileDisplaySummary: () => '', profileKindLabels: {},
    runtimeLabels: {}, runtimeNarrative: {beat: 'all'},
    runtimeRetrievalQuery: () => '', runtimeConfirmedSceneCards: () => [],
    currentRuntimeCard: () => scene, renderRuntimeRetrieval: () => {},
    runtimeCardFieldsHtml: () => '', runtimeRelatedHtml: () => '',
    locationMaterialsHtml: () => '', sourceHtml: () => '',
    runtimeCards: () => [clockOne, clockTwo], sessionLogLabels: {}
  });
  ordered.context.state.session.log = [];
  Object.assign(ordered.context.state.data.profiles['cthulhu-dark-2e'], {
    gm_moves: [], prompts: {}, risk_axes: []
  });
  ordered.context.state.data.bundle.cards.push(clockTwo);
  ordered.context.state.data.bundle.prep_selections = {
    'cthulhu-dark-2e': [clockOne.id, clockTwo.id]
  };
  ordered.context.document.querySelectorAll = (selector) => selector.includes('[data-clock-card]')
    ? [...ordered.getElement('runtime-clocks').innerHTML.matchAll(/data-clock-card="([^"]+)"/g)]
      .map((match) => ({dataset: {clockCard: match[1]}}))
    : [];
  vm.runInContext(extract('renderRuntime'), ordered.context);
  evaluate(ordered.context, 'renderRuntime()');
  assert.match(ordered.getElement('runtime-prep-selection-list').innerHTML,
    /data-clock-locate="card_ct_clock"/, 'first render locates the visible clock');
  scene = {...scene, runtime_group_ids: ['two']};
  evaluate(ordered.context, 'renderRuntime()');
  const switchedHtml = ordered.getElement('runtime-prep-selection-list').innerHTML;
  assert.match(switchedHtml, /data-clock-locate="card_clock_two"/);
  assert.doesNotMatch(switchedHtml, /data-clock-locate="card_ct_clock"/,
    'the preceding situation no longer supplies a dead locate action');

  // Reset confirmations name what is actually lost, and cancelling changes nothing.
  const confirmHarness = harness({confirmText: false});
  const sessionBefore = evaluate(confirmHarness.context, 'JSON.stringify(state.session)');
  evaluate(confirmHarness.context, 'startPlan("plan_one")');
  assert.match(confirmHarness.confirmations[0], /触发状态/);
  assert.match(confirmHarness.confirmations[0], /运行位置/);
  assert.match(confirmHarness.confirmations[0], /本次准备选择会保留/);
  assert.equal(evaluate(confirmHarness.context, 'JSON.stringify(state.session)'), sessionBefore,
    'cancelling startPlan leaves the running state untouched');
  await evaluate(confirmHarness.context, 'resetSession()');
  assert.match(confirmHarness.confirmations[1], /触发状态/);
  assert.match(confirmHarness.confirmations[1], /备注/);
  assert.match(confirmHarness.confirmations[1], /运行位置/);
  assert.match(confirmHarness.confirmations[1], /本次准备选择会保留/);
  assert.equal(confirmHarness.requests.length, 0, 'cancelling the reset sends no request');

  console.log('PASS: selection drafts, project/board isolation, in-flight saves, clock quick entries, and reset confirmations behave as specified');
}

Promise.resolve(main()).catch((error) => {
  console.error(error);
  process.exit(1);
});
