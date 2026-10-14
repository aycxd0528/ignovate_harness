import {el,button,icon,iconButton,openPopover,toast} from './ui.js';
import {createEffortFire} from './effort-fire.js';

export function showReasoning({anchor,configuration:conf,projectId,save,openModel,isCurrent}){
  const choices=conf.reasoning_options||[['default','模型默认','沿用模型服务端默认行为']],usable=choices.length>1;
  const popup=openPopover(anchor,'推理等级');popup.panel.id='reasoning-popover';popup.panel.classList.add('reasoning-popover','effort-panel');popup.panel.setAttribute('role','dialog');
  let committed=conf.reasoning_effort,pending=null,inFlight=null,saving=false,closed=false;
  const initial=Math.max(0,choices.findIndex(choice=>choice[0]===committed));
  const header=el('div','effort-heading'),label=el('strong','effort-value');
  const model=button('',()=>{popup.close();openModel();},'reasoning-model','选择模型');
  const modelName=el('span','effort-model-name',conf.model);modelName.append(icon('chevron'));model.append(label,modelName);
  const close=iconButton('close','关闭推理面板',()=>popup.close());header.append(icon('storm'),model,close);
  const labels=el('div','reasoning-labels'),track=el('div','reasoning-track'),canvas=el('canvas','effort-fire'),ticks=el('div','reasoning-ticks'),thumb=el('div','reasoning-thumb'),slider=el('input','reasoning-slider');
  slider.type='range';slider.min=0;slider.max=100;slider.step=1;slider.value=usable?initial/(choices.length-1)*100:0;slider.disabled=!usable;slider.setAttribute('aria-label','推理等级');
  choices.forEach((choice,index)=>{const span=el('span','',choice[0]==='off'?'OFF':choice[0]==='max'?'MAX':choice[0]==='default'?'默认':choice[1]);const position=usable?index/(choices.length-1)*100:50;span.style.left=position+'%';labels.append(span);const dot=el('i');dot.style.left=position+'%';ticks.append(dot);});
  track.append(el('div','reasoning-rail'),canvas,ticks,thumb,slider);
  const description=el('p','reasoning-description muted'),feedback=el('div','effort-feedback'),reset=iconButton('regenerate','恢复模型默认',()=>{slider.value=Math.max(0,choices.findIndex(choice=>choice[0]==='default'))/(choices.length-1)*100;commit();}),status=el('span','muted');feedback.append(status,reset);
  popup.panel.append(header,labels,track,description,feedback);popup.show();
  const fire=usable?createEffortFire(canvas):{update(){},dispose(){}};canvas.hidden=!usable;popup.onClose(()=>{closed=true;pending=null;fire.dispose();});
  const index=()=>Math.max(0,Math.min(choices.length-1,Math.round(Number(slider.value)/100*(choices.length-1))));
  function update(){
    const choice=choices[index()],percent=Number(slider.value),position=`calc(${percent}% + ${14-28*percent/100}px)`;
    thumb.style.left=position;canvas.style.clipPath=`inset(0 ${100-percent}% 0 0)`;fire.update(percent/100);
    label.textContent=choice[1];label.classList.toggle('maximum',index()===choices.length-1&&usable);slider.setAttribute('aria-valuetext',choice[1]);slider.setAttribute('aria-valuenow',slider.value);
    [...labels.children].forEach((label,i)=>label.classList.toggle('selected',i===index()));[...ticks.children].forEach((tick,i)=>tick.classList.toggle('selected',i===index()));
    description.textContent=usable?(choice[2]||''): '当前模型仅支持模型默认。';reset.disabled=!usable;
  }
  async function write(){
    if(saving)return;saving=true;
    try{while(pending!==null&&!closed&&isCurrent()){const effort=pending;pending=null;inFlight=effort;status.textContent='保存中…';
      try{await save({model:conf.model,effort,project_id:projectId});committed=effort;if(!closed&&isCurrent())status.textContent='已保存';}
      catch(error){if(!closed){status.textContent='保存失败，重新拖动可重试';toast(error.message);}}finally{inFlight=null;}
    }}finally{saving=false;}
  }
  function commit(){
    if(!usable)return;slider.value=index()/(choices.length-1)*100;track.classList.remove('dragging');update();const effort=choices[index()][0];
    if(effort===inFlight){pending=null;}else if(effort!==committed||saving){pending=effort;write();}
  }
  slider.addEventListener('input',update);slider.addEventListener('pointerdown',()=>track.classList.add('dragging'));slider.addEventListener('change',commit);slider.addEventListener('pointerup',commit);
  slider.addEventListener('keyup',event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(event.key))commit();});
  slider.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();const direction=['ArrowRight','ArrowUp'].includes(event.key)?1:-1;slider.value=Math.max(0,Math.min(choices.length-1,index()+direction))/(choices.length-1)*100;update();}});
  update();slider.focus();return popup;
}
