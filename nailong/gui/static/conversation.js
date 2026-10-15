import {el,icon,iconButton,prose,statusLabel,button,copyText} from './ui.js';
import {fileChange,diffStats,renderDiff} from './diff.js';
import {patchElement,updateProse} from './rendering.js';
import {statusMark} from './react-bits.js';
import {mountThoughtLine} from './react-bits-islands.js';

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
  meta.append(messageTime(timing?.started_at),iconButton('copy','复制输入',()=>copyText(text)));
  root.append(el('div','prompt',text),meta);return root;
}
const expandedTools=new Map(),expandedDetails=new Map(),liveToolRuns=new Set();
let presentation={tool_details:'compact',show_changes:true};
export function configureConversation(preferences){presentation={...presentation,...preferences};}
export const conversationPresentation=()=>presentation.tool_details+':'+presentation.show_changes;
function showToolPatch(details){
  const change=fileChange(details._tool),patch=details.querySelector('.tool-patch');
  if(!details.open||!change||!patch)return;
  const signature=JSON.stringify(change);if(patch._change!==signature||!patch.childElementCount){patch.replaceChildren(renderDiff(change));patch._change=signature;}
}
export function toolRows(tools=[],runStatus,key='',onChanges){
  const wasLive=liveToolRuns.has(key);
  const root=el('div','tool-history');root.dataset.renderKey='tools';root._tools=tools;if(!tools.length)return root;
  if(['queued','running','waiting_approval','stopping'].includes(runStatus)){
    if(key){liveToolRuns.add(key);if(liveToolRuns.size>200)liveToolRuns.delete(liveToolRuns.values().next().value);}
    const pending=tools.filter(tool=>tool.status==='running'),tool=pending.at(-1)||tools.at(-1);
    const label=runStatus==='stopping'?'正在停止':tool.status==='running'?(runStatus==='waiting_approval'?'等待批准':'正在运行'):tool.status==='completed'?'已完成':statusLabel(tool.status);
    const subject=tool.preview?.command||tool.path||tool.preview?.path||tool.name||'工具';
    for(const done of tools.filter(item=>item!==tool&&['completed','failed','stopped'].includes(item.status)).slice(-3)){
      const step=el('div','thought-line-step');step.dataset.renderKey='step-'+(done.call_id||done.id||tools.indexOf(done));
      const description=done.preview?.command||done.path||done.preview?.path||done.name||'工具';
      step.append(statusMark(done.status),el('span','',description));step.title=statusLabel(done.status)+' '+description;root.append(step);
    }
    const current=el('div','active-tool');current.dataset.renderKey='active';
    current.append(icon(tool.name==='run_command'?'terminal':['write_file','edit_file'].includes(tool.name)?'edit':'file'),el('span','tool-operation',label+' '+subject));
    current.title=label+' '+subject;
    if(pending.length>1)current.append(el('span','tool-parallel-count',pending.length+' 个工具同时处理中'));
    root.append(current);return root;
  }
  key=presentation.tool_details+':'+key;
  const group=el('details','tool-group'),heading=el('summary'),list=el('div','tool-activity-list');
  const edited=tools.some(t=>['write_file','edit_file'].includes(t.name)),command=tools.some(t=>t.name==='run_command');
  const label=wasLive?'调用了工具':edited&&command?'编辑了文件，运行了命令':edited?'编辑了文件':command?'运行了命令':'调用了工具';
  heading.append(icon(edited?'edit':command?'terminal':'file'),el('span','',label),el('span','tool-count',String(tools.length)),icon('chevron'));group.append(heading,list);group.open=expandedTools.get(key)??(!wasLive&&presentation.tool_details!=='compact');
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
  }
  return root;
}
function updateTools(root,tools,runStatus,key,onChanges){
  const signature=conversationPresentation()+':'+runStatus+':'+JSON.stringify(tools);
  if(root._signature===signature)return;
  const fresh=toolRows(tools,runStatus,key,onChanges);if(root._presentation&&root._presentation!==conversationPresentation()){root.replaceWith(fresh);root=fresh;}else patchElement(root,fresh);root._tools=tools;root._signature=signature;root._presentation=conversationPresentation();
  for(const [index,tool] of tools.entries()){const details=[...root.querySelectorAll('.tool-activity')].find(item=>item.dataset.toolId===String(tool.call_id||tool.id||index));if(details){details._tool=tool;showToolPatch(details);}}
}
function assistantHeader(status,timing,data={}){
  const meta=el('div','assistant-status turn-meta react-thought-host');meta.dataset.renderKey='status';
  mountThoughtLine(meta,{status,elapsed_ms:timing?.elapsed_ms,receivedAt:performance.now(),...data});return meta;
}
export function renderTurn(node,actions={}){
  const root=el('article','turn'+(actions.focused?' focused':''));root.dataset.nodeId=node.id;
  const assistant=el('div','assistant-message');assistant.dataset.renderKey='assistant';assistant.append(assistantHeader(node.status,node.timing,{tools:node.tools}));
  const answer=prose(node.answer,'answer');answer.dataset.renderKey='answer';
  if(node.tools?.length)assistant.append(toolRows(node.tools,null,actions.toolKey||node.id,actions.changes));assistant.append(answer);
  const footer=el('div','assistant-footer'),controls=el('div','turn-actions');
  footer.dataset.renderKey='footer';
  controls.append(iconButton('copy','复制',()=>copyText(node.answer)),iconButton('reply','引用追问',()=>actions.quote?.(node)),iconButton('info','详情',()=>actions.detail?.(node)));
  for(const [name,label,callback] of [['fork','分叉',actions.fork],['edit','编辑',actions.edit],['regenerate','重新生成',actions.regenerate]]){
    const control=iconButton(name,label,()=>callback?.(node));control.disabled=!node.recoverable||node.status!=='completed';controls.append(control);
  }
  const usage=node.usage||{},total=(usage.input_tokens||0)+(usage.output_tokens||0);
  footer.append(controls,el('span','message-usage',total?'用量 '+total.toLocaleString()+' tokens'+(usage.complete?'':'（不完整）'):'用量未知'),messageTime(node.timing?.finished_at));
  assistant.append(footer);root.append(userMessage(node.prompt,node.timing),assistant);return root;
}
export function renderLive(run,text,receivedAt=performance.now(),onChanges){
  liveToolRuns.add(run.id);if(liveToolRuns.size>200)liveToolRuns.delete(liveToolRuns.values().next().value);
  const root=el('article','turn live');root.dataset.runId=run.id;
  const assistant=el('div','assistant-message');assistant.dataset.renderKey='assistant';
  const meta=assistantHeader(run.status,{elapsed_ms:run.elapsed_ms},{activity:run.activity,tools:run.tools,receivedAt});
  const history=el('div','tool-history');history.dataset.renderKey='tools';history._tools=run.tools||[];
  const answer=prose(text,'answer');answer.dataset.renderKey='answer';
  assistant.append(meta,history,answer);root.append(userMessage(run.message,{started_at:run.started_at}),assistant);return root;
}
export function updateLive(root,run,text,receivedAt,onChanges){
  root.querySelector('.react-thought-host')._reactIsland.update({status:run.status,activity:run.activity,tools:run.tools,elapsed_ms:run.elapsed_ms,receivedAt,now:performance.now()});
  root.querySelector('.tool-history')._tools=run.tools||[];
  updateProse(root.querySelector('.answer'),text,prose);
}
export function installRuntimeClock(){setInterval(()=>{for(const host of document.querySelectorAll('.react-thought-host'))host._reactIsland?.tick();},1000);}
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
