// Exercise the checked-in HTML and script against synthetic in-memory HTTP responses.
// This uses no browser, local server, model library, or private design records.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = resolve(__dirname, '..');
const html = readFileSync(resolve(root, 'frontend/index.html'), 'utf8');
const script = readFileSync(process.env.DCSJ_FRONTEND_TEST_SCRIPT || resolve(root, 'frontend/app.js'), 'utf8');
const parameters = { SizeX: '人工设计', kW: 12 };
const created = '2020-01-02T03:04:05Z';

function deferred() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
}

async function until(check, message) {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (check()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.ok(check(), message);
}

async function settle() {
  for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));
}

async function app(t, route) {
  const errors = [], calls = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://dcsj.invalid/', runScripts: 'outside-only', virtualConsole });
  const w = dom.window, doc = w.document;
  t.after(() => { w.close(); assert.deepEqual(errors, [], 'Unexpected frontend runtime errors'); });
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.URL.createObjectURL = () => 'blob:synthetic-test';
  w.URL.revokeObjectURL = () => {};
  w.fetch = async (url, options = {}) => {
    const request = { url: new URL(url, w.location.href), method: options.method || 'GET',
      body: options.body ? JSON.parse(options.body) : undefined };
    calls.push(request);
    const path = request.url.pathname.replace('/api/v1', '');
    let data;
    if (path === '/status') data = { name: 'DCSJ synthetic test', version: 'test', catalog: {},
      engine: { name: '人工测试', limitations: [] }, reports: { pdf: { available: true } } };
    else if (path === '/schema') data = { groups: [{ id: 'basic', label: '基本参数' }], fields: [
      { key: 'SizeX', label: '设计名称', group: 'basic', type: 'text', role: 'input' },
      { key: 'kW', label: '额定功率', group: 'basic', type: 'number', role: 'input' }] };
    else if (path === '/series') data = { items: ['人工系列'] };
    else data = await route(path, request);
    if (data === undefined && path === '/comparison-sets' && request.method === 'GET') data = { items: [], total: 0 };
    assert.notEqual(data, undefined, `Unexpected API request: ${path}`);
    return { ok: true, status: 200, text: async () => JSON.stringify(data) };
  };
  w.eval(script);
  await until(() => doc.querySelector('#connection-text').textContent === '服务已连接', 'App boot completed');
  await settle();
  const find = (selector, label) => {
    const node = [...doc.querySelectorAll(selector)].find(node => node.textContent === label);
    assert.ok(node, `Missing control: ${label}`);
    return node;
  };
  return { w, doc, calls, find, nav: view => doc.querySelector(`[data-view="${view}"]`).click() };
}

function snapshot(id, name = id) {
  return { calculation_id: id, name, created_at: created, inputs: parameters, values: { ATA1: .9 },
    metrics: [{ key: 'ATA1', label: '人工效率', value: .9, unit: '' }], curves: [], warnings: [], engine: { name: '人工测试' } };
}

function records() {
  return { total: 2, items: ['a', 'b'].map(id => ({ id, name: id, created_at: created, power_kw: 12, efficiency: .9 })) };
}

test('a nonempty model catalog renders and loads a design without project actions', async t => {
  const ui = await app(t, path => {
    if (path === '/models') return { total: 1, items: [{ id: 'synthetic-model', model: '人工型号', source: '人工数据' }] };
    if (path === '/models/synthetic-model') return { model: '人工型号', parameters, source: '人工数据' };
  });
  ui.nav('catalog');
  await until(() => ui.doc.querySelectorAll('#model-table-body tr').length === 1, 'Model row is visible');
  assert.equal(ui.doc.querySelector('#catalog-feedback').textContent, '');
  assert.deepEqual([...ui.doc.querySelectorAll('#model-table-body button')].map(x => x.textContent), ['载入设计 →']);
  ui.find('#model-table-body button', '载入设计 →').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '人工型号', 'Model loads');
  assert.equal(ui.doc.querySelector('#calculate-button').disabled, false);
});

test('saved projects expose history, archive and restore using the current revision', async t => {
  const project = { id: 'project-1', name: '人工项目', revision: 3, archived: 0, updated_at: created, parameters, notes: '' };
  const ui = await app(t, (path, request) => {
    if (path === '/projects') return { items: !project.archived || request.url.searchParams.get('include_archived') === 'true' ? [project] : [] };
    if (path === '/projects/project-1') return project;
    if (path === '/projects/project-1/archive') {
      assert.equal(request.body.expected_revision, 3);
      project.archived = Number(request.body.archived);
      return project;
    }
    if (path === '/projects/project-1/revisions') return { items: [{ revision: 2, name: '人工旧版', created_at: created }] };
    if (path === '/projects/project-1/revisions/2') return { name: '人工旧版', parameters };
  });
  ui.nav('projects');
  await until(() => ui.doc.querySelectorAll('#project-table-body button').length === 3, 'Project controls are available');
  ui.find('#project-table-body button', '打开设计 →').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === project.name, 'Project opens');
  ui.nav('projects'); await settle();
  ui.find('#project-table-body button', '归档').click();
  await until(() => project.archived === 1 && ui.doc.querySelectorAll('#project-table-body tr').length === 0, 'Archived project leaves active list');
  ui.doc.querySelector('#save-design').click();
  assert.equal(ui.doc.querySelector('#save-as-new').checked, true, 'An open archived project defaults to Save As');
  ui.doc.querySelector('#cancel-save').click();
  const include = ui.doc.querySelector('#include-archived'); include.checked = true; include.dispatchEvent(new ui.w.Event('change'));
  await until(() => ui.doc.querySelectorAll('#project-table-body tr').length === 1, 'Archived project is shown when selected');
  ui.find('#project-table-body button', '恢复').click();
  await until(() => project.archived === 0 && ui.doc.querySelector('#project-table-body').textContent.includes('归档'), 'Restore refreshes actions');
  ui.find('#project-table-body button', '版本').click();
  await until(() => ui.doc.querySelectorAll('#history-content button').length === 1, 'Revision list opens');
  ui.find('#history-content button', '以此版本另建方案').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '人工旧版 · v2 副本', 'A revision becomes a new draft');
  assert.equal(ui.doc.querySelector('#history-dialog').open, false);
  ui.doc.querySelector('#save-design').click();
  assert.equal(ui.doc.querySelector('#save-as-new').checked, true);
  assert.equal(ui.doc.querySelector('#save-as-new').disabled, true);
});

test('late snapshot responses cannot overwrite the most recently opened snapshot', async t => {
  const pending = deferred();
  const ui = await app(t, path => {
    if (path === '/calculations') return records();
    if (path === '/calculations/a') return pending.promise;
    if (path === '/calculations/b') return snapshot('b', '较新的选择');
  });
  ui.nav('comparisons');
  await until(() => ui.doc.querySelectorAll('#calculation-table-body tr').length === 2, 'Snapshots are listed');
  const rows = [...ui.doc.querySelectorAll('#calculation-table-body tr')];
  rows[0].querySelector('button').click();
  rows[1].querySelector('button').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '较新的选择', 'Second snapshot opens first');
  pending.release(snapshot('a', '慢速旧请求')); await settle();
  assert.equal(ui.doc.querySelector('#current-design').textContent, '较新的选择');
  const metadata = ui.doc.querySelector('.result-meta').textContent;
  assert.ok(metadata.includes(new ui.w.Date(created).toLocaleString('zh-CN')), 'Snapshot shows its original calculation time');
  assert.match(ui.doc.querySelector('#result-content a').href, /\/reports\/b$/);
});

test('editing the current design invalidates an earlier pending snapshot load', async t => {
  const pending = deferred();
  const ui = await app(t, path => {
    if (path === '/example') return { name: '当前草稿', parameters };
    if (path === '/calculations') return records();
    if (path === '/calculations/a') return pending.promise;
  });
  ui.doc.querySelector('#load-example').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '当前草稿', 'Initial draft loads');
  ui.nav('comparisons'); await settle();
  ui.find('#calculation-table-body button', '打开快照').click();
  ui.nav('workbench');
  const power = ui.doc.querySelector('#parameter-fields input[type="number"]');
  assert.ok(power); power.value = '15'; power.dispatchEvent(new ui.w.Event('input', { bubbles: true }));
  pending.release(snapshot('a', '迟到的记录')); await settle();
  assert.equal(ui.doc.querySelector('#current-design').textContent, '当前草稿');
  assert.equal(JSON.parse(ui.doc.querySelector('#raw-parameters').textContent).kW, 15);
});

test('out-of-order project refreshes preserve the latest archive filter', async t => {
  const pending = deferred();
  const ui = await app(t, (path, request) => {
    if (path === '/projects') return request.url.searchParams.get('include_archived') === 'true'
      ? { items: [{ id: 'archived', name: '归档人工设计', revision: 1, archived: 1, updated_at: created }] }
      : pending.promise;
  });
  ui.nav('projects');
  const include = ui.doc.querySelector('#include-archived'); include.checked = true; include.dispatchEvent(new ui.w.Event('change'));
  await until(() => ui.doc.querySelector('#project-table-body').textContent.includes('归档人工设计'), 'Latest list renders');
  pending.release({ items: [] }); await settle();
  assert.equal(ui.doc.querySelectorAll('#project-table-body tr').length, 1);
  assert.equal(ui.doc.querySelector('#projects-feedback').textContent, '');
});

test('an optimization candidate reuses the saved snapshot for comparison and continued design', async t => {
  const job = { id: 'job-1', name: '人工寻优', status: 'succeeded', evaluated: 2, budget: 2, best_id: 'b',
    request: { objective: 'efficiency', variables: [{ key: 'L' }], parameters: { ...parameters, L: 100 } },
    candidates: [{ ordinal: 1, calculation_id: 'a', parameters: { L: 100 }, objective: .9, feasible: true },
      { ordinal: 2, calculation_id: 'b', parameters: { L: 110 }, objective: .91, feasible: true }] };
  let comparison;
  const ui = await app(t, (path, request) => {
    if (path === '/optimization/config') return { method: '人工流程测试', variables: [{ key: 'L', label: '长度' }],
      objectives: [{ id: 'efficiency', label: '效率' }], materials: [], constraint_metrics: [] };
    if (path === '/optimization/jobs') return { items: [job] };
    if (path === '/optimization/jobs/job-1') return job;
    if (path === '/calculations') return records();
    if (path === '/calculations/b') return { ...snapshot('b', '最佳候选'), inputs: { ...parameters, L: 110 } };
    if (path === '/comparisons') {
      comparison = request.body;
      return { baseline_id: 'a', warnings: ['人工比较提示'], schemes: ['a', 'b'].map(id => ({ id, name: id, warnings: [], provenance: {} })),
        parameter_differences: [{ label: '长度', cells: [{ id: 'a', value: 100 }, { id: 'b', value: 110 }] }],
        metrics: [{ key: 'ATA1', label: '效率', unit: '', cells: [
          { id: 'a', value: .9, delta: 0, delta_percent: 0 }, { id: 'b', value: .91, delta: .01, delta_percent: 1.111 }] }] };
    }
  });
  ui.nav('optimization');
  await until(() => ui.doc.querySelectorAll('#optimization-jobs button').length === 1, 'Task list loads');
  ui.find('#optimization-jobs button', '查看任务').click();
  await until(() => ui.doc.querySelector('#optimization-detail').textContent.includes('打开最佳候选'), 'Candidate controls load');
  ui.find('#optimization-detail button', '与基准对比').click();
  await until(() => ui.doc.querySelector('#comparison-result').textContent.includes('人工比较提示'), 'Comparison renders');
  assert.deepEqual(comparison, { calculation_ids: ['a', 'b'], baseline_id: 'a' });
  assert.match(ui.doc.querySelector('#comparison-result').textContent, /Δ 0\.01/);
  assert.match(ui.doc.querySelector('#comparison-result').textContent, /长度/);
  assert.equal(ui.doc.querySelectorAll('#comparison-result a[href$="/report.pdf"]').length, 2);
  ui.nav('optimization'); await settle();
  ui.find('#optimization-detail button', '打开最佳候选').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '最佳候选', 'Candidate loads in workbench');
  assert.equal(JSON.parse(ui.doc.querySelector('#raw-parameters').textContent).L, 110);
  ui.doc.querySelector('#save-design').click();
  assert.equal(ui.doc.querySelector('#save-as-new').checked, true, 'Candidate becomes a new design when saved');
});

test('quick modeling applies only the current validated preview and ignores stale responses', async t => {
  const pending = deferred(); let previewCalls = 0;
  const base = { ...parameters, L: 100 };
  const preview = length => ({ name: '模板修改方案', parameters: { ...base, L: length }, source: '人工模板',
    geometry: { svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', warnings: [] }, changes: [], warnings: [] });
  const ui = await app(t, (path, request) => {
    if (path === '/modeling/templates') return { warnings: [], method: '人工模板', items: [{ id: 'model-1', model: '人工模板' }] };
    if (path === '/models/model-1') return { id: 'model-1', model: '人工模板', parameters: base };
    if (path === '/modeling/preview') return ++previewCalls === 1 ? pending.promise : preview(request.body.changes.L);
  });
  ui.nav('modeling');
  ui.doc.querySelector('#template-search').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await until(() => ui.doc.querySelectorAll('#template-list button').length === 1, 'Template is listed');
  ui.find('#template-list button', '选用模板').click();
  await until(() => ui.doc.querySelector('#quick-fields input[data-key="L"]'), 'Template editor opens');
  const length = ui.doc.querySelector('#quick-fields input[data-key="L"]');
  const submit = () => ui.doc.querySelector('#quick-model-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  length.value = '110'; length.dispatchEvent(new ui.w.Event('input')); submit();
  length.value = '120'; length.dispatchEvent(new ui.w.Event('input'));
  pending.release(preview(110)); await settle();
  assert.equal(ui.doc.querySelector('#apply-quick-model').disabled, true, 'An outdated preview cannot be applied');
  submit();
  await until(() => !ui.doc.querySelector('#apply-quick-model').disabled, 'Latest preview validates');
  ui.doc.querySelector('#apply-quick-model').click();
  assert.equal(ui.doc.querySelector('#current-design').textContent, '模板修改方案');
  assert.equal(JSON.parse(ui.doc.querySelector('#raw-parameters').textContent).L, 120);
  assert.equal(ui.doc.querySelector('#result-badge').textContent, '等待计算');
});

test('rotor diameter linkage updates the gap, invalidates stale previews and can return to direct input', async t => {
  const base = { ...parameters, Di1: 180, Di2: 50, D1: 300, g: .6, L: 160 };
  const pending = deferred(); let count = 0;
  const preview = body => ({ name: '联动方案', parameters: { ...base, ...body.changes,
    g: body.rotor_outer_mm === undefined ? body.changes.g : (body.changes.Di1 - body.rotor_outer_mm) / 2 },
    source: '人工模板', geometry: { svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', warnings: [] }, changes: [], warnings: [],
    linkages: body.rotor_outer_mm === undefined ? [] : [{ target: 'g', formula: 'g = (Di1 - D2) / 2',
      inputs: { Di1: body.changes.Di1, D2: body.rotor_outer_mm }, value: 1.8, unit: 'mm' }] });
  const ui = await app(t, (path, request) => {
    if (path === '/modeling/templates') return { warnings: [], items: [{ id: 'm', model: '人工模板' }] };
    if (path === '/models/m') return { id: 'm', parameters: base };
    if (path === '/modeling/preview') return ++count === 1 ? pending.promise : preview(request.body);
  });
  ui.nav('modeling');
  ui.doc.querySelector('#template-search').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await until(() => ui.doc.querySelector('#template-list button'), 'Template is listed');
  ui.find('#template-list button', '选用模板').click();
  await until(() => ui.doc.querySelector('#quick-fields input[data-key="g"]'), 'Editor opens');
  const mode = ui.doc.querySelector('#quick-gap-mode'), rotor = ui.doc.querySelector('#quick-rotor-diameter');
  const gap = ui.doc.querySelector('#quick-fields input[data-key="g"]');
  const bore = ui.doc.querySelector('#quick-fields input[data-key="Di1"]');
  const submit = () => ui.doc.querySelector('#quick-model-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  const changeMode = value => { mode.value = value; mode.dispatchEvent(new ui.w.Event('change')); };
  assert.equal(gap.readOnly, false); assert.equal(rotor.disabled, true);
  changeMode('rotor');
  assert.equal(gap.readOnly, true); assert.equal(rotor.value, '178.8');
  rotor.value = '178.4'; rotor.dispatchEvent(new ui.w.Event('input'));
  assert.ok(Math.abs(Number(gap.value) - .8) < 1e-10);
  bore.value = '182'; bore.dispatchEvent(new ui.w.Event('input'));
  assert.ok(Math.abs(Number(gap.value) - 1.8) < 1e-10);
  submit();
  const sent = ui.calls.find(call => call.url.pathname.endsWith('/modeling/preview')).body;
  assert.equal(sent.rotor_outer_mm, 178.4);
  assert.equal(Object.hasOwn(sent.changes, 'g'), false, 'Server receives one source of truth for the gap');
  changeMode('gap');
  pending.release(preview(sent)); await settle();
  assert.equal(ui.doc.querySelector('#apply-quick-model').disabled, true, 'Switching input mode invalidates pending preview');
  assert.equal(gap.readOnly, false); assert.equal(rotor.disabled, true);
  gap.value = '1.8'; gap.dispatchEvent(new ui.w.Event('input')); submit();
  await until(() => !ui.doc.querySelector('#apply-quick-model').disabled, 'Direct mode validates');
  const direct = ui.calls.filter(call => call.url.pathname.endsWith('/modeling/preview')).at(-1).body;
  assert.equal(Object.hasOwn(direct, 'rotor_outer_mm'), false); assert.equal(direct.changes.g, 1.8);
  changeMode('rotor');
  assert.equal(ui.doc.querySelector('#apply-quick-model').disabled, true);
  assert.match(ui.doc.querySelector('#geometry-preview').textContent, /参数已更改/);
  rotor.value = '182'; rotor.dispatchEvent(new ui.w.Event('input'));
  assert.equal(rotor.validity.valid, false, 'A zero gap cannot be submitted');
  rotor.value = '178.4'; rotor.dispatchEvent(new ui.w.Event('input'));
  assert.equal(rotor.validity.valid, true); submit();
  await until(() => !ui.doc.querySelector('#apply-quick-model').disabled, 'Linked mode validates');
  assert.match(ui.doc.querySelector('#geometry-preview').textContent, /g = \(Di1 - D2\) \/ 2/);
  ui.doc.querySelector('#apply-quick-model').click();
  assert.ok(Math.abs(JSON.parse(ui.doc.querySelector('#raw-parameters').textContent).g - 1.8) < 1e-10);
});

test('saving a comparison freezes the displayed selection and reopens after a fresh page load', async t => {
  let saved;
  const result = { baseline_id: 'b', warnings: [], metrics: [], parameter_differences: [],
    schemes: ['a', 'b'].map(id => ({ id, name: id, warnings: [], provenance: {} })) };
  const route = (path, request) => {
    if (path === '/calculations') return records();
    if (path === '/comparisons') {
      assert.deepEqual(request.body, { calculation_ids: ['a', 'b'], baseline_id: 'b' });
      return result;
    }
    if (path === '/comparison-sets' && request.method === 'POST') {
      assert.deepEqual(request.body, { name: '评审批次', notes: '保留评审说明', calculation_ids: ['a', 'b'], baseline_id: 'b' });
      saved = { ...result, id: 'review-1', name: request.body.name, notes: request.body.notes, created_at: created };
      return saved;
    }
    if (path === '/comparison-sets') return { items: saved ? [{ ...saved, scheme_count: 2 }] : [], total: saved ? 1 : 0 };
    if (path === '/comparison-sets/review-1') return saved;
  };
  const ui = await app(t, route);
  ui.nav('comparisons');
  await until(() => ui.doc.querySelectorAll('#calculation-table-body tr').length === 2, 'Calculations load');
  const select = [...ui.doc.querySelectorAll('#calculation-table-body input[type="checkbox"]')];
  select.forEach(input => input.click());
  ui.doc.querySelector('#comparison-baseline').value = 'b';
  ui.doc.querySelector('#run-comparison').click();
  await until(() => ui.doc.querySelector('.comparison-save-form'), 'Comparison can be saved');
  // Changing the picker after comparison must not silently change what gets saved.
  select[0].click();
  ui.doc.querySelector('[name="comparison-name"]').value = '评审批次';
  ui.doc.querySelector('[name="comparison-notes"]').value = '保留评审说明';
  ui.doc.querySelector('.comparison-save-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await until(() => ui.doc.querySelector('#comparison-result a[href="/comparison-reports/review-1"]'), 'Saved report links appear');
  assert.match(ui.doc.querySelector('#comparison-result').textContent, /保留评审说明/);
  const reopened = await app(t, route);
  reopened.nav('comparisons');
  await until(() => reopened.doc.querySelectorAll('#saved-comparisons-body tr').length === 1, 'Saved review survives a fresh page');
  reopened.find('#saved-comparisons-body button', '打开对比').click();
  await until(() => reopened.doc.querySelector('#comparison-result h2')?.textContent === '评审批次', 'Saved review reopens');
  assert.equal(reopened.doc.querySelector('#comparison-baseline').value, 'b');
  assert.equal(reopened.doc.querySelector('#comparison-count').textContent, '已选 2 / 8');
  assert.ok(reopened.doc.querySelector('#comparison-result a[href="/api/v1/comparison-sets/review-1/report.pdf"]'));
});
