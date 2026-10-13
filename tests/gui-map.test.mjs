import test from 'node:test';
import assert from 'node:assert/strict';
import { layoutNodes, visibleNodes, reconcileMapDraft } from '../nailong/gui/static/map.js';

test('independent conversations stay disconnected and hand positions survive additions', () => {
  const nodes = [{ id: 'a', parent_id: null }, { id: 'b', parent_id: 'a' }, { id: 'c', parent_id: null }];
  const placed = layoutNodes(nodes, { b: { x: 911, y: 333 } });
  assert.deepEqual(placed.b, { x: 911, y: 333 });
  assert.notDeepEqual(placed.a, placed.c);
  assert.equal(nodes[2].parent_id, null);
});

test('collapse hides descendants while keeping unrelated conversations', () => {
  const nodes = [{ id: 'a', parent_id: null }, { id: 'b', parent_id: 'a' }, { id: 'c', parent_id: 'b' }, { id: 'd', parent_id: null }];
  assert.deepEqual(visibleNodes(nodes, ['a']).map(n => n.id), ['a', 'd']);
  assert.deepEqual(visibleNodes(nodes, []).map(n => n.id), ['a', 'b', 'c', 'd']);
});

test('a chat turn retires an empty root placeholder while an accepted map request keeps its identity',()=>{
  const draft={kind:'root',branchId:'b',text:'',requestId:null},run={branch_id:'b',request_id:'native-request',status:'running'};
  assert.equal(reconcileMapDraft(draft,[],[run],[{id:'b',head:null}]),null);
  const submitted={...draft,text:'地图提问',requestId:'native-request'};
  assert.equal(reconcileMapDraft(submitted,[],[run],[{id:'b',head:null}]),submitted);
});

test('unsent root text follows its own branch after chat starts; fork drafts retain their boundary',()=>{
  const draft={kind:'root',branchId:'b',text:'保留的问题',permission_mode:'acceptEdits'},nodes=[{id:'root',status:'completed',recoverable:true},{id:'other'}],branches=[{id:'b',head:'root'},{id:'other-b',head:'other'}];
  const resolved=reconcileMapDraft(draft,nodes,[],branches);
  assert.equal(resolved.text,draft.text);assert.equal(resolved.permission_mode,'acceptEdits');assert.equal(resolved.kind,'continue');assert.equal(resolved.nodeId,'root');
  assert.equal(reconcileMapDraft(draft,[],[{status:'running'}],[{id:'b',head:null}]),draft);
  const fork={...draft,kind:'fork',nodeId:'root'};assert.equal(reconcileMapDraft(fork,nodes,[],branches),fork);
});

test('retained root text offers a new conversation when the first chat boundary cannot be recovered',()=>{
  const draft={kind:'root',branchId:'b',text:'不要丢掉我的问题',permission_mode:'default'};
  for(const [status,recoverable] of [['failed',false],['stopped',true],['interrupted',false],['completed',false]]){
    const result=reconcileMapDraft(draft,[{id:'n',status,recoverable}],[],[{id:'b',head:'n'}]);
    assert.equal(result.kind,'root');assert.equal(result.nodeId,undefined);assert.equal(result.text,draft.text);assert.equal(result.blocked,true);
  }
});
