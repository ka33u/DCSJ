/* Account-owned series changes shared by the main and TX design libraries. */
'use strict';
window.DesignSeries=(()=>{
  function selection({all,button,count,onEdit}){
    let items=[], selected=new Map(), controls=[];
    function sync(){
      const eligible=items.filter(DesignVersions.editable);
      all.checked=eligible.length>0&&selected.size===eligible.length;
      all.indeterminate=selected.size>0&&!all.checked;
      all.disabled=!eligible.length||eligible.length>200;
      all.title=eligible.length>200?'单批最多 200 项，请逐条勾选或筛选列表':'';
      count.textContent=eligible.length?`已选 ${selected.size} 项 · 可修改 ${eligible.length} 项`:'没有可修改的设计';
      button.disabled=!eligible.length;
      button.title=eligible.length?'先勾选设计，再批量修改系列名':'仅可修改自己未归档的最新设计';
      for(const [id,input] of controls)input.checked=selected.has(id);
    }
    all.addEventListener('change',()=>{selected.clear();if(all.checked)for(const item of items.filter(DesignVersions.editable))selected.set(item.id,item);sync();});
    button.addEventListener('click',()=>{
      if(!selected.size){count.textContent='请先勾选要修改的设计，或使用「全选本页」。';controls.find(([,input])=>!input.disabled)?.[1].focus();return;}
      onEdit([...selected.values()]);
    });
    return {
      set(value){items=value;selected.clear();controls=[];sync();},
      checkbox(item){
        const input=document.createElement('input');input.type='checkbox';input.className='design-select';
        input.setAttribute('aria-label','选择设计 '+item.name);input.disabled=!DesignVersions.editable(item);
        input.title=input.disabled?DesignVersions.editReason(item):'勾选后可批量修改系列名';
        input.onchange=()=>{if(input.checked&&selected.size>=200){input.checked=false;count.textContent='单批最多 200 项';return;}if(input.checked)selected.set(item.id,item);else selected.delete(item.id);sync();};
        controls.push([item.id,input]);return input;
      },
    };
  }
  function open({items,endpoint,series=[],onSaved=()=>{}}){
    if(!items.length||items.some(item=>!DesignVersions.editable(item))||document.querySelector('#design-series-dialog'))return;
    const dialog=document.createElement('dialog');dialog.id='design-series-dialog';dialog.className='design-series-dialog';
    dialog.setAttribute('aria-labelledby','design-series-title');
    const form=document.createElement('form'),title=document.createElement('h2'),description=document.createElement('p');
    title.id='design-series-title';title.textContent=items.length===1?'修改系列名':'批量修改系列名';
    description.textContent=`已选择 ${items.length} 个设计。修改后保留旧版本，并记录系列名差异；系列名相同的设计不新增版本。`;
    const list=document.createElement('ul');list.className='series-targets';
    for(const item of items){const li=document.createElement('li');li.textContent=`${item.name} · v${item.revision} · ${item.series||'未分类'}`;list.append(li);}
    const host=document.createElement('div');host.id='batch-series-control';
    const error=document.createElement('p');error.className='series-error';error.setAttribute('role','alert');
    const actions=document.createElement('div');actions.className='series-dialog-actions';
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';cancel.className='button';
    const save=document.createElement('button');save.type='submit';save.textContent='保存系列名';save.className='button button-primary primary';
    actions.append(cancel,save);form.append(title,description,list,host,error,actions);dialog.append(form);document.body.append(dialog);
    const picker=SeriesPicker.mount(host);picker.options([...series,...items.map(item=>item.series)]);
    picker.set(items.every(item=>item.series===items[0].series)?items[0].series:'');
    let pending=false;
    cancel.onclick=()=>dialog.close();
    dialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
    dialog.addEventListener('close',()=>dialog.remove());
    form.onsubmit=async event=>{
      event.preventDefault();if(pending)return;error.textContent='';
      try{
        const series=picker.value();pending=true;save.disabled=cancel.disabled=true;picker.disable(true);save.textContent='正在保存…';
        const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({series,items:items.map(item=>({id:item.id,expected_revision:item.revision}))})});
        const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'系列名修改失败，请刷新列表后重试。');
        dialog.close();await onSaved(data);
      }catch(e){error.textContent=e.message;}
      finally{pending=false;save.disabled=cancel.disabled=false;picker.disable(false);save.textContent='保存系列名';}
    };
    dialog.showModal();host.querySelector('select').focus();
  }
  return {open,selection};
})();
