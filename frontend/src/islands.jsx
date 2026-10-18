import React, {useLayoutEffect,useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {flushSync} from 'react-dom';
import {File02Icon,Attachment01Icon} from '@hugeicons/core-free-icons';
import PromptBar from './components/PromptBar.jsx';
import BranchedMenu from './components/BranchedMenu.jsx';
import ThoughtLine from './components/ThoughtLine.jsx';
import IgnovateAvatar from './components/IgnovateAvatar.jsx';

// React owns only these hosts. The native renderer retains messages and output.
const mounted=new Set();
let observer;
function island(host,initial,render){
  const root=createRoot(host);let disposed=false,seen=host.isConnected;
  const controller={props:initial,host,
    update(next){if(disposed)return;controller.props={...controller.props,...next};flushSync(()=>root.render(render(controller.props)));},
    destroy(){if(disposed)return;disposed=true;mounted.delete(controller);root.unmount();delete host._reactIsland;},
    connected(){if(host.isConnected){seen=true;return;}if(seen)controller.destroy();}
  };
  host.dataset.reactIsland='';host._reactIsland=controller;mounted.add(controller);controller.update({});
  observer||=(new MutationObserver(()=>{for(const item of [...mounted])item.connected();}));observer.observe(document.body,{childList:true,subtree:true});return controller;
}
function NativeNode({node}){const ref=useRef();useLayoutEffect(()=>{ref.current.replaceChildren(node);},[node]);return <div ref={ref} className="native-session-slot"/>;}

export function mountBranchedMenu(host,{items,active}){
  return island(host,{items,active},props=><BranchedMenu items={[{label:'',children:props.items}]} defaultOpen={0} activeValue={props.active} headingHidden renderItem={item=><NativeNode node={item.node}/>} width={500} fontSize={12} rowHeight={36} indent={26} trunk={6} radius={8} color="var(--ink)" accentColor="var(--accent)" lineColor="var(--border)"/>);
}

export function mountPromptBar(host,options){
  let controller;
  const change=value=>{controller.update({value});options.onDraft?.();};
  controller=island(host,{value:'',models:[],efforts:['模型默认'],defaultEffort:'模型默认',permission:{value:'default',label:'请求批准'}},props=><PromptBar {...props} width="100%" background="var(--sidebar)" color="var(--ink)" menuBackground="var(--surface)" sparkColor={getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()||'#315cff'} placeholder="向 Ignovate 描述你的任务…" onValueChange={change} onSend={options.onSend} onStop={options.onStop} onModelChange={options.onModelChange} onEffortChange={options.onEffortChange} onPermission={options.onPermission} onError={options.onError} onSettings={options.onSettings} onAddModel={options.onAddModel} onClearMode={options.onClearMode} onSource={options.onSource} onCommand={options.onCommand} sources={[
    {key:'file',name:'项目文件',description:'引用文件内容',icon:File02Icon},
    {key:'folder',name:'项目文件夹',description:'引用目录清单',icon:Attachment01Icon},
    {key:'plan',name:'制定计划',description:'先形成计划，再确认实施',icon:File02Icon},
    {key:'review',name:'只读审查',description:'审查内容与改动',icon:File02Icon},
    {key:'goal',name:'目标模式',description:'按预算持续推进目标',icon:File02Icon}
  ]} commands={[{key:'chat',name:'/chat',description:'实施 / 对话'},{key:'plan',name:'/plan',description:'制定计划'},{key:'review',name:'/review',description:'只读审查'},{key:'goal',name:'/goal',description:'目标模式'}]}/>);
  controller.setDraft=value=>controller.update({value});return controller;
}

function stepsFor(tools=[],status){
  if(!tools.length)return [];
  const pending=tools.filter(t=>t.status==='running'),current=pending.at(-1)||tools.at(-1);
  const subject=t=>t.preview?.command||t.path||t.preview?.path||t.name||'工具';
  const done=tools.filter(t=>t!==current&&['completed','failed','stopped'].includes(t.status)).slice(-3).map((t,i)=>({key:t.call_id||t.id||'done-'+i,text:subject(t),done:true}));
  const verb=status==='waiting_approval'?'等待批准':current.status==='running'?'正在运行':current.status==='completed'?'已完成':'执行结束';
  return [...done,{key:'active',text:verb+' '+subject(current)+(pending.length>1?'（'+pending.length+' 个工具同时处理中）':''),done:current.status!=='running',active:true}];
}
export function mountThoughtLine(host,data){
  const controller=island(host,data,props=>{
    const live=['running','queued','waiting_approval','stopping'].includes(props.status);
    const label=props.status==='waiting_approval'?'等待批准':props.status==='stopping'?'正在停止':props.activity==='tools'?'正在调用工具':props.activity==='thinking'?'思考中':'处理中';
    const elapsed=Number.isFinite(props.elapsed_ms)?Math.max(0,props.elapsed_ms)+(live?Math.max(0,(props.now??performance.now())-props.receivedAt):0):null;
    const doneLabel=props.status==='completed'?'已完成':props.status==='failed'?'执行失败':props.status==='stopped'?'已停止':props.status==='interrupted'?'已中断':'执行结束';
    return <div className="avatar-thought-line"><IgnovateAvatar status={props.status} activity={props.activity} decorative/><ThoughtLine glyph="none" label={label} doneLabel={doneLabel} renderLabel={(text,working)=><span className={working?'runtime-label':'thought-done-label'} data-phase={props.activity||props.status}>{text}</span>} working={live} steps={stepsFor(props.tools,props.status)} elapsed={elapsed===null?undefined:elapsed/1000} showTimer={elapsed!==null} fontSize={12} color="var(--muted)" glyphColor={live?'var(--accent)':'var(--muted)'} collapseOnSettle className="ignovate-thought-line"/></div>;
  });
  controller.tick=()=>{if(['running','queued','waiting_approval','stopping'].includes(controller.props.status))controller.update({now:performance.now()});};return controller;
}

export function mountAvatar(host,options={}){
  return island(host,options,props=><IgnovateAvatar {...props}/>);
}
