'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const state={slotBindings:{},schema:null,parameters:null,base:null,design:null,revision:null,changed:false,result:null,offset:0,total:0,query:'',searchSequence:0,resource:null,busy:false,group:'额定参数',fieldViews:[],groups:[],invalidField:null};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num=(v,d=3)=>Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});
  function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);$('notice').hidden=!text;}
  async function api(path,body,method){const response=await fetch('/api/v1/tx'+path,{method:method||(body?'POST':'GET'),headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));return data;}
  function busy(value){state.busy=value;seriesPicker.disable(value);for(const id of ['calculate','save-design','save-copy','import-input','design-author'])$(id).disabled=value;for(const el of $('parameters').querySelectorAll('input'))el.disabled=value;if(!value&&state.invalidField){state.invalidField.focus();state.invalidField=null;}}
  async function task(fn){if(state.busy)return;busy(true);try{await fn();}catch(e){notice(e.message,true);}finally{busy(false);}}
  function changed(){state.changed=true;slotPicker?.update();if(state.result)$('result-status').textContent='参数已修改；下方保留上次计算快照，请重新计算。';}
  function discard(){return !state.changed||confirm('当前参数尚未保存为设计版本，确定载入其他方案吗？');}
  function fieldLabel(field){
    const coil=/^(NDXP|YDXP)\((\d+)\)$/.exec(field.key);
    return coil?`第 ${coil[2]} 组${coil[1]==='NDXP'?'匝数':'节距'}`:field.label||field.key;
  }
  function input(field,index){
    const label=document.createElement('label');label.className='parameter-field';label.dataset.key=field.key;
    if(field.type==='text')label.classList.add('wide');
    const title=document.createElement('span');title.className='field-caption';
    const text=document.createElement('strong');text.textContent=fieldLabel(field);
    const small=document.createElement('small');small.textContent=field.key+(field.unit?' · '+field.unit:'');
    title.append(text,small);
    const el=document.createElement('input');el.id='tx-parameter-'+index;label.htmlFor=el.id;el.name=field.key;
    el.type=field.type==='number'?'number':'text';el.setAttribute('aria-label',fieldLabel(field));
    if(el.type==='number')el.step=field.key.startsWith('NDXP')?'0.5':field.key.startsWith('YDXP')?'1':'any';
    else el.maxLength=field.key==='TYP'?120:2000;
    if(field.group==='线圈组')el.min='0';
    el.addEventListener('input',()=>{el.removeAttribute('aria-invalid');changed();});
    label.append(title,el);return {field,label,input:el,home:null};
  }
  function selectGroup(name,focus=false){
    state.group=name;$('parameter-search').value='';renderParameters();
    if(focus)state.groups.find(group=>group.name===name)?.tab.focus();
  }
  function buildForm(){
    const host=$('parameters'),tabs=$('parameter-tabs');host.replaceChildren();tabs.replaceChildren();
    state.fieldViews=state.schema.fields.map(input);state.groups=[];
    const order=['额定参数','线圈组','导线与绕组','铁心与槽型','计算系数'];
    for(const field of state.schema.fields)if(!order.includes(field.group))order.push(field.group);
    for(const name of order){
      const views=state.fieldViews.filter(view=>view.field.group===name);if(!views.length)continue;
      const index=state.groups.length,tab=document.createElement('button'),panel=document.createElement('section');
      tab.type='button';tab.className='parameter-tab';tab.id='tx-tab-'+index;tab.setAttribute('role','tab');
      tab.setAttribute('aria-controls','tx-panel-'+index);tab.append(document.createTextNode(name));
      const count=document.createElement('span');count.textContent=views.length;count.className='tab-count';tab.append(count);
      tab.addEventListener('click',()=>selectGroup(name));tabs.append(tab);
      panel.id='tx-panel-'+index;panel.className='parameter-panel';panel.setAttribute('role','tabpanel');
      panel.setAttribute('aria-labelledby',tab.id);panel.tabIndex=0;
      const heading=document.createElement('h3');heading.textContent=name;heading.className='group-heading';panel.append(heading);
      const grid=document.createElement('div');grid.className=name==='线圈组'?'coils-input':'fields';
      if(name==='线圈组'){
        const headings=[['线圈组',1,1],...Array.from({length:6},(_,i)=>[String(i+1),1,i+2]),['匝数',2,1],['节距 / 槽',3,1]];
        for(const [text,row,column] of headings){const node=document.createElement('strong');node.textContent=text;node.style.gridRow=row;node.style.gridColumn=column;grid.append(node);}
        for(const view of views){const match=/^(NDXP|YDXP)\((\d+)\)$/.exec(view.field.key);if(match){view.label.classList.add(match[1]==='NDXP'?'coil-turns':'coil-pitch');view.label.style.setProperty('--coil-column',Number(match[2])+1);}}
      }
      for(const view of views){view.home=grid;grid.append(view.label);}
      panel.append(grid);
      if(name==='线圈组'){
        const help=document.createElement('p');help.className='group-help';
        help.textContent='从第 1 组连续填写，不用的组填 0。匝数支持半匝，节距单位为槽。';panel.append(help);
      }
      host.append(panel);state.groups.push({name,tab,panel,views});
    }
    const searchTab=document.createElement('button');searchTab.id='parameter-search-tab';searchTab.type='button';
    searchTab.className='parameter-tab';searchTab.setAttribute('role','tab');searchTab.setAttribute('aria-controls','parameter-search-results');
    tabs.append(searchTab);
    const results=document.createElement('section');results.id='parameter-search-results';results.className='parameter-panel';
    results.setAttribute('role','tabpanel');results.setAttribute('aria-labelledby',searchTab.id);results.tabIndex=0;host.append(results);
    tabs.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      const buttons=[...tabs.querySelectorAll('[role="tab"]')].filter(tab=>!tab.hidden);
      const current=buttons.indexOf(document.activeElement);if(current<0)return;event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(current+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
      buttons[next].click();buttons[next].focus();
    });
    if(!state.groups.some(group=>group.name===state.group))state.group=state.groups[0]?.name;
    renderParameters();
  }
  function renderParameters(){
    const query=$('parameter-search').value.trim().normalize('NFKC').toLocaleLowerCase();
    const tokens=query.split(/\s+/).filter(Boolean),results=$('parameter-search-results');
    // Move existing controls, never clone or recreate them: edits and validity survive tab/search changes.
    for(const view of state.fieldViews)view.home.append(view.label);
    results.replaceChildren();
    const matches=state.fieldViews.filter(({field})=>{
      const text=[fieldLabel(field),field.key,field.unit,field.group].join(' ').normalize('NFKC').toLocaleLowerCase();
      return tokens.every(token=>text.includes(token));
    });
    for(const group of state.groups){
      const active=!query&&group.name===state.group;
      group.tab.setAttribute('aria-selected',String(active));group.tab.tabIndex=active?0:-1;group.panel.hidden=!active;
      if(query){
        const views=matches.filter(view=>view.field.group===group.name);if(!views.length)continue;
        const section=document.createElement('section'),heading=document.createElement('h3'),grid=document.createElement('div');
        section.className='search-group';heading.className='group-heading';heading.textContent=group.name+' · '+views.length+' 项';grid.className='fields';
        for(const view of views)grid.append(view.label);section.append(heading,grid);results.append(section);
      }
    }
    const searchTab=$('parameter-search-tab');searchTab.hidden=!query;searchTab.textContent='搜索结果 · '+matches.length;
    searchTab.setAttribute('aria-selected',String(!!query));searchTab.tabIndex=query?0:-1;results.hidden=!query;
    $('clear-parameter-search').hidden=!$('parameter-search').value;
    $('parameter-count').textContent=query?`跨组搜索 · 找到 ${matches.length} / ${state.fieldViews.length} 项`:
      `${state.group} · ${state.groups.find(group=>group.name===state.group)?.views.length||0} 项 / 共 ${state.fieldViews.length} 项`;
    if(query&&!matches.length){
      const empty=document.createElement('p');empty.className='parameter-empty';empty.textContent='没有匹配的参数。可尝试中文名称、参数符号或单位，或清除搜索。';results.append(empty);
    }
  }
  function invalidParameter(field,message){
    selectGroup(field.group);const input=$('parameters').elements.namedItem(field.key);
    input.setAttribute('aria-invalid','true');state.invalidField=input;
    throw new Error(message);
  }
  function read(){const p={};for(const f of state.schema.fields){const el=$('parameters').elements.namedItem(f.key);if(f.type==='text')p[f.key]=el.value.trim();else{if(el.value.trim()==='')invalidParameter(f,'请填写 '+fieldLabel(f)+'（'+f.key+'）');p[f.key]=Number(el.value);if(!Number.isFinite(p[f.key]))invalidParameter(f,f.key+' 数值无效');}}return p;}
  function fill(p,{base=null,design=null,revision=null,author='',slot_bindings={},series='',source='手工输入',changed:edited=false}={}){series=window.SeriesPicker.validate(series);for(const f of state.schema.fields)$('parameters').elements.namedItem(f.key).value=p[f.key]??'';$('design-author').value=window.DCSJAuth.username();seriesPicker.set(series);state.parameters=p;state.slotBindings=slot_bindings||{};slotPicker?.update();state.base=base;state.design=design;state.revision=revision;state.changed=edited;$('design-title').textContent=p.TYP||'设计参数';$('source').textContent=source;$('revision').textContent=revision?'用户设计 · v'+revision:'独立方案';state.result=null;$('results').hidden=true;for(const view of state.fieldViews)view.input.removeAttribute('aria-invalid');selectGroup('额定参数');for(const el of $('models').querySelectorAll('button'))el.classList.toggle('active',el.dataset.id===base);}
  async function loadModel(id,ask=true){if(ask&&!discard())return;const m=await api('/models/'+encodeURIComponent(id));if(m.kind==='saved_design'){applyDesign(m.design);notice('');return;}fill(m.parameters,{base:m.id,series:m.series,source:`基础库 · ${m.source.sheet} / 第 ${m.source.row} 行 · 资源 ${m.resource_version}`});notice(m.verified_execution?'':'这条原始记录在批量试算中未完成计算，修改参数后再试算。',!m.verified_execution);}
  async function loadSeries(){
    try{const data=await api('/series'),filter=$('series-filter'),value=filter.value;
      filter.replaceChildren(new Option('全部系列',''),...[...new Set([...data.items,value].filter(Boolean))].map(s=>new Option(s,s)));filter.value=value;seriesPicker.options(data.items);
    }catch(e){notice('系列列表读取失败，仍可选择未分类或新增系列：'+e.message,true);}
  }
  $('series-filter').onchange=()=>{state.offset=0;task(search);};
  async function search(){const sequence=++state.searchSequence;const data=await api(`/models?q=${encodeURIComponent(state.query)}&source=${state.resource?.available?$('catalog-source').value:'saved'}&series=${encodeURIComponent($('series-filter').value)}&limit=60&offset=${state.offset}`);if(sequence!==state.searchSequence)return;state.total=data.total;$('model-count').textContent=data.total+' 个版本';$('models').replaceChildren();for(const m of data.items){const button=document.createElement('button');button.type='button';button.className='model';button.dataset.id=m.id;button.classList.toggle('active',m.id===state.base);button.innerHTML=`<strong>${esc(m.name)}</strong><span>${esc(m.series||'未分类')} · ${num(m.power_kw)} kW · ${m.poles} 极 · ${m.kind==='saved_design'?`已保存设计 · v${m.revision} · ${esc(m.author||'未署名')}`:`${esc(m.source.sheet)}:${esc(m.source.row)}${m.verified_execution?'':' · 待检查'}`}</span>`;button.addEventListener('click',()=>task(()=>loadModel(m.id)));$('models').append(button);}if(!data.items.length)$('models').textContent='没有匹配的型号。';$('paging-label').textContent=data.total?`${state.offset+1}–${Math.min(state.offset+60,data.total)} / ${data.total}`:'0';$('previous').disabled=state.offset===0;$('next').disabled=state.offset+60>=data.total;}
  const table=(headers,rows)=>'<table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
  function coilDiagram(harmonics){const active=harmonics.filter(h=>h.turns>0),max=Math.max(...active.map(h=>h.pitch));const colors=['#126b7a','#4254bb','#a55f25','#884d98','#27774d','#a44455'];let out='<svg viewBox="0 0 540 210" role="img" aria-label="同心线圈节距示意">';active.forEach((h,i)=>{const span=h.pitch/max*300,left=(540-span)/2,top=20+i*13,bottom=180-i*9,color=colors[i];out+=`<path d="M ${left} 100 V ${top+16} Q ${left} ${top} ${left+16} ${top} H ${left+span-16} Q ${left+span} ${top} ${left+span} ${top+16} V ${bottom-16} Q ${left+span} ${bottom} ${left+span-16} ${bottom} H ${left+16} Q ${left} ${bottom} ${left} ${bottom-16} Z" fill="none" stroke="${color}" stroke-width="2"/><text x="5" y="${25+i*26}" fill="${color}" font-size="12">${i+1}组 · ${h.turns}匝</text>`;});return out+'</svg>';}
  function harmonicChart(harmonics){const rows=harmonics.slice(1),limit=Math.max(.1,...rows.map(h=>Math.abs(h.relative_mmf_percent)));let svg='<svg viewBox="0 0 500 210" role="img" aria-label="各次谐波相对磁势柱图"><path d="M 35 102 H 480" stroke="#bccfd5"/>';rows.forEach((h,i)=>{const x=65+i*85,height=Math.abs(h.relative_mmf_percent)/limit*63,y=h.relative_mmf_percent>=0?102-height:102;svg+=`<rect x="${x}" y="${y}" width="34" height="${Math.max(height,.5)}" rx="2" fill="#357e94"/><text x="${x+17}" y="${h.relative_mmf_percent>=0?y-8:y+height+17}" text-anchor="middle" fill="#315b68" font-size="12">${num(h.relative_mmf_percent,4)}%</text><text x="${x+17}" y="198" text-anchor="middle" fill="#708a94" font-size="12">${h.order} 次</text>`;});return svg+'</svg>';}
  function render(record){state.result=record;if(window.SlotPicker)$('slot-result').replaceChildren(window.SlotPicker.summary(record.result.slots));const r=record.result,m=r.metrics;$('results').hidden=false;$('result-name').textContent=record.name;$('result-status').textContent=`${new Date(record.created_at).toLocaleString('zh-CN')} · 资源 ${r.resource_version} · 计算快照`;$('report-link').href='/tx/reports/'+encodeURIComponent(record.id);$('legacy-report-link').href='/tx/reports/'+encodeURIComponent(record.id)+'?layout=legacy';const cards=[['效率',m.efficiency*100,'%'],['功率因数',m.power_factor,''],['最大转矩',m.maximum_torque_ratio,'倍'],['启动转矩',m.starting_torque_ratio,'倍'],['启动电流',m.starting_current_ratio,'倍'],['转速',m.speed_rpm,'r/min']];$('metric-cards').innerHTML=cards.map(([label,v,unit])=>`<div class="metric"><small>${label}</small><strong>${num(v,label==='功率因数'?4:2)}<em>${unit}</em></strong></div>`).join('');$('coil-diagram').innerHTML=coilDiagram(r.harmonics);$('harmonic-chart').innerHTML=harmonicChart(r.harmonics);$('harmonic-table').innerHTML=table(['次数','KDP','FV / %','匝数','节距'],r.harmonics.map(h=>[h.order,num(h.winding_factor,6),num(h.relative_mmf_percent,6),h.turns,h.pitch]));$('coil-table').innerHTML=table(['组','TY / cm','R / cm','端角 / rad','H / cm'],r.coils.map(c=>[c.index,c.span_cm,c.radius_cm,c.end_angle_rad,c.height_cm]));$('raw-report').textContent=r.report_text;const lossKeys=[['iron_loss_w','铁损','W'],['stator_copper_loss_w','定子铜损','W'],['rotor_copper_loss_w','转子铜损','W'],['total_loss_w','总损耗','W'],['current_a','定子电流','A'],['copper_mass_kg','铜重','kg'],['iron_mass_kg','铁重','kg'],['mean_turn_length_mm','平均匝长','mm']];$('losses').innerHTML=lossKeys.map(([key,label,unit])=>`<div><small>${label}</small>${num(r.details[key])} ${unit}</div>`).join('');}
  async function save(copy=false){const parameters=read(),body={parameters,series:seriesPicker.value(),slot_bindings:state.slotBindings,author:window.DCSJAuth.username(),base_model_id:state.base};const updating=state.design&&!copy;if(updating)body.expected_revision=state.revision;const d=await api('/designs'+(updating?'/'+encodeURIComponent(state.design):''),body,updating?'PUT':'POST');$('design-author').value=window.DCSJAuth.username();state.design=d.id;state.revision=d.revision;state.changed=false;$('revision').textContent='用户设计 · v'+d.revision;notice(`已保存「${d.name}」第 ${d.revision} 版，可在型号库按名称、系列或作者检索。`);state.offset=0;await loadSeries();await search();}
  function applyDesign(d){fill(d.parameters,{series:d.series,slot_bindings:d.slot_bindings,design:d.id,revision:d.revision,author:d.author||'',base:d.source.resource_version===state.resource?.resource_version?d.source.model_id:null,source:'用户设计 · 第 '+d.revision+' 版 · '+(d.author||'未署名')+(d.source.name?' · 源自 '+d.source.name:'')});}
  async function openDesign(id,revision){if(!discard())return;const d=await api('/designs/'+encodeURIComponent(id)+(revision?'?revision='+revision:''));applyDesign(d);$('records-dialog').close();notice('');}
  async function records(kind){const data=await api('/'+kind);$('records-title').textContent=kind==='designs'?'我的 TX 设计':'TX 计算记录';$('records-list').replaceChildren();for(const item of data.items){const row=document.createElement('div');row.className='record';const label=document.createElement('span');label.innerHTML=esc(item.name)+`<small>${esc(new Date(item.updated_at||item.created_at).toLocaleString('zh-CN'))}${item.revision?' · '+esc(item.series||'未分类')+' · v'+item.revision+' · '+esc(item.author||'未署名'):''}</small>`;row.append(label);if(kind==='designs'){const version=document.createElement('select');version.setAttribute('aria-label','设计版本');for(let i=item.revision;i>=1;i--){const op=document.createElement('option');op.value=i;op.textContent='v'+i;version.append(op);}row.append(version);const open=document.createElement('button');open.textContent='载入';open.onclick=()=>task(()=>openDesign(item.id,Number(version.value)));row.append(open);}else{const open=document.createElement('button');open.textContent='查看快照';open.onclick=()=>task(async()=>{const r=await api('/calculations/'+encodeURIComponent(item.id));render(r);if(state.changed||!state.parameters||JSON.stringify(read())!==JSON.stringify(r.parameters))$('result-status').textContent='历史计算快照 · '+new Date(r.created_at).toLocaleString('zh-CN')+' · 当前参数未被替换';$('records-dialog').close();$('results').scrollIntoView({behavior:'smooth',block:'start'});});const link=document.createElement('a');link.className='button';link.textContent='新版打印';link.target='_blank';link.rel='noopener';link.href='/tx/reports/'+encodeURIComponent(item.id);const legacy=link.cloneNode();legacy.textContent='旧版打印';legacy.href=link.href+'?layout=legacy';row.append(open,link,legacy);}$('records-list').append(row);}if(!data.items.length)$('records-list').textContent='暂无记录。';$('records-dialog').showModal();}
  $('parameter-search').addEventListener('input',renderParameters);
  $('parameter-search').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();selectGroup(state.group);}});
  $('clear-parameter-search').addEventListener('click',()=>{selectGroup(state.group);$('parameter-search').focus();});
  $('catalog-source').onchange=()=>{state.offset=0;task(search);};
  $('search-form').onsubmit=e=>{e.preventDefault();state.query=$('search').value.trim();state.offset=0;task(search);};$('previous').onclick=()=>task(async()=>{state.offset=Math.max(0,state.offset-60);await search();});$('next').onclick=()=>task(async()=>{state.offset+=60;await search();});$('parameters').onsubmit=e=>e.preventDefault();$('calculate').onclick=()=>task(async()=>{notice('正在计算绕组与电磁性能…');const record=await api('/calculate',{parameters:read(),slot_bindings:state.slotBindings,base_model_id:state.base,persist:true});render(record);notice('计算完成，报告已保存为独立快照。');$('results').scrollIntoView({behavior:'smooth',block:'start'});});$('save-design').onclick=()=>task(()=>save());$('save-copy').onclick=()=>task(()=>save(true));$('designs-open').onclick=()=>task(()=>records('designs'));$('history-open').onclick=()=>task(()=>records('calculations'));$('records-close').onclick=()=>$('records-dialog').close();$('import-input').onclick=()=>$('import-file').click();$('import-file').onchange=()=>task(async()=>{try{const file=$('import-file').files[0];if(!file)return;if(file.size>200000)throw new Error('参数文件不能超过 200 KB');const data=JSON.parse(await file.text());if(data.module&&data.module!=='tx')throw new Error('请选择 TX 模块参数文件');if(data.author!==undefined&&(typeof data.author!=='string'||data.author.length>120))throw new Error('作者署名须为不超过 120 字的文本');const p=data.parameters||data;await api('/validate',{parameters:p});if(!discard())return;fill(p,{series:data.series,slot_bindings:data.slot_bindings,author:data.author||'',source:'导入文件 · '+file.name,changed:true});notice('参数已导入，可继续修改或计算。');}finally{$('import-file').value='';}});$('export-input').onclick=()=>task(async()=>{const parameters=read();const blob=new Blob([JSON.stringify({module:'tx',schema_version:1,series:seriesPicker.value(),slot_bindings:state.slotBindings,author:window.DCSJAuth.username(),parameters},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=(parameters.TYP||'TX').replace(/[<>:"/\\|?*]/g,'_')+'-TX.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});window.addEventListener('beforeunload',e=>{if(state.changed){e.preventDefault();e.returnValue='';}});
  const seriesPicker=window.SeriesPicker.mount($('design-series-control'),{onChange:()=>{state.changed=true;}});
  const slotPicker=window.SlotPicker?.mount($('slot-workbench'),{
    module:'tx',read:()=>{if(!state.parameters)return {};const p={};for(const f of state.schema.fields){const el=$('parameters').elements.namedItem(f.key);if(f.type==='number'&&el.value.trim()==='')throw new Error('请先填写完整参数');p[f.key]=f.type==='number'?Number(el.value):el.value;}return p;},
    bindings:()=>state.slotBindings,busy:()=>state.busy,
    apply:(parameters,binding)=>{for(const f of state.schema.fields)$('parameters').elements.namedItem(f.key).value=parameters[f.key]??'';state.parameters=parameters;state.slotBindings={...state.slotBindings,...binding};changed();notice('槽型已带入，请重新计算。');},
    detach:side=>{delete state.slotBindings[side];changed();},
  });
  task(async()=>{await window.DCSJAuth.ready;window.DCSJAuth.beforeLogout=()=>{if(state.busy){notice('请等待当前操作完成后退出登录。');return false;}if(state.changed&&!confirm('当前设计尚未保存，确定退出登录？'))return false;state.changed=false;return true;};await loadSeries();state.schema=await api('/schema');buildForm();busy(true);state.resource=await api('/status');if(!state.resource.available){$('resource-badge').textContent='TX 资源未安装';notice(state.resource.message,true);$('catalog-source').value='saved';$('catalog-source').disabled=true;await search();return;}$('resource-badge').textContent='资源 '+state.resource.resource_version;await search();await loadModel(state.resource.default_id,false);});
})();
