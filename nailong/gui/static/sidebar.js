export function conversationGroups(branches){
  const groups=new Map(),byId=new Map(branches.map(branch=>[branch.id,branch]));
  for(const branch of branches){
    if(!groups.has(branch.conversation_id))groups.set(branch.conversation_id,[]);
    groups.get(branch.conversation_id).push(branch);
  }
  return [...groups].map(([id,items])=>{
    const depth=branch=>{let value=0,parent=branch.parent_id;const seen=new Set();while(parent&&!seen.has(parent)){seen.add(parent);value++;parent=byId.get(parent)?.parent_id;}return value;};
    return {id,branches:items.map(branch=>({...branch,depth:depth(branch)})).sort((a,b)=>a.depth-b.depth)};
  });
}

export function relativeTime(value,now=Date.now()){
  if(!value||!Number.isFinite(Date.parse(value)))return '';
  const seconds=Math.max(0,(now-Date.parse(value))/1000);
  return seconds<60?'刚刚':seconds<3600?Math.floor(seconds/60)+' 分钟':seconds<86400?Math.floor(seconds/3600)+' 小时':Math.floor(seconds/86400)+' 天';
}
export function visibleBranches(branches,{archive='hidden',sort='updated',order=[]}={}){
  const positions=new Map(order.map((id,i)=>[id,i]));
  return branches.filter(b=>archive==='all'||Boolean(b.archived)===(archive==='only')).sort((a,b)=>
    sort==='manual'?(positions.get(a.id)??order.length)-(positions.get(b.id)??order.length):Date.parse(b.updated_at||b.created||0)-Date.parse(a.updated_at||a.created||0));
}
