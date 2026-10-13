import {el,icon,field,modal} from './ui.js';
import {createPlugins} from './plugins.js';

const sections=[['general','通用设置','sliders'],['models','模型','cloud'],['plugins','内置插件','plugin']];
let currentDialog;

/** The catalog contains provider metadata only. Keys are entered in child forms. */
export function showSettings({section='general',read,savePreferences,provider,plugins,onChanged=()=>{},isCurrent=()=>true,isBusy=()=>false,openConfig}) {
  const previousOrigin=currentDialog?.settingsOrigin;
  currentDialog?.close();
  const origin=previousOrigin?.isConnected?previousOrigin:document.activeElement;
  const dialog=el('dialog','settings-dialog');dialog.id='settings-dialog';dialog.settingsOrigin=origin;
  dialog.setAttribute('aria-labelledby','settings-title');
  let closed=false,catalog=null,active=sections.some(([id])=>id===section)?section:'general';
  let loadVersion=0,saveQueue=Promise.resolve(),providerPending=false,pluginPage=null;
  const optimistic=new Map(),bindings=new Map(),navButtons=new Map();
  const live=()=>!closed&&dialog.isConnected&&isCurrent();
  const childDialog=()=>document.getElementById('dialog');
  const header=el('header','settings-header'),title=el('h1','','设置');title.id='settings-title';
  const headerActions=el('div','settings-header-actions'),headerStatus=el('span','settings-header-status');
  headerStatus.setAttribute('role','status');headerStatus.setAttribute('aria-live','polite');
  const configButton=actionButton('查看配置位置',async()=>{
    if(!live())return;
    headerStatus.textContent='';
    try{await openConfig(catalog?.paths||{});}catch(error){if(live())headerStatus.textContent=message(error);}
  },'settings-config-button');configButton.prepend(icon('folder'));configButton.disabled=!openConfig;
  const closeButton=actionButton('',()=>dialog.close(),'icon-button');closeButton.setAttribute('aria-label','关闭设置');closeButton.append(icon('close'));
  headerActions.append(headerStatus,configButton,closeButton);header.append(title,headerActions);
  const layout=el('div','settings-layout'),navigation=el('nav','settings-nav'),content=el('main','settings-content');
  navigation.setAttribute('aria-label','设置分类');navigation.setAttribute('role','tablist');navigation.setAttribute('aria-orientation','vertical');
  content.id='settings-panel';content.setAttribute('role','tabpanel');
  for(const [id,label,glyph] of sections){
    const control=actionButton('',()=>selectSection(id),'settings-nav-item');control.id='settings-tab-'+id;
    control.setAttribute('role','tab');control.setAttribute('aria-controls',content.id);control.append(icon(glyph),el('span','',label));navButtons.set(id,control);navigation.append(control);
  }
  navigation.addEventListener('keydown',event=>{
    if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();const index=sections.findIndex(([id])=>id===active);
    const next=event.key==='Home'?0:event.key==='End'?sections.length-1:(index+(['ArrowDown','ArrowRight'].includes(event.key)?1:-1)+sections.length)%sections.length;
    selectSection(sections[next][0]);navButtons.get(active).focus();
  });
  layout.append(navigation,content);dialog.append(header,layout);document.body.append(dialog);currentDialog=dialog;
  dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close();});
  dialog.addEventListener('close',()=>{
    if(closed)return;closed=true;loadVersion++;
    // Child forms use the existing global dialog; their parent remains mounted.
    if(childDialog()?.open&&childDialog().dataset.settingsOwner===dialog.id){childDialog().close();delete childDialog().dataset.settingsOwner;}
    dialog.remove();if(currentDialog===dialog)currentDialog=null;
    if(origin?.isConnected&&!currentDialog?.open)origin.focus();
  });
  dialog.selectSection=selectSection;dialog.reload=load;dialog.updateBusy=updateBusy;
  dialog.showModal();selectSection(active);load();
  return dialog;

  function actionButton(text,handler,className=''){
    const control=el('button',className,text);control.type='button';
    control.addEventListener('click',()=>Promise.resolve().then(handler).catch(error=>{if(live())headerStatus.textContent=message(error);}));return control;
  }
  function message(error){return String(error?.message||error||'操作失败，请重试。');}
  function replaceCatalog(result){
    catalog={...result,preferences:{...result.preferences}};
    for(const [key,value] of optimistic)catalog.preferences[key]=value;
  }
  async function load(){
    if(!live())return;const version=++loadVersion;
    if(!catalog){content.setAttribute('aria-busy','true');content.replaceChildren(el('p','settings-loading muted','正在读取设置…'));}
    try{const result=await read();if(!live()||version!==loadVersion)return;replaceCatalog(result);render();}
    catch(error){if(!live()||version!==loadVersion)return;const state=el('div','settings-load-error');state.append(el('p','',message(error)),actionButton('重试',load,'secondary'));content.replaceChildren(state);}
    finally{if(live()&&version===loadVersion)content.removeAttribute('aria-busy');}
  }
  function selectSection(id){
    if(!sections.some(([value])=>value===id)||!live())return;
    active=id;for(const [value,control] of navButtons){const selected=value===id;control.classList.toggle('selected',selected);control.setAttribute('aria-selected',String(selected));control.tabIndex=selected?0:-1;}
    content.setAttribute('aria-labelledby','settings-tab-'+id);if(catalog)render();
  }
  function pageHeading(name,description){const heading=el('div','settings-page-heading');heading.append(el('h2','',name));if(description)heading.append(el('p','muted',description));content.append(heading);}
  function group(name){const group=el('section','settings-group');group.append(el('h3','settings-group-title',name));content.append(group);return group;}
  function row(group,key,label,description){
    const root=el('div','settings-row'),copy=el('div','settings-row-copy'),heading=el('label','settings-row-title',label);
    heading.htmlFor='settings-'+key;copy.append(heading);if(description)copy.append(el('p','muted',description));
    const control=el('div','settings-row-control'),status=el('p','settings-row-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');copy.append(status);root.append(copy,control);group.append(root);
    return {root,copy,control,status};
  }
  function bind(key,entry,update){
    const item={...entry,update};bindings.set(key,item);update(catalog.preferences[key]);
    if(optimistic.has(key))setRowState(key,'saving','正在保存…');return item;
  }
  function setRowState(key,state,text){
    const item=bindings.get(key);if(!item)return;item.root.dataset.state=state;item.status.textContent=text;
    item.root.setAttribute('aria-busy',String(state==='saving'));
    item.control.querySelectorAll('button,input,select').forEach(control=>control.disabled=state==='saving');
  }
  function savePreference(key,value){
    if(!live())return;
    const previous=catalog.preferences[key];if(JSON.stringify(previous)===JSON.stringify(value))return;
    optimistic.set(key,value);catalog.preferences[key]=value;bindings.get(key)?.update(value);setRowState(key,'saving','正在保存…');
    saveQueue=saveQueue.catch(()=>{}).then(async()=>{
      if(!live())return;
      try{
        const result=await savePreferences({[key]:value});if(!live())return;
        optimistic.delete(key);replaceCatalog(result);await onChanged(result);if(!live())return;
        bindings.get(key)?.update(catalog.preferences[key]);setRowState(key,'saved','已保存');
      }catch(error){
        if(!live())return;optimistic.delete(key);catalog.preferences[key]=previous;bindings.get(key)?.update(previous);setRowState(key,'error',message(error));
      }
    });
  }
  function selectRow(group,key,label,description,choices){
    const entry=row(group,key,label,description),select=el('select');select.id='settings-'+key;select.setAttribute('aria-label',label);
    for(const [value,text] of choices){const option=el('option','',text);option.value=value;select.append(option);}
    entry.control.append(select);bind(key,entry,value=>select.value=value);select.addEventListener('change',()=>savePreference(key,select.value));return entry;
  }
  function render(){
    const scroll=content.scrollTop;bindings.clear();content.replaceChildren();
    if(active==='general')renderGeneral();else if(active==='models')renderModels();else renderPlugins();
    content.scrollTop=scroll;updateBusy();
  }
  function renderGeneral(){
    pageHeading('通用设置','设置当前项目的会话默认值与显示方式');
    const chat=group('会话');
    selectRow(chat,'default_permission','新会话默认权限','仅影响新建会话，现有会话保留各自的权限。',[['default','默认权限'],['acceptEdits','自动编辑'],['bypassPermissions','完全访问']]);
    selectRow(chat,'busy_send','运行时发送消息','选择任务执行期间再次发送消息的方式。',[['queue','排队发送'],['draft','保留草稿']]);
    const appearance=group('外观'),theme=row(appearance,'theme','主题','选择工作台的外观。');theme.root.classList.add('settings-theme-row');
    const themes=el('div','settings-theme-choices');themes.id='settings-theme';themes.setAttribute('role','radiogroup');themes.setAttribute('aria-label','主题');
    const themeButtons=new Map();
    for(const [value,label] of [['light','浅色'],['dark','深色'],['system','跟随系统']]){
      const control=actionButton('',()=>savePreference('theme',value),'settings-theme-choice');control.setAttribute('role','radio');control.setAttribute('aria-label',label);control.dataset.theme=value;
      const preview=el('span','settings-theme-preview'),previewSide=el('span','settings-theme-preview-sidebar'),previewBody=el('span','settings-theme-preview-body');
      previewSide.append(el('i'),el('i'),el('i'));previewBody.append(el('i'),el('i'),el('i'));preview.append(previewSide,previewBody);
      const caption=el('span','settings-theme-caption');caption.append(el('span','',label),icon('check'));control.append(preview,caption);themes.append(control);themeButtons.set(value,control);
    }
    themes.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();
      const values=[...themeButtons.keys()],index=values.indexOf(catalog.preferences.theme),next=(index+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+values.length)%values.length;
      themeButtons.get(values[next]).focus();savePreference('theme',values[next]);
    });
    theme.control.append(themes);bind('theme',theme,value=>{for(const [name,control] of themeButtons){control.setAttribute('aria-checked',String(name===value));control.tabIndex=name===value?0:-1;}});
    const font=row(appearance,'font_size','字体大小','调整聊天内容字号，界面文字保持不变。'),fontControl=el('div','settings-font-control'),fontInput=el('input');
    fontInput.id='settings-font_size';fontInput.type='number';fontInput.min='12';fontInput.max='22';fontInput.step='1';fontInput.setAttribute('aria-label','字体大小');fontControl.append(fontInput,el('span','muted','px'));font.control.append(fontControl);
    bind('font_size',font,value=>fontInput.value=value);fontInput.addEventListener('change',()=>{
      const value=Number(fontInput.value);if(!Number.isInteger(value)||value<12||value>22){setRowState('font_size','error','请输入 12–22 之间的整数。');return;}savePreference('font_size',value);
    });
    selectRow(appearance,'tool_details','工具记录','控制聊天中工具调用的默认展开程度。',[['compact','简洁'],['standard','标准'],['detailed','详细']]);
    const changes=row(appearance,'show_changes','显示文件变更入口','在聊天中显示文件变更与差异查看入口。'),toggle=actionButton('',()=>savePreference('show_changes',!catalog.preferences.show_changes),'settings-switch');
    toggle.id='settings-show_changes';toggle.setAttribute('role','switch');toggle.setAttribute('aria-label','显示文件变更入口');toggle.append(el('span'));changes.control.append(toggle);bind('show_changes',changes,value=>toggle.setAttribute('aria-checked',String(value)));
    const shortcuts=group('快捷键');shortcuts.append(el('p','settings-shortcut-help muted','Mod 在 macOS 上表示 ⌘，在 Windows / Linux 上表示 Ctrl。可组合 Shift 或 Alt。'));
    const shortcutEntry=row(shortcuts,'shortcuts','键盘快捷键',''),shortcutFields=el('div','settings-shortcut-fields');shortcutEntry.root.classList.add('settings-shortcut-row');shortcutEntry.copy.hidden=true;shortcutEntry.control.append(shortcutFields);
    const shortcutInputs=new Map();
    for(const [name,label] of [['new_chat','新建会话'],['commands','命令面板'],['find','搜索聊天']]){
      const entry=el('label','settings-shortcut-field'),input=el('input');input.type='text';input.id='settings-shortcut-'+name;input.autocomplete='off';input.spellcheck=false;input.setAttribute('aria-label',label+'快捷键');entry.append(el('span','',label),input);shortcutFields.append(entry);shortcutInputs.set(name,input);
      const commit=()=>{
        const values={};for(const [key,control] of shortcutInputs){const normalized=normalizeShortcut(control.value);if(!normalized){setRowState('shortcuts','error','使用 Mod + 字母或数字，可加入 Shift / Alt。');shortcutEntry.copy.hidden=false;return;}values[key]=normalized;}
        if(new Set(Object.values(values)).size!==3){setRowState('shortcuts','error','快捷键不能重复。');shortcutEntry.copy.hidden=false;return;}
        shortcutEntry.copy.hidden=false;savePreference('shortcuts',values);
      };
      input.addEventListener('change',commit);input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();commit();}});
    }
    bind('shortcuts',shortcutEntry,value=>{for(const [name,input] of shortcutInputs)input.value=value[name]||'';});
  }
  function normalizeShortcut(value){
    const parts=String(value).replace(/\s/g,'').split('+');if(parts.shift()?.toLowerCase()!=='mod')return null;
    const key=parts.pop();if(!/^[a-z0-9]$/i.test(key||''))return null;
    const modifiers=parts.map(part=>part.toLowerCase());if(modifiers.some(part=>!['shift','alt'].includes(part))||new Set(modifiers).size!==modifiers.length)return null;
    return ['Mod',...(modifiers.includes('shift')?['Shift']:[]),...(modifiers.includes('alt')?['Alt']:[]),key.toUpperCase()].join('+');
  }
  function renderModels(){
    pageHeading('模型','管理 OpenAI 兼容的 API 连接与模型');
    const notice=el('p','settings-model-notice muted');notice.dataset.busyNotice='';content.append(notice);
    const providers=el('div','settings-providers');content.append(providers);
    for(const item of catalog.providers||[]){
      const entry=el('article','settings-provider'),mark=el('div','settings-provider-mark');mark.append(icon('cloud'));
      const copy=el('div','settings-provider-copy'),name=el('div','settings-provider-heading');name.append(el('h3','',item.name||'模型提供商'));
      if(item.active)name.append(el('span','settings-current-provider','当前使用'));
      const state=el('span','settings-provider-state',item.has_key?'已配置':'待配置');state.dataset.configured=String(!!item.has_key);state.title=item.has_key?'已保存 API Key':'尚未保存 API Key';name.append(state);
      copy.append(name,el('p','settings-provider-model',item.model||'尚未填写模型 ID'),el('p','muted settings-provider-url',item.api_base||'尚未填写 API 地址'));
      const actions=el('div','settings-provider-actions'),edit=actionButton('编辑',()=>providerForm(item),'secondary');edit.setAttribute('aria-label','编辑 '+item.name);edit.dataset.providerAction='';actions.append(edit);
      if(!item.active){
        const use=actionButton('使用',()=>changeProvider({action:'activate',id:item.id}),'secondary');use.setAttribute('aria-label','使用 '+item.name);use.dataset.providerAction='';use.dataset.needsKey=String(!item.has_key);actions.append(use);
        if(item.id!=='current'){const remove=actionButton('移除',()=>removeProvider(item),'settings-remove-provider');remove.setAttribute('aria-label','移除 '+item.name);remove.dataset.providerAction='';actions.append(remove);}
      }
      entry.append(mark,copy,actions);providers.append(entry);
    }
    const add=actionButton('',()=>providerForm(),'settings-add-provider');add.setAttribute('aria-label','添加模型提供商');add.dataset.providerAction='';add.append(icon('plus'),el('span','','添加模型提供商'));content.append(add);
    content.append(el('p','settings-model-footnote muted','“已配置”表示已保存 API Key；使用时通过当前连接发送请求。'));
  }
  function updateBusy(){
    if(!live())return;const busy=isBusy()||providerPending;
    content.querySelectorAll('[data-provider-action]').forEach(control=>control.disabled=busy||control.dataset.needsKey==='true');
    const notice=content.querySelector('[data-busy-notice]');if(notice){notice.textContent=isBusy()?'任务执行期间可查看模型连接，完成后可修改。':providerPending?'正在保存连接…':'';notice.hidden=!notice.textContent;}
    if(pluginPage?.isConnected)pluginPage.updateBusy();
  }
  function checkProvider(){
    if(!live())throw new Error('工作区已切换，请重新打开设置。');
    if(isBusy())throw new Error('请等待当前任务完成后修改模型连接。');
    if(providerPending)throw new Error('模型连接正在处理，请稍候。');
  }
  async function changeProvider(payload){
    checkProvider();providerPending=true;updateBusy();headerStatus.textContent='';
    try{
      const result=await provider(payload);if(!live())return;replaceCatalog(result);await onChanged(result);if(!live())return;
      if(active==='models')render();headerStatus.textContent='已保存';return result;
    }catch(error){if(live())headerStatus.textContent=message(error);throw error;}
    finally{providerPending=false;if(live())updateBusy();}
  }
  function claimChild(){if(childDialog())childDialog().dataset.settingsOwner=dialog.id;}
  function providerForm(item){
    checkProvider();const body=el('div','settings-provider-form'),error=el('p','settings-form-error');error.setAttribute('role','alert');error.hidden=true;
    const name=field('名称','name',item?.name||''),base=field('API 地址','api_base',item?.api_base||''),model=field('模型 ID','model',item?.model||''),key=field('API Key','api_key','','password');
    for(const control of [name,base,model])control.querySelector('input').required=true;
    base.querySelector('input').type='url';base.querySelector('input').placeholder='https://api.example.com/v1';model.querySelector('input').placeholder='模型 ID';
    const keyInput=key.querySelector('input');keyInput.required=!item?.has_key;keyInput.placeholder=item?.has_key?'留空保留已保存的 API Key':'输入 API Key';keyInput.autocomplete='new-password';
    body.append(name,base,model,key,el('p','muted',item?.has_key?'API Key 已保存在本机；留空将保留原来的密钥。':'API Key 将保存在本机配置中。'),error);
    const activate=!!item?.active||item?.id==='current'||!item&&!(catalog.providers||[]).some(value=>value.active&&value.has_key);
    const submit=item?.id==='current'?'保存连接':!item&&activate?'保存并使用':'保存提供商';
    modal(item?'编辑模型提供商':'添加模型提供商',body,submit,async values=>{
      const payload={action:'save',...(item?{id:item.id}:{}),name:values.name.trim(),api_base:values.api_base.trim(),model:values.model.trim(),api_key:values.api_key,...(activate?{activate:true}:{})};
      try{await changeProvider(payload);}catch(reason){if(live()){error.textContent=message(reason);error.hidden=false;}throw reason;}
    });claimChild();
  }
  function removeProvider(item){
    checkProvider();modal('移除模型提供商',el('p','',`移除 ${item.name} 及其保存在本机的 API Key。`),'移除',()=>changeProvider({action:'remove',id:item.id}));claimChild();
  }
  function renderPlugins(){
    if(!plugins){pageHeading('内置插件');content.append(el('p','muted','插件管理暂不可用。'));return;}
    if(!pluginPage?.isConnected){
      const page=createPlugins({read:plugins.read,mutate:plugins.mutate,isCurrent:()=>page.isConnected&&live(),isBusy});pluginPage=page;pluginPage.id='settings-plugins-page';
      // Existing plugin forms use ui.modal; label them as children of this dialog.
      pluginPage.addEventListener('click',()=>queueMicrotask(()=>{if(childDialog()?.open)claimChild();}));
      const heading=pluginPage.querySelector('h1');if(heading)heading.textContent='内置插件';
    }
    content.append(pluginPage);
  }
}
