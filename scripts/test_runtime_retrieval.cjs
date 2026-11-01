// Runtime retrieval: search, results, and the selected card must stay together,
// scoped to one project's approved material, with plan/chapter limits stated.
//
// Two boundaries this file exists for: drafts never enter runtime retrieval, and every
// offered action is one the runtime actually performs. The fixture is synthetic; real
// generated artifacts are never used as fixtures.
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

// Field labels decide what a GM reads, so the real map is used, not a copy.
const labelsStart = source.indexOf('const fieldLabels = {');
const labelsEnd = source.indexOf('\n};', labelsStart) + 3;
assert(labelsStart >= 0 && labelsEnd > labelsStart + 3, 'field label map must be isolatable');

const extractedNames = [
  'label', 'badge', 'formatValue', 'profileDefinition',
  'currentPlan', 'currentBeat', 'anchorLed', 'planAnchorIds',
  'runtimePlanCards', 'runtimeChapterIncludes', 'runtimeGroupCards', 'runtimeConfirmedSceneCards',
  'sessionLog', 'changeRuntimeScene', 'bindEvents',
  'runtimeCardFieldsHtml',
  'runtimeSearchCards', 'runtimeCardSearchText', 'runtimeRetrievalQuery', 'runtimeRetrievalType',
  'runtimeRetrievalList', 'runtimeRetrievalChapterTitle', 'runtimeRetrievalScopeLabels',
  'runtimeRetrievalScopeText', 'runtimeCardExcerpt', 'runtimeFieldGapHtml',
  'runtimeRetrievalSelectedCard', 'runtimeRetrievalHitHtml', 'runtimeSceneSwitchable',
  'runtimeSceneSwitchNote', 'handleRuntimeRetrievalClick',
  'renderRuntimeRetrieval', 'renderRuntimeRetrievalDetail',
  'runtimeRelatedCards', 'runtimeRelatedHtml'
];

const extracted = extractedNames.map(extract).join('\n\n');

// bindEvents passes many handlers by name. Only the retrieval path matters here, so
// every other handler is stubbed rather than dragging the whole workbench in.
const bindSource = extract('bindEvents');
const handlerStubs = [...new Set(
  [...bindSource.matchAll(/addEventListener\(\s*"[a-z]+"\s*,\s*([A-Za-z_$][\w$]*)/g)].map((match) => match[1])
)].filter((name) => !extractedNames.includes(name))
  .map((name) => `const ${name} = () => {};`)
  .join('\n');

const cards = [
  // Approved material of the current board.
  {id: 'card_a_loc', profile_id: 'rh', type: 'location', title: '卡莱尔公馆', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {normal_state: '公馆常态', relevant_characters: ['艾丽卡']}, fact_ids: [], runtime_group_ids: []},
  {id: 'card_b_npc', profile_id: 'rh', type: 'npc', title: '艾丽卡·卡莱尔', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {role: '卡莱尔家族继承人', wants: '查明真相', offers: '媒体报道真假',
      pressure_point: '对布内的怨恨', refusal_consequence: '关闭图书馆', voice_notes: ''}, fact_ids: []},
  {id: 'card_c_npc', profile_id: 'rh', type: 'npc', title: '艾丽卡（同名候选）', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {role: '另一位同名人物', wants: '不明'}, fact_ids: []},
  {id: 'card_d_clock', profile_id: 'rh', type: 'clock', title: '艾丽卡倒计时', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {name: '倒计时', stages: ['1'], advance_condition: '时间'}, fact_ids: []},
  {id: 'card_l_outside', profile_id: 'rh', type: 'location', title: '计划外地点', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {normal_state: '不在当前计划里的地点'}, fact_ids: []},
  {id: 'card_o_loc2', profile_id: 'rh', type: 'location', title: '可切换地点', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {normal_state: '同计划同章节'}, fact_ids: []},
  // Drafts: never part of runtime retrieval.
  {id: 'card_i_draft', profile_id: 'rh', type: 'npc', title: '艾丽卡（生成草案）', subtitle: '', chapter_ids: [],
    edit_state: 'generated', fields: {role: '未批准内容'}, fact_ids: []},
  {id: 'card_j_edited', profile_id: 'rh', type: 'npc', title: '艾丽卡（修订稿）', subtitle: '', chapter_ids: [],
    edit_state: 'edited', fields: {role: '未重新批准的内容'}, fact_ids: []},
  {id: 'card_k_unknown', profile_id: 'rh', type: 'npc', title: '艾丽卡（状态缺失）', subtitle: '', chapter_ids: [],
    fields: {role: '没有审批状态的内容'}, fact_ids: []},
  // Other board.
  {id: 'card_e_other', profile_id: 'dh', type: 'npc', title: '艾丽卡（其他板块）', subtitle: '', chapter_ids: [],
    edit_state: 'approved', fields: {role: '其他板块人物'}, fact_ids: []},
  // Fantasy/environment material with reviewed links.
  {id: 'card_f_env', profile_id: 'dh', type: 'environment', title: '码头对峙', subtitle: '', chapter_ids: ['ch1'],
    edit_state: 'approved', runtime_group_ids: ['grp'], fields: {narrative_function: '对峙'}, fact_ids: []},
  {id: 'card_g_enemy', profile_id: 'dh', type: 'enemy', title: '码头打手', subtitle: '', chapter_ids: ['ch2'],
    edit_state: 'approved', runtime_group_ids: ['grp'], fields: {intention: '驱赶'}, fact_ids: []},
  {id: 'card_h_npc', profile_id: 'dh', type: 'npc', title: '码头主事', subtitle: '', chapter_ids: ['ch1'],
    edit_state: 'approved', fields: {role: '替码头打手收账的人', current_objective: '见 card_g_enemy'}, fact_ids: []},
  {id: 'card_m_ch2', profile_id: 'dh', type: 'environment', title: '第二章码头', subtitle: '', chapter_ids: ['ch2'],
    edit_state: 'approved', runtime_group_ids: ['grp'], fields: {narrative_function: '另一处对峙'}, fact_ids: []},
  {id: 'card_n_draft_enemy', profile_id: 'dh', type: 'enemy', title: '码头打手（草案）', subtitle: '', chapter_ids: ['ch1'],
    edit_state: 'generated', runtime_group_ids: ['grp'], fields: {intention: '未批准'}, fact_ids: []}
];

const definitions = {
  rh: [
    {type: 'location', display_name: '地点卡', description: '', required_fields: ['normal_state'],
      optional_fields: ['relevant_characters']},
    {type: 'npc', display_name: '功能 NPC 卡', description: '',
      required_fields: ['role', 'wants', 'offers', 'pressure_point', 'refusal_consequence'],
      optional_fields: ['first_impression', 'secret', 'voice_notes']},
    {type: 'clock', display_name: '推进钟', description: '', required_fields: ['name'], optional_fields: []}
  ],
  dh: [
    {type: 'environment', display_name: '环境卡', description: '', required_fields: ['narrative_function'], optional_fields: []},
    {type: 'enemy', display_name: '对手卡', description: '', required_fields: ['intention'], optional_fields: []},
    {type: 'npc', display_name: '人物卡', description: '', required_fields: ['role'], optional_fields: ['current_objective']}
  ]
};

function element() {
  const node = {
    className: '', textContent: '', innerHTML: '', value: '', hidden: true, files: [], listeners: {},
    addEventListener(type, handler) { (this.listeners[type] = this.listeners[type] || []).push(handler); }
  };
  return node;
}

function harness() {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = {
    console,
    Date,
    Math,
    state: {
      runtimeProfileId: 'rh',
      runtimeRetrieval: {cardId: null},
      sessionDirty: false,
      session: {
        current_plan_id: 'plan_rh', current_card_id: 'card_a_loc', current_beat_id: null, log: []
      },
      data: {
        bundle: {
          cards,
          facts: [],
          plans: [
            {id: 'plan_rh', navigation_mode: 'location', beats: [],
              card_ids: ['card_a_loc', 'card_b_npc', 'card_d_clock', 'card_o_loc2'],
              location_card_ids: ['card_a_loc', 'card_o_loc2']},
            {id: 'plan_dh', navigation_mode: 'environment', beats: [],
              card_ids: ['card_f_env', 'card_g_enemy', 'card_h_npc', 'card_m_ch2', 'card_n_draft_enemy'],
              environment_card_ids: ['card_f_env', 'card_m_ch2']}
          ],
          adventure_chapters: [{id: 'ch1', title: '第一章'}, {id: 'ch2', title: '第二章'}],
          story_beats: [{id: 'beat1', chapter_id: 'ch1', title: '第一拍'}]
        },
        profiles: {
          rh: {id: 'rh', card_definitions: definitions.rh},
          dh: {id: 'dh', card_definitions: definitions.dh}
        }
      }
    },
    $: getElement,
    document: {
      querySelectorAll: () => [],
      addEventListener: () => {}
    },
    window: {addEventListener: () => {}},
    sourceHtml: (factIds) => factIds && factIds.length
      ? '<details class="field"><summary class="field-label">来源事实</summary></details>'
      : '',
    currentRuntimeCard: () => cards.find((card) => card.id === vm.runInContext('state.session.current_card_id', contextRef)),
    renderRuntime: () => { renderCalls.count += 1; }
  };
  const renderCalls = {count: 0};
  context.renderCalls = renderCalls;
  const contextRef = context;
  context.profile = (profileId) => context.state.data.profiles[profileId] || null;
  vm.createContext(context);
  vm.runInContext(
    source.slice(labelsStart, labelsEnd) + '\n' +
    'const runtimeLabels = {}, kindLabels = {}, visibilityLabels = {}, evidenceStatusLabels = {};\n' +
    source.slice(escapeStart, escapeEnd) + '\n' +
    'const runtimeClockTypes = ["clock", "operation_clock", "encounter_clock"];\n' +
    'const runtimeNarrative = {workspace: null, chapter: "all", beat: "all"};\n' +
    handlerStubs + '\n' +
    extracted,
    context
  );
  return {context, getElement, renderCalls};
}

function evaluate(context, code) {
  return vm.runInContext(code, context);
}

function target(raw) {
  return {closest: (selector) => raw[selector] || null};
}

function main() {
  const {context, getElement, renderCalls} = harness();

  // P1: only explicitly approved cards of this board, and never clocks.
  const scope = evaluate(context, 'runtimeSearchCards().map((card) => card.id)');
  assert.deepEqual([...scope],
    ['card_a_loc', 'card_b_npc', 'card_c_npc', 'card_l_outside', 'card_o_loc2'],
    'generated, edited, unapproved-by-default, other-board, and clock cards must not qualify');

  // Drafts never surface, even when the query matches them.
  getElement('runtime-reference-search').value = '艾丽卡';
  evaluate(context, 'renderRuntimeRetrieval()');
  const status = getElement('runtime-retrieval-status').textContent;
  assert.match(status, /匹配 3 条/);
  assert.match(status, /多个候选请自行选择，不按同名合并/);
  assert.match(status, /检索本项目已批准的 5 张卡片/);
  assert.doesNotMatch(status, /其他板块/);
  const hitHtml = getElement('runtime-retrieval-list').innerHTML;
  assert.match(hitHtml, /卡莱尔公馆/);
  assert.match(hitHtml, /艾丽卡·卡莱尔/);
  assert.match(hitHtml, /艾丽卡（同名候选）/);
  assert.match(hitHtml, /不在当前计划/, 'an unplanned hit is labelled instead of hidden');
  assert.doesNotMatch(hitHtml, /生成草案|修订稿|状态缺失|其他板块/);
  assert.doesNotMatch(hitHtml, /艾丽卡倒计时/, 'clocks are not retrieval targets');

  // A draft can be neither opened nor described as approved material.
  evaluate(context, 'state.runtimeRetrieval.cardId = "card_i_draft"; renderRuntimeRetrievalDetail()');
  assert.equal(getElement('runtime-retrieval-detail').innerHTML, '', 'a draft detail must stay empty');

  // A miss says why and what the search covers.
  getElement('runtime-reference-search').value = '不存在的人';
  evaluate(context, 'renderRuntimeRetrieval()');
  const miss = getElement('runtime-retrieval-status').textContent;
  assert.match(miss, /没有匹配“不存在的人”/);
  assert.match(miss, /检索只覆盖本项目已批准资料/);

  // Selecting an approved card shows its own fields next to where the search happened.
  getElement('runtime-reference-search').value = '艾丽卡';
  evaluate(context, 'state.runtimeRetrieval.cardId = "card_b_npc"; renderRuntimeRetrieval()');
  const detail = getElement('runtime-retrieval-detail').innerHTML;
  assert.match(detail, /艾丽卡·卡莱尔/);
  assert.match(detail, /身份/);
  assert.match(detail, /想要/);
  assert.match(detail, /说话方式/, 'empty roleplay fields are named, not silently dropped');
  assert.match(detail, /卡片本身没有提供，未作推测/);
  assert.match(detail, /返回当前地点/);
  assert.match(getElement('runtime-retrieval-list').innerHTML, /retrieval-hit selected/);

  // Reviewed links only, and only approved ones.
  evaluate(context, 'state.runtimeProfileId = "dh"; state.session.current_plan_id = "plan_dh"; runtimeNarrative.chapter = "all";');
  const relatedByGroup = evaluate(context, 'runtimeRelatedCards(state.data.bundle.cards[10]).map((item) => item.card.id + ":" + item.via)');
  assert.deepEqual([...relatedByGroup], ['card_g_enemy:同组', 'card_m_ch2:同组'],
    'a group-linked draft is excluded while approved group members stay');
  const relatedById = evaluate(context, 'runtimeRelatedCards(state.data.bundle.cards[12]).map((item) => item.card.id + ":" + item.via)');
  assert.deepEqual([...relatedById], ['card_g_enemy:卡内链接']);
  const relatedByName = evaluate(context, 'runtimeRelatedCards(state.data.bundle.cards[0]).map((item) => item.card.id)');
  assert.deepEqual([...relatedByName], [], 'a shared name in prose is not an approved link');

  // An environment keeps its support cards readable beside it.
  const environmentRelated = evaluate(context, 'runtimeRelatedHtml(state.data.bundle.cards[10])');
  assert.match(environmentRelated, /码头打手/);
  assert.match(environmentRelated, /runtime-reference-card/);
  assert.doesNotMatch(environmentRelated, /草案/);

  // Chapter-limited environment: the filter is reported on the hit and on the switch.
  evaluate(context, 'runtimeNarrative.chapter = "ch1"');
  getElement('runtime-reference-search').value = '码头';
  evaluate(context, 'renderRuntimeRetrieval()');
  const fantasyStatus = getElement('runtime-retrieval-status').textContent;
  assert.match(fantasyStatus, /章节筛选：第一章/);
  assert.match(fantasyStatus, /本章外/);
  evaluate(context, 'state.runtimeRetrieval.cardId = "card_m_ch2"; renderRuntimeRetrievalDetail()');
  const chapterDetail = getElement('runtime-retrieval-detail').innerHTML;
  assert.doesNotMatch(chapterDetail, /data-retrieval-switch/, 'no dead switch button outside the chapter filter');
  assert.match(chapterDetail, /不可直接切换：不在当前章节筛选内/);

  // Plan-outside scene: readable, with a reason, and still no switch button.
  evaluate(context, 'state.runtimeProfileId = "rh"; state.session.current_plan_id = "plan_rh"; runtimeNarrative.chapter = "all";');
  evaluate(context, 'state.runtimeRetrieval.cardId = "card_l_outside"; renderRuntimeRetrievalDetail()');
  const outsideDetail = getElement('runtime-retrieval-detail').innerHTML;
  assert.doesNotMatch(outsideDetail, /data-retrieval-switch/);
  assert.match(outsideDetail, /不可直接切换：不在当前运行计划内，仅供查阅/);
  assert.match(outsideDetail, /返回当前地点/);

  // A scene the plan actually allows keeps the action.
  evaluate(context, 'state.runtimeRetrieval.cardId = "card_o_loc2"; renderRuntimeRetrievalDetail()');
  const switchableDetail = getElement('runtime-retrieval-detail').innerHTML;
  assert.match(switchableDetail, /data-retrieval-switch="card_o_loc2"/);
  assert.doesNotMatch(switchableDetail, /不可直接切换/);

  // The action availability matches the runtime's own switching rule.
  assert.deepEqual([...evaluate(context, 'runtimeConfirmedSceneCards().map((card) => card.id)')],
    ['card_a_loc', 'card_o_loc2']);

  // Event chain: the related-person button inside the scene card must reach the same
  // handler as the retrieval panel, without depending on a sibling container.
  evaluate(context, 'bindEvents()');
  const sceneHolder = getElement('scene-holder');
  const retrieval = getElement('runtime-retrieval');
  assert.equal(sceneHolder.listeners.click?.length, 1, 'the scene card must handle retrieval clicks');
  assert.equal(retrieval.listeners.click?.length, 1, 'the retrieval panel must handle its own clicks');
  const sessionBefore = evaluate(context, 'JSON.stringify({card: state.session.current_card_id, log: state.session.log.length})');

  sceneHolder.listeners.click[0]({target: target({'[data-retrieval-card]': {dataset: {retrievalCard: 'card_b_npc'}}})});
  assert.equal(evaluate(context, 'state.runtimeRetrieval.cardId'), 'card_b_npc');
  assert.match(getElement('runtime-retrieval-detail').innerHTML, /艾丽卡·卡莱尔/);

  retrieval.listeners.click[0]({target: target({'[data-retrieval-close]': {dataset: {}}})});
  assert.equal(evaluate(context, 'state.runtimeRetrieval.cardId'), null);
  assert.equal(getElement('runtime-retrieval-detail').innerHTML, '');
  assert.equal(evaluate(context, 'JSON.stringify({card: state.session.current_card_id, log: state.session.log.length})'),
    sessionBefore, 'reading and returning must not touch the running state');

  // The switch action performs the switch and records it.
  evaluate(context, 'state.runtimeRetrieval.cardId = "card_o_loc2"; renderRuntimeRetrievalDetail()');
  renderCalls.count = 0;
  retrieval.listeners.click[0]({target: target({'[data-retrieval-switch]': {dataset: {retrievalSwitch: 'card_o_loc2'}}})});
  assert.equal(evaluate(context, 'state.session.current_card_id'), 'card_o_loc2');
  assert.equal(evaluate(context, 'state.runtimeRetrieval.cardId'), null, 'switching closes the retrieval detail');
  const logTail = evaluate(context, 'JSON.stringify(state.session.log.slice(-1)[0] || null)');
  assert.match(logTail, /切换场景：可切换地点/);
  assert.equal(renderCalls.count, 1, 'the scene surface re-renders after a switch');

  console.log('PASS: retrieval is approval-scoped, limits are explicit, related clicks and scene actions all resolve');
}

main();
