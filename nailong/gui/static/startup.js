// Show the startup for at least two seconds, and wait longer for native readiness.
(()=>{
  // Apply the last appearance before the first paint, including slow snapshots.
  let theme='system';try{const saved=localStorage.getItem('ignovate-theme');if(['light','dark','system'].includes(saved))theme=saved;}catch{/* Storage can be unavailable for file entry. */}
  document.documentElement.dataset.theme=theme;
  const screen=document.getElementById('startup-screen'),startedAt=performance.now();let dismissed=false,readyTimer;
  const dismiss=()=>{if(!screen||dismissed)return;dismissed=true;screen.classList.add('is-ready');screen.setAttribute('aria-hidden','true');screen.addEventListener('transitionend',()=>screen.remove(),{once:true});setTimeout(()=>screen.remove(),220);};
  const ready=()=>{if(!screen||dismissed||readyTimer!==undefined||screen.dataset.state==='error')return;const remaining=2000-(performance.now()-startedAt);if(remaining>0)readyTimer=setTimeout(dismiss,remaining);else dismiss();};
  const fail=message=>{if(!screen||dismissed)return;clearTimeout(readyTimer);readyTimer=undefined;screen.dataset.state='error';screen.querySelector('.startup-note').textContent=message;screen.querySelector('.startup-retry').hidden=false;};
  screen?.querySelector('.startup-retry').addEventListener('click',()=>location.reload());
  window.ignovateStartup={ready,fail};if(location.protocol==='file:'){dismissed=true;screen?.remove();}
})();
