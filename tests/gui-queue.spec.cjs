const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let sequence=0,changed=false,releaseSave,paused=false,runtime={restart_required:false};
 const branches=[{id:'main',name:'队列回归',conversation_id:'c',node_ids:[],head:null,draft:'',is_default:true,permission_mode:'default'},
 {id:'other',name:'其他会话',conversation_id:'other',node_ids:[],head:null,draft:'',permission_mode:'default'}];
 const runs=[{id:'active',branch_id:'main',message:'正在处理',status:'running',elapsed_ms:1000,tools:[]},
 {id:'q1',branch_id:'main',message:'原始问题',status:'queued'},
 {id:'q2',branch_id:'main',message:'稍后处理',status:'queued'},
 {id:'q-other',branch_id:'other',message:'其他会话的问题',status:'queued'}];
 const snapshot=()=>({sequence,project:{id:'queue',name:'队列测试',path:'/tmp/queue'},configuration:{configured:true,model:'offline',reasoning_options:[['default','默认']]},branches,nodes:[],runs,approvals:[],paused,runtime,layouts:{c:{revision:0,positions:{}}}});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname,reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  if(path==='/api/snapshot')return reply(snapshot());
  if(path==='/api/events'){const reset=changed;changed=false;return reply({sequence,events:[],reset});}
  const {name,payload}=route.request().postDataJSON();
  if(name==='workspaces')return reply([]);
  if(name==='queue_edit'){
   await new Promise(resolve=>{releaseSave=resolve;});
   const run=runs.find(r=>r.id===payload.run_id);
   assert.equal(run.branch_id,payload.branch_id);assert.equal(payload.expected_message,run.message);
   run.message=payload.message;sequence++;changed=true;return reply(run);
  }
  if(name==='queue_remove'){runs.find(r=>r.id===payload.run_id).status='stopped';sequence++;changed=true;}
  return reply({});
 });
 try{
  await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8767')+'/#token=browser-test-only');
  const panel=page.getByRole('region',{name:'等待发送的消息'});
  await panel.waitFor({timeout:6000});
  assert.equal(await panel.locator('.queued-message').count(),2);
  assert.equal(await panel.getByText('其他会话的问题',{exact:true}).count(),0);
  const panelBox=await panel.boundingBox(),composerBox=await page.locator('#composer').boundingBox();
  assert.ok(panelBox.y+panelBox.height<=composerBox.y,'queue is directly above composer');
  await page.locator('#composer').fill('未发送的新草稿');
  const first=panel.locator('[data-run-id="q1"]');
  await first.getByRole('button',{name:'编辑排队消息',exact:true}).click();
  const editor=first.getByRole('textbox',{name:'编辑等待发送的问题'});
  await editor.fill('改过的问题');
  // An unrelated snapshot must preserve typing and keyboard focus.
  sequence++;changed=true;await page.waitForResponse(r=>r.url().includes('/api/snapshot'));
  assert.equal(await editor.inputValue(),'改过的问题');assert.equal(await editor.evaluate(e=>e===document.activeElement),true);
  await first.getByRole('button',{name:'保存修改',exact:true}).click();
  while(!releaseSave)await new Promise(resolve=>setTimeout(resolve,10));
  await editor.press('Escape');
  assert.equal(await editor.isVisible(),true,'pending save must not allow a replacement editor whose text would be lost');
  releaseSave();
  await first.getByText('改过的问题',{exact:true}).waitFor();
  assert.equal(await page.locator('#composer').inputValue(),'未发送的新草稿');
  await first.getByRole('button',{name:'编辑排队消息',exact:true}).click();
  await editor.fill('不保存的修改');await first.getByRole('button',{name:'取消编辑',exact:true}).click();
  assert.equal(runs[1].message,'改过的问题');
  await panel.locator('[data-run-id="q2"]').getByRole('button',{name:'删除排队消息',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.queued-message').length===1);
  assert.equal(runs[0].status,'running');
  runs[1].status='running';sequence++;changed=true;await panel.waitFor({state:'hidden'});
  assert.equal(await page.locator('#composer').inputValue(),'未发送的新草稿');
  // A saved runtime may continue even when old nodes cannot be forked exactly.
  runs.forEach(run=>run.status='stopped');Object.assign(branches[0],{legacy:true,recoverable:false,can_continue:true});sequence++;changed=true;
  await page.locator('#send').waitFor();assert.equal(await page.locator('#send').isEnabled(),true);
  Object.assign(branches[0],{can_continue:false,continuation_reason:'checkpoint 与末轮记录不一致'});sequence++;changed=true;
  await page.waitForFunction(()=>document.querySelector('#send').disabled);
  await page.getByText('checkpoint 与末轮记录不一致',{exact:true}).waitFor();
  Object.assign(runs[2],{status:'failed',error:'TimeoutError: 操作等待超时',started_at:'2026-10-16T11:00:00Z',elapsed_ms:9000});sequence++;changed=true;
  await page.evaluate(()=>{window.ignovateDesktop={writeClipboardText:async text=>{window.copiedFailure=text;}};});
  await page.getByRole('button',{name:'复制错误详情',exact:true}).click({timeout:4000});
  await page.waitForFunction(()=>window.copiedFailure?.includes('TimeoutError'));
  assert.match(await page.evaluate(()=>window.copiedFailure),/q2[\s\S]*2026-10-16T11:00:00Z[\s\S]*9000/);
  // An empty pause latch is ready; paused pending messages are not executing.
  paused=true;Object.assign(branches[0],{can_continue:true,continuation_reason:''});sequence++;changed=true;
  await page.waitForResponse(r=>r.url().includes('/api/snapshot'));
  assert.equal(await page.getByRole('button',{name:'显式恢复队列',exact:true}).count(),0,'no resume action for an empty queue');
  runs[1].status='queued';sequence++;changed=true;
  await page.waitForFunction(()=>document.querySelector('.queued-message'));
  assert.equal(await page.locator('.run-status').innerText(),'等待恢复');
  assert.equal(await page.locator('.run-status').getAttribute('data-state'),'paused');
  assert.ok((await page.locator('.session[data-branch-id="main"] .run-indicator').innerText()).includes('等待恢复'));
  assert.equal(await page.locator('#stop').count(),0,'paused waiting messages must not turn Send into Stop');
  assert.equal(await page.locator('#send').isEnabled(),true);
  runtime={restart_required:true,notice:'应用代码已更新，请重新启动 Ignovate 后继续；会话和草稿会保留。'};
  runs[1].status='stopped';sequence++;changed=true;
  await page.getByText(runtime.notice,{exact:true}).waitFor({timeout:4000});
  assert.equal(await page.locator('#send').isEnabled(),false);
  assert.equal(await page.locator('#composer').inputValue(),'未发送的新草稿');
  assert.deepEqual(errors,[]);
  console.log('Queue UI: scope, placement, edit/save/cancel, polling focus, draft isolation, remove and started transition passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
