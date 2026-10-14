export function effectiveUIPreferences(envelope={},storage=globalThis.localStorage){
  let theme='system';try{theme=storage?.getItem('ignovate-theme')||theme;}catch{}
  if(!['system','light','dark'].includes(theme))theme='system';
  const result={default_permission:'default',theme,font_size:15,tool_details:'compact',show_changes:true,busy_send:'queue',shortcuts:{new_chat:'Mod+N',commands:'Mod+K',find:'Mod+F'}};
  for(const key of envelope.saved_keys||[]){if(Object.hasOwn(result,key)&&Object.hasOwn(envelope.preferences||{},key))result[key]=envelope.preferences[key];}
  return result;
}
export function shortcutMatches(event,binding){
  if(event.isComposing||typeof binding!=='string')return false;
  const parts=binding.split('+');
  return parts[0]==='Mod'&&Boolean(event.metaKey||event.ctrlKey)&&
    Boolean(event.shiftKey)===parts.includes('Shift')&&Boolean(event.altKey)===parts.includes('Alt')&&
    event.key.toUpperCase()===parts.at(-1).toUpperCase();
}
