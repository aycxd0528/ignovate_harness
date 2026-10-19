import React, {useEffect,useRef,useState} from 'react';
import Stepper, {Step} from './Stepper.jsx';
import IgnovateAvatar from './IgnovateAvatar.jsx';
import './Onboarding.css';

const starter='请先只读查看这个项目，概述项目结构、运行方式和可以执行的验证。';
const tone=()=>document.documentElement.dataset.theme==='dark'||document.documentElement.dataset.theme==='system'&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';

export default function Onboarding({snapshot,onChooseProject,onConfigure,onComplete,onSkip,nativePicker=false,requestClose=0}){
  const [step,setStep]=useState(snapshot.onboarding?.status==='completed'&&snapshot.onboarding?.workspace_placeholder?2:1);
  const [theme,setTheme]=useState(tone),[pending,setPending]=useState(false),[error,setError]=useState('');
  const [base,setBase]=useState(snapshot.configuration.api_base||'https://api.deepseek.com');
  const [model,setModel]=useState(snapshot.configuration.model||'deepseek-flash'),[key,setKey]=useState('');
  const [project,setProject]=useState(snapshot.project.path),[example,setExample]=useState(false);
  const root=useRef(),alive=useRef(true),busy=useRef(false),closeCount=useRef(requestClose);
  const configured=snapshot.configuration.configured,placeholder=snapshot.onboarding?.workspace_placeholder;
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{
    const media=matchMedia('(prefers-color-scheme: dark)'),update=()=>setTheme(tone());
    const observer=new MutationObserver(update);observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    media.addEventListener('change',update);return()=>{observer.disconnect();media.removeEventListener('change',update);};
  },[]);
  useEffect(()=>{setProject(snapshot.project.path);setKey('');},[snapshot.project.id]);
  useEffect(()=>{const frame=requestAnimationFrame(()=>root.current?.querySelector('.stepper__pane:not(.stepper__pane--leaving) h2')?.focus({preventScroll:true}));return()=>cancelAnimationFrame(frame);},[step]);
  useEffect(()=>{if(requestClose!==closeCount.current){closeCount.current=requestClose;perform(onSkip);}},[requestClose]);
  async function perform(action){
    if(busy.current)return;busy.current=true;setPending(true);setError('');
    try{await action();}catch(reason){if(alive.current){let text=String(reason?.message||'操作未完成，请重试。');if(key)text=text.replaceAll(key,'[密钥已隐藏]');setError(text);}}
    finally{busy.current=false;if(alive.current)setPending(false);}
  }
  const go=next=>{setError('');setStep(next);};
  const choose=()=>perform(async()=>{const selected=await onChooseProject(nativePicker?undefined:project);if(selected)go(3);});
  const actions=(next,label)=> <div className="onboarding-actions">{step>1&&<button type="button" className="onboarding-back" disabled={pending} onClick={()=>go(step-1)}>返回</button>}<button type="button" className="onboarding-primary" disabled={pending} onClick={next}>{pending?'正在保存…':label}<span aria-hidden="true">→</span></button></div>;
  const heading=(title,subtitle)=><><h2 tabIndex={-1}>{title}</h2><p className="onboarding-description">{subtitle}</p></>;
  return <div className="onboarding-shell" ref={root} aria-busy={pending}>
    <header className="onboarding-header"><div className="onboarding-wordmark"><span className="onboarding-logo" aria-hidden="true">i</span><strong>Ignovate</strong><span>开始使用</span></div><button type="button" className="onboarding-skip" disabled={pending} onClick={()=>perform(onSkip)}>稍后设置</button></header>
    <Stepper step={step} onStepChange={next=>{if(!pending&&next<step)go(next);}} showFooter={false} frame={false} linear disableStepIndicators={pending} theme={theme} width="100%" accentColor="#315cff" aria-label="首次使用步骤" className="onboarding-stepper">
      <Step title="欢迎"><div className="onboarding-intro">
        <div>{heading('让第一个任务，从这里开始','连接本地项目和你选择的模型，把想法变成可以检查的交付。')}<ol className="onboarding-benefits"><li>选择项目，保留现有文件与会话。</li><li>描述目标，查看执行过程与改动。</li><li>核对验证结果，再验收交付。</li></ol></div>
        <div className="onboarding-illustration" aria-hidden="true"><div className="onboarding-avatar"><IgnovateAvatar decorative status="idle" size={84}/></div><div className="onboarding-sample"><i/><i/><i/><span>你的下一个项目</span><div className="onboarding-sample-line"/><div className="onboarding-sample-line short"/><div className="onboarding-sample-prompt">从一个清晰的目标开始<span>↑</span></div></div></div>
      </div>{actions(()=>go(2),'开始设置')}</Step>
      <Step title="工作区">{heading('选择你的工作区','Ignovate 会在这个文件夹中读取项目、修改文件并执行你授权的任务。')}
        <div className="onboarding-workspace"><span className="onboarding-label">{placeholder?'先选择一个项目文件夹':'当前工作区'}</span><strong>{snapshot.project.name}</strong><code>{snapshot.project.path}</code></div>
        {!nativePicker&&<label className="onboarding-field">工作区路径<input value={project} onChange={event=>setProject(event.target.value)} disabled={pending} autoComplete="off"/></label>}
        <div className="onboarding-workspace-buttons"><button type="button" disabled={pending} onClick={choose}>{nativePicker?'选择文件夹':'打开此工作区'}</button>{!placeholder&&<button type="button" className="onboarding-primary" disabled={pending} onClick={()=>go(3)}>使用当前工作区<span aria-hidden="true">→</span></button>}</div>
        {placeholder&&<p className="onboarding-hint">当前为应用的空白起始目录。选择项目后，后续任务会在该项目中执行。</p>}
        <div className="onboarding-actions"><button type="button" className="onboarding-back" disabled={pending} onClick={()=>go(1)}>返回</button></div>
      </Step>
      <Step title="模型">{heading('连接你的模型','填写模型服务的连接信息。保存设置不会发送任务或产生模型调用。')}
        {configured&&<div className="onboarding-existing"><div><span className="onboarding-label">已检测到连接</span><strong>{snapshot.configuration.model_name||snapshot.configuration.model}</strong><small>{snapshot.configuration.api_base}</small></div><button type="button" disabled={pending} onClick={()=>{setKey('');go(4);}}>使用已有连接</button></div>}
        <form className="onboarding-form" onSubmit={event=>{event.preventDefault();perform(async()=>{await onConfigure({api_base:base,model,api_key:key,reasoning_effort:'default'});setKey('');go(4);});}}>
          <label className="onboarding-field">API 地址<input type="url" required maxLength={2048} value={base} onChange={event=>setBase(event.target.value)} disabled={pending} autoComplete="url" spellCheck="false"/></label>
          <label className="onboarding-field">模型 ID<input required maxLength={256} value={model} onChange={event=>setModel(event.target.value)} disabled={pending} autoComplete="off" spellCheck="false"/></label>
          <label className="onboarding-field onboarding-key">API Key<input type="password" required={!configured} maxLength={8192} value={key} onChange={event=>setKey(event.target.value)} disabled={pending} autoComplete="new-password" placeholder={configured?'留空可保留已有密钥':'输入模型服务提供的 API Key'} spellCheck="false"/></label>
          <p className="onboarding-hint">密钥保存在本机配置中，不会写入聊天记录。这里只校验配置格式；发送第一条任务时才会实际连接模型。</p>
          <div className="onboarding-actions"><button type="button" className="onboarding-back" disabled={pending} onClick={()=>go(2)}>返回</button><button type="submit" className="onboarding-primary" disabled={pending}>{pending?'正在保存…':'保存并继续'}<span aria-hidden="true">→</span></button></div>
        </form>
      </Step>
      <Step title="开始">{heading('准备好开始了','先描述目标和验收标准，再把具体任务交给 Ignovate。')}
        <dl className="onboarding-summary"><div><dt>工作区</dt><dd>{snapshot.project.name}<small>{snapshot.project.path}</small></dd></div><div><dt>模型</dt><dd>{snapshot.configuration.model_name||snapshot.configuration.model||'尚未配置'}</dd></div><div><dt>操作权限</dt><dd>{{default:'请求批准',acceptEdits:'允许文件编辑',bypassPermissions:'完全访问'}[snapshot.configuration.permission_mode]||'请求批准'}</dd></div></dl>
        <label className="onboarding-example"><input type="checkbox" checked={example} onChange={event=>setExample(event.target.checked)} disabled={pending}/><span>把「只读了解项目」示例放入输入框<small>只填入草稿，点击发送后才会执行。</small></span></label>
        {actions(()=>perform(()=>onComplete(example?starter:'')),'进入工作台')}
      </Step>
    </Stepper>
    <div className="onboarding-error" role="alert" aria-atomic="true">{error||snapshot.onboarding?.error||''}</div>
    <footer className="onboarding-footnote">本地工作区 · 由你选择模型与操作权限</footer>
  </div>;
}
