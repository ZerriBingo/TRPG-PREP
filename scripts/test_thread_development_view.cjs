// Render actual view functions with two different developments of one storyline.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../frontend/workbench.js'), 'utf8');
const names = ['beatThreadDevelopmentHtml', 'storyThreadRoleLabel', 'chapterThreadDistributionHtml'];
const functions = names.map(name => {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}).join('\n');
const thread = {id: 'thread', title: '<b>Truth</b>', summary: 'A disputed secret', chapter_roles: {one: 'introduce', two: 'resolve'}};
const first = {title: 'Questions', chapter_id: 'one', thread_ids: ['thread'],
  thread_developments: {thread: {development: 'A contradiction emerges', carry_forward: 'The motive remains unknown'}},
  interplay: 'Disclosure risks an alliance'};
const second = {...first, title: 'Bargaining', thread_developments: {thread: {development: 'A rival offers evidence', carry_forward: 'A debt may remain'}}};
const outside = {...first, title: 'OUTSIDE', chapter_id: 'two'};
const context = {thread, first, second, outside};
vm.createContext(context);
const escapeStart = source.indexOf('const esc =');
const escapeEnd = source.indexOf('\n\n', escapeStart);
vm.runInContext(source.slice(escapeStart, escapeEnd) + '\n' + functions, context);
const html = vm.runInContext('chapterThreadDistributionHtml({id:"one"}, [thread], [first, second, outside])', context);
assert(html.includes('A contradiction emerges') && html.includes('A rival offers evidence'));
assert(html.includes('The motive remains unknown') && html.includes('A debt may remain'));
assert(!html.includes('OUTSIDE'));
assert(html.includes('&lt;b&gt;Truth&lt;/b&gt;') && !html.includes('<b>Truth</b>'));
assert(vm.runInContext('beatThreadDevelopmentHtml(first, [thread])', context).includes('Disclosure risks an alliance'));
const old = vm.runInContext('beatThreadDevelopmentHtml({thread_ids:["thread"]}, [thread])', context);
assert(old.includes('尚无本拍展开说明'));
console.log('PASS: chapter distribution preserves distinct developments, excludes other chapters, and labels old beats');
