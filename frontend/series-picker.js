/* Shared series selector; series is revision metadata, never a numeric input. */
'use strict';
window.SeriesPicker=(()=>{
  function validate(value){
    if(value==null)return '';
    if(typeof value!=='string'||value.length>80||/[\p{C}]/u.test(value))throw new Error('系列名须为不超过 80 字的单行文本');
    return value.trim();
  }
  function mount(host,{onChange=()=>{}}={}){
    const label=document.createElement('label'),title=document.createElement('span'),select=document.createElement('select');
    label.className='series-field';title.textContent='系列名';select.id=host.id+'-select';select.setAttribute('aria-label','方案系列名');
    const custom=document.createElement('input');custom.type='text';custom.maxLength=80;custom.placeholder='输入新系列名，如 YE5';custom.setAttribute('aria-label','新系列名');custom.hidden=true;
    label.append(title,select);host.append(label,custom);host.classList.add('series-picker');let names=[];
    function options(items){
      const old=select.value,newMode=old==='new';
      names=[...new Set([...items,old.startsWith('series:')?old.slice(7):''].filter(v=>typeof v==='string'&&v.trim()))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
      select.replaceChildren(new Option('未分类',''),...names.map(name=>new Option(name,'series:'+name)),new Option('＋ 新增系列…','new'));
      select.value=newMode?'new':old;
    }
    function set(value){
      value=validate(value);options([...names,value]);select.value=value?'series:'+value:'';custom.value='';custom.hidden=true;custom.required=false;custom.setCustomValidity('');
    }
    function get(){
      if(select.value!=='new')return validate(select.value.slice(7));
      const value=validate(custom.value);if(!value)throw new Error('请输入新系列名，或选择“未分类”');return value;
    }
    select.onchange=()=>{custom.hidden=select.value!=='new';custom.required=!custom.hidden;if(!custom.hidden)custom.focus();onChange();};
    custom.oninput=()=>{custom.setCustomValidity('');onChange();};options([]);
    return {set,value:get,options,disable:value=>{select.disabled=custom.disabled=value;}};
  }
  return {mount,validate};
})();
