/* Shared slot library picker. Native main/TX parameters are never mixed. */
'use strict';
window.SlotPicker = (() => {
  const names={stator:'定子',rotor:'转子'}, statuses={matched:'已带入所存尺寸',modified:'尺寸已修改',reference_only:'仅图号参考',manual:'手工参数'};
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const number=v=>v==null?'未计算':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:3});
  async function api(path,body,method){
    const response=await fetch('/api/v1'+path,{method:method||(body?'POST':'GET'),headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));return data;
  }
  function action(text,fn){const b=node('button',text,'slot-button');b.type='button';b.onclick=fn;return b;}
  function field(label,input){const l=node('label',undefined,'slot-field');l.append(node('span',label),input);return l;}
  function summary(data){
    const host=node('div',undefined,'slot-summary');
    for(const e of data?.entries||[]){
      const row=node('div',undefined,'slot-summary-row');
      row.append(node('strong',names[e.side]+' · '+e.name),node('span',(e.drawing?'图号 '+e.drawing+' · ':'')+statuses[e.status]));
      const area=e.area;row.append(node('span',`${area.label}：${number(area.value_mm2)}${area.value_mm2==null?'':' mm²'}`));
      if(e.document?.reference_area_mm2!=null)row.append(node('span',`图纸参考面积 ${number(e.document.reference_area_mm2)} mm²`));
      if(area.value_mm2!=null)row.title=area.note;
      host.append(row);
    }
    return host;
  }
  function mount(host,config){
    if(!host)return null;
    let timer,sequence=0;
    const heading=node('div',undefined,'slot-tools');heading.append(node('strong','定转子槽型'));
    const manager=node('a','管理槽型库 ↗');manager.href='/slots';manager.target='_blank';manager.rel='noopener';heading.append(manager);
    const toolbar=node('div',undefined,'slot-tools');
    for(const side of Object.keys(names)){
      toolbar.append(action('选择'+names[side]+'槽型',()=>choose(side)),action('保存当前'+names[side]+'槽型',()=>capture(side)));
    }
    toolbar.append(action('解除槽型关联',()=>{if(config.busy())return;for(const side of Object.keys(names))config.detach(side);update();}));
    const content=node('div'),checks=node('details',undefined,'slot-checks');checks.append(node('summary','气隙 / 外径 / hr0 校核'));
    const checkBody=node('div');checks.append(checkBody);
    host.classList.add('slot-workbench');host.append(heading,toolbar,content,checks);
    const message=node('p',undefined,'slot-message');host.append(message);
    const get=()=>({module:config.module,parameters:config.read(),slot_bindings:config.bindings()});
    const error=e=>{message.textContent=e.message;message.className='slot-message error';};
    async function refresh(){
      const request=++sequence;let body;
      try{body=get();}catch{content.textContent='参数填写完整后显示槽型校核。';return;}
      if(!Object.keys(body.parameters).length){content.textContent='载入设计后可选用或保存槽型。';return;}
      try{
        const data=await api('/slots/preview',body);if(request!==sequence)return;
        content.replaceChildren(summary(data));message.textContent='';
        for(const e of data.entries)if(e.reference){const b=action('解除'+names[e.side]+'槽型关联',()=>{if(config.busy())return;config.detach(e.side);update();});content.append(b);}
        checkBody.replaceChildren();
        for(const check of data.geometry_check.checks){const p=node('p',(check.passed?'✓ ':'需核对 · ')+check.message);p.className=check.passed?'slot-ok':'slot-warning';checkBody.append(p);}
        const mismatches=data.geometry_check.checks.filter(c=>!c.passed).length;
        checks.querySelector('summary').textContent='气隙 / 外径 / hr0 校核'+(mismatches?' · '+mismatches+' 项需核对':'');
        if(!data.geometry_check.hr0_reference_available)checkBody.append(node('p','尚无 hr0 与外径的成套基准，未进行加工联动判定。'));
        const diameter=node('input');diameter.type='number';diameter.step='any';diameter.min='0.000001';diameter.value=data.geometry_check.geometry.rotor_outer_mm??'';
        const controls=node('div',undefined,'slot-tools');controls.append(field('转子外径 D2 · mm',diameter),action('按外径更新气隙',()=>{
          if(config.busy())return;
          const p=config.read(),bore=Number(p[config.module==='tx'?'D3':'Di1'])*(config.module==='tx'?10:1),d=Number(diameter.value);
          if(!diameter.value||!Number.isFinite(d)||!(d>0&&d<bore)){error(new Error('转子外径须大于 0 且小于定子内径'));return;}
          const key=config.module==='tx'?'G':'g';config.apply({...p,[key]:(bore-d)/(config.module==='tx'?20:2)},{});update();
        }));checkBody.append(controls);
      }catch(e){if(request===sequence)error(e);}
    }
    function update(){clearTimeout(timer);++sequence;timer=setTimeout(refresh,300);}
    function dialog(title){const d=node('dialog',undefined,'slot-dialog');const top=node('div',undefined,'slot-tools');top.append(node('h2',title),action('关闭',()=>d.close()));d.append(top);document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal();return d;}
    async function choose(side){
      if(config.busy())return;
      let initial;try{initial=get();if(!Object.keys(initial.parameters).length)throw new Error('请先载入设计参数');}catch(e){error(e);return;}
      const d=dialog('选择'+names[side]+'槽型'),search=node('input');search.type='search';search.placeholder='搜索图号、名称、机座或系列';search.setAttribute('aria-label','搜索槽型');
      const bar=node('div',undefined,'slot-tools'),results=node('div',undefined,'slot-picker-results'),detail=node('div'),status=node('p');
      let offset=0,total=0,query='',searchId=0,selectionId=0;
      bar.append(search,action('搜索',()=>{query=search.value.trim();offset=0;load();}));d.append(bar,status,results,detail);
      search.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();query=search.value.trim();offset=0;load();}};
      const paging=node('div',undefined,'slot-tools'),previous=action('上一页',()=>{offset=Math.max(0,offset-20);load();}),next=action('下一页',()=>{offset+=20;load();});paging.append(previous,next);d.append(paging);
      async function load(){const id=++searchId;++selectionId;detail.replaceChildren();try{const data=await api(`/slots?side=${side}&module=${config.module}&q=${encodeURIComponent(query)}&offset=${offset}&limit=20`);if(id!==searchId||!d.open)return;total=data.total;results.replaceChildren();status.textContent=`${total} 个槽型 · ${offset+1}–${Math.min(offset+20,total)}`;previous.disabled=offset===0;next.disabled=offset+20>=total;for(const item of data.items){const b=action(item.name+' · '+(item.document.drawing||'无图号')+' · '+(Object.keys(item.document.parameters).length?'含尺寸':'仅图号参考'),()=>select(item));b.classList.add('slot-option');results.append(b);}if(!total)status.textContent='没有匹配的槽型。可先保存当前设计的槽型。';}catch(e){status.textContent=e.message;}}
      function select(item){
        const id=++selectionId;detail.replaceChildren(node('h3',item.name));
        const applications=node('select');applications.setAttribute('aria-label','适用规格');applications.append(new Option('槽型基准尺寸',''));
        for(const [i,a] of item.document.applications.entries())applications.append(new Option(`${a.series} · ${a.frame} · ${a.poles} 极 · ${a.power} kW · g=${a.geometry.gap_mm??'—'} mm`,i));
        const include=node('input');include.type='checkbox';const label=field('同时带入参考定子内外径和气隙',include),preview=node('div');
        detail.append(applications,label,node('p',Object.keys(item.document.parameters).length?'将带入已保存的槽尺寸。':'资料未含槽宽、槽深等尺寸：仅关联图号，当前槽尺寸保留。'),preview);
        const apply=action('预览带入内容',async()=>{
          apply.disabled=true;try{
            const body=get(),stamp=JSON.stringify(body),result=await api('/slots/apply',{...body,reference:item.reference,application:applications.value===''?null:Number(applications.value),include_geometry:include.checked});
            if(id!==selectionId||!d.open)return;
            preview.replaceChildren();for(const c of result.changes)preview.append(node('p',`${c.key}：${c.before??'未设置'} → ${c.after}`));
            if(!result.changes.length)preview.append(node('p','数值不变；保存所选槽型的固定版本引用。'));
            for(const check of result.geometry_check.checks)preview.append(node('p',(check.passed?'✓ ':'需核对 · ')+check.message));
            preview.append(action('确认带入当前设计',()=>{if(config.busy())return;if(JSON.stringify(get())!==stamp){status.textContent='当前参数已变化，请重新预览。';return;}config.apply(result.parameters,result.binding);d.close();update();}));
          }catch(e){status.textContent=e.message;}finally{apply.disabled=false;}
        });
        applications.onchange=include.onchange=()=>preview.replaceChildren();detail.append(apply);
      }
      await load();search.focus();
    }
    async function capture(side){
      if(config.busy())return;
      let body;try{body=get();if(!Object.keys(body.parameters).length)throw new Error('请先载入设计');}catch(e){error(e);return;}
      const d=dialog('保存当前'+names[side]+'槽型'),name=node('input'),drawing=node('input'),source=node('input');name.maxLength=drawing.maxLength=120;source.maxLength=2000;source.value='从当前设计提取';
      name.value=(body.parameters.SizeX||body.parameters.TYP||'自定义')+' · '+names[side]+'槽';
      const status=node('p');d.append(field('槽型名称',name),field('冲片图号（可选）',drawing),field('来源',source),node('p','保存所选侧的当前尺寸与外径基准；主程序尺寸为 mm，TX 尺寸为 cm。'),status);
      const save=action('保存到用户槽型库',async()=>{if(config.busy())return;save.disabled=true;try{
        if(!name.value.trim()||!source.value.trim())throw new Error('请填写名称和来源');
        const doc=await api('/slots/capture',{...body,side});doc.drawing=drawing.value.trim();doc.source=source.value.trim();
        const item=await api('/library/slots',{name:name.value.trim(),document:doc});
        if(JSON.stringify(get())===JSON.stringify(body))config.apply(body.parameters,{[side]:item.reference});
        d.close();update();
      }catch(e){status.textContent=e.message;}finally{save.disabled=false;}});d.append(save);name.focus();
    }
    return {update,refresh};
  }
  return {mount,summary,api};
})();
