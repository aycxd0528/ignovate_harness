const activeStatuses=new Set(['queued','running','waiting_approval','stopping']);
const closedAttention=new Set(['resolved','dismissed','closed','revoked','expired']);

export function mapGeometry(density='expanded'){
  return {width:310,height:density==='overview'?200:280,column:360,row:density==='overview'?240:320,draftHeight:280};
}

export function activePathIds(nodes,branches,activeBranch){
  const branch=branches.find(row=>row.id===activeBranch),byId=new Map(nodes.map(row=>[row.id,row])),ids=new Set();
  if(!branch)return ids;
  for(const id of branch.node_ids||[]){let next=id;const seen=new Set();while(next&&!seen.has(next)){seen.add(next);if(byId.has(next))ids.add(next);next=byId.get(next)?.parent_id;}}
  for(const node of nodes)if(node.provisional&&node.branch_id===activeBranch){ids.add(node.id);let parent=node.parent_id;const seen=new Set();while(parent&&!seen.has(parent)){seen.add(parent);if(byId.has(parent))ids.add(parent);parent=byId.get(parent)?.parent_id;}}
  return ids;
}

export function scopedNodes(nodes,branches,activeBranch,scope='all'){
  if(scope!=='current')return nodes;
  const ids=activePathIds(nodes,branches,activeBranch);return nodes.filter(node=>ids.has(node.id));
}

export function uniqueControlRows(rows=[],category='attention'){
  const seen=new Set();return rows.filter(row=>{
    if(category==='attention'&&(row.resolved===true||closedAttention.has(row.status||row.state)))return false;
    const key=category==='items'?row.task_id||row.id:category==='receipts'?row.run_id||row.id:row.id||`${row.kind||row.type||''}:${row.run_id||row.task_id||''}:${row.node_id||row.branch_id||''}:${row.reason||row.title||''}`;
    if(seen.has(key))return false;seen.add(key);return true;
  });
}

export function mapAttention(runs=[],control={}){
  const rows=uniqueControlRows(control.attention||[]),seen=new Set(rows.filter(row=>['approval','waiting_approval','tool_approval'].includes(row.kind||row.type)).map(row=>row.run_id));
  for(const run of runs)if(run.status==='waiting_approval'&&!seen.has(run.id)){
    rows.push({id:'approval:'+run.id,kind:'approval',title:'等待工具批准',reason:run.activity_detail||'运行正在等待原生批准。',run_id:run.id,node_id:run.node_id||'run_'+run.id,branch_id:run.branch_id});seen.add(run.id);
  }
  return rows;
}

export function mapStatistics(nodes,branches,runs=[],control={}){
  const attention=mapAttention(runs,control),running=new Set(runs.filter(run=>!run.node_id&&activeStatuses.has(run.status)).map(run=>run.id));
  return {turns:new Set(nodes.filter(node=>!node.provisional).map(node=>node.id)).size,branches:new Set(branches.map(branch=>branch.id)).size,running:running.size,attention:attention.length,approvals:attention.filter(row=>['approval','waiting_approval','tool_approval'].includes(row.kind||row.type)).length};
}

export function mapDraftPosition(question,positions,geometry=mapGeometry()){
  if(!question?.nodeId){let y=0;while(Object.values(positions).some(point=>Math.abs(point.x)<geometry.width&&Math.abs(point.y-y)<Math.max(geometry.height,geometry.draftHeight)+20))y+=Math.max(geometry.row,geometry.draftHeight+40);return {x:0,y};}
  const from=positions[question.nodeId]||{x:0,y:0},x=from.x+geometry.column;let y=from.y;
  const occupied=Object.values(positions).filter(point=>Math.abs(point.x-x)<geometry.width);
  while(occupied.some(point=>Math.abs(point.y-y)<geometry.height+20))y+=geometry.row;
  return {x,y};
}

export function mapConnectorPath(from,to,geometry=mapGeometry(),toHeight=geometry.height){
  const x=from.x+geometry.width,y=from.y+geometry.height/2,endY=to.y+toHeight/2;
  return `M${x},${y} C${x+35},${y} ${to.x-35},${endY} ${to.x},${endY}`;
}

export function controlSource(item,nodes,branches){
  const source=typeof item.source_ref==='object'?item.source_ref:typeof item.source==='object'?item.source:{};
  const reference=typeof item.source_ref==='string'?/^session:([A-Za-z0-9_-]{1,80})#node:([A-Za-z0-9_-]{1,80})$/.exec(item.source_ref):null;
  const nodeId=item.node_id||source.node_id||reference?.[2]||(typeof item.source_ref==='string'&&nodes.some(node=>node.id===item.source_ref)?item.source_ref:null);
  const node=nodes.find(row=>row.id===nodeId)||nodes.find(row=>item.run_id&&row.run_id===item.run_id);
  const threadId=item.thread_id||source.thread_id||reference?.[1]||item.owner_thread_id;
  const branchId=item.branch_id||source.branch_id||node?.branch_id;
  const branch=branchId?branches.find(row=>row.id===branchId):branches.find(row=>threadId&&row.thread_id===threadId)||branches.find(row=>item.associated_thread_ids?.includes(row.thread_id));
  const taskNode=!node&&!branch&&!branchId&&item.task_id?nodes.findLast(row=>row.task_summary?.task_id===item.task_id):null;
  return {nodeId:node?.id||branch?.head||nodeId||taskNode?.id||null,branchId:branch?.id||item.branch_id||source.branch_id||taskNode?.branch_id||null};
}

export function taskDependencies(steps=[]){
  if(!Array.isArray(steps)||steps.length>200)throw new Error('任务依赖超出展示范围');
  const byId=new Map(steps.map(step=>[step.id,step])),depths=new Map(),visiting=new Set(),rows=new Map(),edges=[];
  if(byId.size!==steps.length)throw new Error('任务依赖身份重复');
  function depth(id){
    if(depths.has(id))return depths.get(id);
    if(visiting.has(id)||!byId.has(id))throw new Error('任务依赖无效');
    visiting.add(id);const dependencies=byId.get(id).dependencies||[];
    if(!Array.isArray(dependencies))throw new Error('任务依赖无效');
    const value=dependencies.length?Math.max(...dependencies.map(depth))+1:0;
    visiting.delete(id);depths.set(id,value);return value;
  }
  const nodes=steps.map(step=>{const d=depth(step.id),row=rows.get(d)||0;rows.set(d,row+1);
    for(const from of new Set(step.dependencies||[]))edges.push({from,to:step.id});
    return {...step,depth:d,row};});
  return {nodes,edges};
}

export function hiddenMapState(nodes,shownIds,{selected,draft,attention=[]}={}){
  const ids=new Set(shownIds),all=new Set(nodes.map(row=>row.id));
  return {selection:!!selected&&all.has(selected)&&!ids.has(selected),draft:!!draft&&(draft.nodeId?all.has(draft.nodeId)&&!ids.has(draft.nodeId):false),attention:uniqueControlRows(attention).filter(row=>row.node_id&&all.has(row.node_id)&&!ids.has(row.node_id)).length};
}
