import {mountOnboarding} from './react-bits-islands.js';

export function showOnboarding({snapshot,getSnapshot,command,chooseProject,onFinished=()=>{},nativePicker=false}){
  const dialog=document.createElement('dialog');dialog.className='onboarding-dialog';dialog.id='onboarding-dialog';dialog.setAttribute('aria-label','开始使用 Ignovate');
  const host=document.createElement('div');dialog.append(host);document.body.append(dialog);
  const origin=document.activeElement;let controller,closed=false,closeRequest=0;
  function close(draft=''){
    if(closed)return;closed=true;dialog.close();controller?.destroy();dialog.remove();
    if(origin?.isConnected)origin.focus();onFinished(draft);
  }
  controller=mountOnboarding(host,{snapshot,nativePicker,
    onChooseProject:async path=>{const selected=await chooseProject(path);const current=getSnapshot();controller.update({snapshot:current});return selected===true&&!current.onboarding?.workspace_placeholder;},
    onConfigure:async values=>{await command('configuration',values);controller.update({snapshot:getSnapshot()});},
    onComplete:async draft=>{await command('onboarding',{action:'complete'});close(draft);},
    onSkip:async()=>{await command('onboarding',{action:'skip'});close();}
  });
  dialog.addEventListener('cancel',event=>{event.preventDefault();controller.update({requestClose:++closeRequest});});
  dialog.refresh=value=>{if(!closed)controller.update({snapshot:value});};
  dialog.showModal();return dialog;
}
