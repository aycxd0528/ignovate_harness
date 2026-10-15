import { el, button, icon, iconButton, prose, badge, permissionIcon, statusLabel } from './ui.js';
import {updateProse} from './rendering.js';
import {mapGeometry,activePathIds,scopedNodes,mapStatistics,mapDraftPosition,mapConnectorPath,hiddenMapState,controlSource,mapAttention,uniqueControlRows,taskDependencies} from './map-control.js';
export {mapGeometry,activePathIds,scopedNodes,mapStatistics,mapDraftPosition,mapConnectorPath,hiddenMapState,controlSource,taskDependencies} from './map-control.js';

let nextMapIdentity=0;

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

export function layoutNodes(nodes, positions = {}, geometry = mapGeometry()) {
  const result = {}, rows = new Map(), depths = new Map();
  let nextRow = 0;
  for (const node of nodes) {
    const depth = node.parent_id ? (depths.get(node.parent_id) || 0) + 1 : 0;
    let row = node.parent_id ? rows.get(node.parent_id) : undefined;
    const sibling = nodes.some(other => other.id !== node.id && other.parent_id === node.parent_id && result[other.id]);
    if (row === undefined || sibling) row = nextRow++;
    rows.set(node.id, row); depths.set(node.id, depth);
    result[node.id] = positions[node.id] || { x: depth * geometry.column, y: row * geometry.row };
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

export function createMap({nodes,branches,runs=[],streams=new Map(),layout={},selected,activeBranch,draft=null,draftMoving=false,taskLabel='',control={items:[],attention:[],receipts:[],feedback:[]},onControl,mapView={},onViewChange,onSelect,onChat,onFork,onArchive,onSave,onDraftChange,onSend,onPermission,onNewDraft}){
  const root=el('div','conversation-map');
  const reserved={...layout.positions};let migrated=false;
  for(const run of runs){const key='run_'+run.id;if(run.node_id&&reserved[key]){reserved[run.node_id]||=reserved[key];delete reserved[key];migrated=true;}else if(!run.node_id&&draft?.requestId===run.request_id&&draft.mapPosition)reserved[key]||=draft.mapPosition;}
  const branchById=new Map(branches.map(b=>[b.id,b]));
  nodes=[...nodes,...runs.filter(run=>!run.node_id&&['queued','running','waiting_approval','stopping','failed','stopped','interrupted'].includes(run.status)).map(run=>({id:'run_'+run.id,run_id:run.id,provisional:true,branch_id:run.branch_id,parent_id:run.map_origin?run.map_origin.node_id:branchById.get(run.branch_id)?.head||null,prompt:run.message||'',answer:streams.get(run.id)||'',status:run.status,activity:run.activity,tools:run.tools||[],recoverable:false,error:run.error}))];
  let view={scope:mapView.scope==='current'?'current':'all',density:mapView.density==='expanded'?'expanded':'overview',panel:mapView.panel||null},geometry=mapGeometry(view.density);
  let viewport={x:40,y:40,zoom:1,...layout.viewport},positions=layoutNodes(nodes,reserved,geometry),collapsed=[...(layout.collapsed||[])],current=selected,question=draft,currentRuns=runs;
  root.dataset.density=view.density;root.style.setProperty('--map-card-width',geometry.width+'px');root.style.setProperty('--map-card-height',geometry.height+'px');root.style.setProperty('--map-draft-height',geometry.draftHeight+'px');
  const byId=new Map(nodes.map(node=>[node.id,node])),byBranch=new Map(branches.map(branch=>[branch.id,branch]));
  const activePath=activePathIds(nodes,branches,activeBranch);let busy=currentRuns.some(run=>['queued','running','waiting_approval','stopping'].includes(run.status));
  const shownNodes=()=>visibleNodes(scopedNodes(nodes,branches,activeBranch,view.scope),collapsed);
  const panel=el('aside','map-control-panel'),hiddenNote=el('div','map-hidden-note'),summary=el('span','map-summary');panel.hidden=true;panel.setAttribute('aria-label','地图控制详情');hiddenNote.hidden=true;
  const toolbar=el('div','map-toolbar'),surface=el('div','map-surface'),world=el('div','map-world'),zoomLabel=el('span','zoom-label','100%');
  surface.tabIndex=0;surface.setAttribute('aria-label','会话地图画布');surface.append(world,panel);
  const save=()=>onSave({positions:Object.fromEntries(Object.entries(positions).map(([id,point])=>[id,{...point}])),collapsed:[...collapsed],viewport:{...viewport}});
  const finishInteraction=()=>{root.interacting=false;root.dispatchEvent(new CustomEvent('map-interaction-end'));};
  const apply=()=>{world.style.transform=`translate(${viewport.x}px,${viewport.y}px) scale(${viewport.zoom})`;zoomLabel.textContent=Math.round(viewport.zoom*100)+'%';};
  const zoom=factor=>{const old=viewport.zoom;viewport.zoom=Math.max(.2,Math.min(4,old*factor));viewport.x=surface.clientWidth/2-(surface.clientWidth/2-viewport.x)*viewport.zoom/old;viewport.y=surface.clientHeight/2-(surface.clientHeight/2-viewport.y)*viewport.zoom/old;apply();save();};
  const heading=el('div','map-toolbar-heading');heading.append(el('strong','map-toolbar-title','会话地图'),summary);toolbar.append(heading);
  const controls=el('div','map-view-controls'),scopeButtons={},densityButtons={};
  function changeView(change){view={...view,...change};geometry=mapGeometry(view.density);root.dataset.density=view.density;root.style.setProperty('--map-card-height',geometry.height+'px');onViewChange?.({...view});render();}
  for(const [value,label] of [['all','全部分支'],['current','当前路径']]){const item=button(label,()=>changeView({scope:value}),'map-view-button');scopeButtons[value]=item;controls.append(item);}
  const divider=el('span','map-toolbar-divider');divider.setAttribute('aria-hidden','true');controls.append(divider);
  for(const [value,label] of [['overview','概览'],['expanded','展开']]){const item=button(label,()=>changeView({density:value}),'map-view-button');item.setAttribute('aria-label',label+'阅读');densityButtons[value]=item;controls.append(item);}
  controls.append(iconButton('layout','整理布局',()=>{positions=layoutNodes(nodes,{},geometry);render();save();}),iconButton('locate','定位当前',()=>locate(current||byBranch.get(activeBranch)?.head)),iconButton('fit','适应视图',()=>{
    const points=shownNodes().map(node=>({...positions[node.id],height:geometry.height}));if(question&&draftVisible())points.push({...draftPosition(),height:geometry.draftHeight});if(!points.length)return;
    const minX=Math.min(...points.map(p=>p.x)),minY=Math.min(...points.map(p=>p.y)),width=Math.max(...points.map(p=>p.x+geometry.width))-minX,height=Math.max(...points.map(p=>p.y+p.height))-minY;
    viewport.zoom=Math.max(.2,Math.min(1,(surface.clientWidth-80)/width,(surface.clientHeight-80)/height));viewport.x=40-minX*viewport.zoom;viewport.y=40-minY*viewport.zoom;apply();save();
  }),iconButton('minus','缩小地图',()=>zoom(.85)),zoomLabel,iconButton('plus','放大地图',()=>zoom(1.18)));
  const list=el('div','map-list');list.hidden=true;controls.append(iconButton('list','列表导航',()=>{list.hidden=!list.hidden;}));
  nodes.forEach(node=>list.append(button(node.prompt.slice(0,60)||'回合',()=>locate(node.id),'map-list-item')));toolbar.append(controls);
  const controlTabs=el('div','map-control-tabs'),tabButtons={};
  for(const [key,label] of [['items','项目任务'],['attention','关注事项'],['receipts','阶段回执'],['feedback','反馈经验']]){const item=button(label,()=>{view.panel=view.panel===key?null:key;onViewChange?.({...view});renderPanel();updateSummary();},'map-control-tab');item.dataset.label=label;tabButtons[key]=item;controlTabs.append(item);}toolbar.append(controlTabs);
  const legend=el('div','map-legend');for(const [kind,label] of [['continuation','接续'],['fork','分支'],['pending','未提交运行 / 草稿']]){const item=el('span','map-legend-item '+kind,label);legend.append(item);}legend.append(el('span','muted','连线表示会话上下文'));toolbar.append(legend);
  function locate(id){
    const node=byId.get(id),point=positions[id];if(!node||!point)return;
    let ancestor=node.parent_id;const seen=new Set();while(ancestor&&!seen.has(ancestor)){seen.add(ancestor);collapsed=collapsed.filter(value=>value!==ancestor);ancestor=byId.get(ancestor)?.parent_id;}
    if(view.scope==='current'&&!activePath.has(id)){view.scope='all';onViewChange?.({...view});}
    current=id;render();select(node);viewport.x=surface.clientWidth/2-(point.x+geometry.width/2)*viewport.zoom;viewport.y=surface.clientHeight/2-(point.y+geometry.height/2)*viewport.zoom;apply();save();
  }
  function draftVisible(){
    if(!question)return false;
    return view.scope!=='current'||question.nodeId?view.scope!=='current'||activePath.has(question.nodeId):question.branchId===activeBranch;
  }
  function updateSummary(){
    const stats=mapStatistics(nodes,branches,currentRuns,control),shown=shownNodes(),turns=shown.filter(node=>!node.provisional).length;
    summary.textContent=`${stats.turns} 个回合 · ${stats.branches} 条分支 · ${stats.running} 运行中 · ${stats.attention} 关注${stats.approvals?' · '+stats.approvals+' 等待批准':''}`;
    if(view.scope==='current'||turns<stats.turns)summary.textContent+=` · 显示 ${turns} / ${stats.turns}`;
    for(const [value,item] of Object.entries(scopeButtons))item.setAttribute('aria-pressed',String(value===view.scope));for(const [value,item] of Object.entries(densityButtons))item.setAttribute('aria-pressed',String(value===view.density));
    for(const [key,item] of Object.entries(tabButtons)){const count=key==='attention'?stats.attention:uniqueControlRows(control[key]||[],key).length;item.textContent=item.dataset.label+(count?' '+count:'');item.setAttribute('aria-label',item.dataset.label);item.setAttribute('aria-expanded',String(view.panel===key));}
    const attention=mapAttention(currentRuns,control).map(item=>({...item,node_id:controlSource(item,nodes,branches).nodeId}));
    const hidden=hiddenMapState(nodes,shown.map(node=>node.id),{selected:current,draft:question,attention}),notes=[];
    if(hidden.selection)notes.push('选中回合');if(hidden.draft||question&&!draftVisible())notes.push('未发送草稿');if(hidden.attention)notes.push(hidden.attention+' 项关注');
    hiddenNote.replaceChildren();hiddenNote.hidden=!notes.length;if(notes.length){hiddenNote.append(el('span','',notes.join('、')+' 位于隐藏路径。'),button('查看全部分支',()=>{collapsed=[];changeView({scope:'all'});save();},'text-button'));}
  }
  const textValue=value=>typeof value==='string'?value:typeof value==='object'&&value?(value.reason||value.condition||value.status||''):'';
  const lifeLabel=value=>({active:'进行中',blocked:'受阻',paused:'已暂停',waiting:'等待条件',ready:'可继续',complete:'已验收',completed:'已结束',candidate:'待确认',confirmed:'已确认',revoked:'已撤销',unknown:'未知'}[value]||statusLabel(value));
  function emitControl(operation,item){
    const source=controlSource(item,nodes,branches),branch=byBranch.get(source.branchId||activeBranch);
    return onControl?.({...item,operation,item,node_id:source.nodeId,branch_id:source.branchId||activeBranch,thread_id:item.thread_id||branch?.thread_id,expected_revision:item.expected_revision??item.revision});
  }
  function controlAction(label,operation,item){const action=button(label,()=>emitControl(operation,item),'map-control-action');action.dataset.controlAction=operation;action.disabled=busy||!onControl;return action;}
  function renderDependencies(item,card){
    if(!item.steps?.length)return;
    card.append(el('p','muted','任务步骤依赖 · 箭头表示前置条件'));
    let graph;try{graph=taskDependencies(item.steps);}catch{card.append(el('p','muted','依赖记录需要核对；未推断关系。'));return;}
    const frame=el('div','map-task-dependencies'),canvas=el('div','map-task-canvas'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    const width=Math.max(...graph.nodes.map(n=>n.depth))*198+172,height=Math.max(...graph.nodes.map(n=>n.row))*82+68;
    canvas.style.width=width+'px';canvas.style.height=height+'px';svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('aria-hidden','true');
    const markerId='task-arrow-'+(++nextMapIdentity),defs=document.createElementNS(svg.namespaceURI,'defs'),marker=document.createElementNS(svg.namespaceURI,'marker'),arrow=document.createElementNS(svg.namespaceURI,'polygon');
    marker.id=markerId;for(const [key,value] of Object.entries({viewBox:'0 0 10 10',refX:'10',refY:'5',markerWidth:'6',markerHeight:'6',orient:'auto'}))marker.setAttribute(key,value);
    arrow.setAttribute('points','0,0 10,5 0,10');marker.append(arrow);defs.append(marker);svg.append(defs);
    const byStep=new Map(graph.nodes.map(n=>[n.id,n]));
    for(const edge of graph.edges){const from=byStep.get(edge.from),to=byStep.get(edge.to),path=document.createElementNS(svg.namespaceURI,'path'),x=from.depth*198+172,y=from.row*82+34,end=to.depth*198,endY=to.row*82+34;
      path.setAttribute('d',`M${x},${y} C${x+14},${y} ${end-14},${endY} ${end},${endY}`);path.setAttribute('marker-end',`url(#${markerId})`);svg.append(path);}
    canvas.append(svg);
    for(const step of graph.nodes){const node=el('div','map-task-step');node.style.left=step.depth*198+'px';node.style.top=step.row*82+'px';node.title=step.title||step.id;
      node.append(el('strong','',step.title||step.id),el('span','muted',({todo:'待处理',doing:'执行中',done:'已完成',blocked:'受阻',skipped:'已跳过'}[step.state]||'状态未知')));canvas.append(node);}
    frame.append(canvas);card.append(frame);
  }
  const panelRows=()=>view.panel==='attention'?mapAttention(currentRuns,control):uniqueControlRows(control[view.panel]||[],view.panel);
  let panelStamp='';
  function renderPanel(){
    const focused=panel.contains(document.activeElement)?document.activeElement:null,focusRow=focused?.closest('.map-control-row')?.dataset.controlId,focusKey=focused&&(focused.dataset.controlAction||focused.getAttribute('aria-label')||focused.textContent);
    panelStamp=JSON.stringify([view.panel,panelRows(),busy]);
    panel.replaceChildren();panel.hidden=!view.panel;if(panel.hidden)return;
    const labels={items:'项目任务',attention:'关注事项',receipts:'阶段回执',feedback:'反馈经验'},header=el('div','map-control-heading');header.append(el('strong','',labels[view.panel]||'来源详情'),iconButton('close','关闭控制详情',()=>{view.panel=null;onViewChange?.({...view});panel.hidden=true;updateSummary();}));panel.append(header);
    const rows=panelRows();
    if(!rows.length)panel.append(el('p','map-control-empty',view.panel==='items'?'没有已登记的项目任务。':'当前没有'+(labels[view.panel]||'来源记录')+'。'));
    for(const item of rows){
      const card=el('section','map-control-row'),actions=el('div','map-control-actions'),source=controlSource(item,nodes,branches);card.dataset.controlId=item.id||item.task_id||item.run_id||'';
      if(view.panel==='items'){
        card.append(el('h4','',item.objective||item.title||item.task_id),el('p','muted',`任务 ${item.task_id||'未知'} · 修订 ${item.revision??'未知'} · ${lifeLabel(item.lifecycle?.status||item.lifecycle||'unknown')}`));
        renderDependencies(item,card);
        if(item.wait_condition){card.append(el('p','',textValue(item.wait_condition)),el('p','muted','恢复条件：'+(item.wait_condition.condition||'未登记')));actions.append(controlAction('条件已满足','clear_wait',item));}else actions.append(controlAction('登记等待','set_wait',item));
        actions.append(controlAction('关联到当前会话','link_task',item),controlAction('记录反馈','feedback_stage',item));
      }else if(view.panel==='attention'){
        card.append(el('h4','',item.title||item.reason||'待处理事项'),el('p','',item.reason||''));if(item.condition||item.resume_condition)card.append(el('p','muted','恢复条件：'+textValue(item.condition||item.resume_condition)));
        if(item.action&&['set_wait','clear_wait','link_task','feedback_stage','feedback_confirm','feedback_revoke','confirm_acceptance'].includes(item.action))actions.append(controlAction(item.action==='clear_wait'?'条件已满足':item.action==='set_wait'?'登记等待':item.action==='confirm_acceptance'?'人工确认':'查看处理选项',item.action,item));
        else if(['approval','waiting_approval','tool_approval'].includes(item.kind||item.type))actions.append(button('查看原生审批',()=>{if(source.nodeId)onChat(byId.get(source.nodeId));else onControl?.({...item,operation:'open_approval',item});},'map-control-action'));
      }else if(view.panel==='receipts'){
        card.append(el('h4','',`运行 ${String(item.run_id||'未知').slice(0,12)}`));const stages=el('ol','map-receipt-stages'),stageLabels={started:'已开始',effects_observed:'副作用已核对',evidence_validated:'验收证据已验证',task_committed:'任务已写回',cost_settled:'费用已有原生结算记录',closed:'已关闭'};
        for(const stage of item.stages||[])stages.append(el('li','',stageLabels[stage.stage]||stage.stage));card.append(stages);
        if(item.recovery)card.append(el('p','muted',({readback_required:'副作用结果未知，需要核对已发生的操作',closeout_required:'存在尚未收尾的阶段',usage_unknown:'用量或价格不完整，费用状态未知',closed:'阶段记录已收尾'}[item.recovery]||textValue(item.recovery)||'恢复状态未知')));if(item.recovery?.reason&&item.recovery.reason!==textValue(item.recovery))card.append(el('p','',item.recovery.reason));
      }else if(view.panel==='feedback'){
        card.append(el('h4','',item.behavior||item.context||'反馈经验'),el('p','muted',`${lifeLabel(item.state)} · 任务修订 ${item.task_revision??'未知'}`));
        for(const [key,label] of [['context','情境'],['outcome','结果'],['boundary','边界']])if(item[key])card.append(el('p','',label+'：'+item[key]));
        if(item.state==='candidate')actions.append(controlAction('确认经验','feedback_confirm',item));if(['candidate','confirmed'].includes(item.state))actions.append(controlAction('撤销经验','feedback_revoke',item));
      }
      if(source.nodeId&&byId.has(source.nodeId))actions.prepend(button('定位来源',()=>locate(source.nodeId),'map-control-action'));else if(source.branchId)actions.prepend(button('打开来源会话',()=>onChat({id:null,branch_id:source.branchId}),'map-control-action'));else card.append(el('p','muted','来源不在当前会话；保留原始来源记录。'));
      card.append(actions);panel.append(card);
    }
    if(focusKey){const replacement=[...panel.querySelectorAll('button')].find(item=>item.closest('.map-control-row')?.dataset.controlId===focusRow&&(item.dataset.controlAction||item.getAttribute('aria-label')||item.textContent)===focusKey);if(replacement&&!replacement.disabled)replacement.focus({preventScroll:true});}
  }
  const runPlaceholder=node=>node.status==='queued'?'等待执行…':node.status==='running'?(node.activity==='tools'?'正在调用工具…':'正在思考…'):node.status==='waiting_approval'?'等待原生工具批准…':node.status==='stopping'?'正在停止…':statusLabel(node.status)+' · 尚无已保存回答。';
  const usable=node=>!busy&&node.recoverable&&node.status==='completed';
  function startDraft(node,kind,text=''){
    let ancestor=node?.id;while(ancestor){collapsed=collapsed.filter(id=>id!==ancestor);ancestor=nodes.find(n=>n.id===ancestor)?.parent_id;}
    question={nodeId:node?.id||null,branchId:node?.branch_id||activeBranch,kind,text,permission_mode:kind==='fork'||(node&&byBranch.get(node.branch_id)?.head!==node.id)?'default':byBranch.get(node?.branch_id||activeBranch)?.permission_mode||'default'};onDraftChange(question);render();
    const point=draftPosition();viewport.x=Math.min(viewport.x,surface.clientWidth-(point.x+geometry.width+20)*viewport.zoom-30);viewport.y=Math.min(viewport.y,surface.clientHeight-(point.y+geometry.draftHeight+15)*viewport.zoom-30);apply();save();world.querySelector('.map-draft textarea')?.focus();
  }
  function draftPosition(){return mapDraftPosition(question,positions,geometry);}
  const markerId='map-arrow-'+(++nextMapIdentity);
  function addMarkers(svg){
    const defs=document.createElementNS(svg.namespaceURI,'defs');
    for(const kind of ['continuation','fork','draft']){const marker=document.createElementNS(svg.namespaceURI,'marker');marker.id=markerId+'-'+kind;marker.setAttribute('viewBox','0 0 10 10');marker.setAttribute('refX','10');marker.setAttribute('refY','5');marker.setAttribute('markerWidth','7');marker.setAttribute('markerHeight','7');marker.setAttribute('orient','auto-start-reverse');const arrow=document.createElementNS(svg.namespaceURI,'polygon');arrow.setAttribute('points','0,0 10,5 0,10');arrow.setAttribute('fill',kind==='continuation'?'var(--muted)':'var(--accent)');marker.append(arrow);defs.append(marker);}svg.append(defs);
  }
  function line(svg,from,to,fork=false,draft=false,toHeight=geometry.height){
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',mapConnectorPath(from,to,geometry,toHeight));path.setAttribute('marker-end',`url(#${markerId}-${draft?'draft':fork?'fork':'continuation'})`);path.classList.add(fork?'fork-link':'continuation');if(draft)path.classList.add('draft-link');svg.append(path);
  }
  function renderDraft(svg){
    if(!question&&!nodes.length){question={nodeId:null,branchId:activeBranch,kind:'root',text:'',permission_mode:byBranch.get(activeBranch)?.permission_mode||'default'};onDraftChange(question);}
    if(!question||!draftVisible()||question.requestId&&currentRuns.some(run=>run.request_id===question.requestId)||question.kind==='root'&&busy)return;const point=draftPosition(),card=el('form','map-draft');card.style.left=point.x+'px';card.style.top=point.y+'px';
    if(question.nodeId&&positions[question.nodeId])line(svg,positions[question.nodeId],point,question.kind==='fork',true,geometry.draftHeight);
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
    const svg=world.querySelector('.map-links');if(!svg)return;svg.replaceChildren();addMarkers(svg);
    svg.setAttribute('width',Math.max(1400,...Object.values(positions).map(point=>point.x+800)));svg.setAttribute('height',Math.max(1400,...Object.values(positions).map(point=>point.y+800)));
    const shown=shownNodes(),ids=new Set(shown.map(node=>node.id));for(const node of shown)if(node.parent_id&&ids.has(node.parent_id))line(svg,positions[node.parent_id],positions[node.id],byId.get(node.parent_id)?.branch_id!==node.branch_id,node.provisional);
    const card=world.querySelector('.map-draft');if(card&&question){const point=draftPosition();card.style.left=point.x+'px';card.style.top=point.y+'px';if(question.nodeId&&positions[question.nodeId])line(svg,positions[question.nodeId],point,question.kind==='fork',true,geometry.draftHeight);}
  }
  function render(){
    world.replaceChildren();const shown=shownNodes(),ids=new Set(shown.map(node=>node.id)),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('map-links');
    svg.setAttribute('width',Math.max(1400,...Object.values(positions).map(point=>point.x+800)));svg.setAttribute('height',Math.max(1400,...Object.values(positions).map(point=>point.y+800)));world.append(svg);addMarkers(svg);
    for(const node of shown){if(node.parent_id&&ids.has(node.parent_id))line(svg,positions[node.parent_id],positions[node.id],byId.get(node.parent_id)?.branch_id!==node.branch_id,node.provisional);}
    for(const node of shown){
      const card=el('article','map-card'+(node.provisional?' map-run-card':'')+(node.id===current?' selected':'')+(activePath.has(node.id)?' active-path':''));card.dataset.nodeId=node.id;if(node.provisional)card.dataset.runId=node.run_id;card.style.left=positions[node.id].x+'px';card.style.top=positions[node.id].y+'px';
      const handle=el('button','map-handle');handle.type='button';handle.setAttribute('aria-label','拖动回合卡片');handle.append(icon('grip'));
      const header=el('div','map-card-header'),title=el('h3','',node.prompt.slice(0,95)||'用户回合'),index=node.provisional?(byBranch.get(node.branch_id)?.node_ids||[]).length+1:Math.max(1,(byBranch.get(node.branch_id)?.node_ids||[]).indexOf(node.id)+1),meta=el('div','map-meta');
      meta.append(el('span','map-branch-label',`${byBranch.get(node.branch_id)?.name||'会话'} · 第 ${index} 轮`),badge(node.status));if(node.tools?.length)meta.append(el('span','tool-count',`工具 ${node.tools.length}`));header.append(title,meta);
      const content=el('div','map-body'),answer=node.answer||'',excerpt=prose((view.density==='overview'?answer.slice(0,1400):answer)||node.error||(node.provisional?runPlaceholder(node):'没有已保存的完整回答'),'map-excerpt');content.append(excerpt);if(view.density==='overview'&&answer.length>1400)content.append(el('span','map-excerpt-note','正文节选 · 展开阅读全文'));
      if(node.task_summary||node.progress||node.continuation){const facts=el('div','map-node-facts');if(node.task_summary){const task=node.task_summary,taskLink=button('任务：'+(task.objective||task.task_id),()=>{view.panel='items';onViewChange?.({...view});renderPanel();updateSummary();},'map-task-link');taskLink.title=task.objective||task.task_id;facts.append(taskLink);}for(const [key,label] of [['progress','进展'],['continuation','续跑']])if(node[key]){const fact=node[key],value=typeof fact==='string'?fact:key==='progress'?({progress:'发现当前新证据',no_change:'未发现有效增量',unknown:'证据不足，状态未知'}[fact.status]||'未知'):({execute:'继续执行',wait:'等待条件',replan:'重新核对计划',stop:'已停止'}[fact.action]||'未知');const text=el('span','',label+'：'+value);text.title=label+'：'+(fact.reason||value);facts.append(text);}card.append(facts);}
      const actions=el('div','map-actions');if(!node.provisional)actions.append(iconButton('info','详情',()=>select(node)));actions.append(iconButton('chat','打开聊天',()=>onChat(node.provisional?{...node,id:null}:node)));
      const legacyFork=iconButton('fork','分叉',()=>onFork(node));legacyFork.disabled=!usable(node);actions.append(legacyFork);if(onArchive){const archive=iconButton('archive','归档对话',()=>onArchive(node));archive.disabled=node.provisional;actions.append(archive);}
      const follow=iconButton('plus','继续追问',()=>startDraft(node,'continue'),'map-port follow-port'),fork=iconButton('fork','创建分支',()=>startDraft(node,'fork'),'map-port branch-port');follow.disabled=fork.disabled=!usable(node);
      const collapse=iconButton(collapsed.includes(node.id)?'plus':'minus',collapsed.includes(node.id)?'展开':'折叠',()=>{collapsed=collapsed.includes(node.id)?collapsed.filter(id=>id!==node.id):[...collapsed,node.id];render();save();[...world.querySelectorAll('.map-card')].find(card=>card.dataset.nodeId===node.id)?.querySelector('.collapse-port')?.focus();},'map-port collapse-port');collapse.hidden=!nodes.some(child=>child.parent_id===node.id);
      card.prepend(handle,header,content);card.append(actions,follow,fork,collapse);world.append(card);
      card.addEventListener('click',event=>{if(event.target.closest('button,textarea,input,a,summary')||window.getSelection()?.toString())return;select(node);});
      let dragged=false;
      card.addEventListener('click',event=>{if(dragged){event.preventDefault();event.stopImmediatePropagation();dragged=false;}},true);
      const drag=event=>{
        if(event.button!==0)return;
        event.preventDefault();dragged=false;root.interacting=true;card.classList.add('dragging');const target=event.currentTarget;target.setPointerCapture(event.pointerId);const start={x:event.clientX,y:event.clientY},position={...positions[node.id]};
        const move=e=>{positions[node.id]={x:position.x+(e.clientX-start.x)/viewport.zoom,y:position.y+(e.clientY-start.y)/viewport.zoom};card.style.left=positions[node.id].x+'px';card.style.top=positions[node.id].y+'px';updateLinks();save();};
        const moving=e=>{if(Math.hypot(e.clientX-start.x,e.clientY-start.y)>3)dragged=true;move(e);};
        const end=()=>{target.removeEventListener('pointermove',moving);target.removeEventListener('pointerup',end);target.removeEventListener('pointercancel',end);target.removeEventListener('lostpointercapture',end);card.classList.remove('dragging');updateLinks();save();finishInteraction();};
        target.addEventListener('pointermove',moving);target.addEventListener('pointerup',end,{once:true});target.addEventListener('pointercancel',end,{once:true});target.addEventListener('lostpointercapture',end,{once:true});
      };
      handle.addEventListener('pointerdown',drag);header.addEventListener('pointerdown',drag);
      handle.addEventListener('keydown',event=>{const shift={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]}[event.key];if(!shift)return;event.preventDefault();positions[node.id].x+=shift[0];positions[node.id].y+=shift[1];render();save();[...world.querySelectorAll('.map-card')].find(card=>card.dataset.nodeId===node.id)?.querySelector('.map-handle').focus();});
    }
    renderDraft(svg);updateSummary();
    if(!shown.length&&nodes.length){const empty=el('div','map-scope-empty');empty.append(el('p','','当前路径没有可显示回合。'),button('查看全部分支',()=>changeView({scope:'all'}),'secondary'));world.append(empty);}
  }
  function select(node){current=node.id;world.querySelectorAll('.map-card').forEach(card=>card.classList.toggle('selected',card.dataset.nodeId===node.id));if(!node.provisional)onSelect?.(node);updateSummary();}
  root.addEventListener('mouseup',event=>{
    // Keep the selection action alive through its own mouseup/click sequence.
    if(event.target.closest('.map-quote-button'))return;
    root.querySelector('.map-quote-button')?.remove();const selection=window.getSelection(),text=selection?.toString().trim(),anchor=selection?.anchorNode?.parentElement,excerpt=anchor?.closest('.map-excerpt');if(!text||!excerpt||!root.contains(excerpt))return;
    const node=byId.get(excerpt.closest('.map-card').dataset.nodeId);if(!usable(node))return;
    const quote=button('引用追问',()=>{startDraft(node,'fork',`引用回合 ${node.id.slice(0,8)}：\n> ${text.replaceAll('\n','\n> ')}\n\n`);quote.remove();},'map-quote-button');
    const rect=selection.getRangeAt(0).getBoundingClientRect(),bounds=root.getBoundingClientRect();quote.style.left=Math.max(8,Math.min(rect.left-bounds.left,root.clientWidth-100))+'px';quote.style.top=Math.max(55,rect.top-bounds.top-36)+'px';root.append(quote);
  });
  surface.addEventListener('pointerdown',event=>{
    if(event.target.closest('.map-card,.map-draft,.map-control-panel'))return;root.interacting=true;surface.setPointerCapture(event.pointerId);const start={x:event.clientX,y:event.clientY,vx:viewport.x,vy:viewport.y};
    const move=e=>{viewport.x=start.vx+e.clientX-start.x;viewport.y=start.vy+e.clientY-start.y;apply();save();};const end=()=>{surface.removeEventListener('pointermove',move);surface.removeEventListener('pointerup',end);surface.removeEventListener('pointercancel',end);save();finishInteraction();};surface.addEventListener('pointermove',move);surface.addEventListener('pointerup',end,{once:true});surface.addEventListener('pointercancel',end,{once:true});
  });
  surface.addEventListener('wheel',event=>{if(event.target.closest('.map-body,.map-draft,.map-control-panel'))return;event.preventDefault();if(event.ctrlKey||event.metaKey)viewport.zoom=Math.max(.2,Math.min(4,viewport.zoom*Math.exp(-event.deltaY*.002)));else{viewport.x-=event.deltaX;viewport.y-=event.deltaY;}apply();save();},{passive:false});
  root.updateRunCards=(nextRuns,nextControl)=>{
    currentRuns=nextRuns;if(nextControl)control=nextControl;busy=currentRuns.some(run=>['queued','running','waiting_approval','stopping'].includes(run.status));
    for(const run of currentRuns){
      const node=byId.get('run_'+run.id);if(!node||run.node_id)continue;
      Object.assign(node,{status:run.status,activity:run.activity,tools:run.tools||[],error:run.error,answer:streams.get(run.id)||''});
      const card=[...world.querySelectorAll('.map-run-card')].find(card=>card.dataset.runId===run.id);if(!card)continue;
      const mark=card.querySelector('.badge');mark.className='badge '+run.status;mark.textContent=statusLabel(run.status);
      const meta=card.querySelector('.map-meta');let count=meta.querySelector('.tool-count');
      if(node.tools?.length){if(!count){count=el('span','tool-count');meta.append(count);}count.textContent='工具 '+node.tools.length;}else count?.remove();
      updateProse(card.querySelector('.map-excerpt'),(view.density==='overview'?node.answer.slice(0,1400):node.answer)||node.error||runPlaceholder(node),prose);
    }
    for(const card of world.querySelectorAll('.map-card')){const node=byId.get(card.dataset.nodeId);for(const label of ['继续追问','创建分支','分叉'])card.querySelector(`[aria-label="${label}"]`)?.toggleAttribute('disabled',!usable(node));}
    const draftCard=world.querySelector('.map-draft');if(draftCard){draftCard.querySelector('textarea').disabled=busy;draftCard.querySelector('[aria-label="发送追问"]').disabled=busy||question.blocked;for(const label of ['取消追问','权限管理'])draftCard.querySelector(`[aria-label="${label}"]`).disabled=busy;}
    for(const action of panel.querySelectorAll('[data-control-action]'))action.disabled=busy||!onControl;updateSummary();if(view.panel&&JSON.stringify([view.panel,panelRows(),busy])!==panelStamp)renderPanel();
  };
  root.append(toolbar,hiddenNote,surface,list,el('span','map-help','拖动画布 · 手柄移动卡片 · 卡片侧边追问 / 分支 · ⌘ / Ctrl + 滚轮缩放'));render();renderPanel();apply();if(migrated)queueMicrotask(()=>{if(root.isConnected)save();});return root;
}
