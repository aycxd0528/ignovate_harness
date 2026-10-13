import { el, button, icon, iconButton, prose, badge, permissionIcon, statusLabel } from './ui.js';
import {updateProse} from './rendering.js';

export function reconcileMapDraft(draft,nodes,runs,branches){
  if(draft?.kind!=='root'||draft.requestId&&runs.some(run=>run.request_id===draft.requestId))return draft;
  if(!nodes.length&&!runs.length)return draft;
  if(!draft.text.trim())return null;
  const head=branches.find(branch=>branch.id===draft.branchId)?.head;
  if(!head||!nodes.some(node=>node.id===head))return draft;
  const node=nodes.find(node=>node.id===head);
  if(node.status!=='completed'||!node.recoverable)return draft.blocked?draft:{...draft,blocked:true};
  return {...draft,kind:'continue',nodeId:head,blocked:false,requestId:null,mapPosition:undefined,mapViewport:undefined};
}

export function layoutNodes(nodes, positions = {}) {
  const result = {}, rows = new Map(), depths = new Map();
  let nextRow = 0;
  for (const node of nodes) {
    const depth = node.parent_id ? (depths.get(node.parent_id) || 0) + 1 : 0;
    let row = node.parent_id ? rows.get(node.parent_id) : undefined;
    const sibling = nodes.some(other => other.id !== node.id && other.parent_id === node.parent_id && result[other.id]);
    if (row === undefined || sibling) row = nextRow++;
    rows.set(node.id, row); depths.set(node.id, depth);
    result[node.id] = positions[node.id] || { x: depth * 360, y: row * 320 };
  }
  return result;
}

export function visibleNodes(nodes, collapsed = []) {
  const byId = new Map(nodes.map(n => [n.id, n])), hidden = new Set(collapsed);
  return nodes.filter(node => {
    let parent = node.parent_id; const seen = new Set();
    while (parent && !seen.has(parent)) {
      if (hidden.has(parent)) return false;
      seen.add(parent); parent = byId.get(parent)?.parent_id;
    }
    return true;
  });
}

export function createMap({nodes,branches,runs=[],streams=new Map(),layout={},selected,activeBranch,draft=null,draftMoving=false,taskLabel='',onSelect,onChat,onFork,onArchive,onSave,onDraftChange,onSend,onPermission,onNewDraft}){
  const root=el('div','conversation-map');
  const reserved={...layout.positions};let migrated=false;
  for(const run of runs){const key='run_'+run.id;if(run.node_id&&reserved[key]){reserved[run.node_id]||=reserved[key];delete reserved[key];migrated=true;}else if(!run.node_id&&draft?.requestId===run.request_id&&draft.mapPosition)reserved[key]||=draft.mapPosition;}
  const branchById=new Map(branches.map(b=>[b.id,b]));
  nodes=[...nodes,...runs.filter(run=>!run.node_id&&['queued','running','waiting_approval','stopping','failed','stopped','interrupted'].includes(run.status)).map(run=>({id:'run_'+run.id,run_id:run.id,provisional:true,branch_id:run.branch_id,parent_id:run.map_origin?run.map_origin.node_id:branchById.get(run.branch_id)?.head||null,prompt:run.message||'',answer:streams.get(run.id)||'',status:run.status,activity:run.activity,tools:run.tools||[],recoverable:false,error:run.error}))];
  let viewport={x:40,y:40,zoom:1,...layout.viewport},positions=layoutNodes(nodes,reserved),collapsed=[...(layout.collapsed||[])],current=selected,question=draft;
  const byId=new Map(nodes.map(node=>[node.id,node])),byBranch=new Map(branches.map(branch=>[branch.id,branch]));
  const activePath=new Set(byBranch.get(activeBranch)?.node_ids||[]),busy=runs.some(run=>['queued','running','waiting_approval','stopping'].includes(run.status));
  const toolbar=el('div','map-toolbar'),surface=el('div','map-surface'),world=el('div','map-world'),zoomLabel=el('span','zoom-label','100%');
  surface.tabIndex=0;surface.setAttribute('aria-label','会话地图画布');surface.append(world);
  const save=()=>onSave({positions:Object.fromEntries(Object.entries(positions).map(([id,point])=>[id,{...point}])),collapsed:[...collapsed],viewport:{...viewport}});
  const finishInteraction=()=>{root.interacting=false;root.dispatchEvent(new CustomEvent('map-interaction-end'));};
  const apply=()=>{world.style.transform=`translate(${viewport.x}px,${viewport.y}px) scale(${viewport.zoom})`;zoomLabel.textContent=Math.round(viewport.zoom*100)+'%';};
  const zoom=factor=>{const old=viewport.zoom;viewport.zoom=Math.max(.2,Math.min(4,old*factor));viewport.x=surface.clientWidth/2-(surface.clientWidth/2-viewport.x)*viewport.zoom/old;viewport.y=surface.clientHeight/2-(surface.clientHeight/2-viewport.y)*viewport.zoom/old;apply();save();};
  toolbar.append(el('span','map-toolbar-title','地图'),iconButton('layout','整理布局',()=>{positions=layoutNodes(nodes);render();save();}),iconButton('locate','定位当前',()=>{
    const point=positions[current||byBranch.get(activeBranch)?.head];if(!point)return;viewport.x=surface.clientWidth/2-(point.x+155)*viewport.zoom;viewport.y=surface.clientHeight/2-(point.y+140)*viewport.zoom;apply();save();
  }),iconButton('fit','适应视图',()=>{
    const points=visibleNodes(nodes,collapsed).map(node=>positions[node.id]);if(!points.length)return;const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y));
    const width=Math.max(...points.map(p=>p.x))-minX+310,height=Math.max(...points.map(p=>p.y))-minY+280;
    viewport.zoom=Math.max(.2,Math.min(1,(surface.clientWidth-80)/width,(surface.clientHeight-80)/height));viewport.x=40-minX*viewport.zoom;viewport.y=40-minY*viewport.zoom;apply();save();
  }),iconButton('minus','缩小地图',()=>zoom(.85)),zoomLabel,iconButton('plus','放大地图',()=>zoom(1.18)));
  const list=el('div','map-list');list.hidden=true;toolbar.append(iconButton('list','列表导航',()=>{list.hidden=!list.hidden;}));
  nodes.forEach(node=>list.append(button(node.prompt.slice(0,60)||'回合',()=>onChat(node.provisional?{...node,id:null}:node),'map-list-item')));
  const usable=node=>!busy&&node.recoverable&&node.status==='completed';
  function startDraft(node,kind,text=''){
    let ancestor=node?.id;while(ancestor){collapsed=collapsed.filter(id=>id!==ancestor);ancestor=nodes.find(n=>n.id===ancestor)?.parent_id;}
    question={nodeId:node?.id||null,branchId:node?.branch_id||activeBranch,kind,text,permission_mode:kind==='fork'||(node&&byBranch.get(node.branch_id)?.head!==node.id)?'default':byBranch.get(node?.branch_id||activeBranch)?.permission_mode||'default'};onDraftChange(question);render();
    const point=draftPosition();viewport.x=Math.min(viewport.x,surface.clientWidth-(point.x+330)*viewport.zoom-30);viewport.y=Math.min(viewport.y,surface.clientHeight-(point.y+295)*viewport.zoom-30);apply();save();world.querySelector('.map-draft textarea')?.focus();
  }
  function draftPosition(){
    if(!question?.nodeId){let y=0;while(Object.values(positions).some(point=>Math.abs(point.x)<310&&Math.abs(point.y-y)<300))y+=320;return {x:0,y};}const from=positions[question.nodeId]||{x:0,y:0};let y=from.y;
    const occupied=Object.values(positions).filter(p=>Math.abs(p.x-(from.x+360))<310);
    while(occupied.some(point=>Math.abs(point.y-y)<300))y+=320;
    return {x:from.x+360,y};
  }
  function line(svg,from,to,fork=false,draft=false){
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',`M${from.x+310},${from.y+140} C${from.x+345},${from.y+140} ${to.x-35},${to.y+140} ${to.x},${to.y+140}`);path.classList.add(fork?'fork-link':'continuation');if(draft)path.classList.add('draft-link');svg.append(path);
  }
  function renderDraft(svg){
    if(!question&&!nodes.length){question={nodeId:null,branchId:activeBranch,kind:'root',text:'',permission_mode:byBranch.get(activeBranch)?.permission_mode||'default'};onDraftChange(question);}
    if(!question||question.requestId&&runs.some(run=>run.request_id===question.requestId)||question.kind==='root'&&busy)return;const point=draftPosition(),card=el('form','map-draft');card.style.left=point.x+'px';card.style.top=point.y+'px';
    if(question.nodeId&&positions[question.nodeId])line(svg,positions[question.nodeId],point,question.kind==='fork',true);
    const header=el('div','map-draft-heading');header.append(el('strong','',question.blocked?'未发送的问题':question.kind==='root'?'新会话':question.kind==='fork'?'新的分支':'继续追问'));
    card.append(header);
    if(question.blocked){card.append(el('p','muted map-draft-note','当前回合无法继续。草稿可移到新会话。'));if(onNewDraft){const move=button('移到新会话',async()=>{if(move.disabled)return;move.disabled=true;try{await onNewDraft(question);}finally{move.disabled=false;}},'secondary');move.disabled=draftMoving;card.append(move);}}
    if(question.kind!=='root'){const chips=el('div','map-followup-chips');for(const value of ['展开说明','举例','通俗易懂','对比解释'])chips.append(button(value,()=>{input.value+=value;question.text=input.value;question.requestId=null;onDraftChange(question);input.focus();},'followup-chip'));card.append(chips);}
    const input=el('textarea');input.rows=4;input.maxLength=100000;input.setAttribute('aria-label','地图追问');input.placeholder=question.kind==='root'?'从一个新问题开始…':question.kind==='fork'?'输入这个分支的新问题…':'输入追问，继续这条路径…';input.value=question.text;input.disabled=busy;
    input.addEventListener('input',()=>{question.text=input.value;question.requestId=null;onDraftChange(question);});
    input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();card.requestSubmit();}});card.append(input);
    const actions=el('div','map-draft-actions'),permission=iconButton('lock','权限管理',()=>onPermission?.(permission,question,()=>{onDraftChange(question);updatePermission();}),'permission-control'),cancel=iconButton('close','取消追问',()=>{question=null;onDraftChange(null);render();}),send=iconButton('send','发送追问');send.classList.add('primary');send.type='submit';send.disabled=busy||question.blocked;cancel.disabled=busy;permission.disabled=busy;
    function updatePermission(){const mode=question.permission_mode||'default';permission.replaceChildren(permissionIcon(mode));permission.dataset.value=mode;permission.dataset.tooltip='权限管理：'+({default:'请求批准',acceptEdits:'帮我批准',bypassPermissions:'完全访问权限'}[mode]);}
    updatePermission();actions.append(permission,el('span','muted',busy?'任务处理中…':taskLabel||'Enter 发送'),cancel,send);card.append(actions);
    card.addEventListener('submit',async event=>{
      event.preventDefault();if(busy||question.blocked||send.disabled||!input.value.trim())return;const captured=question;captured.requestId||=crypto.randomUUID();captured.mapPosition={...draftPosition()};captured.mapViewport={...viewport};onDraftChange(captured);send.disabled=true;input.disabled=true;cancel.disabled=true;
      try{await onSend(captured);if(question===captured){question=null;onDraftChange(null);}}
      catch(error){input.disabled=false;send.disabled=false;cancel.disabled=false;throwMessage(error.message);}
    });world.append(card);
  }
  function throwMessage(message){const event=new CustomEvent('map-error',{detail:message});root.dispatchEvent(event);}
  function updateLinks(){
    const svg=world.querySelector('.map-links');if(!svg)return;svg.replaceChildren();
    svg.setAttribute('width',Math.max(1400,...Object.values(positions).map(point=>point.x+800)));svg.setAttribute('height',Math.max(1400,...Object.values(positions).map(point=>point.y+800)));
    const shown=visibleNodes(nodes,collapsed),ids=new Set(shown.map(node=>node.id));for(const node of shown)if(node.parent_id&&ids.has(node.parent_id))line(svg,positions[node.parent_id],positions[node.id],byId.get(node.parent_id)?.branch_id!==node.branch_id,node.provisional);
    const card=world.querySelector('.map-draft');if(card&&question){const point=draftPosition();card.style.left=point.x+'px';card.style.top=point.y+'px';if(question.nodeId&&positions[question.nodeId])line(svg,positions[question.nodeId],point,question.kind==='fork',true);}
  }
  function render(){
    world.replaceChildren();const shown=visibleNodes(nodes,collapsed),ids=new Set(shown.map(node=>node.id)),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('map-links');
    svg.setAttribute('width',Math.max(1400,...Object.values(positions).map(point=>point.x+800)));svg.setAttribute('height',Math.max(1400,...Object.values(positions).map(point=>point.y+800)));world.append(svg);
    for(const node of shown){if(node.parent_id&&ids.has(node.parent_id))line(svg,positions[node.parent_id],positions[node.id],byId.get(node.parent_id)?.branch_id!==node.branch_id,node.provisional);}
    for(const node of shown){
      const card=el('article','map-card'+(node.provisional?' map-run-card':'')+(node.id===current?' selected':'')+(activePath.has(node.id)?' active-path':''));card.dataset.nodeId=node.id;if(node.provisional)card.dataset.runId=node.run_id;card.style.left=positions[node.id].x+'px';card.style.top=positions[node.id].y+'px';
      const handle=el('button','map-handle');handle.type='button';handle.setAttribute('aria-label','拖动回合卡片');handle.append(icon('grip'));
      const header=el('div','map-card-header'),title=el('h3','',node.prompt.slice(0,95)||'用户回合'),index=node.provisional?(byBranch.get(node.branch_id)?.node_ids||[]).length+1:Math.max(1,(byBranch.get(node.branch_id)?.node_ids||[]).indexOf(node.id)+1),meta=el('div','map-meta');
      meta.append(el('span','',`${node.parent_id?'追问':'会话'} · 第 ${index} 轮`),badge(node.status));if(node.tools.length)meta.append(el('span','tool-count',`工具 ${node.tools.length}`));header.append(title,meta);
      const content=el('div','map-body'),excerpt=prose(node.answer.slice(0,8000)||node.error||(node.provisional?(node.status==='queued'?'等待执行…':node.activity==='tools'?'正在调用工具…':'正在思考…'):'没有已保存的完整回答'),'map-excerpt');content.append(excerpt);if(node.answer.length>8000)content.append(el('p','muted','正文节选，详情查看全文。'));
      const actions=el('div','map-actions');if(!node.provisional)actions.append(iconButton('info','详情',()=>select(node)));actions.append(iconButton('chat','打开聊天',()=>onChat(node.provisional?{...node,id:null}:node)));
      const legacyFork=iconButton('fork','分叉',()=>onFork(node));legacyFork.disabled=!usable(node);actions.append(legacyFork);if(onArchive){const archive=iconButton('archive','归档对话',()=>onArchive(node));archive.disabled=node.provisional;actions.append(archive);}
      const follow=iconButton('plus','继续追问',()=>startDraft(node,'continue'),'map-port follow-port'),fork=iconButton('fork','创建分支',()=>startDraft(node,'fork'),'map-port branch-port');follow.disabled=fork.disabled=!usable(node);
      const collapse=iconButton(collapsed.includes(node.id)?'plus':'minus',collapsed.includes(node.id)?'展开':'折叠',()=>{collapsed=collapsed.includes(node.id)?collapsed.filter(id=>id!==node.id):[...collapsed,node.id];render();save();[...world.querySelectorAll('.map-card')].find(card=>card.dataset.nodeId===node.id)?.querySelector('.collapse-port')?.focus();},'map-port collapse-port');collapse.hidden=!nodes.some(child=>child.parent_id===node.id);
      card.append(handle,header,content,actions,follow,fork,collapse);world.append(card);
      card.addEventListener('click',event=>{if(event.target.closest('button,textarea,input,a,summary')||window.getSelection()?.toString())return;select(node);});
      handle.addEventListener('pointerdown',event=>{
        event.preventDefault();root.interacting=true;handle.setPointerCapture(event.pointerId);const start={x:event.clientX,y:event.clientY},position={...positions[node.id]};
        const move=e=>{positions[node.id]={x:position.x+(e.clientX-start.x)/viewport.zoom,y:position.y+(e.clientY-start.y)/viewport.zoom};card.style.left=positions[node.id].x+'px';card.style.top=positions[node.id].y+'px';updateLinks();save();};
        const end=()=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);updateLinks();save();finishInteraction();};handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end,{once:true});handle.addEventListener('pointercancel',end,{once:true});
      });
      handle.addEventListener('keydown',event=>{const shift={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];if(!shift)return;event.preventDefault();positions[node.id].x+=shift[0];positions[node.id].y+=shift[1];render();save();[...world.querySelectorAll('.map-card')].find(card=>card.dataset.nodeId===node.id)?.querySelector('.map-handle').focus();});
    }
    renderDraft(svg);
  }
  function select(node){current=node.id;world.querySelectorAll('.map-card').forEach(card=>card.classList.toggle('selected',card.dataset.nodeId===node.id));if(!node.provisional)onSelect(node);}
  root.addEventListener('mouseup',event=>{
    // Keep the selection action alive through its own mouseup/click sequence.
    if(event.target.closest('.map-quote-button'))return;
    root.querySelector('.map-quote-button')?.remove();const selection=window.getSelection(),text=selection?.toString().trim(),anchor=selection?.anchorNode?.parentElement,excerpt=anchor?.closest('.map-excerpt');if(!text||!excerpt||!root.contains(excerpt))return;
    const node=byId.get(excerpt.closest('.map-card').dataset.nodeId);if(!usable(node))return;
    const quote=button('引用追问',()=>{startDraft(node,'fork',`引用回合 ${node.id.slice(0,8)}：\n> ${text.replaceAll('\n','\n> ')}\n\n`);quote.remove();},'map-quote-button');
    const rect=selection.getRangeAt(0).getBoundingClientRect(),bounds=root.getBoundingClientRect();quote.style.left=Math.max(8,Math.min(rect.left-bounds.left,root.clientWidth-100))+'px';quote.style.top=Math.max(55,rect.top-bounds.top-36)+'px';root.append(quote);
  });
  surface.addEventListener('pointerdown',event=>{
    if(event.target.closest('.map-card,.map-draft'))return;root.interacting=true;surface.setPointerCapture(event.pointerId);const start={x:event.clientX,y:event.clientY,vx:viewport.x,vy:viewport.y};
    const move=e=>{viewport.x=start.vx+e.clientX-start.x;viewport.y=start.vy+e.clientY-start.y;apply();save();};const end=()=>{surface.removeEventListener('pointermove',move);surface.removeEventListener('pointerup',end);surface.removeEventListener('pointercancel',end);save();finishInteraction();};surface.addEventListener('pointermove',move);surface.addEventListener('pointerup',end,{once:true});surface.addEventListener('pointercancel',end,{once:true});
  });
  surface.addEventListener('wheel',event=>{if(event.target.closest('.map-body,.map-draft'))return;event.preventDefault();if(event.ctrlKey||event.metaKey)viewport.zoom=Math.max(.2,Math.min(4,viewport.zoom*Math.exp(-event.deltaY*.002)));else{viewport.x-=event.deltaX;viewport.y-=event.deltaY;}apply();save();},{passive:false});
  root.updateRunCards=currentRuns=>{
    for(const run of currentRuns){
      const node=byId.get('run_'+run.id);if(!node||run.node_id)continue;
      Object.assign(node,{status:run.status,activity:run.activity,tools:run.tools||[],error:run.error,answer:streams.get(run.id)||''});
      const card=[...world.querySelectorAll('.map-run-card')].find(card=>card.dataset.runId===run.id);if(!card)continue;
      const mark=card.querySelector('.badge');mark.className='badge '+run.status;mark.textContent=statusLabel(run.status);
      const meta=card.querySelector('.map-meta');let count=meta.querySelector('.tool-count');
      if(node.tools.length){if(!count){count=el('span','tool-count');meta.append(count);}count.textContent='工具 '+node.tools.length;}else count?.remove();
      updateProse(card.querySelector('.map-excerpt'),node.answer.slice(0,8000)||node.error||(node.status==='queued'?'等待执行…':node.activity==='tools'?'正在调用工具…':'正在思考…'),prose);
    }
  };
  root.append(toolbar,surface,list,el('span','map-help','拖动画布 · 手柄移动卡片 · 卡片侧边追问 / 分支 · ⌘ / Ctrl + 滚轮缩放'));render();apply();if(migrated)queueMicrotask(()=>{if(root.isConnected)save();});return root;
}
