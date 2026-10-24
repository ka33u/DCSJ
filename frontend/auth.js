'use strict';
window.DCSJAuth=(()=>{
  let current=null;
  const originalFetch=window.fetch.bind(window);
  function expired(){
    let message=document.getElementById('auth-expired');if(message)return;
    message=document.createElement('div');message.id='auth-expired';message.setAttribute('role','alert');
    message.append('登录已失效，当前输入仍保留。');
    const link=document.createElement('a');link.href='/login';link.target='_blank';link.rel='noopener';link.textContent='在新窗口登录后重试';message.append(link);document.body.append(message);
  }
  window.fetch=async(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))return originalFetch(input,options);
    const headers=new Headers(input instanceof Request?input.headers:undefined);
    new Headers(options.headers).forEach((v,k)=>headers.set(k,v));headers.set('X-DCSJ-Request','1');
    const response=await originalFetch(input,{...options,headers});
    if(response.status===401&&current)expired();
    return response;
  };
  const account={beforeLogout:null,username:()=>{
    if(!current)throw new Error('请先登录');return current.username;
  }};
  account.ready=(async()=>{
    const response=await window.fetch('/api/v1/auth/me');
    if(!response.ok){location.replace('/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));throw new Error('请先登录');}
    current=await response.json();
    const host=document.querySelector('[data-account]');
    if(host){
      const label=document.createElement('span');label.textContent='当前用户 · '+current.username;
      const logout=document.createElement('button');logout.type='button';logout.textContent='退出登录';
      logout.onclick=async()=>{
        if(account.beforeLogout&&!account.beforeLogout())return;
        logout.disabled=true;
        try{const r=await window.fetch('/api/v1/auth/logout',{method:'POST'});if(!r.ok&&r.status!==401)throw new Error('退出失败，请重试');location.assign('/login');}
        catch(e){logout.disabled=false;label.textContent=e.message;}
      };host.replaceChildren(label,logout);
    }
    const author=document.getElementById('design-author');if(author){author.value=current.username;author.readOnly=true;}
    return current;
  })();
  account.ready.catch(()=>{});
  return account;
})();
