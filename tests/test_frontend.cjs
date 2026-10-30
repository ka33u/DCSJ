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

test('slot search exposes tolerance keys, stored areas and infeasible diagnostics',async t=>{
  const geometry={passed:true,slots:[{side:'stator',area_mm2:111.78,sector_clearance_mm:3.5,radial_reserve_mm:12.05},
    {side:'rotor',area_mm2:87.704,sector_clearance_mm:4.85,radial_reserve_mm:12.45}]};
  const job={id:'slot-search',name:'槽尺寸检查',status:'succeeded',evaluated:2,budget:2,best_id:null,message:'未找到可行方案',
    request:{parameters:{br1:6.5},variables:[{key:'br1',minimum:6,maximum:50}],constraints:[{metric:'Btru',maximum:16000}],objective:'efficiency',seed:29},
    candidates:[{ordinal:1,parameters:{br1:6.5},objective:.88,feasible:false,violations:['Btru 超限'],violation_score:.01,
      error:null,calculation_id:'nearest',constraint_values:{Btru:16160},slot_geometry:geometry},
      {ordinal:2,parameters:{br1:50},objective:null,feasible:false,violations:['几何无效'],error:'转子相邻槽相交',calculation_id:null}]};
  const ui=await app(t,(path)=>{
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'},{key:'br1',label:'转子槽上宽 mm'}],
      objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:['Btru'],constraint_labels:{Btru:'转子上齿磁密 G'},
      tolerances:{keys:['L','g','br1']},slot_geometry:{note:'每个候选校核几何余量。'}};
    if(path==='/optimization/jobs')return {items:[job]};
    if(path==='/optimization/jobs/slot-search')return job;
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-jobs button'),'Slot task available');
  assert.ok([...ui.doc.querySelectorAll('#optimization-tolerance-rows select option')].some(o=>o.value==='br1'));
  ui.find('#optimization-jobs button','查看任务').click();
  await until(()=>ui.doc.querySelector('.slot-search-geometry'),'Geometry rendered');
  const text=ui.doc.querySelector('.slot-search-geometry').textContent;
  assert.match(text,/111\.78/);assert.match(text,/87\.704/);assert.match(text,/径向余量/);
  assert.match(ui.doc.querySelector('.optimization-nearest').textContent,/仍未达标：Btru 超限/);
  assert.match(ui.doc.querySelector('#optimization-detail').textContent,/转子相邻槽相交/);
  assert.ok(![...ui.doc.querySelectorAll('#optimization-detail button')].some(b=>b.textContent==='打开最佳候选'));
});

function deferred() {
  let release;
  const promise = new Promise(resolve => { release = resolve; });
  return { promise, release };
}

for(const method of ['coordinated_pair_poll_v1','balanced_quadratic_infill_v1','separate_quota_infill_v1'])test(`${method} explains required inputs without changing the draft`,async t=>{
  const note='六变量指定规格，基准在清单内，最多 4096 个组合，不设置公差。';
  const ui=await app(t,path=>{
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'},{key:'ZZ',label:'导体数'},{key:'g',label:'气隙'}],
      objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:[],
      algorithms:[{id:'stratified_v1',label:'分层采样'},{id:method,label:'可选试验搜索'}],[method==='separate_quota_infill_v1'?'separate_quota_search':method==='balanced_quadratic_infill_v1'?'balanced_search':'coordinated_search']:{note}};
    if(path==='/optimization/jobs')return {items:[]};
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-algorithm-note'),'Method note available');
  const select=ui.doc.querySelector('#optimization-algorithm'),message=ui.doc.querySelector('#optimization-algorithm-note');
  assert.equal(select.value,'stratified_v1');assert.ok(message.hidden&&message.classList.contains('hidden'));
  const inputs=()=>[...ui.doc.querySelectorAll('#optimization-variables input,#optimization-variables select')].map(e=>e.value);
  const before=inputs();select.value=method;select.dispatchEvent(new ui.w.Event('change'));
  assert.equal(message.textContent,note);assert.equal(message.hidden,false);assert.ok(!message.classList.contains('hidden'));
  assert.deepEqual(inputs(),before);assert.equal(ui.doc.querySelector('#optimization-tolerance-enabled').checked,false);
  select.value='stratified_v1';select.dispatchEvent(new ui.w.Event('change'));
  assert.equal(message.textContent,'');assert.ok(message.hidden&&message.classList.contains('hidden'));
});

for(const full of [true,false])test(`constraint diagnosis distinguishes ${full?'complete finite grid':'observed points only'}`,async t=>{
  const diagnosis={status:'constraint_conflict',complete_grid:full,scope:full?'declared_finite_grid':'observed_points',
    grid_points:3,resolved_points:full?3:2,unresolved_points:full?0:1,conflict_count:1,
    conflicts:[{constraints:[{metric:'ATA1',minimum:.9},{metric:'PF1',minimum:.8}],
      witnesses:[{metric:'ATA1',nearest_value:.85,other_bounds_satisfied:1,ordinal:2,calculation_id:'test-result'},
        {metric:'PF1',nearest_value:.7,other_bounds_satisfied:1,ordinal:1,calculation_id:'test-result'}]}]};
  const job={id:'diagnosis',name:'人工冲突',status:'succeeded',evaluated:2,budget:2,best_id:null,message:'',
    request:{parameters:{L:160},variables:[{key:'L',values:[150,160,170]}],constraints:[],objective:'efficiency'},
    candidates:[],constraint_diagnosis:diagnosis};
  const ui=await app(t,path=>{
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'}],
      objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:[]};
    if(path==='/optimization/jobs')return {items:[job]};
    if(path==='/optimization/jobs/diagnosis')return job;
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-jobs button'),'Task available');
  ui.find('#optimization-jobs button','查看任务').click();
  await until(()=>ui.doc.querySelector('.constraint-diagnosis'),'Diagnosis available');
  const box=ui.doc.querySelector('.constraint-diagnosis');
  assert.match(box.textContent,/ATA1 ≥ 90%/);assert.match(box.textContent,/PF1 ≥ 0\.8/);
  assert.match(box.textContent,/最接近要求的值为 85%/);
  assert.match(box.textContent,/最接近要求的值为 0\.7/);
  assert.equal(box.querySelectorAll('button').length,2);
  if(full){assert.match(box.textContent,/本次指定离散组合中同时满足/);assert.match(box.textContent,/清单以外/);}
  else{assert.match(box.textContent,/未覆盖的组合仍可能可行/);assert.doesNotMatch(box.textContent,/无法在本次指定离散组合/);}
  assert.ok(!box.querySelector('input'),'No automatic bound edits');
});

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

test('series selector keeps custom input during refresh and retains unknown saved series',async t=>{
  const dom=new JSDOM('<div id="series"></div>',{runScripts:'outside-only'}),w=dom.window,d=w.document;t.after(()=>w.close());
  w.eval(readFileSync(resolve(root,'frontend/series-picker.js'),'utf8'));
  const picker=w.SeriesPicker.mount(d.querySelector('#series'));picker.options(['YE3','YE4']);picker.set('历史自定义');
  assert.equal(picker.value(),'历史自定义');
  const select=d.querySelector('select'),input=d.querySelector('input');select.value='new';select.dispatchEvent(new w.Event('change'));
  assert.equal(input.hidden,false);assert.throws(()=>picker.value(),/新系列名/);
  input.value='  试制系列  ';picker.options(['YE3','YE5']);assert.equal(select.value,'new');assert.equal(picker.value(),'试制系列');
  picker.disable(true);assert.equal(select.disabled,true);assert.equal(input.disabled,true);picker.disable(false);
  picker.set('');assert.equal(picker.value(),'');assert.equal(input.required,false);assert.equal(input.hidden,true);
  assert.throws(()=>picker.set({series:'bad'}),/系列名/);
});

test('login handles errors and duplicate submission and rejects external return URLs',async t=>{
  const {runInNewContext}=require('node:vm');
  const dom=new JSDOM(readFileSync(resolve(root,'frontend/login.html'),'utf8'),{url:'https://dcsj.invalid/login'});
  const doc=dom.window.document;t.after(()=>dom.window.close());
  const pending=deferred(),calls=[],destinations=[];let fail=true;
  const location={origin:'https://dcsj.invalid',search:'?next=https://other.invalid/',replace:path=>destinations.push(path)};
  runInNewContext(readFileSync(resolve(root,'frontend/login.js'),'utf8'),{document:doc,location,URL,URLSearchParams,
    fetch:async(path,options)=>{calls.push({path,options});await pending.promise;return {ok:!fail,json:async()=>fail?{detail:'密码错误'}:{username:'张工'}};}});
  doc.querySelector('#username').value='  张工  ';doc.querySelector('#password').value='1234';
  const submit=()=>doc.querySelector('form').dispatchEvent(new dom.window.Event('submit',{cancelable:true}));
  submit();submit();assert.equal(calls.length,1);assert.equal(doc.querySelector('button').disabled,true);
  pending.release();await settle();assert.match(doc.querySelector('#login-error').textContent,/密码错误/);
  assert.equal(doc.querySelector('button').disabled,false);fail=false;submit();await settle();
  assert.deepEqual(destinations,['/']);assert.equal(calls[0].options.headers['X-DCSJ-Request'],'1');
  assert.equal(JSON.parse(calls[0].options.body).username,'张工');
});

test('shared session locks author, guards API writes and preserves drafts on expiry',async t=>{
  const dom=new JSDOM('<div data-account></div><input id="design-author"><input id="draft" value="未保存的参数">',{url:'https://dcsj.invalid/tx',runScripts:'outside-only'});
  const w=dom.window,doc=w.document;t.after(()=>w.close());w.Headers=Headers;w.Request=Request;
  const calls=[];w.fetch=async(input,options={})=>{
    calls.push({input,options});return input==='/api/v1/auth/me'?{ok:true,status:200,json:async()=>({username:'张工'})}:{ok:false,status:401};
  };
  w.eval(readFileSync(resolve(root,'frontend/auth.js'),'utf8'));await w.DCSJAuth.ready;
  assert.equal(doc.querySelector('#design-author').value,'张工');assert.equal(doc.querySelector('#design-author').readOnly,true);
  assert.match(doc.querySelector('[data-account]').textContent,/张工/);
  await w.fetch('/api/v1/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  assert.equal(calls.at(-1).options.headers.get('X-DCSJ-Request'),'1');
  assert.equal(calls.at(-1).options.headers.get('Content-Type'),'application/json');
  assert.equal(doc.querySelector('#draft').value,'未保存的参数');
  assert.equal(doc.querySelector('#auth-expired a').target,'_blank');
  await w.fetch('/api/v1/projects');assert.equal(doc.querySelectorAll('#auth-expired').length,1);
  w.DCSJAuth.beforeLogout=()=>false;const before=calls.length;doc.querySelector('button').click();await settle();assert.equal(calls.length,before);
  await w.fetch('https://other.invalid/api/test',{method:'POST'});assert.equal(calls.at(-1).options.headers,undefined);
});

async function app(t, route, extraFields = []) {
  const errors = [], calls = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, { url: 'https://dcsj.invalid/', runScripts: 'outside-only', virtualConsole });
  const w = dom.window, doc = w.document;
  w.Headers=Headers;w.Request=Request;
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
    if (path === '/auth/me') data = {username:'张工'};
    else if (path === '/status') data = { name: 'DCSJ synthetic test', version: 'test', catalog: {},
      engine: { name: '人工测试', limitations: [] }, reports: { pdf: { available: true } } };
    else if (path === '/schema') data = { retired_input_keys: ['ATA_req', 'PF_req', 'IST_req', 'TST_req', 'TM_req'], groups: [{ id: 'basic', label: '基本参数' }], fields: [
      { key: 'SizeX', label: '设计名称', group: 'basic', type: 'text', role: 'input' },
      { key: 'kW', label: '额定功率', group: 'basic', type: 'number', role: 'input' }, ...extraFields] };
    else if (path === '/series') data = { items: ['人工系列'] };
    else data = await route(path, request);
    if (data === undefined && path === '/comparison-sets' && request.method === 'GET') data = { items: [], total: 0 };
    assert.notEqual(data, undefined, `Unexpected API request: ${path}`);
    return { ok: true, status: 200, json: async()=>data, text: async () => JSON.stringify(data) };
  };
  w.eval(readFileSync(resolve(root,'frontend/auth.js'),'utf8'));
  w.eval(readFileSync(resolve(root,'frontend/series-picker.js'),'utf8'));
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

test('slot picker previews native changes, rejects stale apply and preserves reference-only semantics', async t => {
  const dom = new JSDOM('<div id="host"></div>', {url:'http://dcsj.invalid/',runScripts:'outside-only'});
  const w=dom.window,doc=w.document;t.after(()=>w.close());
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  let p={Di1:100,g:.5,hr0:1},bindings={},applied=0;
  const entry={name:'Synthetic slot',reference:'user-slot:synthetic@1',document:{drawing:'SYN-R',parameters:{hr0:1},applications:[]}};
  w.fetch=async(url,options={})=>{
    const path=new URL(url,w.location.href).pathname,body=options.body?JSON.parse(options.body):null;
    let data;
    if(path==='/api/v1/slots')data={total:1,items:[entry]};
    else if(path==='/api/v1/slots/apply')data={parameters:{...body.parameters,hr0:1},binding:{rotor:entry.reference},changes:[],geometry_check:{checks:[]}};
    else if(path==='/api/v1/slots/preview')data={entries:[],geometry_check:{checks:[],geometry:{rotor_outer_mm:99},hr0_reference_available:false}};
    else throw new Error(path);
    return {ok:true,json:async()=>data};
  };
  w.eval(readFileSync(resolve(root,'frontend/slot-picker.js'),'utf8'));
  const picker=w.SlotPicker.mount(doc.querySelector('#host'),{module:'main',read:()=>p,bindings:()=>bindings,busy:()=>false,
    apply:(params,b)=>{p=params;bindings={...bindings,...b};applied++;},detach:side=>delete bindings[side]});
  const click=text=>{const b=[...doc.querySelectorAll('button')].find(e=>e.textContent===text);assert.ok(b,text);b.click();};
  click('选择转子槽型');await settle();click('Synthetic slot · SYN-R · 含尺寸');click('预览带入内容');await settle();
  p={...p,g:.6};click('确认带入当前设计');assert.equal(applied,0);assert.match(doc.body.textContent,/当前参数已变化/);
  click('预览带入内容');await settle();click('确认带入当前设计');assert.equal(applied,1);assert.equal(bindings.rotor,entry.reference);
  await picker.refresh();assert.match(doc.body.textContent,/尚无 hr0/);
  click('解除槽型关联');assert.deepEqual(bindings,{});
});

test('slot catalog creates a new user entry and round-trips dimensions without overwriting resources', async t => {
  const dom=new JSDOM(readFileSync(resolve(root,'frontend/slots.html'),'utf8'),{url:'http://dcsj.invalid/slots',runScripts:'outside-only'});
  const w=dom.window,doc=w.document;t.after(()=>w.close());w.confirm=()=>true;
  w.DCSJAuth={ready:Promise.resolve({username:'张工'}),username:()=> '张工'};
  const calls=[];let saved;
  const schema={fields:{main:{stator:[{key:'bs1',label:'槽宽',unit:'mm'}],rotor:[]},tx:{stator:[],rotor:[]}}};
  w.fetch=async(url,options={})=>{const path=new URL(url,w.location.href).pathname;const body=options.body?JSON.parse(options.body):undefined;calls.push({path,method:options.method||'GET',body});let data;
    if(path==='/api/v1/slots/schema')data=schema;
    else if(path==='/api/v1/slots')data={items:saved?[saved]:[],total:saved?1:0};
    else if(path==='/api/v1/library/slots'){saved={...body,id:'synthetic',reference:'user-slot:synthetic@1',revision:1,origin:'user',archived:false};data=saved;}
    else throw new Error(path);
    return {ok:true,json:async()=>data};};
  w.eval(readFileSync(resolve(root,'frontend/slot-picker.js'),'utf8'));w.eval(readFileSync(resolve(root,'frontend/slots.js'),'utf8'));await settle();
  doc.querySelector('#new-slot').click();doc.querySelector('#slot-name').value='Synthetic';doc.querySelector('[data-dimension="bs1"]').value='7.5';
  doc.querySelector('#slot-editor').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  const request=calls.find(c=>c.method==='POST');assert.equal(request.path,'/api/v1/library/slots');assert.equal(request.body.document.parameters.bs1,7.5);assert.equal(request.body.expected_revision,undefined);
  assert.match(doc.querySelector('#feedback').textContent,/已保存/);assert.equal(doc.querySelector('#next').disabled,true);
  assert.equal(doc.querySelector('#save').textContent,'保存新版本');
});

test('versioned plots use reverse x and separate y scales while raw tables and old snapshots stay intact', async t => {
  const value = snapshot('a');
  const raw = [{ name: 'raw I', points: [{ x: .02, y: .4 }, { x: 1, y: 5 }] }];
  value.curves = [{ title: '双刻度人工曲线', x_label: 's', y_label: '倍数', series: raw,
    plot: { x_min: 0, x_max: 1, x_reverse: true, aspect_ratio: 1, x_ticks: 10, y_ticks: 10,
      y_axes: [{ id: 'i', label: 'I', min: 0, max: 10 }, { id: 't', label: 'T', min: 0, max: 5 }],
      series: [{ name: 'I', axis: 'i', color: '#ff0000', points: [{ x: 1, y: 5 }, { x: 0, y: 0 }] },
               { name: 'T', axis: 't', color: '#000000', points: [{ x: 1, y: 2.5 }, { x: 0, y: 0 }] }],
      note: '人工显示端点，不是计算值' } },
    { title: '历史单轴', x_label: 's', series: raw }, { title: '兼容API分图', display: false, series: raw }];
  const ui = await app(t, path => path === '/calculations' ? records() : path === '/calculations/a' ? value : undefined);
  ui.nav('comparisons'); await settle(); ui.find('#calculation-table-body button', '打开快照').click();
  await until(() => ui.doc.querySelectorAll('#result-content .chart-card').length === 2, 'Visible curves render');
  const chart = ui.doc.querySelector('#result-content .chart-card');
  const paths = [...chart.querySelectorAll('path[data-series]')];
  assert.equal(paths[0].getAttribute('d'), paths[1].getAttribute('d'), 'Half-scale values coincide');
  const xLabels = [...chart.querySelectorAll('text[data-axis=x]')].map(n => n.textContent);
  assert.equal(xLabels[0], '1'); assert.equal(xLabels.at(-1), '0');
  const box = chart.querySelector('clipPath rect');
  assert.equal(box.getAttribute('width'), box.getAttribute('height'), 'Legacy square plot is preserved');
  const table = chart.querySelector('.chart-data');
  assert.equal(table.querySelectorAll('tbody tr').length, 2);
  assert.ok(table.textContent.includes('0.02') && table.textContent.includes('0.4'));
  assert.ok(chart.textContent.includes('不是计算值'));
});

test('load chart distinguishes percent efficiency from ratio power factor without changing data or shape', async t => {
  const value = snapshot('a');
  const curve = { id: 'load_characteristic', title: '人工负荷图', series: [
    { name: '功率因数 cosφ', points: [{ x: 1, y: .75 }] },
    { name: '效率 η', points: [{ x: 1, y: .82 }] }],
    plot: { x_min: 20, x_max: 150,
      y_axes: [{ id: 'pf', label: '功率因数 cosφ', min: 0, max: 1 }, { id: 'efficiency', label: '效率 η', min: 0, max: 1 }],
      series: [{ name: '功率因数 cosφ', axis: 'pf', points: [{ x: 100, y: .75 }] },
               { name: '效率 η', axis: 'efficiency', points: [{ x: 100, y: .82 }] }] } };
  value.curves = [curve, { ...structuredClone(curve), id: 'original_ratio_display', title: '原始比值刻度' }];
  const original = JSON.stringify(value);
  const ui = await app(t, path => path === '/calculations' ? records() : path === '/calculations/a' ? value : undefined);
  ui.nav('comparisons'); await settle(); ui.find('#calculation-table-body button', '打开快照').click();
  await until(() => ui.doc.querySelectorAll('#result-content .chart-card').length === 2, 'Charts loaded');
  const [chart, control] = ui.doc.querySelectorAll('#result-content .chart-card');
  const labels = axis => [...chart.querySelectorAll(`text[data-axis=${axis}]`)].map(n => n.textContent);
  assert.equal(labels('pf')[0], '1'); assert.equal(labels('efficiency')[0], '100');
  assert.equal(labels('efficiency').at(-1), '0');
  assert.ok(chart.textContent.includes('η (%)'));
  assert.ok(chart.querySelector('.chart-data').textContent.includes('均为比值'));
  assert.deepEqual([...chart.querySelectorAll('tbody tr')].map(n => n.textContent), ['功率因数 cosφ10.75', '效率 η10.82']);
  const paths = el => [...el.querySelectorAll('path[data-series]')].map(n => n.getAttribute('d'));
  assert.deepEqual(paths(chart), paths(control), 'Unit conversion must preserve the two distinct curves');
  assert.notEqual(paths(chart)[0], paths(chart)[1]);
  assert.equal(JSON.stringify(value), original, 'Saved API response stays unchanged');
});

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
  for (const id of ['a','b']) {
    const modern=ui.doc.querySelector(`#comparison-result a[href="/reports/${id}"]`);
    const legacy=ui.doc.querySelector(`#comparison-result a[href="/reports/${id}?layout=legacy"]`);
    assert.equal(modern.textContent,'新版打印 ↗');
    assert.equal(legacy.textContent,'旧版打印 ↗');
    assert.equal(legacy.target,'_blank');
  }
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

function resourceStatus() {
  return { active: null, installed: [], remote_configured: true, repository: 'synthetic/private', ref: 'main', user_data_policy: 'Test independent data' };
}
function userEntry(kind, changes = {}) {
  return { id: 'ul_test', kind, name: '同名人工记录', revision: 2, archived: 0, reference: 'user-test@2',
    document: kind === 'models' ? { parameters, source: '人工来源', notes: '' } : { source: '人工来源', notes: '', frequency_hz: 50, h_unit: 'A/m', loss_unit: 'W/kg', bh: [[.5, 10], [1.88, 200]], loss: [[.5, 1], [1.88, 3]] },
    compatibility: kind === 'materials' ? { compatible: false, reasons: ['损耗单位未映射'] } : undefined, ...changes };
}

test('one-click resource import sends active-version guard and preserves an open draft', async t => {
  const data = resourceStatus(), pending = deferred();
  const ui = await app(t, (path, request) => {
    if (path === '/example') return { name: '当前草稿', parameters };
    if (path === '/resources') return data;
    if (path.startsWith('/library/')) return { items: [], total: 0 };
    if (path === '/resources/import') {
      assert.deepEqual(request.body, { expected_package_id: null });
      return pending.promise;
    }
  });
  ui.doc.querySelector('#load-example').click(); await settle();
  ui.doc.querySelector('[data-key="kW"]').value = '15';
  ui.doc.querySelector('[data-key="kW"]').dispatchEvent(new ui.w.Event('input', { bubbles: true }));
  ui.nav('resources'); await settle();
  ui.doc.querySelector('#import-resources').click();
  assert.equal(ui.doc.querySelector('#import-resources').disabled, true);
  data.active = { package_id: 'a'.repeat(64), version: 'synthetic.1', counts: { model_versions: 0, materials: 0 } }; data.installed.push(data.active);
  pending.release({ ...data.active, changed: true });
  await until(() => ui.doc.querySelector('#resource-feedback').textContent.includes('已启用'), 'Import completes');
  assert.equal(JSON.parse(ui.doc.querySelector('#raw-parameters').textContent).kW, 15);
  assert.equal(ui.doc.querySelector('#current-design').textContent, '当前草稿');
});

test('editing a manual model sends its revision and retains duplicate-name identities', async t => {
  const first = userEntry('models'), second = userEntry('models', { id: 'ul_second', reference: 'user-second@2' });
  const ui = await app(t, (path, request) => {
    if (path === '/resources') return resourceStatus();
    if (path === '/library/models') return { items: [first, second], total: 2 };
    if (path === '/library/models/ul_test' && request.method === 'PUT') {
      assert.equal(request.body.expected_revision, 2); assert.equal(request.body.document.parameters.kW, 18);
      return { ...first, revision: 3, document: request.body.document };
    }
  });
  ui.nav('resources'); await settle();
  assert.equal(ui.doc.querySelectorAll('#library-list article').length, 2);
  ui.find('#library-list button', '编辑').click();
  assert.equal(ui.doc.querySelector('#entry-kind').disabled, true);
  ui.doc.querySelector('#entry-parameters').value = JSON.stringify({ ...parameters, kW: 18 });
  ui.doc.querySelector('#entry-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await until(() => ui.doc.querySelector('#entry-feedback').textContent.includes('v3'), 'New revision saved');
  assert.equal(ui.calls.filter(call => call.method === 'POST').length, 0, 'Editing is not mistaken for creating');
});

test('material form keeps explicit units and incompatible materials have no compute action', async t => {
  let saved;
  const ui = await app(t, (path, request) => {
    if (path === '/resources') return resourceStatus();
    if (path === '/library/models') return { items: [], total: 0 };
    if (path === '/library/materials') {
      if (request.method === 'POST') { saved = userEntry('materials', { ...request.body, revision: 1 }); return saved; }
      return { items: saved ? [saved] : [], total: saved ? 1 : 0 };
    }
  });
  ui.nav('resources'); await settle();
  const select = ui.doc.querySelector('#entry-kind'); select.value = 'materials'; select.dispatchEvent(new ui.w.Event('change'));
  ui.doc.querySelector('#entry-name').value = '手工曲线';
  ui.doc.querySelector('#entry-bh').value = '0.5, 10\n1.88, 200';
  ui.doc.querySelector('#entry-loss').value = '0.5 1\n1.88 3';
  ui.doc.querySelector('#entry-form').dispatchEvent(new ui.w.Event('submit', { cancelable: true }));
  await until(() => ui.doc.querySelector('#library-list').textContent.includes('损耗单位未映射'), 'Incompatible curve is saved for management');
  assert.equal(saved.document.loss_unit, 'W/kg'); assert.equal(saved.document.h_unit, 'A/m');
  assert.deepEqual(saved.document.bh, [[.5, 10], [1.88, 200]]);
  assert.equal([...ui.doc.querySelectorAll('#library-list button')].some(x => x.textContent === '用于当前设计'), false);
  assert.ok(ui.find('#library-list button', '查看曲线'));
});

test('catalog lineage follows calculation and save, then resets on unrelated design', async t => {
  const ui = await app(t, (path, request) => {
    if (path === '/models') return { total: 1, items: [{ id: 'base-1', model: '来源型号', source: 'test' }] };
    if (path === '/models/base-1') return { id: 'base-1', model: '来源型号', parameters, source: 'test' };
    if (path === '/calculate') return snapshot('calc');
    if (path === '/projects' && request.method === 'POST') return { id: 'p', name: request.body.name, revision: 1, lineage: { id: request.body.base_model_id } };
    if (path === '/example') return { name: '独立示例', parameters };
  });
  ui.nav('catalog');
  await until(() => ui.doc.querySelectorAll('#model-table-body button').length, 'Catalog loaded');
  ui.find('#model-table-body button', '载入设计 →').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '来源型号', 'Source model loaded');
  ui.doc.querySelector('#calculate-button').click();
  await until(() => ui.calls.some(c => c.url.pathname.endsWith('/calculate')), 'Calculation sent');
  assert.equal(ui.calls.find(c => c.url.pathname.endsWith('/calculate')).body.base_model_id, 'base-1');
  await settle();
  ui.doc.querySelector('#save-design').click();
  ui.doc.querySelector('#save-form').dispatchEvent(new ui.w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => ui.calls.some(c => c.url.pathname.endsWith('/projects') && c.method === 'POST'), 'Save sent');
  assert.equal(ui.calls.find(c => c.url.pathname.endsWith('/projects') && c.method === 'POST').body.base_model_id, 'base-1');
  await settle();
  ui.doc.querySelector('#load-example').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === '独立示例', 'Example loaded');
  ui.doc.querySelector('#calculate-button').click();
  await until(() => ui.calls.filter(c => c.url.pathname.endsWith('/calculate')).length === 2, 'Second calculation sent');
  assert.equal(ui.calls.filter(c => c.url.pathname.endsWith('/calculate'))[1].body.base_model_id, null);
});

test('old comparison-only requirements do not reappear when continuing a snapshot', async t => {
  const old = snapshot('old');
  old.inputs = { ...parameters, ATA: .9, PF: .8, IST: 7, TST: 2, TM: 2.5,
    ATA_req: .99, PF_req: .95, IST_req: 5, TST_req: 4, TM_req: 5 };
  const ui = await app(t, (path) => {
    if (path === '/calculations') return { total: 1, items: [{ id: 'old', name: '旧记录', created_at: created }] };
    if (path === '/calculations/old') return old;
    if (path === '/calculate') return snapshot('new');
  });
  ui.nav('comparisons');
  await until(() => ui.doc.querySelector('#calculation-table-body button'), 'History loaded');
  ui.find('#calculation-table-body button', '打开快照').click();
  await until(() => ui.doc.querySelector('#current-design').textContent === old.name, 'Snapshot loaded');
  const inputs = JSON.parse(ui.doc.querySelector('#raw-parameters').textContent);
  assert.equal(inputs.ATA, .9);
  assert.ok(!Object.keys(inputs).some(key => key.endsWith('_req')));
  assert.ok(!ui.doc.querySelector('#parameter-tabs').textContent.includes('设计要求'));
  ui.doc.querySelector('#calculate-button').click();
  await until(() => ui.calls.some(call => call.url.pathname.endsWith('/calculate')), 'Recalculation sent');
  const sent = ui.calls.find(call => call.url.pathname.endsWith('/calculate')).body.parameters;
  assert.equal(sent.ATA, .9);
  assert.ok(!Object.keys(sent).some(key => key.endsWith('_req')));
  assert.equal(old.inputs.ATA_req, .99, 'Original snapshot remains unchanged');
});

async function txApp(t, route = () => undefined) {
  const p = {TYP:'人工 TX 设计', P2:7.2, A1:2, D3:18, FE:1};
  const fields = Object.entries({TYP:'额定参数',P2:'额定参数',A1:'导线与绕组',D3:'铁心与槽型',FE:'计算系数'})
    .map(([key,group])=>({key,label:key,group,unit:key==='D3'?'cm':'',type:key==='TYP'?'text':'number'}));
  for(let i=1;i<=6;i++)for(const prefix of ['NDXP','YDXP']){
    const key=`${prefix}(${i})`;p[key]=i<4?(prefix==='NDXP'?8:10-i):0;fields.push({key,label:key,group:'线圈组',type:'number',unit:''});
  }
  const result={metrics:{efficiency:.88,power_factor:.81,maximum_torque_ratio:2.7,starting_torque_ratio:2.1,starting_current_ratio:6.8,speed_rpm:1700},
    resource_version:'synthetic.2',harmonics:[1,5,7,11,13,17].map((order,i)=>({order,turns:i<3?8:0,pitch:i<3?9-i:0,winding_factor:.1,relative_mmf_percent:i?-.2:100})),
    coils:[{index:1,span_cm:12,radius_cm:8,end_angle_rad:.8,height_cm:6}],details:{iron_loss_w:40,stator_copper_loss_w:50,rotor_copper_loss_w:30,total_loss_w:150,current_a:10,copper_mass_kg:3,iron_mass_kg:20,mean_turn_length_mm:300},report_text:'人工输出'};
  const errors=[],calls=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e.message));
  const dom=new JSDOM(readFileSync(resolve(root,'frontend/tx.html'),'utf8'),{url:'https://dcsj.invalid/tx',runScripts:'outside-only',virtualConsole});
  const w=dom.window,doc=w.document;
  w.Headers=Headers;w.Request=Request;
  w.HTMLElement.prototype.scrollIntoView=function(){};w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};w.confirm=()=>true;
  w.fetch=async(url,options={})=>{
    const request={path:new URL(url,w.location.href).pathname.replace('/api/v1/tx',''),method:options.method||'GET',body:options.body?JSON.parse(options.body):null};calls.push(request);
    let data=request.path==='/api/v1/auth/me'?{username:'张工'}:await route(request,{p,result});
    if(data===undefined){
      if(request.path==='/series')data={items:['人工系列']};
      if(request.path==='/schema')data={fields};
      if(request.path==='/status')data={available:true,resource_version:'synthetic.2',default_id:'synthetic'};
      if(request.path==='/models')data={items:[{id:'synthetic',name:p.TYP,power_kw:p.P2,poles:4,source:{sheet:'Test',row:1},verified_execution:true}],total:1};
      if(request.path==='/models/synthetic')data={id:'synthetic',parameters:p,source:{sheet:'Test',row:1},resource_version:'synthetic.2',verified_execution:true};
      if(request.path==='/calculate')data={id:'synthetic-result',parameters:request.body.parameters,name:p.TYP,result,created_at:created};
    }
    assert.notEqual(data,undefined,request.path);return {ok:true,json:async()=>data};
  };
  t.after(()=>{w.close();assert.deepEqual(errors,[]);});w.eval(readFileSync(resolve(root,'frontend/auth.js'),'utf8'));w.eval(readFileSync(resolve(root,'frontend/series-picker.js'),'utf8'));w.eval(readFileSync(resolve(root,'frontend/tx.js'),'utf8'));
  await until(()=>doc.querySelector('#design-title').textContent===p.TYP&&!doc.querySelector('#calculate').disabled,'TX starts');
  return {w,doc,calls,p,result};
}

test('TX computes its own parameter contract and renders immutable results, harmonics and report link',async t=>{
  const pending=deferred();const a=await txApp(t,async(req,{p,result})=>{
    if(req.path==='/calculate'){await pending.promise;return {id:'synthetic-snapshot',parameters:req.body.parameters,name:p.TYP,result,created_at:created};}
  });
  a.doc.querySelector('#calculate').click();await settle();
  assert.equal(a.doc.querySelector('[name="NDXP(1)"]').disabled,true);
  a.doc.querySelector('#calculate').click();assert.equal(a.calls.filter(c=>c.path==='/calculate').length,1);
  const sent=a.calls.find(c=>c.path==='/calculate').body;
  assert.equal(sent.parameters.D3,18);assert.equal(sent.parameters['NDXP(1)'],8);assert.equal(sent.base_model_id,'synthetic');
  pending.release();await until(()=>!a.doc.querySelector('#results').hidden,'TX results');
  assert.match(a.doc.querySelector('#metric-cards').textContent,/88/);
  assert.match(a.doc.querySelector('#metric-cards').textContent,/0.81/);
  assert.equal(a.doc.querySelectorAll('#harmonic-table tbody tr').length,6);
  assert.equal(a.doc.querySelectorAll('#coil-diagram svg').length,1);
  assert.equal(a.doc.querySelector('#report-link').getAttribute('href'),'/tx/reports/synthetic-snapshot');
  assert.equal(a.doc.querySelector('#report-link').textContent,'新版打印 ↗');
  assert.equal(a.doc.querySelector('#legacy-report-link').getAttribute('href'),'/tx/reports/synthetic-snapshot?layout=legacy');
  assert.equal(a.doc.querySelector('#legacy-report-link').textContent,'旧版打印 ↗');
  const n=a.doc.querySelector('[name="NDXP(1)"]');n.value='9';n.dispatchEvent(new a.w.Event('input',{bubbles:true}));
  assert.match(a.doc.querySelector('#result-status').textContent,/参数已修改/);
  assert.equal(a.doc.querySelector('#report-link').getAttribute('href'),'/tx/reports/synthetic-snapshot');
  assert.equal(a.doc.querySelector('#legacy-report-link').getAttribute('href'),'/tx/reports/synthetic-snapshot?layout=legacy');
});

test('TX saves revisions with optimistic guards and creates a separate copy',async t=>{
  const a=await txApp(t,(req,{p})=>req.path.startsWith('/designs')?{id:'user-tx',name:p.TYP,revision:req.method==='PUT'?2:1}:undefined);
  a.doc.querySelector('#save-design').click();await until(()=>a.doc.querySelector('#revision').textContent.includes('v1'),'saved first revision');
  a.doc.querySelector('#save-design').click();await until(()=>a.doc.querySelector('#revision').textContent.includes('v2'),'saved second revision');
  const update=a.calls.find(c=>c.method==='PUT');assert.equal(update.body.expected_revision,1);assert.equal(update.path,'/designs/user-tx');
  a.doc.querySelector('#save-copy').click();await settle();
  assert.equal(a.calls.filter(c=>c.path==='/designs'&&c.method==='POST').length,2);
});

test('target fill exposes effective wire without overwriting editable design or snapshot inputs', async t => {
  const input = { ...parameters, JJ: 4, Djj: 1, MM: 0, Dmm: 0, _wire_target: .8 };
  const used = { JJ: 3, Djj: 1.25, MM: 1, Dmm: .75 };
  const result = { ...snapshot('wire'), inputs: input, values: { ATA1: .9, Sf: .775 },
    wire_selection: { mode: 'target_fill', input, used, target_fill: .8, actual_fill: .775, fill_delta: -.025 } };
  const fields = ['JJ', 'Djj', 'MM', 'Dmm', '_wire_target'].map(key => ({ key, label: key, type: 'number', role: 'input', group: 'basic' }));
  const ui = await app(t, path => path === '/example' ? { parameters: input, name: 'wire' } : path === '/calculate' ? result : undefined, fields);
  ui.doc.querySelector('#load-example').click(); await settle();
  const first = ui.doc.querySelector('input[data-key=JJ]'), target = ui.doc.querySelector('input[data-key=_wire_target]');
  assert.equal(first.value, '4'); assert.equal(first.readOnly, true);
  assert.equal(ui.doc.querySelector('#wire-mode-note').classList.contains('hidden'), false);
  assert.match(ui.doc.querySelector('#wire-mode-note').textContent, /输入暂不采用/);
  ui.doc.querySelector('#parameter-form').dispatchEvent(new ui.w.Event('submit', { bubbles: true, cancelable: true }));
  await until(() => ui.doc.querySelector('[data-wire-key=JJ]'), 'Actual wire result visible');
  assert.deepEqual([...ui.doc.querySelector('[data-wire-key=JJ]').children].map(n => n.textContent), ['第一组根数 JJ', '4', '3', '根']);
  assert.match(ui.doc.querySelector('.wire-result').textContent, /目标槽满率 80% · 实际 77.5% · 差值 -2.5 个百分点/);
  assert.equal(first.value, '4', 'Calculation does not silently overwrite inputs');
  target.value = '0'; target.dispatchEvent(new ui.w.Event('input', { bubbles: true }));
  assert.equal(first.readOnly, false);
  assert.equal(ui.doc.querySelector('#wire-mode-note').classList.contains('hidden'), true);
  assert.ok(ui.doc.querySelector('#stale-result'), 'Results remain previous snapshot after mode change');
  assert.match(ui.doc.querySelector('.wire-result').textContent, /原输入（未采用）/);
  assert.equal(result.inputs._wire_target, .8, 'Frozen snapshot stays in target-fill mode');
  const call = ui.calls.find(r => r.url.pathname === '/api/v1/calculate');
  assert.equal(call.body.parameters.JJ, 4);
  assert.equal(JSON.parse(ui.doc.querySelector('#raw-parameters').textContent)._wire_target, 0);
});

test('old automatic-wire snapshot never displays submitted wire as effective wire', async t => {
  const value = { ...snapshot('old-wire'), inputs: { ...parameters, _wire_target: .8, JJ: 4, Djj: 1, MM: 0, Dmm: 0 } };
  const ui = await app(t, path => path === '/calculations' ? { items: [{ id: 'old-wire', name: 'old-wire' }], total: 1 } : path === '/calculations/old-wire' ? value : undefined);
  ui.nav('comparisons'); await settle(); ui.find('#calculation-table-body button', '打开快照').click();
  await until(() => ui.doc.querySelector('.wire-result'), 'Old wire fallback visible');
  assert.match(ui.doc.querySelector('.wire-result').textContent, /旧记录未保存反算后的线规/);
  assert.equal(ui.doc.querySelector('[data-wire-key=JJ]'), null);
});

test('saving a signed design refreshes catalog and loading it updates the same design', async t => {
  let project = null;
  const reference = 'saved-design:project-author@1';
  const ui = await app(t, (path, request) => {
    if (path === '/models') return {total:project?1:0,items:project?[{id:reference,kind:'saved_design',model:project.name,revision:project.revision,author:project.author}]:[]};
    if (path === '/example') return {name:'署名测试',parameters};
    if (path === '/projects' && request.method === 'POST') return project={...request.body,id:'project-author',revision:1,lineage:{},archived:0};
    if (path === '/models/'+encodeURIComponent(reference)) return {kind:'saved_design',model:project.name,project};
    if (path === '/projects/project-author' && request.method === 'PUT') return project={...project,...request.body,revision:2};
  });
  ui.nav('catalog'); await settle();
  ui.doc.querySelector('#load-example').click();
  await until(()=>ui.doc.querySelector('#current-design').textContent==='署名测试','Example loaded');
  ui.doc.querySelector('#save-design').click();
  ui.doc.querySelector('#design-author').value='  张工 <team>  ';
  ui.doc.querySelector('#design-series-control-select').value='series:人工系列';
  ui.doc.querySelector('#save-form').dispatchEvent(new ui.w.Event('submit',{cancelable:true}));
  await until(()=>project&&!ui.doc.querySelector('#save-dialog').open,'Design saved');
  assert.equal(project.series,'人工系列');assert.ok(!('series' in project.parameters));
  assert.equal(project.author,'张工');assert.equal(ui.doc.querySelector('#design-author').readOnly,true); assert.ok(!('author' in project.parameters));
  ui.nav('catalog');
  await until(()=>ui.doc.querySelector('#model-table-body').textContent.includes(project.author),'Saved design replaces cached empty catalog');
  assert.equal(ui.doc.querySelector('#model-table-body team'),null,'Author is rendered as text');
  assert.equal(ui.calls.filter(c=>c.url.pathname==='/api/v1/models').at(-1).url.searchParams.get('source'),'all');
  ui.find('#model-table-body button','载入设计 →').click();
  await until(()=>ui.doc.querySelector('#parameter-source').textContent.includes('已保存设计'),'Saved catalog record opened');
  ui.doc.querySelector('#save-design').click();
  assert.equal(ui.doc.querySelector('#design-author').value,project.author);
  assert.equal(ui.doc.querySelector('#design-series-control-select').value,'series:人工系列');
  assert.equal(ui.doc.querySelector('#save-as-new').checked,false);
  ui.doc.querySelector('#design-author').value='李工';
  ui.doc.querySelector('#save-form').dispatchEvent(new ui.w.Event('submit',{cancelable:true}));
  await until(()=>project.revision===2,'Updated in place');
  const update=ui.calls.find(c=>c.method==='PUT');
  assert.equal(update.body.expected_revision,1);assert.equal(update.body.author,'张工');assert.equal(update.body.base_model_id,null);
  ui.doc.querySelector('#catalog-source').value='saved';
  ui.doc.querySelector('#catalog-source').dispatchEvent(new ui.w.Event('change'));
  assert.equal(ui.doc.querySelector('#series-filter').disabled,false);
  ui.doc.querySelector('#series-filter').value='人工系列';ui.doc.querySelector('#series-filter').dispatchEvent(new ui.w.Event('change'));
  await settle();
  assert.equal(ui.calls.filter(c=>c.url.pathname==='/api/v1/models').at(-1).url.searchParams.get('series'),'人工系列');
  assert.equal(ui.calls.filter(c=>c.url.pathname==='/api/v1/models').at(-1).url.searchParams.get('source'),'saved');
});

test('TX author is revision metadata and a saved library record resumes its original identity',async t=>{
  let design;
  const reference='saved-design:tx-author@1';
  const a=await txApp(t,(req,{p})=>{
    if(req.path==='/designs'&&req.method==='POST')return design={id:'tx-author',name:p.TYP,revision:1,...req.body,source:{resource_version:'synthetic.2',model_id:'synthetic'}};
    if(req.path==='/models'&&design)return {total:1,items:[{id:reference,name:design.name,kind:'saved_design',power_kw:p.P2,poles:4,revision:1,author:design.author,source:{}}]};
    if(req.path==='/models/'+encodeURIComponent(reference))return {kind:'saved_design',design};
    if(req.path==='/designs/tx-author'&&req.method==='PUT')return design={...design,...req.body,revision:2};
  });
  assert.equal(a.doc.querySelector('#design-author').readOnly,true);
  assert.equal(a.doc.querySelector('#design-author').value,'张工');
  a.doc.querySelector('#design-series-control-select').value='series:人工系列';
  a.doc.querySelector('#design-author').value='伪造姓名';a.doc.querySelector('#save-design').click();
  await until(()=>a.doc.querySelector('#models').textContent.includes('张工')&&!a.doc.querySelector('#save-design').disabled,'Saved record indexed');
  assert.equal(design.series,'人工系列');assert.ok(!('series' in design.parameters));
  assert.equal(design.author,'张工');assert.ok(!('author' in design.parameters));
  a.doc.querySelector('#design-author').value='尚未保存';
  a.doc.querySelector('#models button').click();await settle();
  assert.equal(a.doc.querySelector('#design-author').value,'张工');
  assert.equal(a.doc.querySelector('#design-series-control-select').value,'series:人工系列');
  a.doc.querySelector('#design-author').value='李工';a.doc.querySelector('#save-design').click();
  await until(()=>design.revision===2,'Saved design updated');
  const update=a.calls.find(c=>c.method==='PUT');assert.equal(update.path,'/designs/tx-author');
  assert.equal(update.body.expected_revision,1);assert.equal(update.body.author,'张工');assert.equal(update.body.base_model_id,'synthetic');
});

test('TX tabs and global parameter search preserve every edited value and calculation input',async t=>{
  const a=await txApp(t),d=a.doc;
  const tabs=[...d.querySelectorAll('#parameter-tabs [role="tab"]')].filter(tab=>!tab.hidden);
  assert.equal(tabs.length,5);
  assert.equal(d.querySelectorAll('#parameters > [role="tabpanel"]:not([hidden])').length,1);
  assert.match(tabs[0].textContent,/额定参数/);
  tabs[1].click();
  assert.match(d.querySelector('#parameter-count').textContent,/线圈组/);
  const turns=d.querySelector('[name="NDXP(2)"]');turns.value='8.5';turns.dispatchEvent(new a.w.Event('input',{bubbles:true}));
  const search=d.querySelector('#parameter-search');
  const query=value=>{search.value=value;search.dispatchEvent(new a.w.Event('input',{bubbles:true}));};
  query('匝数');
  assert.equal(d.querySelectorAll('#parameter-search-results input').length,6);
  assert.equal(d.querySelector('#parameter-search-tab').getAttribute('aria-selected'),'true');
  assert.equal(d.querySelectorAll('#parameter-tabs [aria-selected="true"]').length,1);
  assert.equal(d.querySelector('[name="NDXP(2)"]'),turns,'Search moves the original control');
  query('ndxp(2)');assert.equal(d.querySelectorAll('#parameter-search-results input').length,1);
  assert.equal(d.querySelector('#parameter-search-results input').value,'8.5');
  query('不存在的参数');assert.equal(d.querySelectorAll('#parameter-search-results input').length,0);
  assert.match(d.querySelector('#parameter-search-results').textContent,/没有匹配/);
  assert.equal(d.querySelectorAll('#parameters input').length,Object.keys(a.p).length,'No controls disappear from the form');
  query('cm');assert.equal(d.querySelector('#parameter-search-results input').name,'D3');
  const diameter=d.querySelector('[name="D3"]');diameter.value='19';diameter.dispatchEvent(new a.w.Event('input',{bubbles:true}));
  search.dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  assert.equal(search.value,'');assert.equal(tabs[1].getAttribute('aria-selected'),'true');
  query('FE');tabs[0].click();assert.equal(search.value,'');
  assert.equal(d.querySelector('[name="NDXP(2)"]').value,'8.5');
  // Calculate while most inputs are in hidden groups, then verify the complete payload.
  d.querySelector('#calculate').click();await until(()=>a.calls.some(c=>c.path==='/calculate'),'Calculated after tab and search edits');
  const sent=a.calls.find(c=>c.path==='/calculate').body.parameters;
  assert.deepEqual(Object.keys(sent).sort(),Object.keys(a.p).sort());
  assert.equal(sent['NDXP(2)'],8.5);assert.equal(sent.D3,19);assert.equal(sent.FE,1);
});

test('TX tabs support keyboard navigation and reveal invalid hidden parameters',async t=>{
  const a=await txApp(t),d=a.doc;
  const tabs=[...d.querySelectorAll('#parameter-tabs [role="tab"]')].filter(tab=>!tab.hidden);
  const press=(element,key)=>element.dispatchEvent(new a.w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));
  tabs[0].focus();press(tabs[0],'ArrowRight');
  assert.equal(d.activeElement,tabs[1]);assert.equal(tabs[1].getAttribute('aria-selected'),'true');
  press(tabs[1],'End');assert.equal(d.activeElement,tabs[4]);
  press(tabs[4],'ArrowRight');assert.equal(d.activeElement,tabs[0]);
  press(tabs[0],'ArrowLeft');assert.equal(d.activeElement,tabs[4]);
  const hidden=d.querySelector('[name="D3"]');hidden.value='';
  d.querySelector('#calculate').click();await settle();
  assert.equal(a.calls.filter(c=>c.path==='/calculate').length,0);
  assert.match(d.querySelector('#notice').textContent,/D3/);
  assert.equal(tabs[3].getAttribute('aria-selected'),'true');assert.equal(d.activeElement,hidden);
  assert.equal(hidden.getAttribute('aria-invalid'),'true');
  hidden.value='18';hidden.dispatchEvent(new a.w.Event('input',{bubbles:true}));
  assert.equal(hidden.hasAttribute('aria-invalid'),false);
});

test('multiobjective search submits independent goals and renders only feasible Pareto points', async t => {
  let submitted;
  const job={id:'tradeoffs',name:'折中验收',status:'succeeded',evaluated:3,budget:3,best_id:'b',feasible_count:2,pareto_ids:['a','b'],
    request:{objective:'efficiency',secondary_objective:'material_cost',algorithm:'hybrid_de_v1',variables:[{key:'L'}],parameters},
    candidates:[{ordinal:1,calculation_id:'a',parameters:{L:100},objective:.90,objectives:{efficiency:.90,material_cost:100},feasible:true},
      {ordinal:2,calculation_id:'b',parameters:{L:110},objective:.91,objectives:{efficiency:.91,material_cost:110},feasible:true},
      {ordinal:3,calculation_id:'c',parameters:{L:90},objective:.95,objectives:{efficiency:.95,material_cost:90},feasible:false,violations:['功率因数低于约束']}]};
  const ui=await app(t,(path,request)=>{
    if(path==='/optimization/config')return {method:'人工混合算法',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率最高'},{id:'material_cost',label:'材料成本最低'}],algorithms:[{id:'hybrid_de_v1',label:'混合差分进化'},{id:'stratified_v1',label:'原方法'}],materials:[],constraint_metrics:[]};
    if(path==='/example')return {parameters:{...parameters,L:100},name:'基准'};
    if(path==='/optimization/jobs'&&request.method==='POST'){submitted=request.body;return job;}
    if(path==='/optimization/jobs')return {items:[job]};
    if(path==='/optimization/jobs/tradeoffs')return job;
    if(path==='/calculations/a')return snapshot('a','折中候选A');
  },[{key:'L',label:'铁心长度',group:'basic',type:'number',role:'input'}]);
  ui.doc.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();
  ui.doc.querySelector('#optimization-use-current').click();
  ui.doc.querySelector('#optimization-secondary').value='efficiency';
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted,undefined);assert.match(ui.doc.querySelector('#optimization-error').textContent,/不同/);
  ui.doc.querySelector('#optimization-secondary').value='material_cost';
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted.secondary_objective,'material_cost');assert.equal(submitted.algorithm,'hybrid_de_v1');
  assert.equal(submitted.objective,'efficiency');assert.equal(submitted.parameters.L,100);
  const detail=ui.doc.querySelector('#optimization-detail');assert.match(detail.textContent,/折中候选 · 2 个/);
  assert.equal(detail.querySelectorAll('.optimization-pareto-chart circle').length,2);
  assert.match(detail.textContent,/第二目标：100/);assert.match(detail.textContent,/功率因数低于约束/);
  assert.equal(detail.querySelectorAll(':scope > .table-scroll tbody tr').length,3);
  [...detail.querySelectorAll('button')].find(b=>b.textContent.startsWith('#1 ·')).click();await settle();
  assert.equal(ui.doc.querySelector('#current-design').textContent,'折中候选A');
});

for (const method of ['adaptive_poll_v1','coupled_poll_v1','ranked_poll_v1','curvature_rescue_v1']) test(`${method} selected in the UI reaches the queued request`,async t=>{
  let submitted;const job={id:'adaptive',name:'近邻验证',status:'succeeded',budget:24,evaluated:0,candidates:[],request:{objective:'efficiency',algorithm:method,variables:[],parameters}};
  const ui=await app(t,(path,request)=>{
    if(path==='/example')return {parameters:{...parameters,L:100},name:'基准'};
    if(path==='/optimization/config')return {method:'自适应邻域搜索',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],algorithms:[{id:'stratified_v1',label:'分层采样'},{id:method,label:'邻域或联合变量搜索'}],materials:[],constraint_metrics:[]};
    if(path==='/optimization/jobs'&&request.method==='POST'){submitted=request.body;return job;}
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/adaptive')return job;
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  ui.doc.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();
  assert.equal(ui.doc.querySelector('#optimization-algorithm').value,'stratified_v1');
  ui.doc.querySelector('#optimization-use-current').click();ui.doc.querySelector('#optimization-algorithm').value=method;
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted.algorithm,method);assert.equal(submitted.parameters.L,100);
  assert.equal(submitted.max_evaluations,24);assert.equal(submitted.secondary_objective,null);
  if(method==='ranked_poll_v1')assert.match(ui.doc.querySelector('#optimization-detail').textContent,/双目标优先探索可能改善折中的方案/);
  if(method==='curvature_rescue_v1')assert.match(ui.doc.querySelector('#optimization-detail').textContent,/试验方法在不同预算下可能优于或逊于邻域预测排序/);
});

test('material quotations preserve edits, require complete prices and freeze the displayed request',async t=>{
  const submitted=[];let job;
  const ui=await app(t,(path,req)=>{
    if(path==='/example')return {name:'报价基准',parameters:{...parameters,L:100,Paihao:'A',Cfe:6.5}};
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'},{key:'Paihao',label:'材料'}],objectives:[{id:'material_cost',label:'材料成本'}],materials:['A','B <试制>','C'],constraint_metrics:[]};
    if(path==='/optimization/jobs'&&req.method==='POST'){
      submitted.push(req.body);job={id:'prices',name:'报价验证',status:'succeeded',budget:2,evaluated:1,request:req.body,
        candidates:[{ordinal:1,parameters:{Paihao:'B <试制>',Cfe:2.5},objective:125,feasible:true,
          material_pricing:{method:'per-material-cfe-v1',cfe:2.5}}]};return job;
    }
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/prices')return job;
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  const d=ui.doc,change=(input,value,event='change')=>{input.value=value;input.dispatchEvent(new ui.w.Event(event));};
  const submit=async()=>{d.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{cancelable:true}));await settle();};
  d.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();d.querySelector('#optimization-use-current').click();
  const panel=d.querySelector('#optimization-price-panel'),toggle=d.querySelector('#optimization-price-enabled');
  assert.equal(panel.hidden,true);
  const row=d.querySelector('.search-variable');change(row.querySelector('select[name=key]'),'Paihao');
  const choices=row.querySelector('[name=choices]');change(choices,'B <试制>','input');
  assert.equal(panel.hidden,false);assert.match(d.querySelector('#optimization-price-note').textContent,/6.5/);
  await submit();assert.equal(submitted[0].material_prices,undefined,'Legacy unified Cfe is the default');
  toggle.checked=true;toggle.dispatchEvent(new ui.w.Event('change'));
  const price=name=>[...d.querySelectorAll('#optimization-price-rows input')].find(input=>input.dataset.material===name);
  assert.equal(price('A').value,'6.5','Baseline price is required even outside candidate set');
  change(price('A'),'6.3','input');change(price('B <试制>'),'2.5','input');
  change(choices,'B <试制>，C','input');assert.equal(price('B <试制>').value,'2.5');assert.equal(price('C').value,'6.5');
  change(price('C'),'','input');await submit();assert.equal(submitted.length,1);assert.match(d.querySelector('#optimization-error').textContent,/每个候选牌号/);
  change(choices,'B <试制>','input');await submit();assert.equal(submitted.length,2);
  assert.deepEqual(submitted[1].material_prices,{'A':6.3,'B <试制>':2.5});
  assert.equal(submitted[1].parameters.Cfe,6.5,'Workbench base is not silently rewritten in the client');
  const detail=d.querySelector('#optimization-detail');assert.match(detail.textContent,/B <试制> = 2.5/);assert.match(detail.textContent,/Cfe=2.5 \/kg/);
  assert.equal(detail.querySelector('试制'),null,'Material references are text, not markup');
  assert.match(detail.textContent,/"material_prices"/,'Stored configuration includes the quote table');
  change(row.querySelector('select[name=key]'),'L');assert.equal(panel.hidden,true);await submit();assert.equal(submitted.at(-1).material_prices,undefined);
  change(row.querySelector('select[name=key]'),'Paihao');assert.equal(price('A').value,'6.3');assert.equal(price('B <试制>').value,'2.5');
  toggle.checked=false;toggle.dispatchEvent(new ui.w.Event('change'));await submit();assert.equal(submitted.at(-1).material_prices,undefined);
  assert.ok([...d.querySelectorAll('#optimization-price-rows input')].every(input=>input.disabled));
});

test('curve peak constraint is distinct from TM1 and displays saved readings and unavailable sweeps',async t=>{
  let submitted;
  const constraints=[{metric:'TM_curve',minimum:3.1}];
  const job={id:'curve-constraint',name:'采样峰值验证',status:'succeeded',budget:2,evaluated:2,feasible_count:1,
    request:{parameters:{...parameters,_curve:1},variables:[{key:'L'}],constraints,objective:'efficiency'},
    candidates:[{ordinal:1,parameters:{L:100},objective:.9,feasible:true,constraint_values:{TM_curve:3.2},torque_peak:{slip:.3}},
      {ordinal:2,parameters:{L:101},objective:.95,feasible:false,violations:['TM_curve 无有效结果'],constraint_values:{TM_curve:null},torque_peak:{reason:'转矩曲线采样不完整：50/51'}}]};
  const ui=await app(t,(path,request)=>{
    if(path==='/example')return {parameters:{...parameters,L:100,_curve:0},name:'基准'};
    if(path==='/optimization/config')return {method:'人工测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:['TM1','TM_curve'],constraint_labels:{TM1:'最大转矩（原公式估计，倍）',TM_curve:'转矩曲线采样峰值（倍）'}};
    if(path==='/optimization/jobs'&&request.method==='POST'){submitted=request.body;return job;}
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/curve-constraint')return job;
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  ui.doc.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();
  ui.doc.querySelector('#optimization-use-current').click();ui.doc.querySelector('#add-optimization-constraint').click();
  const row=ui.doc.querySelector('.search-constraint'),metric=row.querySelector('select');
  assert.match(metric.textContent,/原公式估计/);assert.match(metric.textContent,/采样峰值/);
  assert.equal(ui.doc.querySelector('#optimization-curve-note').hidden,true);
  metric.value='TM_curve';metric.dispatchEvent(new ui.w.Event('change'));row.querySelector('[name=minimum]').value='3.1';
  assert.equal(ui.doc.querySelector('#optimization-curve-note').hidden,false);
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.deepEqual(submitted.constraints,constraints);
  assert.equal(submitted.parameters._curve,0,'Server resolves effective curve option; workbench draft stays unchanged');
  const detail=ui.doc.querySelector('#optimization-detail');
  assert.match(detail.textContent,/已启用转矩曲线计算/);
  assert.match(detail.textContent,/TM_curve=3.2 · s=0.3/);
  assert.match(detail.textContent,/TM_curve=—/);assert.match(detail.textContent,/50\/51/);
  row.querySelector('button').click();assert.equal(ui.doc.querySelector('#optimization-curve-note').hidden,true);
});

test('load cycle form submits hours and load fractions and hides unused fields without losing draft',async t=>{
  let submitted;
  const job={id:'cycle',name:'周期验收',status:'succeeded',budget:2,evaluated:2,feasible_count:1,
    request:{parameters:{...parameters,_load_curve:1},variables:[{key:'L'}],constraints:[],objective:'cycle_energy',load_cycle:[{load_fraction:.5,hours:4},{load_fraction:.75,hours:2},{load_fraction:1,hours:2}]},
    candidates:[{ordinal:1,parameters:{L:100},objective:18.75,feasible:true,load_cycle:{status:'available',input_energy_kwh:18.75,efficiency:.88}},
      {ordinal:2,parameters:{L:101},objective:null,feasible:false,violations:['无有效周期结果'],load_cycle:{status:'unavailable',reason:'负载特性必须包含全部 9 个原始采样点'}}]};
  const ui=await app(t,(path,req)=>{
    if(path==='/example')return {parameters:{...parameters,L:100,_load_curve:0},name:'基准'};
    if(path==='/optimization/config')return {method:'人工测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'},{id:'cycle_energy',label:'周期耗电 kWh'},{id:'cycle_efficiency',label:'周期效率'}],materials:[],constraint_metrics:['cycle_efficiency']};
    if(path==='/optimization/jobs'&&req.method==='POST'){submitted=req.body;return job;}
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/cycle')return job;
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  ui.doc.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();
  ui.doc.querySelector('#optimization-use-current').click();
  const form=ui.doc.querySelector('#optimization-cycle'),objective=ui.doc.querySelector('#optimization-objective');
  assert.equal(form.hidden,true);assert.equal(form.disabled,true);
  objective.value='cycle_energy';objective.dispatchEvent(new ui.w.Event('change'));
  assert.equal(form.hidden,false);assert.equal(form.disabled,false);
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.deepEqual(submitted.load_cycle,job.request.load_cycle);assert.equal(submitted.parameters._load_curve,0);
  assert.match(ui.doc.querySelector('#optimization-detail').textContent,/18.75 kWh/);
  assert.match(ui.doc.querySelector('#optimization-detail').textContent,/周期效率 88%/);
  assert.match(ui.doc.querySelector('#optimization-detail').textContent,/全部 9 个原始采样点/);
  const hours=form.querySelector('[name=hours]');hours.value='7';
  objective.value='efficiency';objective.dispatchEvent(new ui.w.Event('change'));
  assert.equal(form.hidden,true);assert.equal(form.disabled,true);
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted.load_cycle,undefined);
  ui.doc.querySelector('#add-optimization-constraint').click();assert.equal(form.hidden,false);assert.equal(hours.value,'7');
  form.querySelector('[name=load]').value='20';submitted=null;
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted,null);assert.match(ui.doc.querySelector('#optimization-error').textContent,/30%–120%/);
});

test('explicit sizes preserve drafts and serialize lists with optional rotor linkage',async t=>{
  let submitted;
  const job={id:'sizes',name:'规格验证',status:'succeeded',budget:8,evaluated:1,candidates:[],request:{variables:[],parameters,objective:'efficiency'}};
  const ui=await app(t,(path,req)=>{
    if(path==='/example')return {parameters:{...parameters,L:160,g:.6,hr0:.5},name:'基准'};
    if(path==='/optimization/config')return {method:'人工测试',variables:[{key:'L',label:'长度'},{key:'g',label:'气隙'},{key:'Paihao',label:'材料'}],objectives:[{id:'efficiency',label:'效率'}],materials:['A'],constraint_metrics:[]};
    if(path==='/optimization/jobs'&&req.method==='POST'){submitted=req.body;job.request=req.body;return job;}
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/sizes')return job;
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  ui.doc.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();
  ui.doc.querySelector('#optimization-use-current').click();
  const row=ui.doc.querySelector('.search-variable'),mode=row.querySelector('[name=mode]'),values=row.querySelector('[name=values]');
  mode.value='values';mode.dispatchEvent(new ui.w.Event('change'));values.value='150，160 172';
  assert.equal(row.querySelector('[name=minimum]').disabled,true);assert.equal(values.required,true);
  mode.value='range';mode.dispatchEvent(new ui.w.Event('change'));assert.equal(values.disabled,true);assert.equal(values.required,false);
  mode.value='values';mode.dispatchEvent(new ui.w.Event('change'));assert.equal(values.value,'150，160 172');
  ui.doc.querySelector('#add-optimization-variable').click();
  const gap=ui.doc.querySelectorAll('.search-variable')[1];gap.querySelector('[name=mode]').value='values';gap.querySelector('[name=mode]').dispatchEvent(new ui.w.Event('change'));gap.querySelector('[name=values]').value='.4,.6,.8';
  ui.doc.querySelector('#optimization-gap-mode').value='fixed_neck_bottom';
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.deepEqual(submitted.variables,[{key:'L',values:[150,160,172]},{key:'g',values:[.4,.6,.8]}]);
  assert.equal(submitted.rotor_gap_mode,'fixed_neck_bottom');assert.equal(submitted.parameters.hr0,.5);
  assert.match(ui.doc.querySelector('#optimization-detail').textContent,/指定规格/);
  assert.match(ui.doc.querySelector('#optimization-detail').textContent,/保持转子槽颈底位置/);
  values.value='150,150';submitted=null;
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted,null);assert.match(ui.doc.querySelector('#optimization-error').textContent,/不重复/);
  gap.querySelector('button').click();assert.equal(ui.doc.querySelector('#optimization-gap-mode').disabled,true);
  values.value='150';ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.equal(submitted.rotor_gap_mode,'independent');
  const key=row.querySelector('[name=key]');key.value='Paihao';key.dispatchEvent(new ui.w.Event('change'));
  assert.equal(values.disabled,true);assert.equal(row.querySelector('[name=choices]').required,true);
});

test('tolerance search keeps drafts, validates budget and distinguishes worst samples from nominal curves',async t=>{
  let submitted;
  const robust={status:'complete',feasible:true,note:'有限采样，不保证角点之间没有更差值。',evaluated_samples:3,expected_samples:3,
    tolerances:[{key:'g',delta:.02}],objectives:{efficiency:.88},worst_samples:{efficiency:1},objective_specs:{efficiency:{metric:'ATA1',label:'效率最高'}},
    metric_ranges:{ATA1:{minimum:.88,maximum:.92,complete:true}},samples:[{index:1,label:'g-0.02',parameters:{g:.28,hr0:.52},values:{ATA1:.88},violations:[],calculation_id:'sample'}]};
  const job={id:'tolerance',name:'公差测试',status:'succeeded',budget:8,evaluated:6,best_id:'nominal',
    request:{parameters:{...parameters,L:100,g:.3,hr0:.5},variables:[{key:'L'}],constraints:[{metric:'ATA1',minimum:.85}],objective:'efficiency',tolerances:robust.tolerances},
    candidates:[{ordinal:1,calculation_id:'nominal',parameters:{L:100},objective:.88,feasible:true,robustness:robust,constraint_values:{ATA1:.9}}],samples:robust.samples.map(s=>({...s,ordinal:1}))};
  const ui=await app(t,(path,req)=>{
    if(path==='/example')return {parameters:job.request.parameters,name:'基准'};
    if(path==='/optimization/config')return {method:'人工测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:['ATA1']};
    if(path==='/optimization/jobs'&&req.method==='POST'){submitted=req.body;return job;}
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/tolerance')return job;
    if(path==='/calculations/nominal')return {...snapshot('nominal'),inputs:job.request.parameters,optimization:{robustness:robust}};
    if(path==='/calculations/sample')return {...snapshot('sample','原始公差采样'),inputs:{...job.request.parameters,g:.28}};
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  const d=ui.doc,change=n=>n.dispatchEvent(new ui.w.Event('change')),submit=async()=>{submitted=null;d.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{cancelable:true}));await settle();};
  d.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();d.querySelector('#optimization-use-current').click();
  const enabled=d.querySelector('#optimization-tolerance-enabled'),form=d.querySelector('#optimization-tolerances'),delta=form.querySelector('[name=delta]');
  assert.equal(form.disabled,true);assert.equal(form.hidden,true);assert.equal(delta.value,'','No factory tolerance presumed');
  enabled.checked=true;change(enabled);assert.equal(form.hidden,false);assert.equal(form.disabled,false);
  await submit();assert.equal(submitted,null);assert.match(d.querySelector('#optimization-error').textContent,/偏差须大于/);
  delta.value='.02';d.querySelector('#optimization-budget').value='5';await submit();assert.equal(submitted,null);assert.match(d.querySelector('#optimization-error').textContent,/预算不足/);
  d.querySelector('#optimization-budget').value='8';d.querySelector('#tolerance-gap-mode').value='fixed_neck_bottom';await submit();
  assert.deepEqual(submitted.tolerances,[{key:'g',delta:.02}]);assert.equal(submitted.tolerance_gap_mode,'fixed_neck_bottom');
  const interior=d.querySelector('#tolerance-interior-samples');assert.equal(interior.value,'0');
  interior.value='4';change(interior);
  assert.match(d.querySelector('#tolerance-budget-note').textContent,/计算 7 次/);
  assert.match(d.querySelector('#tolerance-budget-note').textContent,/至少需 14 次/);
  d.querySelector('#optimization-budget').value='13';await submit();assert.equal(submitted,null);
  job.request.tolerance_interior_samples=4;
  job.request.tolerance_sampling='nested_lhs_v1';
  d.querySelector('#optimization-budget').value='14';await submit();assert.equal(submitted.tolerance_interior_samples,4);
  assert.match(d.querySelector('#optimization-detail').textContent,/逐级保留已有点/);
  const detail=d.querySelector('#optimization-detail');assert.match(detail.textContent,/最不利采样目标值/);assert.match(detail.textContent,/ATA1=0.88–0.92/);assert.match(detail.textContent,/次情景计算/);
  enabled.checked=false;change(enabled);assert.equal(form.disabled,true);await submit();assert.equal(submitted.tolerances,undefined);assert.equal(submitted.tolerance_gap_mode,undefined);assert.equal(submitted.tolerance_interior_samples,undefined);
  enabled.checked=true;change(enabled);assert.equal(delta.value,'.02');assert.equal(interior.value,'4');
  d.querySelector('#add-tolerance').click();const second=form.querySelectorAll('[name=key]')[1];second.value='g';change(second);await submit();assert.equal(submitted,null);
  ui.find('#optimization-detail button','打开最佳候选').click();await settle();
  assert.match(d.querySelector('.tolerance-result').textContent,/名义尺寸结果/);
  assert.match(d.querySelector('.tolerance-result').textContent,/0.88/);
  ui.find('.tolerance-result button','打开采样').click();await settle();
  assert.equal(d.querySelector('#current-design').textContent,'原始公差采样');
  assert.equal(JSON.parse(d.querySelector('#raw-parameters').textContent).g,.28);
});

test('refined torque constraint remains separate and exposes recorded convergence samples',async t=>{
  let submitted;
  const peak={status:'available',value:3.32158,slip:.3238,delta_from_legacy:-.0011,sample_count:72,
    note:'直接调用原核；有限采样不保证连续全局峰值。',samples:[{slip:.3238,torque:3.32158,current:4.976,iterations:7,residual:.000001}]};
  const job={id:'refined',name:'加密验收',status:'succeeded',budget:2,evaluated:2,feasible_count:1,best_id:'refined-best',
    request:{parameters:{...parameters,_curve:1},variables:[{key:'L'}],constraints:[{metric:'TM_refined',minimum:3.3}],objective:'efficiency'},
    candidates:[{ordinal:1,calculation_id:'refined-best',parameters:{L:100},objective:.9,feasible:true,constraint_values:{TM_refined:3.32158},refined_torque_peak:peak},
      {ordinal:2,parameters:{L:101},feasible:false,violations:['TM_refined 无有效结果'],constraint_values:{TM_refined:null},refined_torque_peak:{reason:'加密采样未收敛'}}]};
  const ui=await app(t,(path,request)=>{
    if(path==='/example')return {parameters:{...parameters,L:100,_curve:0},name:'基准'};
    if(path==='/optimization/config')return {method:'人工测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:['TM1','TM_curve','TM_refined'],constraint_labels:{TM_refined:'加密转矩峰值（收紧迭代，倍）'}};
    if(path==='/optimization/jobs'&&request.method==='POST'){submitted=request.body;return job;}
    if(path==='/optimization/jobs')return {items:[]};
    if(path==='/optimization/jobs/refined')return job;
    if(path==='/calculations/refined-best')return {inputs:parameters,values:{TM1:2.78},metrics:[],curves:[],curve_metrics:{refined_torque_peak:peak},calculation_id:'refined-best'};
  },[{key:'L',label:'长度',group:'basic',type:'number',role:'input'}]);
  ui.doc.querySelector('#load-example').click();await settle();ui.nav('optimization');await settle();
  ui.doc.querySelector('#optimization-use-current').click();ui.doc.querySelector('#add-optimization-constraint').click();
  const row=ui.doc.querySelector('.search-constraint'),metric=row.querySelector('select');
  metric.value='TM_refined';metric.dispatchEvent(new ui.w.Event('change'));row.querySelector('[name=minimum]').value='3.3';
  assert.match(ui.doc.querySelector('#optimization-curve-note').textContent,/257/);
  ui.doc.querySelector('#optimization-form').dispatchEvent(new ui.w.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.deepEqual(submitted.constraints,job.request.constraints);
  const detail=ui.doc.querySelector('#optimization-detail');assert.match(detail.textContent,/加密峰值 s=0.3238/);assert.match(detail.textContent,/加密采样未收敛/);
  ui.find('#optimization-detail button','打开最佳候选').click();await settle();
  const result=ui.doc.querySelector('.torque-refinement-result');assert.match(result.textContent,/3.32158/);assert.match(result.textContent,/1.000e-6/);assert.match(result.textContent,/迭代次数/);
});

test('unconverged peak snapshot exposes saved iteration diagnostics without recalculation',async t=>{
  const value=snapshot('a');
  value.curve_metrics={refined_torque_peak:{status:'unavailable',value:null,samples:[],reason:'周期 2 重复，同时存在饱和系数插值换段',
    diagnostic:{note:'数值求解失败不能据此判定电机性能不达标。',history:[
      {iteration:101,input_current:7.4,current:7.45,residual:.05,saturation_input:.499,saturation_factor:.42,interpolation_window:1}]}}};
  const ui=await app(t,path=>path==='/calculations'?records():path==='/calculations/a'?value:undefined);
  ui.nav('comparisons');await settle();ui.find('#calculation-table-body button','打开快照').click();await settle();
  const result=ui.doc.querySelector('.torque-refinement-result'),details=result.querySelector('.torque-iteration-history');
  assert.match(result.textContent,/周期 2/);assert.match(result.textContent,/不能据此判定电机性能不达标/);
  assert.match(details.textContent,/输入电流倍数/);assert.match(details.textContent,/0.499/);assert.match(details.textContent,/101/);
  assert.ok(!ui.calls.some(c=>c.path==='/calculate'));
});


test('continuous torque opt-in shows model scope and keeps both saved results distinct',async t=>{
  const peak={status:'available',value:3.3298,slip:.324,note:'试验模型：插值规则已改变，常规效率仍按原模型计算。',
    samples:[{slip:.324,torque:3.3298,current:5,iterations:8,residual:1e-6,factor_residual:2e-7}]};
  const value=snapshot('a');value.curve_metrics={refined_torque_peak:{...peak,value:3.3216},continuous_torque_peak:peak};
  const ui=await app(t,path=>{
    if(path==='/calculations')return records();
    if(path==='/calculations/a')return value;
    if(path==='/optimization/config')return {method:'人工测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:['TM_refined','TM_continuous']};
  });
  ui.nav('optimization');await settle();ui.doc.querySelector('#add-optimization-constraint').click();
  const metric=ui.doc.querySelector('.search-constraint select');metric.value='TM_continuous';metric.dispatchEvent(new ui.w.Event('change'));
  assert.match(ui.doc.querySelector('#optimization-curve-note').textContent,/PCHIP/);
  assert.match(ui.doc.querySelector('#optimization-curve-note').textContent,/原软件插值不同/);
  ui.nav('comparisons');await settle();ui.find('#calculation-table-body button','打开快照').click();await settle();
  const sections=ui.doc.querySelectorAll('.torque-refinement-result');assert.equal(sections.length,2);
  assert.match(sections[0].textContent,/3.3216/);assert.match(sections[1].textContent,/3.3298/);
  assert.match(sections[1].textContent,/第二状态差/);assert.match(sections[1].textContent,/2.000e-7/);
});

test('post-search verification freezes a selected candidate, exposes its budget and links source records',async t=>{
  const robust={status:'complete',feasible:true,expected_samples:7,metric_ranges:{},objectives:{efficiency:.9}};
  const source={id:'search-source',name:'源搜索',status:'succeeded',evaluated:14,budget:14,best_id:'best',
    request:{seed:42,objective:'efficiency',variables:[{key:'L'}],parameters:{...parameters,L:100},tolerances:[{key:'g',delta:.02}],constraints:[]},
    candidates:[{ordinal:1,calculation_id:'best',feasible:true,parameters:{L:100},objective:.9,robustness:robust},
      {ordinal:2,calculation_id:'other',feasible:true,parameters:{L:110},objective:.89,robustness:robust}]};
  const child={...source,id:'check-child',name:'补充复核',best_id:null,candidates:[{ordinal:1,parameters:{L:110},calculation_id:null,feasible:false,violations:['公差评估未完成。']}],evaluated:0,budget:23,status:'running',
    request:{...source.request,verification:{source_job_id:source.id,source_calculation_id:'other',source_candidate_ordinal:2,prior_samples:7,interior_samples:16,seed:99}}};
  let posted;
  const ui=await app(t,(path,req)=>{
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:[]};
    if(path==='/optimization/jobs')return {items:[source]};
    if(path==='/optimization/jobs/search-source')return source;
    if(path==='/optimization/jobs/search-source/verify'){posted=req.body;source.verifications=[child];return child;}
    if(path==='/optimization/jobs/check-child')return child;
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-jobs button'),'List ready');
  ui.find('#optimization-jobs button','查看任务').click();await until(()=>ui.doc.querySelector('#verification-form'),'Verification controls ready');
  const d=ui.doc,form=d.querySelector('#verification-form');
  assert.equal(d.querySelector('#verification-candidate').value,'1');assert.equal(d.querySelector('#verification-seed').value,'43');
  assert.match(form.textContent,/复算 7 \+ 新增 8 = 15 次计算/);
  d.querySelector('#verification-candidate').value='2';d.querySelector('#verification-count').value='16';d.querySelector('#verification-count').dispatchEvent(new ui.w.Event('change'));d.querySelector('#verification-seed').value='99';
  assert.match(form.textContent,/复算 7 \+ 新增 16 = 23 次计算/);
  form.dispatchEvent(new ui.w.Event('submit',{cancelable:true}));await until(()=>posted&&d.querySelector('#optimization-detail').textContent.includes('新点种子 99'),'Queued verification');
  assert.deepEqual(posted,{candidate_ordinal:2,interior_samples:16,seed:99});assert.equal(posted.parameters,undefined);
  assert.match(d.querySelector('#optimization-detail').textContent,/未参与原搜索/);
  assert.equal(d.querySelector('#objective-progress'),null,'Fixed-plan verification uses scenario accounting, not nominal attempt charts');
  assert.doesNotMatch(d.querySelector('#optimization-detail').textContent,/★/,'An unfinished candidate is not marked as best');
  ui.find('#optimization-detail button','查看源任务').click();await until(()=>d.querySelector('#verification-form'),'Source reopened');
  assert.match(d.querySelector('#optimization-detail').textContent,/关联复核/);
  assert.equal(source.best_id,'best');
});

test('nominal continuation sends only extra budget and distinguishes inherited and new work',async t=>{
  const source={id:'original-search',name:'原搜索',status:'succeeded',evaluated:3,budget:3,best_id:'old-best',
    request:{seed:19,objective:'efficiency',algorithm:'curvature_rescue_v1',variables:[{key:'L'}],parameters,constraints:[]},
    candidates:[{ordinal:1,calculation_id:'old-best',parameters:{L:140},objective:.9,objectives:{efficiency:.9},feasible:true},
      {ordinal:2,parameters:{L:150},feasible:false,error:'未收敛'},
      {ordinal:3,calculation_id:'old-other',parameters:{L:160},objective:.89,objectives:{efficiency:.89},feasible:true}],extensions:[]};
  const child={...source,id:'extended-search',name:'续算',status:'running',evaluated:4,budget:7,
    request:{...source.request,continuation:{source_job_id:source.id,inherited_attempts:3,additional_evaluations:4,inherited_best_id:'old-best'}},
    candidates:[...source.candidates,{ordinal:4,calculation_id:'new',parameters:{L:145},objective:.88,objectives:{efficiency:.88},feasible:true}]};
  let posted,count=0;const pending=deferred();
  const ui=await app(t,(path,req)=>{
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:[]};
    if(path==='/optimization/jobs')return {items:[source]};
    if(path==='/optimization/jobs/original-search')return source;
    if(path==='/optimization/jobs/original-search/extend'){posted=req.body;count++;source.extensions=[child];return pending.promise.then(()=>child);}
    if(path==='/optimization/jobs/extended-search')return child;
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-jobs button'),'Jobs ready');
  ui.find('#optimization-jobs button','查看任务').click();await until(()=>ui.doc.querySelector('#continuation-form'),'Continuation form ready');
  const d=ui.doc,form=d.querySelector('#continuation-form'),budget=d.querySelector('#continuation-budget');
  assert.equal(d.querySelector('#feedback-form'),null);assert.equal(d.querySelector('#verification-form'),null);
  assert.equal(budget.min,'1');assert.equal(budget.max,'128');
  budget.value='4';budget.dispatchEvent(new ui.w.Event('input'));
  assert.match(form.textContent,/继承 3 \+ 新增预算 4 = 累计最多 7/);
  form.dispatchEvent(new ui.w.Event('submit',{cancelable:true}));form.dispatchEvent(new ui.w.Event('submit',{cancelable:true}));
  assert.equal(count,1);assert.deepEqual(posted,{additional_evaluations:4});assert.equal(budget.disabled,true);
  pending.release();await until(()=>d.querySelector('#optimization-detail h2').textContent.includes('续算'),'Child rendered');
  const detail=d.querySelector('#optimization-detail'),progress=detail.querySelector('progress');
  assert.equal(progress.max,4);assert.equal(progress.value,1);
  assert.match(detail.textContent,/本次新增 1 \/ 4；继承 3；累计 4 \/ 7/);
  assert.match(detail.textContent,/1 基准 · 继承 ★/);assert.match(detail.textContent,/4 · 新增/);
  assert.match(detail.textContent,/当前第一目标最佳仍为继承方案/);
  assert.equal(d.querySelector('#continuation-form'),null);
  ui.find('#optimization-detail button','查看续算源任务').click();
  await until(()=>d.querySelector('#continuation-form'),'Source reopened');
  assert.match(detail.textContent,/追加预算任务：/);
});

async function benefitApp(t,job,extraRoute=()=>undefined){
  const ui=await app(t,(path,request)=>{
    if(path==='/optimization/config')return {method:'人工验收',variables:[{key:'L',label:'长度'}],materials:[],constraint_metrics:[],
      objectives:[{id:'efficiency',label:'效率',direction:'max'},{id:'material_cost',label:'成本',direction:'min'},
        {id:'cycle_efficiency',label:'周期效率',direction:'max'},{id:'cycle_energy',label:'周期耗电',direction:'min'}]};
    if(path==='/optimization/jobs')return {items:[job]};
    if(path==='/optimization/jobs/'+job.id)return job;
    return extraRoute(path,request);
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-jobs button'),'Benefit task available');
  ui.find('#optimization-jobs button','查看任务').click();
  await until(()=>ui.doc.querySelector(job.request.continuation?'#continuation-benefit':'#objective-progress'),'Optimization result rendered');
  return {...ui,panel:ui.doc.querySelector('#continuation-benefit')};
}

function benefitJob(candidates,{objective='efficiency',secondary=null,inherited=2,prior='old',current='new',status='succeeded'}={}){
  return {id:'benefit-job',name:'人工续算验收',status,evaluated:candidates.length,budget:candidates.length,best_id:current,
    request:{objective,secondary_objective:secondary,variables:[{key:'L'}],parameters,constraints:[],seed:19,
      continuation:{source_job_id:'source',inherited_attempts:inherited,additional_evaluations:Math.max(1,candidates.length-inherited),inherited_best_id:prior}},
    candidates:candidates.map((c,index)=>({ordinal:index+1,parameters:{L:100+index},feasible:true,violations:[],...c})),pareto_ids:[]};
}

test('continuation benefit compares complete best designs and shows a secondary regression with correct units',async t=>{
  const job=benefitJob([
    {calculation_id:'base',objective:100,objectives:{material_cost:100,efficiency:.88}},
    {calculation_id:'old',objective:90,objectives:{material_cost:90,efficiency:.9}},
    {calculation_id:'new',objective:80,objectives:{material_cost:80,efficiency:.88}},
    {calculation_id:'tradeoff',objective:95,objectives:{material_cost:95,efficiency:.91}},
    {calculation_id:'duplicate',objective:90,objectives:{material_cost:90,efficiency:.9}},
    {calculation_id:'infeasible',objective:1,objectives:{material_cost:1,efficiency:.99},feasible:false,violations:['槽满率超限']},
    {calculation_id:null,feasible:false,error:'计算失败'},
  ],{objective:'material_cost',secondary:'efficiency'});
  job.pareto_ids=['old','new','tradeoff','duplicate'];
  let comparison;
  const ui=await benefitApp(t,job,(path,request)=>{
    if(path==='/calculations')return {items:[],total:0};
    if(path==='/comparisons'){comparison=request.body;return {baseline_id:'old',warnings:[],schemes:[],metrics:[],parameter_differences:[]};}
  });
  assert.deepEqual([...ui.panel.querySelectorAll('.metric-value')].map(n=>n.textContent),['5','3','1','2']);
  const cost=ui.panel.querySelector('[data-objective=material_cost]'),efficiency=ui.panel.querySelector('[data-objective=efficiency]');
  assert.deepEqual([...cost.querySelectorAll('td')].map(n=>n.textContent),['90 报价币种','80 报价币种','减少 10 报价币种（改善）']);
  assert.deepEqual([...efficiency.querySelectorAll('td')].map(n=>n.textContent),['90 %','88 %','减少 2 个百分点（退步）']);
  assert.equal(cost.querySelector('[data-change]').dataset.change,'improved');
  assert.equal(efficiency.querySelector('[data-change]').dataset.change,'regressed');
  assert.match(ui.panel.textContent,/追加前最佳 #2当前最佳 #3/);
  assert.doesNotMatch(ui.panel.textContent,/91 %|99 %|NaN|Infinity/);
  ui.find('#continuation-benefit button','对比追加前后方案').click();
  await until(()=>comparison,'Explicit comparison requested');
  assert.deepEqual(comparison,{calculation_ids:['old','new'],baseline_id:'old'});
  assert.equal(ui.calls.filter(c=>c.url.pathname==='/api/v1/calculate').length,0);
});

test('unchanged first objective can gain a distinct tradeoff without counting equal-vector duplicates',async t=>{
  const job=benefitJob([
    {calculation_id:'base',objective:.88,objectives:{efficiency:.88,material_cost:110}},
    {calculation_id:'old',objective:.9,objectives:{efficiency:.9,material_cost:100}},
    {calculation_id:'tradeoff',objective:.89,objectives:{efficiency:.89,material_cost:80}},
    {calculation_id:'duplicate',objective:.9,objectives:{efficiency:.9,material_cost:100}},
    {calculation_id:'duplicate-tradeoff',objective:.89,objectives:{efficiency:.89,material_cost:80}},
  ],{secondary:'material_cost',current:'old'});
  job.pareto_ids=['old','tradeoff','duplicate','duplicate-tradeoff'];
  const ui=await benefitApp(t,job);
  assert.deepEqual([...ui.panel.querySelectorAll('.metric-value')].map(n=>n.textContent),['3','3','0','1']);
  assert.match(ui.panel.textContent,/当前第一目标最佳仍为继承方案/);
  assert.equal(ui.panel.querySelectorAll('[data-change=unchanged]').length,2);
  assert.equal(ui.panel.querySelector('button'),null);
});

test('continuation benefit does not invent a baseline when feasibility is first achieved',async t=>{
  const job=benefitJob([
    {calculation_id:'infeasible',objective:.99,objectives:{efficiency:.99},feasible:false,violations:['约束超限']},
    {feasible:false,error:'未收敛'},
    {calculation_id:'new',objective:.901,objectives:{efficiency:.901}},
  ],{prior:null});
  const ui=await benefitApp(t,job);
  assert.match(ui.panel.textContent,/本次首次找到满足全部约束的方案 #3/);
  assert.deepEqual([...ui.panel.querySelector('tbody tr').querySelectorAll('td')].map(n=>n.textContent),['—','90.1 %','—']);
  assert.equal(ui.panel.querySelector('[data-change]'),null);
});

test('continuation benefit preserves zero cost and does not coerce missing objectives or missing candidates',async t=>{
  for(const incomplete of [false,true])await t.test(incomplete?'partial candidate list':'missing objective',async t=>{
    const job=benefitJob([
      {calculation_id:'base',objective:3,objectives:{material_cost:3,efficiency:.9}},
      {calculation_id:'old',objective:0,objectives:{material_cost:0,efficiency:null}},
      {calculation_id:'new',objective:0,objectives:{material_cost:0,efficiency:.91}},
    ],{objective:'material_cost',secondary:'efficiency'});
    if(incomplete)job.evaluated=4;
    const ui=await benefitApp(t,job);
    if(incomplete){
      assert.match(ui.panel.textContent,/候选记录尚未完整/);assert.equal(ui.panel.querySelector('table'),null);
    }else{
      assert.deepEqual([...ui.panel.querySelector('[data-objective=material_cost]').querySelectorAll('td')].map(n=>n.textContent),['0 报价币种','0 报价币种','未变化']);
      assert.deepEqual([...ui.panel.querySelector('[data-objective=efficiency]').querySelectorAll('td')].map(n=>n.textContent),['—','91 %','—']);
    }
    assert.doesNotMatch(ui.panel.textContent,/NaN|Infinity/);
  });
});

test('queued or failed-only additions keep prior values and count only new attempts',async t=>{
  for(const queued of [true,false])await t.test(queued?'queued':'all new failed',async t=>{
    const candidates=[
      {calculation_id:'base',objective:120,objectives:{cycle_energy:120,cycle_efficiency:.85}},
      {calculation_id:'old',objective:100,objectives:{cycle_energy:100,cycle_efficiency:.9}},
    ];
    if(!queued)candidates.push({feasible:false,error:'计算失败'},{feasible:false,error:'未收敛'});
    const job=benefitJob(candidates,{objective:'cycle_energy',secondary:'cycle_efficiency',current:'old',status:queued?'queued':'succeeded'});
    const ui=await benefitApp(t,job);
    assert.deepEqual([...ui.panel.querySelectorAll('.metric-value')].map(n=>n.textContent),queued?['0','0','0','0']:['2','0','2','0']);
    assert.deepEqual([...ui.panel.querySelector('[data-objective=cycle_energy]').querySelectorAll('td')].map(n=>n.textContent),['100 kWh','100 kWh','未变化']);
    assert.deepEqual([...ui.panel.querySelector('[data-objective=cycle_efficiency]').querySelectorAll('td')].map(n=>n.textContent),['90 %','90 %','未变化']);
    if(queued)assert.match(ui.panel.textContent,/尚无新增候选完成计算/);
  });
});

test('objective progress uses charged attempts, independent objective extremes and exact saved snapshots',async t=>{
  const job=benefitJob([
    {feasible:false,objective:.99,violations:['不满足约束']},
    {calculation_id:'old',objective:.9,objectives:{efficiency:.9,material_cost:100}},
    {feasible:false,error:'计算失败'},
    {calculation_id:'rejected',feasible:false,objective:.98,objectives:{efficiency:.98,material_cost:1},violations:['不满足约束']},
    {calculation_id:'cost-best',objective:.89,objectives:{efficiency:.89,material_cost:80}},
    {calculation_id:'new',objective:.91,objectives:{efficiency:.91,material_cost:90}},
    {calculation_id:'equal',objective:.91,objectives:{efficiency:.91,material_cost:90}},
  ],{secondary:'material_cost',inherited:4});
  job.budget=100;const before=JSON.stringify(job);
  const ui=await benefitApp(t,job,path=>path==='/calculations/cost-best'?snapshot('cost-best'):undefined);
  const progress=ui.doc.querySelector('#objective-progress');
  const efficiency=progress.querySelector('[data-objective=efficiency]'),cost=progress.querySelector('[data-objective=material_cost]');
  assert.match(efficiency.textContent,/当前最佳 91 % · 方案 #6/);
  assert.match(cost.textContent,/当前最佳 80 报价币种 · 方案 #5/);
  assert.match(efficiency.textContent,/最近 1 次评估/);assert.match(cost.textContent,/最近 2 次评估/);
  assert.deepEqual([...efficiency.querySelectorAll('[data-improvement-attempt]')].map(n=>n.dataset.improvementAttempt),['2','6']);
  assert.deepEqual([...cost.querySelectorAll('[data-improvement-attempt]')].map(n=>n.dataset.improvementAttempt),['2','5']);
  assert.equal(progress.querySelectorAll('[data-inherited-boundary="4"]').length,2);
  const path=efficiency.querySelector('path').getAttribute('d');
  assert.match(path,/^M218\.86,/,'First feasible result starts at attempt2, not zero');
  assert.equal((path.match(/H/g)||[]).length,5,'Failed and infeasible attempts retain horizontal plateaus');
  assert.doesNotMatch(path,/[LCQ]/,'No smoothing or diagonal interpolation');
  assert.equal([...efficiency.querySelectorAll('[data-attempt-tick]')].at(-1).textContent,'7','No unspent-budget projection');
  assert.deepEqual([...cost.querySelectorAll('tbody tr')].map(r=>[...r.children].map(c=>c.textContent)),[['2','100 报价币种','#2'],['5','80 报价币种','#5']]);
  assert.match(ui.panel.textContent,/当前最佳 #6/);assert.match(ui.panel.textContent,/90 报价币种/,'Complete-design comparison keeps primary-best cost');
  assert.equal(JSON.stringify(job),before,'Rendering cannot rewrite the saved task');
  ui.find('#objective-progress button','打开此目标最佳 #5').click();
  await until(()=>ui.doc.querySelector('#current-design').textContent==='cost-best','Secondary extreme snapshot opened');
  assert.ok(ui.calls.some(c=>c.url.pathname==='/api/v1/calculations/cost-best'));
  assert.ok(!ui.calls.some(c=>c.method!=='GET'),'Inspecting progress must not spend budget');
});

test('objective progress handles a zero-valued constant legacy root without inventing later improvements',async t=>{
  const job=benefitJob([{calculation_id:'a',objective:0},{calculation_id:'b',objective:0}],{objective:'material_cost',current:'a'});
  delete job.request.continuation;
  const ui=await benefitApp(t,job),progress=ui.doc.querySelector('#objective-progress');
  assert.match(progress.textContent,/当前最佳 0 报价币种 · 方案 #1/);
  assert.equal(progress.querySelectorAll('[data-improvement-attempt]').length,1);
  assert.equal(progress.querySelector('[data-inherited-boundary]'),null);
  assert.match(progress.textContent,/最近 1 次评估/);
  assert.doesNotMatch(progress.innerHTML,/NaN|Infinity/);
  assert.ok(progress.querySelector('path').getAttribute('d').includes('H536.00'));
});

test('objective progress suppresses incomplete histories and missing feasible objective values',async t=>{
  for(const defect of ['partial','duplicate ordinal','missing objective','missing snapshot'])await t.test(defect,async t=>{
    const job=benefitJob([
      {calculation_id:'old',objective:.9,objectives:{efficiency:.9,cycle_energy:100}},
      {calculation_id:'new',objective:.91,objectives:{efficiency:.91,cycle_energy:90}},
    ],{secondary:'cycle_energy',inherited:1});
    if(defect==='partial')job.evaluated=3;
    if(defect==='duplicate ordinal')job.candidates[1].ordinal=1;
    if(defect==='missing objective')job.candidates[1].objectives.cycle_energy=null;
    if(defect==='missing snapshot')job.candidates[1].calculation_id=null;
    const ui=await benefitApp(t,job),progress=ui.doc.querySelector('#objective-progress');
    if(defect==='missing objective'){
      assert.ok(progress.querySelector('[data-objective=efficiency] svg'));
      assert.equal(progress.querySelector('[data-objective=cycle_energy] svg'),null);
      assert.match(progress.querySelector('[data-objective=cycle_energy]').textContent,/缺少已保存的有效目标值/);
    }else assert.equal(progress.querySelector('svg'),null);
    assert.doesNotMatch(progress.innerHTML,/NaN|Infinity/);
  });
});

test('objective progress does not draw before a first feasible result or for an empty queue',async t=>{
  for(const empty of [false,true])await t.test(empty?'queued':'none feasible',async t=>{
    const job=benefitJob(empty?[]:[{feasible:false,error:'失败'},{calculation_id:'bad',feasible:false,objective:.99,violations:['超限']}],{status:empty?'queued':'succeeded',current:null,prior:null});
    delete job.request.continuation;
    const ui=await benefitApp(t,job),progress=ui.doc.querySelector('#objective-progress');
    assert.equal(progress.querySelector('svg'),null);assert.equal(progress.querySelector('button'),null);
    assert.match(progress.textContent,empty?/尚无已完成候选/:/尚无满足约束的已算方案/);
  });
});

test('objective progress preserves cycle energy and efficiency units',async t=>{
  const job=benefitJob([
    {calculation_id:'old',objective:120,objectives:{cycle_energy:120,cycle_efficiency:.85}},
    {calculation_id:'new',objective:100,objectives:{cycle_energy:100,cycle_efficiency:.9}},
  ],{objective:'cycle_energy',secondary:'cycle_efficiency',inherited:1});
  const ui=await benefitApp(t,job),progress=ui.doc.querySelector('#objective-progress');
  assert.match(progress.querySelector('[data-objective=cycle_energy]').textContent,/当前最佳 100 kWh/);
  assert.match(progress.querySelector('[data-objective=cycle_efficiency]').textContent,/当前最佳 90 %/);
  assert.doesNotMatch(progress.innerHTML,/NaN|Infinity/);
});

test('failed verification retains all scenarios for a new search with explicit budget',async t=>{
  const source={id:'failed-check',name:'复核失败',status:'failed',evaluated:11,budget:11,best_id:null,
    request:{seed:42,objective:'efficiency',variables:[{key:'L',values:[133,140]}],parameters:{...parameters,L:133},tolerances:[{key:'g',delta:.04}],constraints:[],
      verification:{source_job_id:'root',source_calculation_id:'base',source_candidate_ordinal:2,seed:43,prior_samples:3,interior_samples:8,plan:Array.from({length:11},(_,index)=>({index}))}},
    samples:Array.from({length:11},(_,index)=>({index,parameters:{},label:'情景'})),
    candidates:[{ordinal:1,parameters:{L:133},feasible:false,violations:['计算失败']}],continuations:[]};
  const child={...source,id:'continued',name:'纳入情景寻优',status:'queued',evaluated:0,budget:33,candidates:[],samples:[],
    request:{...source.request,verification:undefined,tolerance_interior_samples:4,feedback:{source_job_id:source.id,retained_samples:11,source_failed_samples:4}}};
  let posted,count=0;const pending=deferred();
  const ui=await app(t,(path,req)=>{
    if(path==='/optimization/config')return {method:'测试',variables:[{key:'L',label:'长度'}],objectives:[{id:'efficiency',label:'效率'}],materials:[],constraint_metrics:[]};
    if(path==='/optimization/jobs')return {items:[source]};
    if(path==='/optimization/jobs/failed-check')return source;
    if(path==='/optimization/jobs/failed-check/resume-search'){posted=req.body;count++;source.continuations=[child];return pending.promise.then(()=>child);}
    if(path==='/optimization/jobs/continued')return child;
  });
  ui.nav('optimization');await until(()=>ui.doc.querySelector('#optimization-jobs button'),'Jobs ready');
  ui.find('#optimization-jobs button','查看任务').click();await until(()=>ui.doc.querySelector('#feedback-form'),'Feedback form ready');
  const d=ui.doc,form=d.querySelector('#feedback-form');
  assert.equal(d.querySelector('#verification-form'),null);
  assert.equal(d.querySelector('#continuation-form'),null);
  assert.equal(d.querySelector('#feedback-budget').value,'22');assert.equal(d.querySelector('#feedback-seed').value,'44');
  d.querySelector('#feedback-budget').value='33';d.querySelector('#feedback-budget').dispatchEvent(new ui.w.Event('input'));
  assert.match(form.textContent,/3 个完整候选/);
  form.dispatchEvent(new ui.w.Event('submit',{cancelable:true}));form.dispatchEvent(new ui.w.Event('submit',{cancelable:true}));
  await until(()=>posted,'Request sent');assert.equal(count,1);assert.deepEqual(posted,{max_evaluations:33,seed:44});
  pending.release();await until(()=>d.querySelector('#optimization-detail').textContent.includes('复核情景参与搜索'),'New search displayed');
  assert.match(d.querySelector('#optimization-detail').textContent,/全部 11 个相对偏差/);
  assert.doesNotMatch(d.querySelector('#optimization-detail').textContent,/区间内部采样：每组增加/);
  ui.find('#optimization-detail button','查看源复核').click();await until(()=>d.querySelector('#feedback-form'),'Source restored');
  assert.match(d.querySelector('#optimization-detail').textContent,/后续搜索/);
});
