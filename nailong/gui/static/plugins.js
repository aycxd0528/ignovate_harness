import {el,button,icon,iconButton,field,selectField,modal} from './ui.js';

const statusLabels={disconnected:'未连接',connecting:'连接中',connected:'已连接',error:'连接失败'};
export function createPlugins({read,mutate,isCurrent,isBusy}) {
  const root=el('div','plugins-page');root.id='plugins-page';
  let catalog=null,pending=false;
  const header=el('div','plugins-header'),heading=el('div');heading.append(el('h1','','插件'),el('p','muted','安装、启用和配置 Skills 与 MCP 服务'));
  const actions=el('div','plugin-header-actions'),refresh=iconButton('regenerate','刷新插件',load),add=button('',addDialog,'secondary','添加插件');add.append(icon('plus'),el('span','','添加插件'));actions.append(refresh,add);header.append(heading,actions);
  const body=el('div','plugins-body'),notice=el('p','plugin-notice muted');root.append(header,notice,body);
  const updateBusy=()=>{
    const busy=pending||isBusy();add.disabled=busy;
    root.querySelectorAll('.plugin-row button').forEach(button=>button.disabled=busy);
    notice.textContent=isBusy()?'任务执行期间可查看插件，完成后可修改。':'';notice.hidden=!notice.textContent;
  };
  root.updateBusy=updateBusy;
  async function load(){
    refresh.disabled=true;body.setAttribute('aria-busy','true');
    try{const result=await read();if(!isCurrent())return;catalog=result;render();}
    catch(error){if(isCurrent()){body.replaceChildren(el('p','muted',error.message),button('重试',load,'secondary'));}}
    finally{refresh.disabled=false;body.removeAttribute('aria-busy');}
  }
  async function change(operation,payload){
    if(!isCurrent())throw new Error('项目已切换，请重新打开插件。');
    if(pending)throw new Error('插件操作正在处理，请稍候。');
    pending=true;updateBusy();root.setAttribute('aria-busy','true');
    try{const result=await mutate(operation,payload);if(!isCurrent())return;catalog=result;render();return result;}
    finally{pending=false;root.removeAttribute('aria-busy');if(isCurrent())updateBusy();}
  }
  function toggle(name,label,checked,handler){
    const control=button('',handler,'plugin-switch',label);control.setAttribute('role','switch');control.setAttribute('aria-checked',String(checked));control.append(el('span'));return control;
  }
  function group(title,count){const section=el('section','plugin-group'),heading=el('h2','');const amount=el('span','muted',count);amount.setAttribute('aria-hidden','true');heading.append(document.createTextNode(title),amount);section.append(heading);body.append(section);return section;}
  function row(glyph,name,description){const row=el('article','plugin-row'),mark=el('div','plugin-mark');mark.append(icon(glyph));const copy=el('div','plugin-copy');copy.append(el('h3','',name),el('p','muted',description));row.append(mark,copy);return {row,copy};}
  function render(){
    body.replaceChildren();
    const skills=group('Skills',catalog.skills.length);
    if(!catalog.skills.length)skills.append(el('p','plugin-empty muted','尚未安装 Skills，可添加本地 Skill 目录。'));
    for(const skill of catalog.skills){
      const {row:entry,copy}=row('plugin',skill.name,skill.description);
      const source=el('span','plugin-source muted',skill.source+' · '+(skill.enabled?'已启用':'已禁用'));source.title=skill.path;copy.append(source);
      entry.append(toggle(skill.name,'启用 '+skill.name,skill.enabled,()=>change('skill_enable',{name:skill.name,enabled:!skill.enabled})));skills.append(entry);
    }
    const mcp=group('MCP',catalog.mcp.length);
    if(!catalog.mcp.length)mcp.append(el('p','plugin-empty muted','尚未配置 MCP 服务，添加后可手动连接。'));
    for(const server of catalog.mcp){
      const {row:entry,copy}=row('terminal',server.name,server.definition.transport==='stdio'?server.definition.command:server.definition.url);
      copy.append(el('span','plugin-source muted',`${statusLabels[server.status]||server.status}${server.status==='connected'?' · '+server.tool_count+' 个工具':''}`));
      if(server.error)copy.append(el('p','plugin-error',server.error));
      const actions=el('div','plugin-row-actions');actions.append(iconButton('settings','配置 '+server.name,()=>configure(server)),iconButton('archive','移除 '+server.name,()=>remove(server)),toggle(server.name,'连接 '+server.name,server.status==='connected',()=>change(server.status==='connected'?'mcp_disconnect':'mcp_connect',{name:server.name})));
      entry.append(actions);mcp.append(entry);
    }
    if(catalog.diagnostics.length){const diagnostics=el('details','plugin-diagnostics');diagnostics.append(el('summary','',catalog.diagnostics.length+' 项 Skills 发现提示'));for(const diagnostic of catalog.diagnostics)diagnostics.append(el('p','muted',diagnostic.path+' · '+diagnostic.reason));body.append(diagnostics);}
    updateBusy();
  }
  function definitionField(definition){
    const wrapper=el('label','field');wrapper.append(el('span','','服务声明 JSON'));
    const input=el('textarea','plugin-json');input.name='definition';input.value=JSON.stringify(definition,null,2);input.spellcheck=false;input.required=true;wrapper.append(input);return wrapper;
  }
  const help=()=>el('p','muted','配置保存到当前项目。env / headers 使用 ${ENV_NAME} 引用；点击连接后才启动 MCP 服务。');
  function parse(value){try{return JSON.parse(value);}catch{throw new Error('服务声明 JSON 格式无效。');}}
  function addDialog(){
    const body=el('div'),kind=selectField('类型','kind',[['mcp','MCP 服务'],['skill','本地 Skill']],'mcp'),fields=el('div');
    const type=kind.querySelector('select');type.setAttribute('aria-label','类型');const update=()=>{
      fields.replaceChildren();
      if(type.value==='skill'){fields.append(field('本地 Skill 目录','path'),el('p','muted','选择包含 SKILL.md 的目录，安装到当前项目的 .agents/skills。'));}
      else{fields.append(field('名称','name'),definitionField({transport:'stdio',command:'',args:[],env:{}}),help());}
    };
    type.addEventListener('change',update);body.append(kind,fields);update();
    modal('添加插件',body,'添加',values=>values.kind==='skill'?change('skill_install',{path:values.path}):change('mcp_add',{name:values.name,definition:parse(values.definition)}));
  }
  function configure(server){
    const body=el('div');body.append(el('p','muted',server.name),definitionField(server.definition),help());
    if(server.status==='connected')body.append(el('p','muted','请先断开连接再保存配置。'));
    modal('MCP 配置',body,'保存配置',values=>change('mcp_update',{name:server.name,definition:parse(values.definition)}));
  }
  function remove(server){modal('移除 MCP 配置',el('p','muted',`移除 ${server.name} 的项目配置并断开连接。`),'移除',()=>change('mcp_remove',{name:server.name}));}
  load();return root;
}
