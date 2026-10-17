// Load as a classic script so file:// can show a useful launch instruction
// before browser security blocks the workbench's ES modules.
function explainEntry(title, description, retry=false) {
  window.ignovateStartup?.ready();
  const section=document.createElement('section');section.className='empty-state';section.setAttribute('role','alert');
  const heading=document.createElement('h1');heading.textContent=title;
  const detail=document.createElement('p');detail.textContent=description;
  const command=document.createElement('pre');command.className='code-block';command.textContent='python -m nailong.gui --browser';
  const note=document.createElement('p');note.textContent='在项目目录的 Python 环境中运行此命令，浏览器会自动打开已连接的工作台。';
  section.append(heading,detail,command,note);
  if(retry){const button=document.createElement('button');button.className='primary';button.textContent='重新加载';button.addEventListener('click',()=>location.reload());section.append(button);}
  document.getElementById('app').replaceChildren(section);
}
async function startWorkbench() {
  const { $, el, button, icon, iconButton, field, selectField, modal, prose, badge, toast, statusLabel, download, installTooltips, closePopover, openPopover, choicePopover } = await import('./ui.js');
  const { createMap,reconcileMapDraft } = await import('./map.js');
  const {createTrace}=await import('./trace.js');
  const {renderUsageDetails}=await import('./usage-details.js');
  const { permissionGlyph,permissionIcon } = await import('./icons.js');
  const {renderTurn,renderLive,updateLive,renderArtifacts,installRuntimeClock,elapsedText,configureConversation,conversationPresentation}=await import('./conversation.js');
  const {createPlugins}=await import('./plugins.js');
  const {conversationGroups,visibleBranches,relativeTime}=await import('./sidebar.js');
  const {fileChangesReview}=await import('./diff.js');
  const {showSettings}=await import('./settings.js');
  const {effectiveUIPreferences,shortcutMatches}=await import('./preferences.js');
  const {synchronize,patchElement,updateProse}=await import('./rendering.js');
  const {installRubberSegment,installSwipeRow,installBranchedMenu,statusMark}=await import('./react-bits.js');
  const {mountPromptBar,mountBranchedMenu}=await import('./react-bits-islands.js');

const supplied = new URLSearchParams(location.hash.slice(1)).get('token');
if (supplied) { sessionStorage.setItem('ignovate-token', supplied); history.replaceState(null, '', location.pathname); }
const token = sessionStorage.getItem('ignovate-token') || '';
const state = { snapshot: null, branch: null, node: null, view: 'chat', profile:'chat', streams: new Map(), search: '', chatSearch:'', chatLimit:80, archive: false,
  sidebar:window.matchMedia('(min-width:901px)').matches&&localStorage.getItem('ignovate-sidebar')!=='closed',detail:localStorage.getItem('ignovate-detail')==='open' };
const working = new Set(), mapDrafts=new Map(),movingMapDrafts=new Set();
const traceViews=new Map();
const mapViews=new Map();
const pendingDrafts = new Map();
const pendingLayouts = new Map(),layoutTimers=new Map(),layoutSaves=new Map();
let draftTimer, pollTimer, menuOrigin, creationPromise, promptUI;
let workspaceCatalog=[],catalogKey='',catalogLoading=null;
let settingsDialog,uiPreferences=effectiveUIPreferences();
let sidebarStamp='';const dirtyStreams=new Set();let streamPaint=0,connectionInterrupted=false;
const projectBusy=()=>state.snapshot?.runs.some(run=>['queued','running','waiting_approval','stopping'].includes(run.status));
function applyPreferences(envelope){
  uiPreferences=effectiveUIPreferences(envelope);configureConversation(uiPreferences);
  document.documentElement.style.setProperty('--chat-font-size',uiPreferences.font_size+'px');
  localStorage.setItem('ignovate-theme',uiPreferences.theme);renderTheme();
}
const navPreferences=readNavPreferences();
function readNavPreferences(){try{const data=JSON.parse(localStorage.getItem('ignovate-navigation')||'{}');return {group:['workspace','tree','list'].includes(data.group)?data.group:'tree',sort:data.sort==='manual'?'manual':'updated',archive:['hidden','all','only'].includes(data.archive)?data.archive:'hidden',collapsed:Array.isArray(data.collapsed)?data.collapsed:[],order:data.order&&typeof data.order==='object'?data.order:{}};}catch{return {group:'tree',sort:'updated',archive:'hidden',collapsed:[],order:{}};}}
function saveNavPreferences(){localStorage.setItem('ignovate-navigation',JSON.stringify(navPreferences));renderSidebar();}

async function api(path, body) {
  const response = await fetch('/api/'+path, { method: body ? 'POST' : 'GET',
    headers: { Authorization: 'Bearer '+token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  const result = await response.json(); if (!response.ok) throw new Error(result.error || '连接失败'); return result;
}
async function command(name, payload = {}, refresh = true) {
  const result = await api('command', { name, payload: { project_id: state.snapshot?.project.id, ...payload } }); if (refresh) await reload(); return result;
}
const active = () => state.snapshot?.branches.find(b => b.id === state.branch);
const nodesById = () => new Map(state.snapshot.nodes.map(n => [n.id, n]));
const path = () => (active()?.node_ids || []).map(id => nodesById().get(id)).filter(Boolean);
const running = () => state.snapshot?.runs.filter(r => r.branch_id === state.branch && ['running','waiting_approval','stopping','queued'].includes(r.status)) || [];

function shell() {
  const app = $('#app'); app.replaceChildren();
  const sidebar = el('aside', 'sidebar'); sidebar.id = 'sidebar';
  const brand = el('div', 'brand'); brand.append(el('strong', 'brand-name', 'Ignovate'), el('strong', 'brand-note', 'Harness'),iconButton('sidebar','收起侧栏',()=>toggleSidebar(false)));
  const newChat=button('',createConversation,'new-conversation','新会话');newChat.append(icon('plus'),el('span','','新会话'));
  sidebar.append(brand,newChat);
  const plugins=button('',()=>switchView('plugins'),'plugin-navigation','插件');plugins.id='plugin-navigation';plugins.append(icon('plugin'),el('span','','插件'));sidebar.append(plugins);
  const search = el('input', 'search'); search.placeholder = '搜索会话与内容'; search.setAttribute('aria-label', '搜索会话');
  search.addEventListener('input', () => { state.search=search.value; renderSidebar(); });
  search.hidden=true;
  const workspaceLabel=el('div','workspace-heading');workspaceLabel.append(el('span','','工作区'),iconButton('search','搜索会话',()=>{search.hidden=!search.hidden;if(!search.hidden)search.focus();}),iconButton('sliders','会话分组与筛选',sidebarPreferences),iconButton('folder','添加工作区',openProject));
  const list = el('div', 'session-list'); list.id = 'sessions';
  installBranchedMenu(list);
  const footer = el('div', 'sidebar-footer');
  footer.append(iconButton('folder','选择工作区',workspacePicker),iconButton('archive','归档会话',()=>{navPreferences.archive=navPreferences.archive==='only'?'hidden':'only';saveNavPreferences();}),iconButton('settings','设置',()=>settings()));
  sidebar.append(workspaceLabel,search,list,footer);
  const split = el('div', 'resize-handle'); split.setAttribute('aria-hidden', 'true');
  split.addEventListener('pointerdown', e => {
    split.setPointerCapture(e.pointerId);
    split.onpointermove = p => document.documentElement.style.setProperty('--sidebar-width', Math.max(200, Math.min(340,p.clientX))+'px');
    split.onpointerup = () => { split.onpointermove=null; };
  });
  const main = el('main', 'workbench');
  const header = el('header', 'workbench-header');
  const title = el('div', 'workspace-title'); title.id = 'workspace-title';
  const navigation = el('nav', 'view-tabs');
  for (const [id, label] of [['chat','聊天'],['map','会话地图'],['trace','轨迹']]) {
    const tab = button(label, () => switchView(id), 'view-tab'); tab.dataset.view=id; navigation.append(tab);
  }
  installRubberSegment(navigation);
  const sidebarToggle=iconButton('sidebar','切换侧栏',()=>toggleSidebar());sidebarToggle.id='sidebar-toggle';
  const detailToggle=iconButton('panel','切换详情栏',()=>toggleDetail());detailToggle.id='detail-toggle';
  const themeToggle=iconButton('monitor','主题',theme);themeToggle.id='theme-toggle';
  header.append(sidebarToggle,title,navigation,detailToggle,themeToggle);
  const subheader = el('div', 'subheader'); subheader.id = 'subheader';
  const content = el('section', 'work-content'); content.id = 'content';
  const composerArea = el('section', 'composer-area');
  const approval = el('div', 'approval-area'); approval.id = 'approvals';
  const composerWrap = el('div', 'composer-wrap react-prompt-host');
  const target = el('div', 'composer-target'); target.id = 'composer-target';
  const status=el('div','status-bar'); status.id='status-bar';
  promptUI=mountPromptBar(composerWrap,{
    onDraft:saveDraft,onSend:send,onStop:stopRun,onPermission:()=>permissions(),
    onModelChange:name=>command('model',{action:'select',name,project_id:state.snapshot.project.id}),
    onEffortChange:label=>{const conf=state.snapshot.configuration,effort=conf.reasoning_options.find(option=>option[1]===label)?.[0];if(!effort)throw new Error('推理选项已变化，请重新选择。');return command('reasoning',{model:conf.model,effort,project_id:state.snapshot.project.id});},
    onSource:key=>key==='file'?referenceFile():key==='folder'?referenceFolder():chooseTaskMode(key),
    onCommand:chooseTaskMode,onClearMode:()=>{state.profile='chat';renderStatus();},
    onSettings:()=>settings('models'),onAddModel:addPromptModel,onError:error=>toast(error.message)
  });
  composerArea.append(approval,target,composerWrap,status);
  main.append(header,subheader,content,composerArea);
  const inspector=el('aside','inspector'); inspector.id='inspector'; inspector.append(el('div','inspector-title','详情与成果'),el('p','muted','选择一个回合，查看对话产物、工具过程和来源。'));
  const backdrop=button('',()=>toggleSidebar(false),'sidebar-backdrop','关闭侧栏');backdrop.hidden=true;backdrop.id='sidebar-backdrop';
  app.append(sidebar,split,backdrop,main,inspector);
  applyPaneState();renderTheme();installTooltips();installRuntimeClock();
}

function resizeComposer(){const input=$('#composer'),width=input.clientWidth;if(!width||input._sizingText===input.value&&input._sizingWidth===width)return;input._sizingText=input.value;input._sizingWidth=width;input.style.height='auto';const height=Math.min(160,input.scrollHeight);input.style.height=height+'px';input.style.overflowY=input.scrollHeight>160?'auto':'hidden';}
const taskModes=[['chat','实施 / 对话','直接处理任务'],['plan','制定计划','先形成计划，再确认实施'],['review','只读审查','审查当前内容与改动'],['goal','目标模式','按轮次和费用上限持续推进目标']];
let goalLimits={max_rounds:20,max_cost_usd:1};
function chooseTaskMode(value){
  if(!taskModes.some(row=>row[0]===value))return;
  if(value!=='goal'){state.profile=value;renderStatus();return;}
  const body=el('div');body.append(field('最多轮次','max_rounds',String(goalLimits.max_rounds),'number'),field('费用上限（美元）','max_cost_usd',String(goalLimits.max_cost_usd),'number'));body.querySelector('[name=max_cost_usd]').step='0.01';
  modal('目标模式',body,'启用目标模式',data=>{const rounds=Number(data.max_rounds),cost=Number(data.max_cost_usd);if(!Number.isInteger(rounds)||rounds<1||rounds>100||!Number.isFinite(cost)||cost<=0||cost>100)throw new Error('请设置 1–100 轮及 0–100 美元之间的正数。');goalLimits={max_rounds:rounds,max_cost_usd:cost};state.profile=value;renderStatus();});
}
function addPromptModel(){
  const projectId=state.snapshot.project.id,body=el('div');body.append(field('模型名称','name',''),field('模型 ID','model',''),el('p','muted','新增模型使用当前连接：'+state.snapshot.configuration.api_base));
  modal('新增模型',body,'添加并选择',data=>{if(state.snapshot.project.id!==projectId)throw new Error('工作区已切换，请重新添加。');return command('model',{action:'add',...data,project_id:projectId});});
}
function setComposerText(text){promptUI.setDraft(text);}
async function permissions(anchor=$('#permission-mode')){
  if(creationPromise)await creationPromise;
  if(!active())await createConversation(true);
  const branch=active(),projectId=state.snapshot.project.id;
  choicePopover(anchor,'权限管理',[
    ['default','请求批准','文件修改与命令执行均请求批准',permissionGlyph('default')],
    ['acceptEdits','帮我批准','项目内文件修改自动批准，命令执行仍需批准',permissionGlyph('acceptEdits')],
    ['bypassPermissions','完全访问权限','任意文件路径与网络目标，跳过逐项审批；受系统权限约束',permissionGlyph('bypassPermissions')]
  ],branch.permission_mode||'default',mode=>command('permission',{branch_id:branch.id,mode,project_id:projectId}));
}
function mapPermissions(anchor,draft,onChange){
  choicePopover(anchor,'权限管理',[
    ['default','请求批准','文件修改与命令执行均请求批准',permissionGlyph('default')],
    ['acceptEdits','帮我批准','项目内文件修改自动批准，命令执行仍需批准',permissionGlyph('acceptEdits')],
    ['bypassPermissions','完全访问权限','跳过逐项审批，受系统权限约束',permissionGlyph('bypassPermissions')]
  ],draft.permission_mode||'default',mode=>{draft.permission_mode=mode;draft.requestId=null;onChange();});
}
function applyPaneState(){
  const narrow=window.matchMedia('(max-width:900px)').matches;
  $('#sidebar').hidden=!state.sidebar;$('.resize-handle').hidden=!state.sidebar||narrow;
  document.body.classList.toggle('navigation-open',state.sidebar);
  $('#sidebar-backdrop').hidden=!(narrow&&state.sidebar);
  $('#sidebar-toggle').hidden=state.sidebar;$('#sidebar-toggle').setAttribute('aria-expanded',String(state.sidebar));
  $('#inspector').hidden=!state.detail||state.view==='plugins';$('#detail-toggle').disabled=state.view==='plugins';$('#detail-toggle').setAttribute('aria-expanded',String(state.detail&&state.view!=='plugins'));
}
function toggleSidebar(value=!state.sidebar){state.sidebar=value;localStorage.setItem('ignovate-sidebar',value?'open':'closed');applyPaneState();}
function toggleDetail(value=!state.detail){state.detail=value;localStorage.setItem('ignovate-detail',value?'open':'closed');applyPaneState();}
window.addEventListener('resize',applyPaneState);

async function reload() {
  const next=await api('snapshot');
  // A snapshot may have been captured before a layout POST was acknowledged.
  if(next.project.id===state.snapshot?.project.id){
    for(const [conversation,layout] of Object.entries(state.snapshot.layouts||{})){
      if(layout.revision>(next.layouts?.[conversation]?.revision??-1)){next.layouts||={};next.layouts[conversation]=layout;}
    }
  }
  state.snapshot=next;
  applyPreferences(next.gui_settings);
  settingsDialog?.updateBusy();
  state.receivedAt=performance.now();
  if (!active()) {
    const remembered=localStorage.getItem('ignovate-branch-'+state.snapshot.project.id);
    state.branch=state.snapshot.branches.find(b=>b.id===remembered)?.id || state.snapshot.branches.find(b=>!b.archived && b.is_default)?.id || state.snapshot.branches[0]?.id;
  }
  renderSidebar(); renderHeader(); renderContent(); renderApprovals(); renderStatus();
}
function sidebarPreferences(){
  const anchor=[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='会话分组与筛选'),popup=openPopover(anchor,'会话分组与筛选');popup.panel.setAttribute('role','menu');
  const groups=[['分组方式','group',[['workspace','按工作区'],['tree','按工作区树'],['list','单列表']]],['排序方式','sort',[['manual','手动排序'],['updated','最近更新']]],['筛选会话','archive',[['hidden','隐藏已归档'],['all','全部对话（显示已归档）'],['only','仅显示已归档']]]];
  for(const [label,key,choices] of groups){popup.panel.append(el('div','menu-heading',label));for(const [value,text] of choices){const item=button('',()=>{navPreferences[key]=value;saveNavPreferences();popup.close();},'choice-item');item.setAttribute('role','menuitemradio');item.setAttribute('aria-label',text);item.setAttribute('aria-checked',String(navPreferences[key]===value));item.append(el('span','',text),icon(navPreferences[key]===value?'check':'info'));if(navPreferences[key]!==value)item.lastChild.classList.add('choice-placeholder');popup.panel.append(item);}popup.panel.append(el('hr'));}
  popup.panel.lastChild.remove();popup.show();popup.panel.querySelector('button')?.focus();
  popup.panel.addEventListener('keydown',event=>{if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const items=[...popup.panel.querySelectorAll('button')],index=items.indexOf(document.activeElement);items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();});
}
async function loadWorkspaceCatalog(){
  const projectId=state.snapshot.project.id,paths=recentWorkspaces().map(w=>w.path),key=JSON.stringify(paths);
  if(key===catalogKey||catalogLoading)return;
  catalogLoading=command('workspaces',{paths,project_id:projectId},false).then(items=>{if(state.snapshot.project.id===projectId){workspaceCatalog=Array.isArray(items)?items:[];catalogKey=key;renderSidebar();}}).catch(error=>toast(error.message)).finally(()=>{catalogLoading=null;if(JSON.stringify(recentWorkspaces().map(w=>w.path))!==key)loadWorkspaceCatalog();});
  return catalogLoading;
}
function moveSession(branch,direction){
  const key=state.snapshot.project.id,all=visibleBranches(state.snapshot.branches,{...navPreferences,order:navPreferences.order[key]||[]});
  const items=navPreferences.group==='tree'?all.filter(b=>branch.parent_id?b.parent_id===branch.parent_id:!b.parent_id):all,index=items.findIndex(b=>b.id===branch.id),target=index+direction;
  if(target<0||target>=items.length)return;
  [items[index],items[target]]=[items[target],items[index]];
  if(navPreferences.group==='tree'&&!branch.parent_id){const families=conversationGroups(all);navPreferences.order[key]=items.flatMap(root=>families.find(g=>g.id===root.conversation_id)?.branches.map(b=>b.id)||[root.id]);}
  else{let position=0;navPreferences.order[key]=all.map(b=>items.some(item=>item.id===b.id)?items[position++].id:b.id);}
  navPreferences.sort='manual';saveNavPreferences();
}
function updateSidebarSelection() {
  const list=$('#sessions'),project=state.snapshot.project,selection=project.path+':'+state.branch;
  for(const row of list.querySelectorAll('.session')){
    const selected=row.dataset.workspacePath===project.path&&row.dataset.branchId===state.branch;
    row.classList.toggle('selected',selected);
    if(selected)row.setAttribute('aria-current','page');else row.removeAttribute('aria-current');
    row.closest('.session-row').classList.toggle('branch-path-active',selected);
    if(list._selection!==selection)row.closest('.session-row').closeSwipe?.();
  }
  for(const family of list.querySelectorAll('.conversation-group[data-react-island]')){
    const active=family.closest('.workspace-group').dataset.workspacePath===project.path?state.branch:'';
    if(family._reactIsland.props.active!==active)family._reactIsland.update({active});
  }
  list._selection=selection;
}
function renderSidebar() {
  const project=state.snapshot.project;rememberWorkspace(project);
  const stamp=JSON.stringify([project.id,state.search,navPreferences,workspaceCatalog,recentWorkspaces(),Math.floor(Date.now()/60000),state.snapshot.branches.map(b=>[b.id,b.name,b.parent_id,b.archived,b.summary,b.head,b.updated_at,b.created,b.is_default]),state.snapshot.runs.map(r=>[r.id,r.branch_id,r.status])]);
  // Selection must update the mounted SVG paths; rebuilding them skips CSS transitions.
  if(stamp===sidebarStamp){updateSidebarSelection();return;}sidebarStamp=stamp;
  const list=$('#sessions');list.replaceChildren();const query=state.search.toLocaleLowerCase();
  const workspaces=recentWorkspaces().map(w=>w.path===project.path?{...project,branches:state.snapshot.branches}:workspaceCatalog.find(item=>item.path===w.path)||{...w,branches:[]});
  const select=async(workspace,branch)=>{closePopover();if(workspace.path!==state.snapshot.project.path)await openProject(workspace.path);if(branch)selectBranch(branch.id);};
  let sessionCount=0;
  function appendBranch(section,workspace,branch,depth=0){
    const isCurrent=workspace.path===project.path;
    if(query&&!(branch.name+' '+(branch.summary||'')+' '+(isCurrent?(branch.node_ids||[]).map(id=>{const node=nodesById().get(id);return (node?.prompt||'')+' '+(node?.answer||'');}).join(' '):'')).toLocaleLowerCase().includes(query))return;
    const row=button('',()=>select(workspace,branch),'session'+(isCurrent&&branch.id===state.branch?' selected':''));row.dataset.branchId=branch.id;row.dataset.workspacePath=workspace.path;row.title=branch.name;
    if(isCurrent&&branch.id===state.branch)row.setAttribute('aria-current','page');
    row.append(el('span','session-name',branch.name));if(navPreferences.group==='list')row.append(el('span','session-workspace',workspace.name));
    const time=el('time','session-updated',relativeTime(branch.updated_at||branch.created));time.title=branch.updated_at||branch.created||'';row.append(time);
    const run=isCurrent&&state.snapshot.runs.find(r=>r.branch_id===branch.id&&['running','waiting_approval','queued','stopping'].includes(r.status));if(run)row.append(el('span','run-indicator',statusLabel(run.status)));
    const wrapper=el('div','session-row');wrapper.classList.toggle('branch-row',depth>0);wrapper.style.setProperty('--branch-depth',Math.min(3,depth));
    const openMenu=async origin=>{const rect=origin.getBoundingClientRect();await select(workspace,branch);const current=state.snapshot.branches.find(b=>b.id===branch.id);if(current)openBranchMenu(current,rect.left,rect.bottom,origin);};
    const more=iconButton('more','会话操作',()=>openMenu(more),'session-menu-trigger');wrapper.append(row,more);
    wrapper.addEventListener('contextmenu',event=>{event.preventDefault();openMenu(row).catch(e=>toast(e.message));});
    row.addEventListener('keydown',event=>{if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){event.preventDefault();openMenu(row).catch(e=>toast(e.message));}});
    wrapper.classList.toggle('branch-path-active',isCurrent&&branch.id===state.branch);
    installSwipeRow(wrapper,{label:branch.name,archived:branch.archived,onArchive:async()=>{
      if(workspace.path!==state.snapshot.project.path)await openProject(workspace.path);
      if(state.snapshot.project.path!==workspace.path)throw new Error('工作区已切换，请重新操作。');
      await command('branch',{branch_id:branch.id,archived:!branch.archived,project_id:state.snapshot.project.id});
    },onError:error=>toast(error.message)});
    section.append(wrapper);sessionCount++;
  }
  if(navPreferences.group==='list'){
    const items=workspaces.flatMap(w=>visibleBranches(w.branches,{...navPreferences,order:navPreferences.order[w.id]||[]}).map(branch=>({workspace:w,branch})));
    if(navPreferences.sort==='updated')items.sort((a,b)=>Date.parse(b.branch.updated_at||b.branch.created||0)-Date.parse(a.branch.updated_at||a.branch.created||0));
    for(const item of items)appendBranch(list,item.workspace,item.branch);
  }else for(const workspace of workspaces){
    const section=el('section','workspace-group');section.dataset.workspacePath=workspace.path;
    const header=el('div','workspace-row'),collapsed=navPreferences.collapsed.includes(workspace.path)&&!query;
    const toggle=iconButton(collapsed?'chevron':'expand',(collapsed?'展开':'收起')+'工作区 '+workspace.name,()=>{navPreferences.collapsed=collapsed?navPreferences.collapsed.filter(p=>p!==workspace.path):[...navPreferences.collapsed,workspace.path];saveNavPreferences();},'workspace-collapse');toggle.setAttribute('aria-expanded',String(!collapsed));
    const name=button('',()=>workspace.path===project.path?workspacePicker():select(workspace),'project-switch',workspace.path===project.path?'选择当前工作区':workspace.name);if(workspace.path===project.path)name.id='project-name';name.title=workspace.path;name.append(icon('folder'),el('span','',workspace.name));
    const more=iconButton('more','工作区操作 '+workspace.name,()=>{const popup=openPopover(more,'工作区操作');popup.panel.setAttribute('role','menu');for(const [label,handler] of [['选择工作区',workspacePicker],['在此新建会话',async()=>{await select(workspace);await createConversation();}],['打开其他工作区',openProject]]){const item=button(label,()=>{popup.close();return handler();},'choice-item');item.setAttribute('role','menuitem');popup.panel.append(item);}popup.show();popup.panel.querySelector('button')?.focus();});
    const add=iconButton('plus','在 '+workspace.name+' 新建会话',async()=>{await select(workspace);await createConversation();},'workspace-new');add.dataset.tooltip='新会话 '+uiPreferences.shortcuts.new_chat.replace('Mod','⌘ / Ctrl');header.append(toggle,name,more,add);section.append(header);
    if(!collapsed){const sessions=el('div','workspace-sessions'),items=visibleBranches(workspace.branches,{...navPreferences,order:navPreferences.order[workspace.id]||[]});
      if(navPreferences.group==='tree')for(const group of conversationGroups(items)){const family=el('div','conversation-group');family.dataset.conversationId=group.id;for(const branch of group.branches)appendBranch(family,workspace,branch,branch.depth);if(family.childElementCount){const rows=[...family.children];mountBranchedMenu(family,{items:rows.map(row=>{const item=row.querySelector('.session');return {value:item.dataset.branchId,label:item.title,node:row};}),active:workspace.path===project.path?state.branch:''});sessions.append(family);}}
      else for(const branch of items)appendBranch(sessions,workspace,branch);
      section.append(sessions);
    }
    list.append(section);
  }
  if(!sessionCount&&!navPreferences.collapsed.length)list.append(el('p','empty-nav',navPreferences.archive==='only'?'暂无归档会话':query?'没有找到会话':'还没有会话。开始一项任务吧。'));
  updateSidebarSelection();
  loadWorkspaceCatalog();
}
function renderHeader() {
  const branch=active(); const title=$('#workspace-title');
  const header=$('.workbench-header'),stamp=JSON.stringify([state.snapshot.project.id,state.snapshot.project.name,state.branch,branch?.name,state.view]);
  if(header._stamp===stamp){const search=$('.chat-search');if(search&&search.value!==state.chatSearch)search.value=state.chatSearch;applyPaneState();return;}header._stamp=stamp;
  title.replaceChildren();
  title.append(el('strong','',state.view==='plugins'?'插件':branch?.name || '开始探索'),el('span','muted',state.snapshot.project.name));
  $('#plugin-navigation').classList.toggle('selected',state.view==='plugins');
  $('.composer-wrap').hidden=!['chat','trace'].includes(state.view);$('#status-bar').hidden=state.view==='plugins';applyPaneState();
  $('.view-tabs').querySelectorAll('button').forEach(t=>t.classList.toggle('active',t.dataset.view===state.view));
  $('.view-tabs').updateSelection();
  const bar=$('#subheader'); bar.replaceChildren();
  bar.hidden=state.view==='plugins';
  bar.append(el('span','workspace-note','共享工作区 · 分支切换不会恢复文件'));
  if(state.view==='map')bar.append(el('span','map-scope-label','当前会话及分叉路径'));
  if(state.view==='trace')bar.append(el('span','map-scope-label','当前分支的执行轨迹'));
  if(state.view==='chat') {
    const search=el('input','chat-search');search.placeholder='查找当前对话';search.setAttribute('aria-label','查找当前对话');search.value=state.chatSearch;
    search.addEventListener('input',()=>{state.chatSearch=search.value;renderContent();});bar.append(search);
  }
}
function closeBranchMenu(restoreFocus=false){
  const menu=$('#branch-menu');if(!menu)return false;
  menu.remove();
  if(restoreFocus&&menuOrigin){
    const row=[...document.querySelectorAll('.session')].find(element=>element.dataset.branchId===menuOrigin.branchId);
    const target=menuOrigin.element.isConnected?menuOrigin.element:menuOrigin.more?row?.parentElement.querySelector('.session-menu-trigger'):row;
    target?.focus();
  }
  menuOrigin=null;return true;
}
function openBranchMenu(branch,x,y,origin){
  closeBranchMenu();const projectId=state.snapshot.project.id,identity=branch.id,menu=el('div','branch-menu');menu.id='branch-menu';menu.setAttribute('role','menu');
  menuOrigin={element:origin,branchId:identity,more:origin.classList.contains('session-menu-trigger')};
  const action=(glyph,label,handler)=>{const item=button('',()=>{closeBranchMenu();return handler();},'branch-menu-item');item.setAttribute('role','menuitem');item.append(icon(glyph),el('span','',label));menu.append(item);};
  action('edit','重命名',()=>rename(branch,projectId));
  action('check',branch.is_default?'主分支':'设为主分支',()=>command('branch',{branch_id:identity,default:true,project_id:projectId}));
  action('archive',branch.archived?'取消归档':'归档对话',()=>command('branch',{branch_id:identity,archived:!branch.archived,project_id:projectId}));
  action('download','导出对话',async()=>{const data=await command('export',{branch_id:identity,project_id:projectId},false);download(data.filename,data.text);});
  action('expand','向上移动',()=>moveSession(branch,-1));action('chevron','向下移动',()=>moveSession(branch,1));
  document.body.append(menu);menu.style.left=Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';
  menu.addEventListener('keydown',event=>{const items=[...menu.querySelectorAll('[role=menuitem]')],current=items.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?items.length-1:(current+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next].focus();}});
  menu.querySelector('button')?.focus();
}
document.addEventListener('pointerdown',event=>{if(!event.target.closest('#branch-menu,.session-menu-trigger'))closeBranchMenu();});
function selectBranch(identity, node=null) {
  closePopover();
  if(state.view==='plugins')state.view='chat';
  const previous=state.branch;
  if(previous&&pendingDrafts.has(previous))flushDraft(previous).catch(e=>toast(e.message));
  state.branch=identity; state.node=node;state.chatSearch='';state.chatLimit=80;closeBranchMenu();
  localStorage.setItem('ignovate-branch-'+state.snapshot.project.id,identity);
  setComposerText(pendingDrafts.get(identity)??active()?.draft??'');
  if(window.matchMedia('(max-width:900px)').matches)toggleSidebar(false);
  updateSidebarSelection();renderHeader();renderContent();renderApprovals();renderStatus();
}
async function switchView(view) {
  closePopover();
  if(creationPromise)await creationPromise;
  if(view==='map'&&!active())await createConversation();
  if(view==='map' && !state.node) state.node=active()?.head;
  state.view=view; localStorage.setItem('ignovate-view',view);renderHeader();renderContent();
  if(state.node&&state.detail){const node=nodesById().get(state.node);if(node)inspectNode(node).catch(e=>toast(e.message));}
}
function renderContent() {
  const content=$('#content');
  if(state.view==='trace'){
    const projectId=state.snapshot.project.id,branchId=state.branch,key=projectId+':'+branchId;
    let trace=traceViews.get(key);
    if(!trace){trace=createTrace({read:limit=>branchId?command('trace',{branch_id:branchId,limit,project_id:projectId},false):Promise.resolve({sequence:state.snapshot.sequence,total_turns:0,turns:[],tool_calls:0,model_calls:0,elapsed_ms:null}),isCurrent:()=>state.view==='trace'&&state.snapshot.project.id===projectId&&state.branch===branchId,onChat:()=>switchView('chat')});traceViews.set(key,trace);if(traceViews.size>20)traceViews.delete(traceViews.keys().next().value);}
    if(content.firstChild!==trace)content.replaceChildren(trace);trace.update(state.snapshot.sequence);return;
  }
  const existingMap=content.querySelector('.conversation-map');
  if(state.view==='map'&&existingMap?.interacting&&existingMap.dataset.projectId===state.snapshot.project.id&&existingMap.dataset.conversationId===active()?.conversation_id)return;
  if(state.view==='plugins'){
    const existing=$('#plugins-page');if(existing?.dataset.projectId===state.snapshot.project.id){existing.updateBusy();return;}
    const projectId=state.snapshot.project.id;
    const plugins=createPlugins({read:()=>command('plugins',{project_id:projectId},false),mutate:(operation,payload)=>command('plugin',{operation,...payload,project_id:projectId},false),
      isCurrent:()=>state.view==='plugins'&&state.snapshot.project.id===projectId&&plugins.isConnected,
      isBusy:()=>state.snapshot.runs.some(run=>['queued','running','waiting_approval','stopping'].includes(run.status))});
    plugins.dataset.projectId=projectId;content.replaceChildren(plugins);return;
  }
  if(state.view==='map') {
    const branch=active(),conversation=branch?.conversation_id;const branches=state.snapshot.branches.filter(b=>b.conversation_id===conversation);
    const allowed=new Set(branches.flatMap(b=>b.node_ids));
    const nodes=state.snapshot.nodes.filter(n=>allowed.has(n.id));
    const runs=state.snapshot.runs.filter(run=>branches.some(branch=>branch.id===run.branch_id));

    const projectId=state.snapshot.project.id,key=projectId+':'+conversation;
    if(!mapViews.has(key)){
      let saved=null;try{saved=JSON.parse(localStorage.getItem('ignovate-map-view-'+key)||'null');}catch{}
      mapViews.set(key,{scope:saved?.scope==='current'?'current':'all',density:saved?.density==='expanded'?'expanded':'overview',panel:['items','attention','receipts','feedback'].includes(saved?.panel)?saved.panel:null});
    }
    const mapView=mapViews.get(key),control=state.snapshot.control||{items:[],attention:[],receipts:[],feedback:[]};
    const layout=pendingLayouts.get(key)?.layout||state.snapshot.layouts?.[conversation]||state.snapshot.layout;
    const draft=readMapDraft(key,nodes,runs,branches)||(!nodes.length&&!runs.length&&active()?.draft?{nodeId:null,branchId:state.branch,kind:'root',text:active().draft,permission_mode:active().permission_mode||'default'}:null);
    const stamp=JSON.stringify([projectId,conversation,state.branch,state.node,layout,nodes,branches.map(b=>[b.id,b.name,b.head,b.node_ids,b.permission_mode,b.archived]),runs.map(r=>[r.id,r.branch_id,r.node_id,r.map_origin]),runs.some(r=>['queued','running','waiting_approval','stopping'].includes(r.status)),draft&&[draft.nodeId,draft.branchId,draft.kind,draft.requestId,draft.blocked],movingMapDrafts.has(key),state.profile,goalLimits,mapView,control]);
    if(existingMap?._stamp===stamp){existingMap.updateRunCards(runs,control);return;}
    content.replaceChildren();
    const map=createMap({nodes,branches,runs,streams:state.streams,layout,selected:state.node,activeBranch:state.branch,draftMoving:movingMapDrafts.has(key),taskLabel:state.profile==='chat'?'':taskModes.find(row=>row[0]===state.profile)[1]+(state.profile==='goal'?` · ${goalLimits.max_rounds} 轮 / $${goalLimits.max_cost_usd}`:''),draft,
      control,mapView,onControl:workControlAction,onViewChange:value=>{mapViews.set(key,{...value});try{localStorage.setItem('ignovate-map-view-'+key,JSON.stringify(value));}catch{toast('地图阅读偏好无法保存，本次仍可使用。');}},
      onDraftChange:draft=>{if(draft||map.isConnected)storeMapDraft(key,draft);},onPermission:mapPermissions,
      onNewDraft:async draft=>{
        if(movingMapDrafts.has(key))return;movingMapDrafts.add(key);
        try{
          const origin=state.branch,branch=await command('create',{name:'新会话',project_id:projectId},false);
          if(state.snapshot.project.id!==projectId)return;
          storeMapDraft(projectId+':'+branch.conversation_id,{...draft,kind:'root',branchId:branch.id,nodeId:null,blocked:false,requestId:null,mapPosition:undefined,mapViewport:undefined});
          if(mapDrafts.get(key)===draft)storeMapDraft(key,null);
          await reload();if(state.snapshot.project.id===projectId&&state.branch===origin&&state.view==='map')selectBranch(branch.id);
        }finally{movingMapDrafts.delete(key);if(state.snapshot.project.id===projectId&&state.view==='map')renderContent();}
      },
      onSend:async draft=>{
        const currentBranch=state.branch;const result=await command('map_send',{branch_id:draft.branchId,node_id:draft.nodeId,kind:draft.kind,message:draft.text,permission_mode:draft.permission_mode,request_id:draft.requestId,profile:state.profile,...(state.profile==='goal'?{goal_limits:goalLimits}:{}),project_id:projectId},false);
        if(state.snapshot.project.id!==projectId)return;
        const previous=pendingLayouts.get(key)?.layout||state.snapshot.layouts?.[conversation]||state.snapshot.layout;
        const reserved={...previous,positions:{...previous.positions,['run_'+result.run_id]:previous.positions?.['run_'+result.run_id]||draft.mapPosition},viewport:previous.viewport||draft.mapViewport,collapsed:(previous.collapsed||[]).filter(id=>id!==draft.nodeId)};
        if(draft.mapPosition){pendingLayouts.set(key,{layout:reserved,projectId,conversation});try{await flushLayout(key);}catch(error){toast('问题已发送，地图布局保存失败：'+error.message);}}
        const ownDraft=mapDrafts.get(key)===draft;if(ownDraft)storeMapDraft(key,null);await reload();if(ownDraft&&state.snapshot.project.id===projectId&&state.branch===currentBranch)selectBranch(result.branch_id);
      },onArchive:n=>command('branch',{branch_id:n.branch_id,archived:true,project_id:projectId}),
      onSelect:n=>inspectNode(n),onChat:n=>{selectBranch(n.branch_id,n.id);switchView('chat'); setTimeout(()=>$(`[data-node-id="${n.id}"]`)?.scrollIntoView({block:'center'}),0);},
      onFork:n=>fork(n),onSave:layout=>{pendingLayouts.set(key,{layout,projectId,conversation});clearTimeout(layoutTimers.get(key));layoutTimers.set(key,setTimeout(()=>flushLayout(key).catch(e=>toast(e.message)),220));}});
    map.dataset.projectId=projectId;map.dataset.conversationId=conversation;map._stamp=stamp;
    map.addEventListener('map-interaction-end',()=>{if(map.isConnected){clearTimeout(layoutTimers.get(key));flushLayout(key).catch(e=>toast(e.message));renderContent();}});
    map.addEventListener('map-error',event=>toast(event.detail));content.append(map);return;
  }

  let history=content.querySelector('.chat-history');const context=state.snapshot.project.id+':'+state.branch;
  if(!history||history.dataset.context!==context){history=el('div','chat-history');history.dataset.context=context;history._turns=new Map();content.replaceChildren(history);}
  const items=[],cache=history._turns;
  if(!path().length&&!running().length){history._empty||=emptyState();items.push(history._empty);}
  const historyPath=path();const selectedIndex=historyPath.findIndex(n=>n.id===state.node);
  const start=Math.min(Math.max(0,historyPath.length-state.chatLimit),selectedIndex<0?historyPath.length:selectedIndex);
  if(start&&!state.chatSearch)items.push(button(`加载更早的 ${Math.min(80,start)} 个回合`,()=>{state.chatLimit+=80;renderContent();},'text-button'));
  const query=state.chatSearch.toLocaleLowerCase();
  const shown=query?historyPath.filter(n=>(n.prompt+' '+n.answer).toLocaleLowerCase().includes(query)):historyPath.slice(start);
  const keys=new Set();
  shown.forEach(node=>{
    const run=state.snapshot.runs.find(r=>r.node_id===node.id),key=run?'run:'+run.id:'node:'+node.id;keys.add(key);
    const signature=conversationPresentation()+JSON.stringify([node.status,node.recoverable,node.usage,node.timing,node.tools]);let entry=cache.get(key);
    if(!entry){entry={root:turn(node)};cache.set(key,entry);}else if(entry.presentation!==conversationPresentation()){entry.root=turn(node);}else if(entry.signature!==signature||entry.answer!==node.answer||entry.prompt!==node.prompt){entry.root=patchElement(entry.root,turn(node));}
    const toolHistory=entry.root.querySelector('.tool-history');if(toolHistory){toolHistory._tools=node.tools||[];for(const [index,tool] of (node.tools||[]).entries()){const details=[...toolHistory.querySelectorAll('.tool-activity')].find(item=>item.dataset.toolId===String(tool.call_id||tool.id||index));if(details)details._tool=tool;}}
    entry.presentation=conversationPresentation();
    entry.signature=signature;entry.answer=node.answer;entry.prompt=node.prompt;entry.root.classList.toggle('focused',node.id===state.node);items.push(entry.root);
  });
  if(query&&!shown.length)items.push(el('p','muted','当前路径没有匹配的回合。'));
  for(const run of running().filter(r=>r.status!=='queued')) {
    if(run.node_id) continue;
    const key='run:'+run.id;keys.add(key);let entry=cache.get(key);const text=state.streams.get(run.id)||'';
    if(!entry){entry={root:renderLive(run,text,state.receivedAt,showChanges)};cache.set(key,entry);}
    updateLive(entry.root,run,text,state.receivedAt,showChanges);entry.presentation=conversationPresentation();items.push(entry.root);
  }
  for(const key of cache.keys())if(!keys.has(key))cache.delete(key);
  const stick=content.scrollHeight-content.scrollTop-content.clientHeight<72,before=content.scrollHeight;
  synchronize(history,items);
  if(stick&&content.scrollHeight>before){const behavior=content.style.scrollBehavior;content.style.scrollBehavior='auto';content.scrollTop=content.scrollHeight;content.style.scrollBehavior=behavior;}
}
function readMapDraft(key,nodes,runs,branches){
  if(!mapDrafts.has(key))try{const draft=JSON.parse(localStorage.getItem('ignovate-map-draft-'+key)||'null');
    if(draft&&typeof draft.text==='string'&&draft.text.length<=100000&&['root','continue','fork'].includes(draft.kind)&&(!draft.nodeId||nodes.some(node=>node.id===draft.nodeId)))mapDrafts.set(key,draft);
  }catch{/* Keep the in-memory workflow usable when local storage is unavailable. */}
  const draft=mapDrafts.get(key)||null,resolved=reconcileMapDraft(draft,nodes,runs,branches);
  if(resolved!==draft)storeMapDraft(key,resolved);return resolved;
}
function storeMapDraft(key,draft){
  if(draft)mapDrafts.set(key,draft);else mapDrafts.delete(key);
  try{if(draft)localStorage.setItem('ignovate-map-draft-'+key,JSON.stringify(draft));else localStorage.removeItem('ignovate-map-draft-'+key);}catch{/* Existing drafts remain available in this window. */}
}
function emptyState() {
  const empty=el('section','empty-state'); empty.append(el('h1','','从一个问题开始。'),
    el('p','','理解项目，尝试不同方案，在同一张会话地图中保留探索路径。'));
  const suggestions=el('div','suggestions');
  ['了解项目结构','先制定实施计划','审查当前改动'].forEach(text=>suggestions.append(button(text,()=>{setComposerText(text);saveDraft();$('#composer').focus();},'suggestion')));
  empty.append(suggestions,el('p','empty-footnote','执行由 Ignovate Harness 驱动。每项改动都能查看来源。'));return empty;
}
function turn(node) {
  return renderTurn(node,{toolKey:state.snapshot.runs.find(run=>run.node_id===node.id)?.id||node.id,changes:showChanges,focused:node.id===state.node,detail:inspectNode,quote,fork:n=>fork(n),edit:n=>fork(n,true),regenerate:n=>fork(n,true,true)});
}
function showChanges(tools){modal('本轮文件变更',fileChangesReview(tools),null);$('#dialog .dialog-actions button').textContent='关闭';}
function workControlAction(action){
  const operation=action.operation;
  if(['open_branch','open_approval','show_approval'].includes(operation)){
    const branch=state.snapshot.branches.find(b=>b.id===action.branch_id);
    if(branch){selectBranch(branch.id,action.node_id||branch.head);switchView('chat');}
    return;
  }
  const projectId=state.snapshot.project.id;
  const branchId=operation==='link_task'?state.branch:action.branch_id||state.branch;
  const branch=state.snapshot.branches.find(b=>b.id===branchId);
  const item=action.item||action;
  const current=(state.snapshot.control?.items||[]).find(t=>t.task_id===(action.task_id||item.task_id));
  const base={operation,branch_id:branchId,project_id:projectId,task_id:action.task_id||item.task_id,
    expected_revision:action.expected_revision??item.revision??current?.revision};
  const apply=async extra=>{
    if(state.snapshot.project.id!==projectId)throw new Error('项目已切换，请重新打开操作。');
    await command('work_control',{...base,...extra},false);await reload();
  };
  if(operation==='link_task'){
    const body=el('div');body.append(el('p','',`将“${item.objective||'此任务'}”关联到当前会话，接续同一份要求和验收。权限与目标预算由当前会话负责。`));
    modal('关联项目任务',body,'确认关联',()=>apply({}));
  }else if(operation==='set_wait'){
    const body=el('div');body.append(field('等待原因','reason','等待外部结果'),field('恢复条件','condition',''));
    modal('登记等待条件',body,'保存等待',values=>apply(values));
  }else if(operation==='clear_wait'){
    const body=el('div');body.append(el('p','',`恢复条件：${item.wait_condition?.condition||item.reason||'核对等待条件已满足'}`));
    modal('确认条件已满足',body,'解除等待',()=>apply({expected_wait_condition:item.wait_condition||current?.wait_condition}));
  }else if(operation==='confirm_acceptance'){
    const body=el('div');body.append(el('p','',item.reason||'请核对人工验收要求'),field('实际检查结果','reason',''));
    modal('人工验收',body,'确认检查结果',values=>apply({...values,acceptance_id:action.acceptance_id||item.acceptance_id}));
  }else if(operation==='feedback_stage'){
    const nodeId=action.node_id||state.node||branch?.head;
    const node=nodesById().get(nodeId),task=node?.task_summary||current;
    if(!node||!task){toast('请选择有任务来源的真实回合。');return;}
    const body=el('div');body.append(field('适用情境','context',task.objective||''),field('实际结果或纠正','outcome',''),field('今后建议怎么做','behavior',''),field('何时需要重新判断','boundary',''));
    const requestId=crypto.randomUUID();
    modal('整理反馈经验候选',body,'保存候选',values=>apply({...values,node_id:nodeId,expected_revision:task.revision,request_id:requestId}));
  }else if(operation==='feedback_confirm'){
    const body=el('div');body.append(el('p','',item.behavior||'请核对经验内容'),el('p','muted','确认后保存到项目记忆，仍须核对适用范围和当前代码。'));
    modal('确认项目经验',body,'确认经验',()=>apply({id:action.id||item.id}));
  }else if(operation==='feedback_revoke'){
    const body=el('div');body.append(el('p','',item.behavior||'撤销此经验，保留来源记录。'));
    modal('撤销项目经验',body,'撤销经验',()=>apply({id:action.id||item.id}));
  }
}
function toolDetails(tools) {
  const wrapper=el('details','tool-details');wrapper.append(el('summary','',`${tools.length} 个工具过程`));
  for(const tool of tools) {const item=el('details','tool-item');const summary=el('summary'); summary.append(el('span','',tool.name||'工具'),badge(tool.status),el('span','muted',tool.elapsed_ms?Math.round(tool.elapsed_ms)+' ms':''));
    item.append(summary,el('pre','tool-output',JSON.stringify(tool,null,2)));wrapper.append(item);}
  return wrapper;
}
async function inspectNode(node) {
  const projectId=state.snapshot.project.id,view=state.view;
  state.node=node.id;toggleDetail(true);const inspector=$('#inspector');
  const detail=await command('node',{node_id:node.id,project_id:projectId},false);
  if(state.snapshot.project.id!==projectId||state.node!==node.id||state.view!==view)return;
  inspector.replaceChildren();
  const title=el('div','inspector-title');title.append(el('strong','',view==='map'?'回合详情':'回合产物'),iconButton('close','关闭详情',()=>toggleDetail(false)));
  inspector.append(title,el('div','source-label',`${state.snapshot.branches.find(b=>b.id===node.branch_id)?.name||'来源分支'} · ${node.id.slice(0,8)}`),
    el('p','inspector-prompt',detail.prompt));
  if(view==='map')inspector.append(prose(detail.answer,'answer'));
  inspector.append(el('p','source-label','历史产物来源。文件预览展示当前共享工作区内容。'),renderArtifacts(detail,async artifact=>{
      const data=await command('file',{path:artifact.path,project_id:projectId},false);if(data.error)throw new Error(data.error);
      if(state.snapshot.project.id!==projectId||state.node!==node.id)return;
      modal('当前文件 · '+artifact.path,el('pre','file-preview',data.content||''),null);
    }),toolDetails(detail.tools));
  inspector.append(button('在聊天中打开',()=>{selectBranch(node.branch_id,node.id);switchView('chat');},'text-button'));
  const info=el('dl','facts');for(const [label,value] of [['处理时间',elapsedText(detail.timing?.elapsed_ms)],['验证',statusLabel(detail.delivery.status)],['用量',detail.usage.complete?`${detail.usage.input_tokens} 输入 / ${detail.usage.output_tokens} 输出`:'未知 / 不完整'],['费用',detail.usage.cost_usd!=null?'$'+detail.usage.cost_usd.toFixed(5):'未知'],['checkpoint',detail.checkpoint_id||'不可恢复']]){info.append(el('dt','',label),el('dd','',value));}
  inspector.append(info,renderUsageDetails(detail),button('核对当前证据',()=>inspectNode(node),'text-button'),button('查看当前工作区',workspace,'secondary'),button('运行已配置验证',()=>verify(node.branch_id),'text-button'));
  const events=el('details','tool-details');events.append(el('summary','','原始执行事件'),el('pre','tool-output',JSON.stringify(detail.events.filter(event=>!['user','token','final'].includes(event.kind)),null,2)));inspector.append(events);
}
async function quote(node) {
  const selection=window.getSelection();const text=selection?.toString().trim();
  if(!text) {toast('先在完整回答中选中文字。');return;}
  const anchor=selection.anchorNode?.nodeType===1?selection.anchorNode:selection.anchorNode?.parentElement;
  const origin=anchor?.closest('#inspector .answer, .turn .answer, .map-excerpt');
  if(!origin) {toast('请在回答正文中选择引用。');return;}
  if(origin.closest('.turn')?.dataset.nodeId&&origin.closest('.turn').dataset.nodeId!==node.id){toast('请从当前回合的回答中选择引用。');return;}
  selectBranch(node.branch_id,node.id);
  setComposerText(`引用回合 ${node.id.slice(0,8)}：\n> ${text.replaceAll('\n','\n> ')}\n\n请展开说明：`);
  await command('draft',{branch_id:state.branch,text:$('#composer').value},false);active().draft=$('#composer').value;$('#composer').focus();renderStatus();
}
async function fork(node,before=false,regenerate=false) {
  const sourceBranch=node.branch_id,projectId=state.snapshot.project.id;
  const body=el('div');body.append(el('p','',before?'新分支从本次输入之前开始，原输入与后续历史会保留。':'新分支继承到这个完整回合，原分支的后续历史会保留。'),
    el('p','muted','共享当前文件工作区。分叉不会撤销已执行的操作；重新生成可能再次调用工具并计费。'),field('分支名称','name',regenerate?'重新生成':'备选方案'));
  if(before&&!regenerate) {const label=el('label','field');label.append(el('span','','修改后的输入'));const input=el('textarea');input.name='message';input.value=node.prompt;input.rows=5;label.append(input);body.append(label);}
  modal('从历史回合分叉',body,'创建分支',async data=>{
    const branch=await command('fork',{branch_id:sourceBranch,node_id:node.id,name:data.name,before,project_id:projectId});selectBranch(branch.id);
    if(before) {setComposerText(regenerate?node.prompt:data.message);await send();}
  });
}
function renderApprovals() {
  const area=$('#approvals');area.replaceChildren();
  for(const approval of state.snapshot.approvals) {
    const card=el('div','approval-card');card.append(el('strong','',`等待批准 · ${approval.action.name}`),el('div','muted',state.snapshot.branches.find(b=>b.id===approval.branch_id)?.name||'来源分支'),el('pre','',JSON.stringify(approval.action,null,2)));
    const actions=el('div','approval-actions');
    [['拒绝','reject'],['批准一次','approve_once'],['本次会话批准','approve_session']].forEach(([label,decision])=>actions.append(button(label,()=>command('approve',{action_id:approval.action_id,run_id:approval.run_id,decision}),decision==='reject'?'secondary':'primary')));
    card.append(actions);area.append(card);
  }
}
function renderStatus() {
  const conf=state.snapshot.configuration;
  $('#composer-target').textContent=active()?`发送到 ${active().name}${active().legacy&&!active().recoverable?' · 旧会话仅可查看':''}`:'新任务';
  const mode=active()?.permission_mode||'default',busy=running().length>0;
  const models=(conf.models?.length?conf.models:[{name:'default',model:conf.model||'连接模型'}]).map(item=>({key:item.name,name:item.model,tag:item.name==='default'?'默认':item.name}));
  promptUI.update({
    scopeKey:state.snapshot.project.id+':'+state.branch,busy,connected:conf.configured,
    disabled:Boolean(active()?.archived||working.has(state.branch)||active()?.history_conflict||(!active()?.recoverable&&active()?.legacy)||(uiPreferences.busy_send==='draft'&&projectBusy())),
    allowQueue:uiPreferences.busy_send!=='draft',configurationDisabled:projectBusy(),permissionDisabled:busy,
    models,defaultModel:conf.model_name||models.find(item=>item.name===conf.model)?.key||models[0].key,
    efforts:(conf.reasoning_options||[['default','模型默认']]).map(item=>item[1]),defaultEffort:conf.reasoning_options?.find(item=>item[0]===conf.reasoning_effort)?.[1]||'模型默认',
    modeLabel:state.profile==='chat'?'':taskModes.find(row=>row[0]===state.profile)?.[1],
    permission:{value:mode,label:{default:'请求批准',acceptEdits:'帮我批准',bypassPermissions:'完全访问权限'}[mode]}
  });
  resizeComposer();
  const status=$('#status-bar');
  const statusStamp=JSON.stringify([state.snapshot.project.id,state.branch,state.snapshot.paused,running().map(r=>[r.id,r.status,r.message]),active()?.weather,active()?.history_conflict,active()?.legacy,active()?.recoverable,path().map(n=>n.usage),state.snapshot.runs.filter(r=>r.branch_id===state.branch).map(r=>[r.id,r.status,r.error,r.plan_id,r.goal])]);
  if(status._stamp===statusStamp)return;status._stamp=statusStamp;status.replaceChildren();
  const run=running()[0];const totals=path().reduce((v,n)=>v+n.usage.input_tokens+n.usage.output_tokens,0);
  const weather=active()?.weather||{level:'clear',text:'Clear · 0% · 0 / 未知窗口'};
  const forecast=el('div','token-weather');forecast.id='token-weather';forecast.dataset.level=weather.level;
  forecast.title='最近一次主模型输入量与上下文窗口占用；累计计费单独显示。';
  forecast.append(icon({clear:'sun',cloudy:'cloud',showers:'showers',storm:'storm',unknown:'info'}[weather.level]||'info'),el('span','weather-text',weather.text.replace(/^[☀☁☂⚡]\s*/u,'')));
  status.append(forecast,
    el('span','billing-summary',`累计 ${totals} tokens · 费用${path().every(n=>n.usage.cost_usd!=null)&&path().length?' $'+path().reduce((v,n)=>v+n.usage.cost_usd,0).toFixed(5):'未知'}`));
  if(state.snapshot.paused)status.append(button('显式恢复队列',()=>command('resume_queue'),'text-button'));
  for(const queued of running().filter(r=>r.status==='queued'))status.append(button('移除排队任务：'+queued.message.slice(0,20),()=>command('stop',{run_id:queued.id}),'text-button'));
  if(active()?.history_conflict||active()?.legacy&&!active()?.recoverable)status.append(button('基于摘要新建',summaryConversation,'text-button'));
  const last=state.snapshot.runs.filter(r=>r.branch_id===state.branch).at(-1);
  if(last?.error)status.append(el('span','error-text',last.error));
  if(last&&['interrupted','failed','stopped'].includes(last.status))status.append(button('继续上次任务',()=>command('resume_run',{run_id:last.id,request_id:crypto.randomUUID()}),'text-button'));
  if(last?.plan_id)status.append(button('批准计划并实施',()=>command('accept_plan',{run_id:last.id,request_id:crypto.randomUUID()}),'text-button'));
  if(last?.goal)status.append(el('span','goal-status muted',`目标${{active:'推进中',paused:'已暂停',complete:'已完成',blocked:'受阻'}[last.goal.state]||last.goal.state} · ${last.goal.round}/${last.goal.max_rounds} 轮${last.goal.pause_reason?' · '+last.goal.pause_reason:''}`));
  const readiness=el('span','run-status',run?statusLabel(run.status):'就绪');readiness.dataset.state=run?'busy':'ready';readiness.prepend(statusMark(run?.status||'pending'));status.append(readiness);
}
async function createConversation(preserveDraft=false) {
  if(creationPromise)return creationPromise;
  creationPromise=(async()=>{
    if(!preserveDraft)setComposerText('');$('#composer').focus();
    const branch=await command('create',{name:'新会话'});
    const typedDuringCreation=$('#composer').value;selectBranch(branch.id);
    if(typedDuringCreation){setComposerText(typedDuringCreation);saveDraft(true);}
    if(state.view==='chat')$('#composer').focus();return branch;
  })();
  try{return await creationPromise;}finally{creationPromise=null;}
}
async function send() {
  const text=$('#composer').value.trim();if(!text)return;
  if(creationPromise)await creationPromise;
  if(!state.snapshot.configuration.configured){settings('models');return;}
  if(uiPreferences.busy_send==='draft'&&projectBusy()){saveDraft();toast('当前任务运行中，消息已保留为草稿。');return;}
  if(!active()){await createConversation();setComposerText(text);}
  const branch=state.branch;if(working.has(branch))return;working.add(branch);renderStatus();clearTimeout(draftTimer);
  try {await command('send',{branch_id:branch,message:text,profile:state.profile,...(state.profile==='goal'?{goal_limits:goalLimits}:{}),request_id:crypto.randomUUID()});
    const record=state.snapshot.branches.find(b=>b.id===branch),draft=state.branch===branch?$('#composer').value:pendingDrafts.get(branch)??record?.draft??text;
    if(draft.trim()===text){pendingDrafts.delete(branch);if(state.branch===branch)setComposerText('');if(record)record.draft='';}
    else{pendingDrafts.set(branch,draft);if(record)record.draft=draft;}
    $('#content').scrollTop=$('#content').scrollHeight;
  } finally {working.delete(branch);renderStatus();}
}
async function stopRun() {const run=running().find(r=>r.status!=='queued')||running()[0];if(run)await command('stop',{run_id:run.id});}
function saveDraft(force=false) {
  resizeComposer();
  if(creationPromise&&force!==true)return;
  const branch=state.branch,text=$('#composer').value;if(!branch)return;active().draft=text;pendingDrafts.set(branch,text);clearTimeout(draftTimer);
  draftTimer=setTimeout(()=>flushDraft(branch).catch(e=>toast(e.message)),300);
}
async function flushDraft(branch) {
  if(!pendingDrafts.has(branch))return;
  const text=pendingDrafts.get(branch),projectId=state.snapshot.project.id;
  await command('draft',{branch_id:branch,text,project_id:projectId},false);
  if(pendingDrafts.get(branch)===text)pendingDrafts.delete(branch);
}
function flushLayout(key){
  if(layoutSaves.has(key))return layoutSaves.get(key);
  const save=(async()=>{
    // One writer per conversation; newer moves wait for the returned revision.
    while(pendingLayouts.has(key)){
      const pending=pendingLayouts.get(key),{layout,projectId,conversation}=pending;
      const revision=state.snapshot.layouts?.[conversation]?.revision??0;
      const result=await command('layout',{layout,revision,conversation_id:conversation,project_id:projectId},false);
      if(state.snapshot.project.id!==projectId)break;
      state.snapshot.layouts||={};state.snapshot.layouts[conversation]=result;
      if(pendingLayouts.get(key)===pending)pendingLayouts.delete(key);
    }
  })().finally(()=>layoutSaves.delete(key));
  layoutSaves.set(key,save);return save;
}
function rename(branch=active(),projectId=state.snapshot.project.id) {const body=field('会话名称','name',branch.name);modal('重命名',body,'保存',data=>command('branch',{branch_id:branch.id,name:data.name,project_id:projectId}));}
function recentWorkspaces(){try{return JSON.parse(localStorage.getItem('ignovate-workspaces')||'[]').filter(p=>p&&typeof p.path==='string'&&typeof p.name==='string').slice(0,8);}catch{return [];}}
function rememberWorkspace(project){localStorage.setItem('ignovate-workspaces',JSON.stringify([{name:project.name,path:project.path},...recentWorkspaces().filter(p=>p.path!==project.path)].slice(0,8)));}
function workspacePicker(){
  const anchor=$('#project-name')||[...document.querySelectorAll('button')].find(b=>b.getAttribute('aria-label')==='选择工作区'),projectId=state.snapshot.project.id;
  const choices=recentWorkspaces().map(project=>[project.path,project.name,project.path]);
  const popup=choicePopover(anchor,'选择工作区',choices,state.snapshot.project.path,path=>{if(state.snapshot.project.id!==projectId)throw new Error('工作区已切换，请重新选择。');return openProject(path);});
  const open=button('',()=>{popup.close();return openProject();},'choice-item');open.setAttribute('role','menuitem');open.setAttribute('aria-label','打开其他工作区');open.append(icon('plus'),el('span','','打开其他工作区'));popup.panel.append(open);popup.show();
}
async function openProject(selectedPath) {
  if(creationPromise)await creationPromise;
  const currentProject=state.snapshot.project.id;
  const open=async selected=>{
    await Promise.all([...pendingDrafts.keys()].map(flushDraft));
    await Promise.all([...pendingLayouts.keys()].map(flushLayout));
    await command('open_project',{path:selected,project_id:currentProject});
    clearTimeout(draftTimer);for(const timer of layoutTimers.values())clearTimeout(timer);layoutTimers.clear();pendingLayouts.clear();pendingDrafts.clear();state.streams.clear();state.branch=null;state.node=null;
    await reload();$('#inspector').replaceChildren(el('p','muted','选择一个回合查看详情。'));setComposerText(active()?.draft||'');
  };
  if(typeof selectedPath==='string'){await open(selectedPath);return;}
  if(window.ignovateDesktop) {const selected=await window.ignovateDesktop.chooseProject();if(selected)await open(selected);return;}
  modal('打开工作区',field('目录路径','path',state.snapshot.project.path),'打开',data=>open(data.path));
}
function settings(section='general') {
  if(settingsDialog?.open){settingsDialog.selectSection(section);return;}
  const projectId=state.snapshot.project.id;
  const current=()=>state.snapshot?.project.id===projectId;
  const catalog=data=>({...data,preferences:effectiveUIPreferences(data)});
  const scoped=(name,payload={})=>command(name,{...payload,project_id:projectId},false);
  settingsDialog=showSettings({section,
    read:async()=>catalog(await scoped('settings')),
    savePreferences:async changes=>catalog(await scoped('settings_update',{changes})),
    provider:async payload=>catalog(await scoped('provider',payload)),
    plugins:{read:()=>scoped('plugins'),mutate:(operation,payload)=>scoped('plugin',{operation,...payload})},
    onChanged:async()=>{if(current())await reload();},isCurrent:current,isBusy:projectBusy,
    openConfig:paths=>{const body=el('div');body.append(el('p','muted','设置会自动保存。模型密钥仅保存在本机配置文件中。'));
      for(const [key,label] of [['preferences','界面设置'],['providers','模型提供商'],['configuration','当前连接']]){body.append(el('h3','',label),el('pre','file-preview',paths[key]||'暂无配置路径'));}
      modal('配置文件位置',body,null);
    }});
}
async function workspace() {
  const data=await command('workspace',{},false);toggleDetail(true);const inspector=$('#inspector');inspector.replaceChildren();
  inspector.append(el('div','inspector-title','当前工作区'),el('p','muted',`基准 ${data.base}。此处为当前文件差异，不是历史分支快照。`));
  if(!data.files.length)inspector.append(el('p','','没有可显示的改动。'));
  for(const file of data.files) {const details=el('details','diff-file');details.append(el('summary','',`${file.status} ${file.path}`),el('pre','code-block',file.patch));
    details.append(button('预览文件',async()=>{const f=await command('file',{path:file.path},false);modal(file.path,el('pre','file-preview',f.content||f.error),null);},'text-button'));
    if(window.ignovateDesktop)details.append(button('打开外部编辑器',()=>window.ignovateDesktop.openFile(state.snapshot.project.path,file.path),'text-button'));
    inspector.append(details);}
  if(data.truncated||data.skipped.length)inspector.append(el('p','error-text','部分差异未覆盖或已截断。'));
}
async function verify(branchId=state.branch) {if(!branchId)return;await command('verify',{branch_id:branchId,request_id:crypto.randomUUID()});}
async function summaryConversation() {
  const source=active();if(!source)return;
  const detail=source.node_ids.length?await command('node',{node_id:source.node_ids.at(-1)},false):null;
  const branch=await command('create',{name:source.name.slice(0,60)+' · 摘要继续'});selectBranch(branch.id);
  setComposerText(`从旧会话 ${source.name} 的摘要开始，请先核对当前工作区：\n${detail?.answer||source.summary}`);
  saveDraft();$('#composer').focus();
}
async function diagnostics() {const data=await command('diagnostics',{},false);const body=el('div');body.append(el('p','',data.ok?'本地环境检查通过。':'有需要修复的环境项。'));for(const item of data.checks||[]){body.append(el('h3','',`${item.name} · ${item.status}`),el('p','muted',item.detail),el('pre','tool-output',item.repair||''));}modal('项目与环境检查',body,null);}
function referenceFile() {
  const branch=state.branch,projectId=state.snapshot.project.id;
  const body=el('div');body.append(el('p','muted','读取当前工作区文件并填入草稿。主动发送后，内容才会进入模型请求。'),field('项目相对路径','path','README.md'));
  modal('引用项目文件',body,'加入草稿',async data=>{
    const file=await command('file',{path:data.path,project_id:projectId},false);
    if(file.error||!file.content)throw new Error(file.error||'文件没有可引用的文本');
    if(state.snapshot.project.id!==projectId||state.branch!==branch)throw new Error('目标分支已切换，请重新引用。');
    setComposerText($('#composer').value+(`\n\n参考当前项目文件 ${data.path}${file.truncated?'（内容截断）':''}：\n\u0060\u0060\u0060\n${file.content}\n\u0060\u0060\u0060\n`));
    saveDraft();
  });
}
function referenceFolder(){
  const projectId=state.snapshot.project.id,branch=state.branch,body=el('div');body.append(el('p','muted','将项目内文件夹路径与文件清单加入草稿。'),field('项目相对路径','path','.'));
  modal('添加文件夹',body,'加入草稿',async data=>{const folder=await command('folder',{path:data.path,project_id:projectId},false);if(!folder.ok)throw new Error(folder.error||'无法读取文件夹');if(state.snapshot.project.id!==projectId||state.branch!==branch)throw new Error('目标会话已切换，请重新添加。');setComposerText($('#composer').value+(`\n\n参考项目文件夹 ${data.path}${folder.truncated?'（清单截断）':''}：\n${folder.files.join('\n')}\n`));saveDraft();});
}
function renderTheme(){const current=localStorage.getItem('ignovate-theme')||'system';document.documentElement.dataset.theme=current;const button=$('#theme-toggle');button.replaceChildren(icon({system:'monitor',light:'sun',dark:'moon'}[current]||'monitor'));button.dataset.tooltip='主题：'+{system:'跟随系统',light:'浅色',dark:'深色'}[current];}
async function theme(){const current=localStorage.getItem('ignovate-theme')||'system',next={system:'light',light:'dark',dark:'system'}[current],projectId=state.snapshot.project.id;localStorage.setItem('ignovate-theme',next);renderTheme();try{await command('settings_update',{changes:{theme:next},project_id:projectId});}catch(error){localStorage.setItem('ignovate-theme',current);renderTheme();throw error;}}
async function poll() {
  try {
    const result=await api('events?after='+state.snapshot.sequence);let changed=result.reset||connectionInterrupted;if(changed)for(const trace of traceViews.values())trace.invalidate();connectionInterrupted=false;
    for(const event of result.events){if(event.kind==='token'||event.kind==='final'){
      const text=event.kind==='final'?event.data.text||state.streams.get(event.run_id)||'':(state.streams.get(event.run_id)||'')+(event.data.text||'');state.streams.set(event.run_id,text);
      dirtyStreams.add(event.run_id);if(event.kind==='final')changed=true;
    }
      else changed=true;}
    state.snapshot.sequence=result.sequence;if(dirtyStreams.size&&!streamPaint)streamPaint=requestAnimationFrame(paintStreams);if(changed)await reload();
  }catch(e){connectionInterrupted=true;const status=$('#status-bar');status._stamp=null;status.textContent='连接中断：'+e.message+'。重连不会重放工具。';}
  pollTimer=setTimeout(poll,100);
}
function paintStreams(){
  streamPaint=0;const content=$('#content'),stick=content.scrollHeight-content.scrollTop-content.clientHeight<72,before=content.scrollHeight;
  for(const id of dirtyStreams){const text=state.streams.get(id)||'',live=$(`[data-run-id="${id}"] .answer`),card=$(`[data-run-id="${id}"] .map-excerpt`);if(live)updateProse(live,text,prose);if(card)updateProse(card,text.slice(0,8000),prose);}
  dirtyStreams.clear();content.querySelector('.conversation-map')?.updateRunCards(state.snapshot.runs);if(stick&&content.scrollHeight>before){const behavior=content.style.scrollBehavior;content.style.scrollBehavior='auto';content.scrollTop=content.scrollHeight;content.style.scrollBehavior=behavior;}
  if(state.view==='trace')renderContent();
}
document.addEventListener('keydown',event=>{
  if(document.querySelector('dialog[open]'))return;
  if(event.key==='Escape'){const closed=closeBranchMenu(true);if(!closed&&window.matchMedia('(max-width:900px)').matches&&state.sidebar)toggleSidebar(false);}
  if(shortcutMatches(event,uiPreferences.shortcuts.commands)){event.preventDefault();const body=el('div','command-list');for(const [label,fn]of [['新会话',createConversation],['打开项目',openProject],['设置',()=>settings()],['模型设置',()=>settings('models')],['环境检查',diagnostics],['当前工作区',workspace],['验证',verify]])body.append(button(label,()=>{$('#dialog').close();return fn();},'command-entry'));modal('操作入口',body,null);}
  if(shortcutMatches(event,uiPreferences.shortcuts.find)){event.preventDefault();const search=$('.trace-search input')||$('.chat-search')||$('.search');search.hidden=false;search.focus();}
  if(shortcutMatches(event,uiPreferences.shortcuts.new_chat)){event.preventDefault();createConversation().catch(e=>toast(e.message));}
});
document.documentElement.dataset.theme=localStorage.getItem('ignovate-theme')||'system';state.view=['chat','map','trace','plugins'].includes(localStorage.getItem('ignovate-view'))?localStorage.getItem('ignovate-view'):'chat';
shell();reload().then(()=>{setComposerText(active()?.draft||'');resizeComposer();window.ignovateStartup?.ready();if(!state.snapshot.configuration.configured)settings('models');poll();}).catch(e=>{window.ignovateStartup?.fail(e.message);$('#content').replaceChildren(el('div','empty-state',e.message));});
}
if(location.protocol==='file:') {
  explainEntry('Ignovate GUI 需要启动本地服务','这个 HTML 文件是界面资源。Python 本地服务负责连接内核、会话和工具执行。');
} else {
  startWorkbench().catch(()=>explainEntry('界面资源加载失败','请确认本地服务仍在运行，然后重新加载页面。',true));
}
