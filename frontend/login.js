'use strict';
(() => {
  const form=document.getElementById('login-form'),button=document.getElementById('login-submit'),error=document.getElementById('login-error');
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(button.disabled)return;
    button.disabled=true;button.textContent='正在登录…';error.hidden=true;
    try{
      const response=await fetch('/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json','X-DCSJ-Request':'1'},
        body:JSON.stringify({username:form.elements.username.value.trim(),password:form.elements.password.value})});
      const data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'请检查用户名和密码后重试');
      const next=new URL(new URLSearchParams(location.search).get('next')||'/',location.origin);
      location.replace(next.origin===location.origin&&next.pathname!=='/login'?next.pathname+next.search+next.hash:'/');
    }catch(e){error.textContent=e.message;error.hidden=false;button.disabled=false;button.textContent='登录工作台 →';}
  });
})();
