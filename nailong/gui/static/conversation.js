import {el,icon,iconButton,prose,statusLabel,button} from './ui.js';
import {fileChange,diffStats,renderDiff} from './diff.js';
import {patchElement,updateProse} from './rendering.js';

export function elapsedText(value){
  if(!Number.isFinite(value)||value<0)return '用时未知';
  const seconds=Math.max(1,Math.floor(value/1000));return '用时 '+(seconds<60?seconds+' 秒':Math.floor(seconds/60)+' 分 '+seconds%60+' 秒');
}
function messageTime(value){
  const time=el('time','message-time');
  if(value&&Number.isFinite(Date.parse(value))){time.dateTime=value;time.textContent=new Date(value).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});time.title=new Date(value).toLocaleString();}
  return time;
}
function userMessage(text,timing){
  const root=el('div','user-message'),meta=el('div','user-message-meta');
  root.dataset.renderKey='user';meta.dataset.renderKey='user-meta';
  meta.append(messageTime(timing?.started_at),iconButton('copy','复制输入',()=>navigator.clipboard.writeText(text)));
  root.append(el('div','prompt',text),meta);return root;
}
const expandedTools=new Map(),expandedDetails=new Map();
let presentation={tool_details:'compact',show_changes:true};
export function configureConversation(preferences){presentation={...presentation,...preferences};}
export const conversationPresentation=()=>presentation.tool_details+':'+presentation.show_changes;
function showToolPatch(details){
  const change=fileChange(details._tool),patch=details.querySelector('.tool-patch');
  if(!details.open||!change||!patch)return;
  const signature=JSON.stringify(change);if(patch._change!==signature||!patch.childElementCount){patch.replaceChildren(renderDiff(change));patch._change=signature;}
}
export function toolRows(tools=[],runStatus,key='',onChanges){
  key=presentation.tool_details+':'+key;
  const root=el('div','tool-history');root.dataset.renderKey='tools';root._tools=tools;if(!tools.length)return root;
  const group=el('details','tool-group'),heading=el('summary'),list=el('div','tool-activity-list');
  const edited=tools.some(t=>['write_file','edit_file'].includes(t.name)),command=tools.some(t=>t.name==='run_command');
  const label=edited&&command?'编辑了文件，运行了命令':edited?'编辑了文件':command?'运行了命令':'调用了工具';
  heading.append(icon(edited?'edit':command?'terminal':'file'),el('span','',label),el('span','tool-count',String(tools.length)),icon('chevron'));group.append(heading,list);group.open=expandedTools.get(key)??presentation.tool_details!=='compact';
  if(presentation.show_changes&&onChanges&&tools.some(fileChange)){const review=button('查看文件变更',()=>onChanges(review.closest('.tool-history')._tools),'file-changes-button');review.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();});heading.append(review);}
  group.addEventListener('toggle',()=>{if(key&&group.isConnected){expandedTools.set(key,group.open);if(expandedTools.size>200)expandedTools.delete(expandedTools.keys().next().value);}});root.append(group);
  for(const [index,tool] of tools.entries()){
    const details=el('details','tool-activity'),summary=el('summary');
    details.dataset.toolId=String(tool.call_id||tool.id||index);details._tool=tool;
    const identity=key+':'+(tool.call_id||tool.id||index);details.open=expandedDetails.get(identity)??presentation.tool_details==='detailed';
    details.addEventListener('toggle',()=>{if(key&&details.isConnected){expandedDetails.set(identity,details.open);if(expandedDetails.size>500)expandedDetails.delete(expandedDetails.keys().next().value);}});
    const label=tool.status==='running'?(runStatus==='waiting_approval'?'等待批准':'正在运行'):tool.status==='completed'?'已完成':statusLabel(tool.status);
    const subject=tool.preview?.command||tool.path||tool.preview?.path||tool.name||'工具';
    const change=fileChange(tool);
    summary.append(icon(tool.name==='run_command'?'terminal':change?'edit':'file'),el('span','tool-operation',(change?'已编辑':label)+' '+subject));
    if(change)summary.append(diffStats(change));
    const output=tool.output_preview||tool.output_snippet||tool.summary;
    if(change)details.append(el('div','tool-patch'));else if(output)details.append(el('pre','tool-output',output));
    details.addEventListener('toggle',()=>showToolPatch(details));showToolPatch(details);
    if(tool.elapsed_ms!=null)summary.append(el('span','tool-duration',Math.round(tool.elapsed_ms)/1000+' 秒'));
    details.prepend(summary);list.append(details);
    if(tool.status==='running'){
      const current=el('div','active-tool');current.append(icon(tool.name==='run_command'?'terminal':'file'),el('span','tool-operation',label+' '+subject));root.append(current);
      current.dataset.renderKey='active-'+details.dataset.toolId;
    }
  }
  return root;
}
function updateTools(root,tools,runStatus,key,onChanges){
  const signature=conversationPresentation()+':'+runStatus+':'+JSON.stringify(tools);
  if(root._signature===signature)return;
  const fresh=toolRows(tools,runStatus,key,onChanges);if(root._presentation&&root._presentation!==conversationPresentation()){root.replaceWith(fresh);root=fresh;}else patchElement(root,fresh);root._tools=tools;root._signature=signature;root._presentation=conversationPresentation();
  for(const [index,tool] of tools.entries()){const details=[...root.querySelectorAll('.tool-activity')].find(item=>item.dataset.toolId===String(tool.call_id||tool.id||index));if(details){details._tool=tool;showToolPatch(details);}}
}
function assistantHeader(status,timing){
  const meta=el('div','assistant-status turn-meta');
  meta.dataset.renderKey='status';
  const label=status==='completed'?'已完成':status==='running'?'处理中':statusLabel(status);
  meta.append(el('span','',label),el('span','task-elapsed',elapsedText(timing?.elapsed_ms)));return meta;
}
export function renderTurn(node,actions={}){
  const root=el('article','turn'+(actions.focused?' focused':''));root.dataset.nodeId=node.id;
  const assistant=el('div','assistant-message');assistant.dataset.renderKey='assistant';assistant.append(assistantHeader(node.status,node.timing));
  const answer=prose(node.answer,'answer');answer.dataset.renderKey='answer';
  if(node.tools?.length)assistant.append(toolRows(node.tools,null,actions.toolKey||node.id,actions.changes));assistant.append(answer);
  const footer=el('div','assistant-footer'),controls=el('div','turn-actions');
  footer.dataset.renderKey='footer';
  controls.append(iconButton('copy','复制',()=>navigator.clipboard.writeText(node.answer)),iconButton('reply','引用追问',()=>actions.quote?.(node)),iconButton('info','详情',()=>actions.detail?.(node)));
  for(const [name,label,callback] of [['fork','分叉',actions.fork],['edit','编辑',actions.edit],['regenerate','重新生成',actions.regenerate]]){
    const control=iconButton(name,label,()=>callback?.(node));control.disabled=!node.recoverable||node.status!=='completed';controls.append(control);
  }
  const usage=node.usage||{},total=(usage.input_tokens||0)+(usage.output_tokens||0);
  footer.append(controls,el('span','message-usage',total?'用量 '+total.toLocaleString()+' tokens'+(usage.complete?'':'（不完整）'):'用量未知'),messageTime(node.timing?.finished_at));
  assistant.append(footer);root.append(userMessage(node.prompt,node.timing),assistant);return root;
}
export function renderLive(run,text,receivedAt=performance.now(),onChanges){
  const root=el('article','turn live');root.dataset.runId=run.id;
  const assistant=el('div','assistant-message'),meta=assistantHeader(run.status,{elapsed_ms:run.elapsed_ms});
  assistant.dataset.renderKey='assistant';
  const duration=meta.querySelector('.task-elapsed');duration.dataset.runtimeElapsed=run.elapsed_ms??'';duration.dataset.receivedAt=receivedAt;
  if(run.activity==='thinking')meta.firstChild.textContent='思考中';
  if(run.activity==='tools'&&run.status==='running')meta.firstChild.textContent='正在调用工具';
  const answer=prose(text,'answer');answer.dataset.renderKey='answer';
  assistant.append(meta,toolRows(run.tools,run.status,run.id,onChanges),answer);root.append(userMessage(run.message,{started_at:run.started_at}),assistant);return root;
}
export function updateLive(root,run,text,receivedAt,onChanges){
  const label=root.querySelector('.assistant-status>span:first-child'),phase=run.status==='waiting_approval'?'等待批准':run.activity==='thinking'?'思考中':run.activity==='tools'&&run.status==='running'?'正在调用工具':statusLabel(run.status);
  if(label.textContent!==phase)label.textContent=phase;
  label.classList.add('runtime-label');label.dataset.phase=run.activity||run.status;
  const duration=root.querySelector('.task-elapsed');
  if(Number.isFinite(run.elapsed_ms)){const estimated=Number(duration.dataset.runtimeElapsed||0)+Math.max(0,performance.now()-Number(duration.dataset.receivedAt||performance.now()));duration.dataset.runtimeElapsed=Math.max(run.elapsed_ms,estimated);duration.dataset.receivedAt=receivedAt;}
  updateTools(root.querySelector('.tool-history'),run.tools||[],run.status,run.id,onChanges);
  updateProse(root.querySelector('.answer'),text,prose);
}
export function installRuntimeClock(){
  setInterval(()=>{for(const label of document.querySelectorAll('[data-runtime-elapsed]')){
    if(label.dataset.runtimeElapsed!=='')label.textContent=elapsedText(Number(label.dataset.runtimeElapsed)+Math.max(0,performance.now()-Number(label.dataset.receivedAt)));
  }},1000);
}
export function renderArtifacts(node,onPreview){
  const list=el('div','artifact-list');
  if(!node.artifacts?.length){list.append(el('p','muted','本回合没有记录文件或命令产物。'));return list;}
  for(const artifact of node.artifacts){
    const card=el('section','artifact-card');card.dataset.kind=artifact.kind;
    const title=el('div','artifact-title');title.append(icon(artifact.kind==='command'?'terminal':artifact.kind==='file'?'file':'check'),el('strong','',artifact.title));card.append(title);
    if(artifact.kind==='file')card.append(el('p','artifact-path muted',artifact.path),button('预览产物',()=>onPreview(artifact),'secondary'));
    else if(artifact.kind==='command'){
      card.append(el('p','muted',statusLabel(artifact.status)+(artifact.exit_code!=null?' · 退出码 '+artifact.exit_code:'')),el('pre','tool-output',artifact.content||'没有记录输出。'));
    }else card.append(el('p','muted',statusLabel(artifact.status)),el('pre','tool-output',JSON.stringify(artifact.report,null,2)));
    list.append(card);
  }
  return list;
}
