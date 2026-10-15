const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.GUI_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/control-map-fixture',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><html data-theme="light"><head><link rel="stylesheet" href="/style.css"></head><body><div id="fixture" style="width:100vw;height:100vh"></div><div id="toast"></div></body></html>'}));
 try{
  await page.goto('http://127.0.0.1:8767/control-map-fixture');
  await page.evaluate(async()=>{
   const {createMap}=await import('/map.js');
   const base={status:'completed',recoverable:true,tools:[],answer:'可见正文可以引用。\n\n**明确来源**，保留真实结果。'};
   const nodes=[{...base,id:'a',branch_id:'main',parent_id:null,prompt:'共享起点：一段足够长的问题标题应该保留两行以帮助阅读原来的真实问题'},
     {...base,id:'b',branch_id:'main',parent_id:'a',prompt:'主线后续',answer:base.answer+'\n\n'+('长正文，'.repeat(2000))+'\n\n正文末尾标记'},
     {...base,id:'c',branch_id:'fork',parent_id:'a',prompt:'独立分支',task_summary:{task_id:'task-c',revision:3,objective:'修复实际问题',lifecycle:'waiting',wait_condition:{reason:'等待 CI',condition:'CI 通过'}}}];
   const branches=[{id:'main',name:'主线',thread_id:'tm',node_ids:['a','b'],head:'b'},{id:'fork',name:'探索',thread_id:'tf',node_ids:['a','c'],head:'c'}];
   const runs=[{id:'live',request_id:'accepted-request',branch_id:'fork',map_origin:{node_id:'c'},message:'正在执行的原生问题',status:'running',tools:[]}];
   const control={items:[{task_id:'task-c',objective:'修复实际问题',revision:3,thread_id:'tf',lifecycle:'waiting',wait_condition:{reason:'等待 CI',condition:'CI 通过'}}],
     attention:[{id:'wait-c',kind:'wait',title:'等待 CI',reason:'CI 尚未返回',node_id:'c',branch_id:'fork',task_id:'task-c',expected_revision:3,action:'clear_wait'}],
     receipts:[{run_id:'live',thread_id:'tf',stages:[{stage:'started',recorded_at:1,details:{}}],recovery:{status:'unknown',reason:'尚未确认关闭'}}],
     feedback:[{id:'f1',task_id:'task-c',task_revision:3,source_ref:{node_id:'c',branch_id:'fork'},context:'这次修复',outcome:'验证成功',behavior:'先核对输入',boundary:'相同输入',state:'candidate',revision:2}]};
   window.fixture={nodes,branches,runs,control,events:[],saves:[],viewChanges:[],draft:{kind:'fork',nodeId:'c',branchId:'fork',text:'保留未发送问题',permission_mode:'default'}};
   const f=window.fixture;
   f.map=createMap({nodes,branches,runs,control,selected:'c',activeBranch:'main',layout:{positions:{a:{x:0,y:0},b:{x:360,y:0},c:{x:360,y:260}},viewport:{x:40,y:100,zoom:1}},draft:f.draft,
     onViewChange:view=>f.viewChanges.push(view),onControl:value=>f.events.push(value),onSelect:node=>f.events.push({select:node.id}),onChat:node=>f.events.push({chat:node.id}),onFork:()=>{},onSave:value=>f.saves.push(value),onDraftChange:value=>f.draft=value,onSend:()=>{}});
   document.querySelector('#fixture').append(f.map);
  });
  const toolbar=page.locator('.map-toolbar');
  await toolbar.getByRole('button',{name:'概览阅读',exact:true}).waitFor();
  assert.equal(await page.locator('.map-card').first().evaluate(c=>c.getBoundingClientRect().height),200,'overview increases map density');
  assert.equal(await toolbar.getByRole('button',{name:'概览阅读',exact:true}).getAttribute('aria-pressed'),'true');
  assert.match(await page.locator('.map-summary').textContent(),/3 个回合.*2 条分支.*1 运行中.*1 关注/);
  assert.equal(await page.locator('.map-links path[marker-end]').count(),4,'committed, pending and unsent relations all point forward');
  const original=await page.locator('.map-card[data-node-id="c"]').evaluate(c=>({x:c.style.left,y:c.style.top}));
  await toolbar.getByRole('button',{name:'当前路径',exact:true}).click();
  assert.equal(await page.locator('.map-card').count(),2);
  assert.match(await page.locator('.map-hidden-note').textContent(),/选中回合.*草稿.*关注/);
  assert.equal(await page.locator('.map-draft').count(),0);
  assert.equal(await page.evaluate(()=>fixture.draft.text),'保留未发送问题');
  await toolbar.getByRole('button',{name:'全部分支',exact:true}).click();
  assert.deepEqual(await page.locator('.map-card[data-node-id="c"]').evaluate(c=>({x:c.style.left,y:c.style.top})),original);
  await toolbar.getByRole('button',{name:'展开阅读',exact:true}).click();
  assert.equal(await page.locator('.map-card').first().evaluate(c=>c.getBoundingClientRect().height),280);
  assert.match(await page.locator('.map-card[data-node-id="b"] .map-excerpt').textContent(),/正文末尾标记/);
  assert.equal(await page.locator('.map-card[data-node-id="b"] .map-excerpt strong').textContent(),'明确来源');
  assert.deepEqual(await page.locator('.map-card[data-node-id="c"]').evaluate(c=>({x:c.style.left,y:c.style.top})),original);
  await toolbar.getByRole('button',{name:'概览阅读',exact:true}).click();
  await page.evaluate(()=>{const p=document.querySelector('.map-card[data-node-id="a"] .map-excerpt p');const r=document.createRange();r.selectNodeContents(p);const s=getSelection();s.removeAllRanges();s.addRange(r);p.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));});
  // A running request correctly keeps quoting disabled; after native stop it is available again.
  assert.equal(await page.getByRole('button',{name:'引用追问',exact:true}).count(),0);
  await page.evaluate(()=>{fixture.runs[0].status='stopped';fixture.map.updateRunCards(fixture.runs);});
  assert.match(await page.locator('.map-summary').textContent(),/0 运行中/);
  assert.match(await page.locator('.map-run-card .map-excerpt').textContent(),/已停止.*尚无已保存回答/,'a stopped run cannot still claim to be thinking');
  await page.evaluate(()=>{const p=document.querySelector('.map-card[data-node-id="a"] .map-excerpt p');const r=document.createRange();r.selectNodeContents(p);const s=getSelection();s.removeAllRanges();s.addRange(r);p.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));});
  await page.getByRole('button',{name:'引用追问',exact:true}).click();
  assert.match(await page.getByLabel('地图追问').inputValue(),/> 可见正文可以引用/);
  await page.getByRole('button',{name:'取消追问',exact:true}).click();
  await page.locator('.map-card[data-node-id="a"]').getByRole('button',{name:'折叠',exact:true}).click();
  await page.getByRole('button',{name:'关注事项',exact:true}).click();
  await page.locator('.map-control-panel').getByRole('button',{name:'定位来源',exact:true}).click();
  assert.equal(await page.locator('.map-card[data-node-id="c"]').count(),1,'source location expands collapsed ancestors');
  assert.equal(await page.locator('.map-card[data-node-id="c"]').getAttribute('class').then(c=>c.includes('selected')),true);
  assert.equal(await toolbar.getByRole('button',{name:'全部分支',exact:true}).getAttribute('aria-pressed'),'true');
  await page.locator('.map-control-panel').getByRole('button',{name:'条件已满足',exact:true}).click();
  assert.equal(await page.evaluate(()=>fixture.events.at(-1).operation),'clear_wait');
  assert.equal(await page.evaluate(()=>fixture.events.at(-1).expected_revision),3);
  await page.evaluate(()=>{fixture.control.attention.push({id:'manual',kind:'manual',title:'等待人工验收',reason:'检查实际输出',branch_id:'fork',task_id:'task-c',acceptance_id:'manual-output',expected_revision:3,action:'confirm_acceptance'});fixture.map.updateRunCards(fixture.runs,fixture.control);});
  await page.locator('.map-control-panel').getByRole('button',{name:'人工确认',exact:true}).click();
  assert.equal(await page.evaluate(()=>fixture.events.at(-1).acceptance_id),'manual-output');
  await page.evaluate(()=>{fixture.control.attention.pop();fixture.control.items[0].steps=[{id:'check',title:'核对来源',state:'done',dependencies:[]},{id:'finish',title:'完成修改',state:'todo',dependencies:['check']}];fixture.control.feedback[0].source_ref='session:tf#node:c';fixture.map.updateRunCards(fixture.runs,fixture.control);});
  await page.getByRole('button',{name:'反馈经验',exact:true}).click();
  await page.locator('.map-control-panel').getByRole('button',{name:'确认经验',exact:true}).click();
  assert.equal(await page.evaluate(()=>fixture.events.at(-1).operation),'feedback_confirm');
  assert.equal(await page.evaluate(()=>fixture.events.at(-1).id),'f1');
  await page.getByRole('button',{name:'项目任务',exact:true}).click();
  assert.match(await page.locator('.map-control-panel').textContent(),/修复实际问题.*等待 CI/);
  assert.equal(await page.locator('.map-task-dependencies path').count(),1);
  assert.match(await page.locator('.map-task-dependencies').textContent(),/核对来源.*完成修改/);
  await page.getByRole('button',{name:'阶段回执',exact:true}).click();
  assert.match(await page.locator('.map-control-panel').textContent(),/开始.*尚未确认关闭/);
  await page.evaluate(()=>{fixture.runs[0].status='waiting_approval';fixture.map.updateRunCards(fixture.runs);});
  await page.getByRole('button',{name:'关注事项',exact:true}).click();
  assert.equal(await page.locator('.map-control-row').count(),2,'native approval joins the existing wait item');
  await page.evaluate(()=>{fixture.runs[0].status='stopped';fixture.map.updateRunCards(fixture.runs);});
  assert.equal(await page.locator('.map-control-row').count(),1,'resolved native approval leaves the open panel without a whole-map rerender');
  await page.getByRole('button',{name:'阶段回执',exact:true}).click();
  await page.screenshot({path:path.resolve('.superpowers/loopx-control-map/map-artifacts/control-map-light.png')});
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');
  await page.screenshot({path:path.resolve('.superpowers/loopx-control-map/map-artifacts/control-map-dark.png')});
  assert.deepEqual(errors,[]);
  console.log('Control map density, source navigation, explicit callbacks, filtering, full Markdown, quotes and dynamic counts passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
