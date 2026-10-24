// Independent material editor regression tests; uses synthetic data only.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = resolve(__dirname, '..');
async function settle() { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); }
const materialDocument = (unit = 'A/m') => ({ source: '人工测试来源', notes: '保留来源单位', frequency_hz: 50,
  h_unit: unit, loss_unit: 'kernel_native', bh: [[.5, unit === 'A/m' ? 137 : 1.37], [.55, unit === 'A/m' ? 223 : 2.23], [1.88, unit === 'A/m' ? 17000 : 170]],
  loss: [[.5, 2], [.6, 3], [1.88, 40.5]] });
const entry = document => ({ id: 'synthetic-material', kind: 'materials', name: '人工材料', revision: 7, document,
  reference: 'user-material:synthetic-material@7', compatibility: { compatible: true, reasons: [] } });
async function app(t, initial = [], original) {
  let saved;
  const calls = [], errors = [], vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(readFileSync(resolve(root, 'frontend/index.html'), 'utf8'), { url: 'https://dcsj.invalid/', runScripts: 'outside-only', virtualConsole: vc });
  const w = dom.window, d = w.document;
  w.Headers=Headers;w.Request=Request;
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.fetch = async (url, options = {}) => {
    const path = new URL(url, w.location.href).pathname.replace('/api/v1', ''), method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : null; calls.push({ path, method, body }); let data;
    if (path === '/auth/me') data = {username:'张工'};
    else if (path === '/status') data = { catalog: {}, engine: { name: '人工测试', limitations: [] } };
    else if (path === '/schema') data = { groups: [], fields: [] };
    else if (path === '/series') data = { items: [] };
    else if (path === '/resources') data = { active: null, installed: [], remote_configured: false };
    else if (path === '/library/models') data = { items: [], total: 0 };
    else if (path.startsWith('/library/materials')) {
      if (method === 'POST' || method === 'PUT') { saved = { ...entry(body.document), name: body.name, revision: method === 'PUT' ? body.expected_revision + 1 : 1 }; data = saved; }
      else data = { items: saved ? [saved] : initial, total: saved ? 1 : initial.length };
    } else if (path === '/materials') data = { items: [{ id: 'original', name: '原始测试', point_count: 3, loss_point_count: 2, source: '人工原表' }] };
    else if (path === '/materials/original') data = original;
    else throw new Error('Unexpected request ' + path);
    return { ok: true, status: 200, json:async()=>data, text: async () => JSON.stringify(data) };
  };
  w.eval(readFileSync(resolve(root,'frontend/auth.js'),'utf8'));
  w.eval(readFileSync(resolve(root,'frontend/series-picker.js'),'utf8'));
  w.eval(readFileSync(resolve(root, 'frontend/app.js'), 'utf8')); await settle();
  t.after(() => { w.close(); assert.deepEqual(errors, []); });
  const change = (selector, value, event = 'change') => { const node = d.querySelector(selector); node.value = value; node.dispatchEvent(new w.Event(event, { bubbles: true })); };
  d.querySelector('[data-view="resources"]').click(); await settle(); change('#entry-kind', 'materials');
  d.querySelector('#entry-name').value = '手工材料';
  const rows = key => [...d.querySelectorAll(`#material-rows-${key} tr`)].map(row => [...row.querySelectorAll('input')].map(input => input.value));
  const fill = (key, values) => values.forEach((row, i) => row.forEach((value, j) => change(`#material-rows-${key} tr:nth-child(${i + 1}) td:nth-of-type(${j + 1}) input`, value, 'input')));
  const submit = async () => { d.querySelector('#entry-form').dispatchEvent(new w.Event('submit', { cancelable: true })); await settle(); };
  return { w, d, calls, change, rows, fill, submit, saved: () => saved, settle,
    find: (selector, text) => [...d.querySelectorAll(selector)].find(node => node.textContent === text) };
}

test('native row editing converts units and preserves different B1 and B2 axes', async t => {
  const ui = await app(t);
  ui.fill('bh', [[5000, 1.37], [5500, 2.23]]); ui.fill('loss', [[5000, 2], [6000, 3]]);
  ui.d.querySelector('#material-add-bh').click(); ui.fill('bh', [[5000, 1.37], [5500, 2.23], [18800, 170]]);
  assert.equal(ui.rows('loss').length, 2);
  ui.d.querySelector('#material-rows-bh tr:nth-child(2) button').click();
  assert.deepEqual(ui.rows('bh'), [['5000', '1.37'], ['18800', '170']]);
  assert.deepEqual(ui.rows('loss'), [['5000', '2'], ['6000', '3']]);
  await ui.submit();
  assert.deepEqual(ui.saved().document.bh, [[.5, 137], [1.88, 17000]]);
  assert.deepEqual(ui.saved().document.loss, [[.5, 2], [.6, 3]]);
  assert.equal(ui.saved().document.h_unit, 'A/m');
});

test('native and 0.6 batch formats retain independent sample counts', async t => {
  const ui = await app(t); ui.change('#material-format-bh', 'native');
  ui.change('#entry-bh', '5000, 1.37\n5500\t2.23\n18800，170', 'input');
  ui.change('#entry-loss', '.5 2\n1.88 40.5', 'input');
  assert.equal(ui.d.querySelector('#material-rows-bh input').disabled, true);
  ui.d.querySelector('#preview-material-draft').click();
  assert.deepEqual(ui.rows('bh'), [['5000', '1.37'], ['5500', '2.23'], ['18800', '170']]);
  assert.deepEqual(ui.rows('loss'), [['5000', '2'], ['18800', '40.5']]);
  assert.equal(ui.d.querySelector('#material-draft-preview').classList.contains('hidden'), false);
  assert.equal(ui.d.querySelectorAll('#material-draft-preview .chart-card').length, 2);
  assert.match(ui.d.querySelector('#material-draft-status').textContent, /磁化 3 点 · 损耗 2 点/);
  assert.equal(ui.calls.some(call => call.method === 'POST'), false);
  await ui.submit(); assert.equal(ui.saved().document.bh.length, 3); assert.equal(ui.saved().document.loss.length, 2);
});

for (const unit of ['A/m', 'A/cm']) test(`editing a ${unit} revision round-trips the 0.6 document and version guard`, async t => {
  const original = materialDocument(unit), ui = await app(t, [entry(original)]);
  ui.change('#library-kind', 'materials'); await ui.settle(); ui.find('#library-list button', '编辑').click();
  assert.equal(ui.d.querySelector('#entry-kind').disabled, true);
  assert.deepEqual(ui.rows('bh'), [['5000', '1.37'], ['5500', '2.23'], ['18800', '170']]);
  assert.deepEqual(ui.rows('loss'), [['5000', '2'], ['6000', '3'], ['18800', '40.5']]);
  await ui.submit();
  const put = ui.calls.find(call => call.method === 'PUT'); assert.equal(put.body.expected_revision, 7);
  assert.deepEqual(put.body.document, original); assert.equal(ui.saved().revision, 8);
  assert.equal(ui.calls.some(call => call.method === 'POST'), false);
});

test('untouched imported floating-point samples retain their exact API values', async t => {
  const original = materialDocument(); original.bh[1] = [.5501234567890123, 223.12345678901234];
  const ui = await app(t, [entry(original)]); ui.change('#library-kind', 'materials'); await ui.settle();
  ui.find('#library-list button', '编辑').click(); await ui.submit();
  assert.deepEqual(ui.saved().document, original);
});

test('duplicate B, descending values and partial rows fail visibly without a save', async t => {
  const ui = await app(t); ui.fill('bh', [[5000, 1.37], [5000, 2.23]]); ui.fill('loss', [[5000, 2], [6000, 3]]);
  await ui.submit(); assert.match(ui.d.querySelector('#material-draft-error').textContent, /B1.*严格递增/);
  ui.fill('bh', [[5000, 1.37], [5500, 1]]); await ui.submit();
  assert.match(ui.d.querySelector('#material-draft-error').textContent, /H1.*单调不减/);
  ui.fill('bh', [[5000, 1.37], [5500, '']]); await ui.submit();
  assert.match(ui.d.querySelector('#material-draft-error').textContent, /第 2 行须填入/);
  assert.deepEqual(ui.rows('loss'), [['5000', '2'], ['6000', '3']]);
  assert.equal(ui.calls.some(call => call.method === 'POST' || call.method === 'PUT'), false);
});

test('preview becomes stale after a unit or table edit and pending batch cancellation restores row editing', async t => {
  const ui = await app(t); ui.fill('bh', [[5000, 1], [5500, 2]]); ui.fill('loss', [[5000, 2], [6000, 3]]);
  ui.d.querySelector('#preview-material-draft').click();
  ui.change('#entry-h-unit', 'A/cm'); assert.equal(ui.d.querySelector('#material-draft-preview').classList.contains('hidden'), true);
  ui.d.querySelector('#preview-material-draft').click(); ui.fill('loss', [[5000, 2], [6000, 4]]);
  assert.equal(ui.d.querySelector('#material-draft-preview').classList.contains('hidden'), true);
  ui.change('#entry-loss', 'invalid data', 'input'); assert.equal(ui.d.querySelector('#material-add-loss').disabled, true);
  ui.find('#material-point-editors section:nth-child(2) button', '取消批量输入').click();
  assert.equal(ui.d.querySelector('#material-add-loss').disabled, false); assert.deepEqual(ui.rows('loss'), [['5000', '2'], ['6000', '4']]);
});

test('copying original material reads raw API samples, never the transposed plot or matching row numbers', async t => {
  const doc = materialDocument(), original = { name: '原始测试', frequency_hz: 50, source: { path: '人工原表', table: '原表' }, warnings: ['Pfe 单位待核验'],
    curves: ['bh', 'loss'].map(key => ({ id: key, x_label: '磁密 B (T)', y_label: key === 'bh' ? '磁场强度 H (A/m)' : '原表损耗数值（单位待核验）',
      series: [{ name: '原始测试', points: (key === 'bh' ? doc.bh : [doc.loss[0], doc.loss[2]]).map(([x, y]) => ({ x, y })) }],
      plot: { x_label: '不应读取此处', series: [{ points: [{ x: 999, y: 999 }] }] } })) };
  const ui = await app(t, [], original); ui.d.querySelector('[data-view="materials"]').click(); await ui.settle();
  ui.d.querySelector('.material-item').click(); await ui.settle();
  assert.equal(ui.d.querySelectorAll('.material-source-data').length, 2);
  ui.find('#material-detail button', '复制到用户材料编辑器').click();
  assert.deepEqual(ui.rows('bh'), [['5000', '1.37'], ['5500', '2.23'], ['18800', '170']]);
  assert.deepEqual(ui.rows('loss'), [['5000', '2'], ['18800', '40.5']]);
  assert.equal(ui.d.querySelector('#entry-loss-unit').value, 'kernel_native');
  assert.match(ui.d.querySelector('#entry-source').value, /人工原表.*原表/);
  assert.match(ui.d.querySelector('#entry-notes').value, /Pfe 单位待核验/);
  assert.equal(ui.calls.some(call => call.method !== 'GET'), false);
});
