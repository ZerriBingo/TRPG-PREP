// Exercise the actual runtime selectors without loading application startup.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../frontend/workbench.js'), 'utf8');
const names = ['planAnchorIds', 'runtimeConfirmedSceneCards', 'runtimeChapterIncludes', 'runtimeBeatIncludes',
  'runtimeCards', 'runtimePlanCards', 'renderRuntimeOverview', 'sourceHtml'];
const functions = names.map(name => {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}).join('\n');
const card = (id, chapter, facts) => ({id, chapter_ids: [chapter], fact_ids: facts, profile_id: 'daggerheart'});
const cards = [card('direct', 'one', ['f1']), card('thread', 'one', ['f2']),
  card('other', 'two', ['f2']), card('unrelated', 'one', ['f3'])];
const plan = {navigation_mode: 'environment', card_ids: cards.map(c => c.id), environment_card_ids: cards.map(c => c.id)};
const beat = {id: 'beat', chapter_id: 'one', thread_ids: ['thread'], fact_ids: ['f1']};
const context = {
  state: {runtimeProfileId: 'daggerheart', data: {bundle: {cards, story_beats: [beat],
    story_threads: [{id: 'thread', chapter_roles: {one: 'introduce', two: 'resolve'}, fact_ids: ['f2']}]}}},
  runtimeNarrative: {chapter: 'all', beat: 'beat'},
  currentPlan: () => plan,
  anchorLed: () => true,
};
vm.createContext(context);
vm.runInContext(functions, context);
const selected = () => Array.from(vm.runInContext('runtimeConfirmedSceneCards().map(c => c.id)', context));
assert.deepEqual(selected(), ['direct', 'thread', 'unrelated']);
const linked = () => Array.from(vm.runInContext('runtimeConfirmedSceneCards().filter(runtimeBeatIncludes).map(c => c.id)', context));
assert.deepEqual(linked(), []);
cards[0].story_beat_ids = ['beat'];
cards[2].story_beat_ids = ['beat'];
assert.deepEqual(linked(), ['direct']);
beat.fact_ids = ['missing'];
beat.thread_ids = [];
assert.deepEqual(selected(), ['direct', 'thread', 'unrelated']);
context.runtimeNarrative.beat = 'all';
context.runtimeNarrative.chapter = 'two';
assert.deepEqual(selected(), ['other']);
plan.navigation_mode = 'location';
plan.location_card_ids = cards.map(c => c.id);
assert.deepEqual(selected(), cards.map(c => c.id));
assert.deepEqual(cards[0].story_beat_ids, ['beat']);
plan.navigation_mode = 'environment';
context.runtimeNarrative.chapter = 'all';
context.runtimeNarrative.beat = 'beat';
cards.forEach(c => { c.type = 'npc'; });
const support = expression => Array.from(vm.runInContext(expression, context));
assert.deepEqual(support('runtimePlanCards().map(c => c.id)'), ['direct', 'thread', 'unrelated']);
assert.deepEqual(support('runtimeCards(["npc"]).map(c => c.id)'), ['direct', 'thread', 'unrelated']);
context.runtimeNarrative.beat = 'all';
assert.deepEqual(support('runtimePlanCards().map(c => c.id)'), cards.map(c => c.id));
context.runtimeNarrative.chapter = 'two';
assert.deepEqual(support('runtimeCards(["npc"]).map(c => c.id)'), ['other']);
context.runtimeNarrative.chapter = 'all';
context.runtimeNarrative.beat = 'missing';
assert.deepEqual(support('runtimePlanCards().map(c => c.id)'), cards.map(c => c.id));

const elements = new Map();
Object.assign(context, {
  $: id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); },
  esc: value => String(value ?? ''),
  badge: () => '',
  label: value => value,
  factEvidenceStatus: () => 'confirmed',
  factSourceRefs: fact => fact.source_refs || [],
  sourceRefLabel: ref => `p${ref.page}`,
  runtimeCardFieldsHtml: () => '',
  beatThreadDevelopmentHtml: () => '',
  chapterThreadDistributionHtml: () => '',
});
const bundle = context.state.data.bundle;
Object.assign(bundle, {
  id: 'bundle',
  adventure_chapters: [{id: 'one', title: 'Chapter one', summary: '', reference_fact_ids: []}],
  facts: [{id: 'handout', text: 'Letter source text', source_refs: [{page: 42}]},
    {id: 'global', text: 'Global reference text'}],
  fact_dispositions: [{fact_id: 'handout', kind: 'display_material'},
    {fact_id: 'global', kind: 'global_reference'}],
  display_materials: [],
});
vm.runInContext('renderRuntimeOverview()', context);
const references = elements.get('runtime-chapter-reference').innerHTML;
assert.match(references, /Letter source text/);
assert.match(references, /p42/);
assert.match(references, /Global reference text/);
assert.match(references, /展示材料来源参考/);
assert.deepEqual(bundle.display_materials, []);
bundle.fact_dispositions = [];
vm.runInContext('renderRuntimeOverview()', context);
assert.equal(elements.get('runtime-chapter-reference').innerHTML, '');
console.log('PASS: only explicit same-chapter links recommend environments; other chapter environments stay available');
console.log('PASS: support filters use the effective chapter; display dispositions retain source references without display records');
