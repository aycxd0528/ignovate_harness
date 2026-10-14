import {el,button,icon,field,modal,openPopover} from './ui.js';

export function showModels({anchor,configuration,save,settings,isCurrent}){
  const popup=openPopover(anchor,'选择模型');popup.panel.id='model-list';popup.panel.setAttribute('role','menu');
  popup.panel.append(el('div','menu-heading','选择模型'));
  for(const item of configuration.models||[{name:'default',model:configuration.model}]){
    const selected=item.name===configuration.model_name||!configuration.model_name&&item.model===configuration.model;
    const row=button('',async()=>{
      if(!isCurrent())throw new Error('工作区已切换，请重新选择模型。');
      row.disabled=true;try{await save({action:'select',name:item.name});popup.close();anchor.focus();}finally{row.disabled=false;}
    },'choice-item');row.setAttribute('role','menuitemradio');row.setAttribute('aria-label',item.name==='default'?'默认':item.name);row.setAttribute('aria-checked',String(selected));
    const copy=el('div');copy.append(el('strong','',item.name==='default'?'默认':item.name),el('span','muted',item.model));row.append(copy,icon(selected?'check':'info'));if(!selected)row.lastChild.classList.add('choice-placeholder');popup.panel.append(row);
  }
  const add=button('',()=>{popup.close();const body=el('div');body.append(field('模型名称','name',''),field('模型 ID','model',''),el('p','muted','新增模型使用当前连接：'+configuration.api_base));
    modal('新增模型',body,'添加并选择',data=>{if(!isCurrent())throw new Error('工作区已切换，请重新添加。');return save({action:'add',...data});});
  },'choice-item');add.setAttribute('role','menuitem');add.setAttribute('aria-label','新增模型');add.append(icon('plus'),el('span','','新增模型'));
  const connection=button('',()=>{popup.close();settings();},'choice-item');connection.setAttribute('role','menuitem');connection.append(icon('settings'),el('span','','连接设置'));
  popup.panel.append(el('hr'),add,connection);popup.show();popup.panel.querySelector('[aria-checked=true]')?.focus();
  popup.panel.addEventListener('keydown',event=>{if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;event.preventDefault();const items=[...popup.panel.querySelectorAll('button')],i=items.indexOf(document.activeElement);items[event.key==='Home'?0:event.key==='End'?items.length-1:(i+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();});
  return popup;
}
