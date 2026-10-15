import test from 'node:test';
import assert from 'node:assert/strict';
import * as map from '../nailong/gui/static/map.js';

const nodes=[
  {id:'a',parent_id:null,branch_id:'main',status:'completed'},
  {id:'b',parent_id:'a',branch_id:'main',status:'completed'},
  {id:'c',parent_id:'a',branch_id:'fork',status:'completed'},
  {id:'run_live',parent_id:'c',branch_id:'fork',provisional:true,run_id:'live',status:'running'},
];
const branches=[{id:'main',node_ids:['a','b'],head:'b'},{id:'fork',node_ids:['c'],head:'c'}];

test('current path keeps shared ancestors and only its native pending run',()=>{
  assert.equal(typeof map.activePathIds,'function','active path filtering is available');
  assert.deepEqual([...map.activePathIds(nodes,branches,'fork')],['c','a','run_live']);
  assert.deepEqual(map.scopedNodes(nodes,branches,'fork','current').map(n=>n.id),['a','c','run_live']);
  assert.deepEqual(map.scopedNodes(nodes,branches,'main','current').map(n=>n.id),['a','b']);
  assert.deepEqual(map.scopedNodes(nodes,branches,'unknown','current'),[]);
});

test('counted turns, live runs and attention deduplicate committed runs and sources',()=>{
  assert.equal(typeof map.mapStatistics,'function','native map statistics are available');
  const runs=[{id:'done',node_id:'b',status:'completed'},{id:'live',branch_id:'fork',status:'waiting_approval'},{id:'live',branch_id:'fork',status:'waiting_approval'}];
  const attention=[{id:'approval-live',run_id:'live',kind:'approval',status:'pending'},{id:'approval-live',run_id:'live',kind:'approval',status:'pending'},{id:'old',status:'resolved'},{id:'task-wait',task_id:'t',kind:'wait',status:'pending'}];
  assert.deepEqual(map.mapStatistics(nodes,branches,runs,{attention}),{turns:3,branches:2,running:1,attention:2,approvals:1});
  assert.equal(map.mapStatistics(nodes,branches,[{id:'live',status:'completed'}],{}).running,0);
  assert.equal(map.mapStatistics(nodes,branches,[{id:'live',status:'waiting_approval'}],{}).attention,1);
});

test('compact geometry drives placement, directed endpoints and draft occupancy together',()=>{
  assert.equal(typeof map.mapGeometry,'function','shared geometry is available');
  const compact=map.mapGeometry('overview'),expanded=map.mapGeometry('expanded');
  assert.ok(compact.height<expanded.height);
  const placed=map.layoutNodes([{id:'a',parent_id:null},{id:'b',parent_id:'a'},{id:'c',parent_id:null}],{},compact);
  assert.equal(placed.b.x,compact.column);
  assert.equal(placed.c.y,compact.row);
  assert.deepEqual(map.mapDraftPosition({nodeId:'a'},placed,compact),{x:compact.column,y:compact.row});
  assert.match(map.mapConnectorPath({x:10,y:20},{x:700,y:100},compact),new RegExp(`^M${10+compact.width},${20+compact.height/2} `));
  assert.match(map.mapConnectorPath({x:10,y:20},{x:700,y:100},compact),new RegExp(`700,${100+compact.height/2}$`));
});

test('filter and density changes never replace saved positions or drop hidden drafts',()=>{
  assert.equal(typeof map.hiddenMapState,'function','hidden map state is available');
  const saved={a:{x:42,y:24},c:{x:811,y:244}},draft={kind:'fork',nodeId:'c',branchId:'fork',text:'Do not lose this',requestId:'accepted'};
  const placed=map.layoutNodes(nodes,saved,map.mapGeometry('overview'));
  assert.deepEqual(map.layoutNodes(nodes,placed,map.mapGeometry('expanded')),placed);
  assert.deepEqual(map.hiddenMapState(nodes,['a','b'],{selected:'c',draft,attention:[{id:'q',node_id:'c'},{id:'q',node_id:'c'}]}),{selection:true,draft:true,attention:1});
  assert.deepEqual(draft,{kind:'fork',nodeId:'c',branchId:'fork',text:'Do not lose this',requestId:'accepted'});
});

test('source resolution uses actual node or branch identities without inventing a task edge',()=>{
  assert.equal(typeof map.controlSource,'function','source localization is available');
  assert.deepEqual(map.controlSource({source:{node_id:'c',branch_id:'fork'}},nodes,branches),{nodeId:'c',branchId:'fork'});
  assert.deepEqual(map.controlSource({thread_id:'thread-main'},nodes,[{...branches[0],thread_id:'thread-main'}]),{nodeId:'b',branchId:'main'});
  assert.deepEqual(map.controlSource({task_id:'missing'},nodes,branches),{nodeId:null,branchId:null});
  assert.deepEqual(map.controlSource({source_ref:'session:tf#node:c'},nodes,[{...branches[1],thread_id:'tf'}]),{nodeId:'c',branchId:'fork'});
  assert.deepEqual(map.controlSource({branch_id:'outside',node_id:'saved-source'},[],branches),{nodeId:'saved-source',branchId:'outside'});
  assert.deepEqual(map.controlSource({branch_id:'outside',associated_thread_ids:['tm']},[],[{...branches[0],thread_id:'tm'}]),{nodeId:null,branchId:'outside'});
  assert.deepEqual(map.controlSource({task_id:'same-task',branch_id:'outside'},[{...nodes[1],task_summary:{task_id:'same-task'}}],branches),{nodeId:null,branchId:'outside'});
});

test('task relations come only from registered step dependencies',()=>{
  const graph=map.taskDependencies([{id:'inspect',title:'调查',state:'done',dependencies:[]},
    {id:'fix',title:'修复',state:'doing',dependencies:['inspect']},
    {id:'verify',title:'验证',state:'todo',dependencies:['fix']}]);
  assert.deepEqual(graph.edges,[{from:'inspect',to:'fix'},{from:'fix',to:'verify'}]);
  assert.deepEqual(graph.nodes.map(n=>n.depth),[0,1,2]);
  assert.throws(()=>map.taskDependencies([{id:'x',dependencies:['y']},{id:'y',dependencies:['x']}]),/依赖/);
  assert.throws(()=>map.taskDependencies([{id:'x',dependencies:['missing']}]),/依赖/);
});
