/* Native DOM adaptations of React Bits (David Haz), MIT + Commons Clause.
 * Sources and integration boundaries: docs/REACT_BITS.md.
 * Keep the existing ES-module renderer and backend contracts. */
const svgNS='http://www.w3.org/2000/svg';
const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;

export function statusMark(status){
  const mark=document.createElementNS(svgNS,'svg');
  mark.setAttribute('viewBox','0 0 24 24');mark.setAttribute('class','status-mark');mark.setAttribute('aria-hidden','true');
  const ring=document.createElementNS(svgNS,'circle');ring.setAttribute('cx','12');ring.setAttribute('cy','12');ring.setAttribute('r','9');ring.setAttribute('class','status-mark-ring');
  const check=document.createElementNS(svgNS,'path');check.setAttribute('d','M7.5 12.25 10.5 15.25 16.75 8.75');check.setAttribute('class','status-mark-check');
  const cross=document.createElementNS(svgNS,'path');cross.setAttribute('d','M8.5 8.5 15.5 15.5M15.5 8.5 8.5 15.5');cross.setAttribute('class','status-mark-cross');
  mark.append(ring,check,cross);setStatusMark(mark,status);return mark;
}
export function setStatusMark(mark,status){
  mark.dataset.status=status==='completed'?'done':['failed','error','blocked'].includes(status)?'failed':['running','stopping','busy','thinking','tools'].includes(status)?'running':['stopped','interrupted','cancelled'].includes(status)?'cancelled':'pending';
}

// Stretch the leading edge first, then let the trailing edge catch up.
export function installRubberSegment(track){
  track.classList.add('rubber-segment');track.setAttribute('aria-label','切换工作视图');
  const thumb=document.createElement('span');thumb.className='rubber-segment-thumb';thumb.setAttribute('aria-hidden','true');track.prepend(thumb);
  let initialized=false,grip,suppressClick=false;
  const buttons=()=>[...track.querySelectorAll('button')];
  const place=(target,animate=true)=>{
    if(!target||!track.offsetWidth)return;
    const a=thumb.getBoundingClientRect(),box=track.getBoundingClientRect(),b=target.getBoundingClientRect();
    const from=a.left-box.left-track.clientLeft,to=target.offsetLeft;
    thumb.getAnimations().forEach(animation=>animation.cancel());
    thumb.style.left=to+'px';thumb.style.width=b.width+'px';thumb.hidden=false;
    if(initialized&&animate&&!reduced()&&Math.abs(from-to)>.5){
      const left=Math.min(from,to),right=Math.max(from+a.width,to+b.width);
      thumb.animate([{left:from+'px',width:a.width+'px',transform:'scaleY(1)'},{left:left+'px',width:right-left+'px',transform:'scaleY(.92)',offset:.42},{left:to+'px',width:b.width+'px',transform:'scaleY(1)'}],{duration:380,easing:'cubic-bezier(.23,1,.32,1)'});
    }
    initialized=true;
  };
  track.updateSelection=(animate=true)=>{
    const items=buttons(),selected=items.find(button=>button.classList.contains('active'));
    for(const item of items){const active=item===selected;item.setAttribute('aria-pressed',String(active));item.tabIndex=active||!selected&&item===items[0]?0:-1;}
    if(selected)place(selected,animate);else thumb.hidden=true;
  };
  track.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const items=buttons(),index=items.indexOf(document.activeElement);if(index<0)return;
    event.preventDefault();const next=items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowRight'?1:-1)+items.length)%items.length];next.focus();next.click();
  });
  track.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;suppressClick=false;grip={id:event.pointerId,x:event.clientX,y:event.clientY,dragging:false};
  });
  track.addEventListener('pointermove',event=>{
    if(!grip||grip.id!==event.pointerId)return;
    if(!grip.dragging){if(Math.abs(event.clientX-grip.x)<6)return;if(Math.abs(event.clientY-grip.y)>Math.abs(event.clientX-grip.x)){grip=null;return;}grip.dragging=true;track.setPointerCapture(event.pointerId);}
    event.preventDefault();const box=track.getBoundingClientRect(),items=buttons(),nearest=items.reduce((best,item)=>Math.abs(item.offsetLeft+item.offsetWidth/2-(event.clientX-box.left))<Math.abs(best.offsetLeft+best.offsetWidth/2-(event.clientX-box.left))?item:best);
    grip.target=nearest;place(nearest,false);
  });
  const finish=event=>{
    if(!grip||grip.id!==event.pointerId)return;const last=grip;grip=null;
    if(last.dragging){suppressClick=true;if(track.hasPointerCapture(event.pointerId))track.releasePointerCapture(event.pointerId);if(event.type!=='pointercancel'){last.target?.focus();last.target?.click();}else track.updateSelection(false);}
  };
  track.addEventListener('pointerup',finish);track.addEventListener('pointercancel',finish);
  track.addEventListener('click',event=>{if(suppressClick&&event.isTrusted){suppressClick=false;event.preventDefault();event.stopImmediatePropagation();}},true);
  new ResizeObserver(()=>track.updateSelection(false)).observe(track);
  document.fonts?.ready.then(()=>track.updateSelection(false));
}

export function installSwipeRow(wrapper,{label,archived=false,onArchive,onError}){
  wrapper.classList.add('swipe-row');
  const surface=document.createElement('div');surface.className='swipe-row-surface';surface.append(...wrapper.childNodes);
  const action=document.createElement('button');action.type='button';action.className='swipe-row-action';action.textContent=archived?'恢复':'归档';action.setAttribute('aria-label',(archived?'恢复会话 ':'归档会话 ')+label);action.hidden=true;
  wrapper.append(action,surface);let open=false,grip,suppressClick=false,busy=false;
  const width=72;
  const settle=next=>{open=next;wrapper.classList.toggle('swipe-open',open);wrapper.classList.remove('swipe-dragging');wrapper.style.setProperty('--swipe-x',open?-width+'px':'0px');action.hidden=!open;};
  wrapper.closeSwipe=()=>settle(false);
  const archive=async()=>{if(busy)return;busy=true;action.disabled=true;try{await onArchive();}catch(error){onError?.(error);}finally{busy=false;action.disabled=false;settle(false);}};
  action.addEventListener('click',archive);
  surface.addEventListener('pointerdown',event=>{
    if(event.button!==0||event.target.closest('.session-menu-trigger'))return;
    // A new pointer interaction starts a new click; only the drag's own click is swallowed.
    suppressClick=false;grip={id:event.pointerId,x:event.clientX,y:event.clientY,offset:open?width:0,dragging:false};
  });
  surface.addEventListener('pointermove',event=>{
    if(!grip||grip.id!==event.pointerId)return;
    const dx=event.clientX-grip.x,dy=event.clientY-grip.y;
    if(!grip.dragging){if(Math.max(Math.abs(dx),Math.abs(dy))<10)return;if(Math.abs(dy)>=Math.abs(dx)){grip=null;return;}grip.dragging=true;surface.setPointerCapture(event.pointerId);wrapper.classList.add('swipe-dragging');action.hidden=false;}
    event.preventDefault();grip.distance=Math.max(0,Math.min(wrapper.clientWidth,grip.offset-dx));wrapper.style.setProperty('--swipe-x',-grip.distance+'px');
  });
  const finish=event=>{
    if(!grip||grip.id!==event.pointerId)return;const last=grip;grip=null;
    if(!last.dragging)return;suppressClick=true;
    if(surface.hasPointerCapture(event.pointerId))surface.releasePointerCapture(event.pointerId);
    if(event.type==='pointercancel')return settle(open);
    if(last.distance>=Math.max(wrapper.clientWidth*.6,width+40)){settle(true);archive();}else settle(last.distance>=width*.45);
  };
  surface.addEventListener('pointerup',finish);surface.addEventListener('pointercancel',finish);
  surface.addEventListener('click',event=>{if(suppressClick){suppressClick=false;event.preventDefault();event.stopImmediatePropagation();}},true);
  wrapper.addEventListener('keydown',event=>{
    if(event.key==='Escape'){settle(false);surface.querySelector('.session')?.focus();}
    if(event.key==='ArrowLeft'&&event.target.matches('.session')){event.preventDefault();settle(true);action.focus();}
  });
}

export function installBranchedMenu(list){
  list.classList.add('session-navigation');list.setAttribute('aria-label','工作区与会话');
  list.addEventListener('pointerdown',event=>{for(const row of list.querySelectorAll('.swipe-open'))if(!row.contains(event.target))row.closeSwipe?.();});
  list.addEventListener('keydown',event=>{
    if(!event.target.matches('.session')||!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const rows=[...list.querySelectorAll('.session')],index=rows.indexOf(event.target);event.preventDefault();rows[event.key==='Home'?0:event.key==='End'?rows.length-1:(index+(event.key==='ArrowDown'?1:-1)+rows.length)%rows.length]?.focus();
  });
}
