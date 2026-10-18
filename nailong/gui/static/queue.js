import {el,button,iconButton,toast} from './ui.js';
import {synchronize} from './rendering.js';

// Keep an editor's DOM and local text across runtime polling updates.
export function renderQueue(host,{scope,runs,edit,remove,paused=false}){
  host.dataset.paused=String(paused);
  if(host._scope!==scope){host.replaceChildren();host._entries=new Map();host._scope=scope;}
  const cache=host._entries,wanted=[];
  for(const run of runs){
    let entry=cache.get(run.id);
    if(!entry){
      const row=el('div','queued-message');row.dataset.runId=run.id;
      const text=el('span','queued-message-text'),actions=el('div','queued-message-actions');
      entry={row,text,run};cache.set(run.id,entry);
      const show=()=>{
        entry.editing=false;entry.editor=null;text.textContent=entry.run.message;
        row.replaceChildren(text,actions);
      };
      const start=()=>{
        entry.editing=true;const expected=entry.run.message;
        const editor=el('textarea','queued-message-editor');editor.value=expected;editor.rows=2;
        editor.setAttribute('aria-label','编辑等待发送的问题');entry.editor=editor;
        const controls=el('div','queued-message-actions');
        const save=button('保存修改',async()=>{
          const message=editor.value;if(!message.trim()){toast('问题不能为空。');return;}
          entry.saving=true;save.disabled=cancel.disabled=editor.readOnly=true;
          try{await edit(entry.run,message,expected);show();}
          finally{entry.saving=false;save.disabled=cancel.disabled=editor.readOnly=false;}
        },'text-button');
        const cancel=button('取消编辑',show,'text-button');controls.append(cancel,save);
        row.replaceChildren(editor,controls);editor.focus();
        editor.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();if(entry.saving)return;show();actions.firstChild.focus();}});
      };
      actions.append(iconButton('edit','编辑排队消息',start),iconButton('close','删除排队消息',()=>remove(entry.run)));
      show();
    }
    entry.run=run;if(!entry.editing)entry.text.textContent=run.message;
    wanted.push(entry.row);
  }
  for(const [id] of cache)if(!runs.some(run=>run.id===id))cache.delete(id);
  synchronize(host,wanted);host.hidden=wanted.length===0;
}
