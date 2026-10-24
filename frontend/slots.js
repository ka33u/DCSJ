'use strict';
(() => {
  const $=id=>document.getElementById(id),api=window.SlotPicker.api;
  const state={side:'stator',offset:0,total:0,limit:30,item:null,schema:null,sequence:0,dirty:false,busy:false,module:'main'};
  const geometryLabels={stator_outer_mm:'定子外径',bore_mm:'定子内径',gap_mm:'单边气隙',rotor_outer_mm:'转子外径',hr0_mm:'转子槽口高度 hr0'};
  const n=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const notice=(text,error=false)=>{$('feedback').textContent=text;$('feedback').className=error?'slot-error':'slot-state';};
  function discard(){return !state.dirty||confirm('当前槽型有未保存的修改，仍要切换吗？');}
  async function task(fn){if(state.busy)return;state.busy=true;const controls=[...document.querySelectorAll('button,input,select,textarea')];const before=controls.map(el=>el.disabled);controls.forEach(el=>el.disabled=true);try{await fn();}catch(e){notice(e.message,true);}finally{controls.forEach((el,i)=>el.disabled=before[i]);state.busy=false;configureActions();}}
  function configureActions(){const e=state.item;const user=e?.origin==='user'&&e.id;$('save').textContent=user?'保存新版本':'保存到用户槽型库';$('save').disabled=!!e?.archived;$('archive').hidden=$('versions').hidden=!user;$('archive').textContent=e?.archived?'恢复':'归档';$('copy').hidden=!e?.id;$('previous').disabled=state.offset===0;$('next').disabled=state.offset+state.limit>=state.total;}
  async function load(){
    const sequence=++state.sequence;const data=await api(`/slots?side=${state.side}&source=${$('origin').value}&q=${encodeURIComponent($('query').value.trim())}&include_archived=${!!$('archived').value}&limit=${state.limit}&offset=${state.offset}`);
    if(sequence!==state.sequence)return;state.total=data.total;
    $('slot-list').replaceChildren();$('count').textContent=data.total+' 个';$('page-label').textContent=data.total?`${state.offset+1}–${Math.min(state.offset+state.limit,data.total)}`:'0';
    $('previous').disabled=state.offset===0;$('next').disabled=state.offset+state.limit>=data.total;
    for(const item of data.items){const b=n('button');b.type='button';b.classList.toggle('active',item.reference===state.item?.reference);b.append(n('strong',item.name),n('span',item.document.drawing||'未填写图号'),n('small',`${item.origin==='user'?'用户 · v'+item.revision:'私有参考'} · ${Object.keys(item.document.parameters).length?'含 '+Object.keys(item.document.parameters).length+' 项尺寸':'仅图号参考'}${item.archived?' · 已归档':''}`));b.onclick=()=>{if(!state.busy&&discard())fill(item);};$('slot-list').append(b);}
    if(!data.total)$('slot-list').append(n('p','没有匹配的槽型。可导入私有资源或新增用户槽型。'));
  }
  function dimensionFields(values={}){
    const module=$('module').value;state.module=module;$('dimension-fields').replaceChildren();
    const fields=state.schema.fields[module]?.[state.side]||[];
    if(!fields.length){$('dimension-fields').append(n('p','参考资料未包含详细槽尺寸。选择计算模块后可逐项补全，保存为用户槽型。'));return;}
    for(const f of fields){const label=n('label');label.className='slot-field';label.append(n('span',f.key+' · '+f.label+(f.unit?' / '+f.unit:'')));const input=n('input');input.type='number';input.step='any';input.dataset.dimension=f.key;input.value=values[f.key]??'';label.append(input);$('dimension-fields').append(label);}
  }
  function fill(item){
    state.item=item;state.dirty=false;const d=item.document;state.side=d.side;
    document.querySelectorAll('[data-side]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.side===state.side)));
    $('empty').hidden=true;$('slot-editor').hidden=false;$('slot-name').value=item.name;$('drawing').value=d.drawing||'';$('source').value=d.source||'手工录入';$('notes').value=d.notes||'';$('reference-area').value=d.reference_area_mm2??'';$('module').value=d.module;
    $('editor-title').textContent=(state.side==='stator'?'定子':'转子')+'槽型资料';$('entry-kind').textContent=item.origin==='resource'?'私有参考 · 保存时创建用户副本':item.id?'用户槽型 · v'+item.revision:'新增用户槽型';$('entry-version').textContent=item.reference||'';
    dimensionFields(d.parameters);$('geometry-fields').replaceChildren();for(const [key,label] of Object.entries(geometryLabels)){const wrap=n('label');wrap.className='slot-field';const input=n('input');input.type='number';input.step='any';input.min=key==='hr0_mm'?'0':'0.000001';input.dataset.geometry=key;input.value=d.geometry?.[key]??'';wrap.append(n('span',label),input);$('geometry-fields').append(wrap);}
    $('raw').textContent=JSON.stringify(item,null,2);$('history').replaceChildren();$('slot-area-preview').textContent='';
    const table=n('table'),head=n('tr');for(const label of ['系列','机座','极数','kW','铁心长 mm','定子内径 mm','气隙 mm','转子外径 mm','硅片','来源'])head.append(n('th',label));table.append(head);
    for(const a of d.applications||[]){const row=n('tr');for(const value of [a.series,a.frame,a.poles,a.power,a.stack_mm,a.geometry.bore_mm,a.geometry.gap_mm,a.geometry.rotor_outer_mm,a.material,a.source])row.append(n('td',value??'—'));table.append(row);}$('applications').replaceChildren(table);if(!d.applications?.length)$('applications').textContent='暂无适用规格；可在备注中记录使用范围。';
    configureActions();notice('');
  }
  function read(){const parameters={},geometry={};for(const input of document.querySelectorAll('[data-dimension]'))if(input.value!=='')parameters[input.dataset.dimension]=Number(input.value);for(const input of document.querySelectorAll('[data-geometry]'))geometry[input.dataset.geometry]=input.value===''?null:Number(input.value);return {name:$('slot-name').value.trim(),document:{side:state.side,module:$('module').value,drawing:$('drawing').value.trim(),source:$('source').value.trim(),notes:$('notes').value,reference_area_mm2:$('reference-area').value===''?null:Number($('reference-area').value),parameters,geometry,applications:state.item?.document.applications||[]}};}
  async function save(copy=false){
    const body=read();const updating=state.item?.origin==='user'&&state.item.id&&!copy;
    if(updating)body.expected_revision=state.item.revision;
    const saved=await api('/library/slots'+(updating?'/'+state.item.id:''),body,updating?'PUT':'POST');fill({...saved,origin:'user'});await load();notice(`已保存「${saved.name}」第 ${saved.revision} 版。`);
  }
  $('slot-editor').oninput=()=>{state.dirty=true;$('slot-area-preview').replaceChildren();};
  $('preview').onclick=()=>task(async()=>{
    const doc=read().document;if(doc.module==='reference'){notice('参考资料尚未含详细尺寸，选择计算模块并补全尺寸后可预览。');return;}
    const preview=await api('/slots/preview',{module:doc.module,parameters:doc.parameters});
    const entry=preview.entries.find(e=>e.side===state.side);const area=entry.area;
    $('slot-area-preview').replaceChildren(n('p',area.value_mm2==null?'当前参数暂不能计算面积。TX 面积在运行电磁计算后显示。':`${area.label}：${area.value_mm2.toFixed(3)} mm² · ${area.note}`));
    const drawing=preview.drawings?.find(d=>d.side===state.side);if(drawing){const figure=n('div');figure.style.maxWidth='360px';figure.innerHTML=drawing.svg;$('slot-area-preview').append(figure);}
  });
  $('slot-editor').onsubmit=e=>{e.preventDefault();task(()=>save());};$('copy').onclick=()=>{if($('slot-editor').reportValidity())task(()=>save(true));};
  $('module').onchange=()=>{const previous=state.module;const has=[...document.querySelectorAll('[data-dimension]')].some(i=>i.value!=='');if(has&&!confirm('切换计算模块将清空当前槽尺寸；主程序与 TX 的单位和槽型代码分别维护。')){$('module').value=previous;return;}dimensionFields();state.dirty=true;};
  $('slot-search').onsubmit=e=>{e.preventDefault();state.offset=0;task(load);};$('origin').onchange=$('archived').onchange=()=>{state.offset=0;task(load);};
  $('previous').onclick=()=>{state.offset=Math.max(0,state.offset-state.limit);task(load);};$('next').onclick=()=>{state.offset+=state.limit;task(load);};
  document.querySelectorAll('[data-side]').forEach(el=>el.onclick=()=>{if(state.busy||!discard())return;state.side=el.dataset.side;state.offset=0;state.item=null;state.dirty=false;$('slot-editor').hidden=true;$('empty').hidden=false;document.querySelectorAll('[data-side]').forEach(b=>b.setAttribute('aria-pressed',String(b===el)));task(load);});
  $('new-slot').onclick=()=>{if(discard())fill({name:'',origin:'user',document:{side:state.side,module:'main',parameters:{},geometry:{},applications:[],source:'手工录入',notes:''}});};
  $('archive').onclick=()=>{if(!discard())return;task(async()=>{const item=state.item;const result=await api(`/library/slots/${item.id}/archive`,{expected_revision:item.revision,archived:!item.archived});fill({...result,origin:'user'});await load();notice(result.archived?'已归档，历史设计的引用继续有效。':'已恢复。');});};
  $('versions').onclick=()=>task(async()=>{const data=await api(`/library/slots/${state.item.id}/revisions`);$('history').replaceChildren();for(const v of data.items){const b=n('button',`v${v.revision} · ${new Date(v.created_at).toLocaleString('zh-CN')}`);b.type='button';b.className='slot-button';b.onclick=()=>{if(!discard())return;task(async()=>fill({...await api(`/library/slots/${state.item.id}?revision=${v.revision}`),origin:'user'}));};$('history').append(b);}});
  window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
  task(async()=>{await window.DCSJAuth.ready;window.DCSJAuth.beforeLogout=()=>{if(state.busy||!discard())return false;state.dirty=false;return true;};state.schema=await api('/slots/schema');await load();});
})();
