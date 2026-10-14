export const $ = (selector, root = document) => root.querySelector(selector);
export function el(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== '') node.textContent = String(text);
  return node;
}
export function toast(message) {
  const node = $('#toast'); node.textContent = String(message); node.classList.add('visible');
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove('visible'), 5000);
}
export function button(text, handler, className = '', title = '') {
  const node = el('button', className, text); node.type = 'button';
  if (title) { node.title = title; node.setAttribute('aria-label', title); }
  if (handler) node.addEventListener('click', () => Promise.resolve().then(handler).catch(e => toast(e.message)));
  return node;
}
export function iconButton(name,label,handler,className='') {
  const node=button('',handler,'icon-button '+className,label);node.dataset.tooltip=label;node.removeAttribute('title');node.append(icon(name));return node;
}
let currentPopover;
export function closePopover(){currentPopover?.close();}
export function openPopover(anchor,label){
  closePopover();
  const panel=el('div','control-popover');panel.setAttribute('popover','auto');panel.setAttribute('aria-label',label);
  const cleanups=[];let closed=false;
  anchor.setAttribute('aria-expanded','true');
  const close=()=>{if(closed)return;closed=true;cleanups.forEach(cleanup=>cleanup());if(panel.matches(':popover-open'))panel.hidePopover();panel.remove();anchor.setAttribute('aria-expanded','false');if(currentPopover?.panel===panel)currentPopover=null;};
  currentPopover={panel,close};document.body.append(panel);
  panel.addEventListener('toggle',event=>{if(event.newState==='closed')close();});
  panel.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();anchor.focus();}});
  const show=()=>{
    panel.showPopover();const rect=anchor.getBoundingClientRect();
    panel.style.left=Math.max(8,Math.min(rect.left,innerWidth-panel.offsetWidth-8))+'px';
    const above=rect.top-panel.offsetHeight-8,below=rect.bottom+8;
    panel.style.top=(above>=8?above:below+panel.offsetHeight<=innerHeight-8?below:8)+'px';
  };
  return {panel,show,close,onClose:cleanup=>cleanups.push(cleanup)};
}
export function choicePopover(anchor,label,choices,selected,onSelect){
  const popup=openPopover(anchor,label);popup.panel.setAttribute('role','menu');
  for(const [value,text,description,glyph] of choices){
    const row=button('',async()=>{
      const items=[...popup.panel.querySelectorAll('button')];items.forEach(item=>item.disabled=true);
      try{await onSelect(value);popup.close();anchor.focus();}finally{items.forEach(item=>item.disabled=false);}
    },'choice-item');
    row.setAttribute('role','menuitemradio');row.setAttribute('aria-label',text);row.setAttribute('aria-checked',String(value===selected));
    if(glyph)row.dataset.value=value;
    const copy=el('div',glyph?'choice-copy':'');copy.append(el('strong','',text));if(description)copy.append(el('span','muted',description));
    if(glyph)row.append(icon(glyph));
    row.append(copy,icon(value===selected?'check':'info'));if(value!==selected)row.lastChild.classList.add('choice-placeholder');popup.panel.append(row);
  }
  popup.panel.addEventListener('keydown',event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    event.preventDefault();const items=[...popup.panel.querySelectorAll('button')],index=items.indexOf(document.activeElement);
    items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();
  });
  popup.show();popup.panel.querySelector('[aria-checked=true]')?.focus();return popup;
}
export function installTooltips(){
  const tip=el('div','control-tooltip');tip.id='control-tooltip';tip.setAttribute('role','tooltip');tip.setAttribute('popover','manual');document.body.append(tip);
  let origin,timer;
  const hide=()=>{
    clearTimeout(timer);if(tip.matches(':popover-open'))tip.hidePopover();
    if(origin){const ids=(origin.getAttribute('aria-describedby')||'').split(' ').filter(id=>id&&id!==tip.id);if(ids.length)origin.setAttribute('aria-describedby',ids.join(' '));else origin.removeAttribute('aria-describedby');}
    origin=null;
  };
  const show=(target,delay)=>{
    const control=target?.closest?.('button');if(!control?.querySelector('svg'))return;
    if(origin===control&&tip.matches(':popover-open'))return;
    hide();origin=control;
    timer=setTimeout(()=>{
      if(!control.isConnected)return hide();
      tip.textContent=control.dataset.tooltip||control.getAttribute('aria-label')||control.title||control.textContent;
      if(!tip.textContent)return hide();tip.showPopover();
      control.setAttribute('aria-describedby',[(control.getAttribute('aria-describedby')||''),tip.id].filter(Boolean).join(' '));
      const rect=control.getBoundingClientRect();tip.style.left=Math.max(8,Math.min(rect.left+(rect.width-tip.offsetWidth)/2,innerWidth-tip.offsetWidth-8))+'px';
      tip.style.top=(rect.top-tip.offsetHeight-7>=8?rect.top-tip.offsetHeight-7:Math.min(rect.bottom+7,innerHeight-tip.offsetHeight-8))+'px';
    },delay);
  };
  document.addEventListener('pointerover',event=>show(event.target,180));
  document.addEventListener('pointerout',event=>{if(origin&&!origin.contains(event.relatedTarget))hide();});
  document.addEventListener('focusin',event=>show(event.target,0));document.addEventListener('focusout',hide);
  document.addEventListener('pointerdown',hide);document.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')hide();});
}
export function field(label, name, value = '', type = 'text') {
  const wrapper = el('label', 'field'); wrapper.append(el('span', '', label));
  const input = el('input'); input.name = name; input.type = type; input.value = value; input.autocomplete = 'off';
  wrapper.append(input); return wrapper;
}
export function selectField(label, name, choices, value = '') {
  const wrapper = el('label', 'field'); wrapper.append(el('span', '', label));
  const select = el('select'); select.name = name;
  choices.forEach(([id, text]) => { const option = el('option', '', text); option.value = id; select.append(option); });
  select.value = value; wrapper.append(select); return wrapper;
}
export function modal(title, body, submitText, onSubmit) {
  const dialog = $('#dialog'); dialog.replaceChildren();
  const form = el('form', 'modal-form'); form.append(el('h2', '', title), body);
  const actions = el('div', 'dialog-actions');
  actions.append(button('取消', () => dialog.close(), 'secondary'));
  if (submitText) {
    const submit = button(submitText, null, 'primary'); submit.type = 'submit'; actions.append(submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); submit.disabled = true;
      try { await onSubmit(Object.fromEntries(new FormData(form))); dialog.close(); }
      catch (e) { toast(e.message); } finally { submit.disabled = false; }
    });
  }
  form.append(actions); dialog.append(form); dialog.showModal();
  setTimeout(() => form.querySelector('input, textarea, select')?.focus(), 0);
}
const escapeMarkup=value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const markdown=new Marked({gfm:true,breaks:true,renderer:{html({text}){return escapeMarkup(text);},image({text}){return escapeMarkup(text);}}});
const allowedTags=new Set('P BR H1 H2 H3 H4 H5 H6 STRONG EM DEL CODE PRE BLOCKQUOTE UL OL LI TABLE THEAD TBODY TR TH TD HR A INPUT'.split(' '));
export function prose(text, className = '') {
  const root=el('div','prose '+className),template=document.createElement('template');
  template.innerHTML=markdown.parse(String(text||''),{async:false});
  for(const node of [...template.content.querySelectorAll('*')]){
    if(!allowedTags.has(node.tagName)){node.replaceWith(document.createTextNode(node.textContent));continue;}
    const href=node.tagName==='A'?node.getAttribute('href'):null,language=node.tagName==='CODE'?node.className.match(/(?:^|\s)language-([\w-]{1,40})(?:\s|$)/)?.[1]:null;
    const checked=node.tagName==='INPUT'&&node.hasAttribute('checked'),start=node.tagName==='OL'?node.getAttribute('start'):null;
    for(const attribute of [...node.attributes])node.removeAttribute(attribute.name);
    if(node.tagName==='INPUT'){node.type='checkbox';node.disabled=true;node.checked=checked;}
    if(start&&/^\d+$/.test(start))node.setAttribute('start',start);
    if(language)node.dataset.language=language;
    if(href){try{const url=new URL(href,location.href);if(['http:','https:'].includes(url.protocol)){node.href=url.href;node.target='_blank';node.rel='noopener noreferrer';}}catch{/* Invalid destinations stay plain text. */}}
  }
  root.append(template.content);
  for(const pre of root.querySelectorAll('pre')){
    pre.classList.add('code-block');const code=pre.querySelector('code');
    const wrapper=el('div','code-frame'),header=el('div','code-header');
    const copy=iconButton('copy','复制代码',()=>navigator.clipboard.writeText(copy.closest('.code-frame')?.querySelector('pre')?.textContent||''));
    header.append(el('span','',code?.dataset.language||'代码'),copy);
    pre.replaceWith(wrapper);wrapper.append(header,pre);
  }
  for(const table of root.querySelectorAll('table')){const wrapper=el('div','markdown-table');table.replaceWith(wrapper);wrapper.append(table);}
  return root;
}
export const statusLabel = value => ({ completed: '已结束', running: '运行中', queued: '排队中', waiting_approval: '等待批准',
  stopping: '停止中', stopped: '已停止', interrupted: '已中断', failed: '失败', unknown: '结果未知', passed: '通过', stale: '已过期', unverified: '未验证' }[value] || value || '未知');
export function badge(status) { return el('span', 'badge '+status, statusLabel(status)); }
export function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const link = el('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
import {icon} from './icons.js';
import {Marked} from './markdown-engine.js';
export {icon};
export {permissionGlyph,permissionIcon} from './icons.js';
