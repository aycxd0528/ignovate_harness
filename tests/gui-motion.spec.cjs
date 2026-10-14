const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'dark'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let sequence=0,events=[],answer='首段回答',liveTools=[{call_id:'one',name:'read_file',path:'README.md',status:'running',output_preview:'文件内容'}],activity='thinking',committed=false;
 const node={id:'n1',branch_id:'b',parent_id:null,prompt:'已完成任务',answer:'# 现有回答\n\n这段内容应保持可选择。\n\n'+('历史说明。'.repeat(200)),status:'completed',recoverable:true,tools:[],usage:{input_tokens:1,output_tokens:1,cost_usd:0},timing:{elapsed_ms:1000}};
 const snapshot=()=>({sequence,project:{id:'p',name:'动态测试',path:'/tmp/motion-test'},configuration:{configured:true,model:'fake',reasoning_effort:'default',reasoning_options:[['default','默认']]},branches:[{id:'b',name:'运行中的任务',conversation_id:'c',node_ids:committed?['n1','n2']:['n1'],head:committed?'n2':'n1',draft:'',recoverable:true,is_default:true,permission_mode:'default'}],nodes:committed?[node,{...node,id:'n2',parent_id:'n1',prompt:'执行任务',answer,tools:liveTools}]:[node],runs:[{id:'r',request_id:'r',branch_id:'b',node_id:committed?'n2':null,message:'执行任务',status:committed?'completed':'running',activity,elapsed_ms:1000,tools:liveTools}],approvals:[],paused:false,layouts:{c:{revision:0,positions:{},collapsed:[],viewport:{x:40,y:40,zoom:1}}}});
 await page.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname;const reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});if(path==='/api/snapshot')return reply(snapshot());if(path==='/api/events'){const batch=events;events=[];return reply({sequence,events:batch,reset:false});}return reply([]);});
 try{
  await page.goto('http://127.0.0.1:8767/#token=browser-test-only');await page.locator('.live .tool-group').waitFor({state:'attached'});
  await page.locator('.live .tool-group>summary').click();await page.locator('.live .tool-activity>summary').click();
  await page.locator('.chat-search').focus();
  await page.evaluate(()=>{window.motionRefs={history:document.querySelector('.chat-history'),turn:document.querySelector('[data-node-id="n1"]'),live:document.querySelector('[data-run-id="r"]'),tool:document.querySelector('.live .tool-activity'),answer:document.querySelector('.live .answer'),search:document.querySelector('.chat-search')};});
  activity='tools';liveTools=[{...liveTools[0],elapsed_ms:250}];sequence++;events.push({sequence,kind:'status',run_id:'r',branch_id:'b',data:{status:'tools'}});
  await page.waitForFunction(()=>document.querySelector('.live .assistant-status').textContent.includes('调用工具'));
  const stable=await page.evaluate(()=>Object.fromEntries(Object.entries(window.motionRefs).map(([key,node])=>[key,node.isConnected])));
  assert.ok(Object.values(stable).every(Boolean),'status refresh must retain history/live/tool/answer/search DOM: '+JSON.stringify(stable));
  assert.equal(await page.evaluate(()=>document.activeElement===window.motionRefs.search),true,'chat search focus survives tool events');
  assert.equal(await page.locator('.live .tool-group').evaluate(n=>n.open),true);assert.equal(await page.locator('.live .tool-activity').evaluate(n=>n.open),true);
  await page.evaluate(()=>{const text=document.querySelector('[data-node-id="n1"] .answer p').firstChild;const range=document.createRange();range.setStart(text,0);range.setEnd(text,5);getSelection().removeAllRanges();getSelection().addRange(range);});
  const selection=await page.evaluate(()=>getSelection().toString());
  for(let i=0;i<30;i++){answer+=' 字';sequence++;events.push({sequence,kind:'token',run_id:'r',data:{text:i===0?'首段回答 字':' 字'}});}
  await page.locator('.live .answer').filter({hasText:answer}).waitFor();
  assert.equal(await page.evaluate(()=>window.motionRefs.answer===document.querySelector('.live .answer')),true,'stream updates retain answer container');
  assert.equal(await page.evaluate(()=>getSelection().toString()),selection,'old answer selection survives streaming');
  liveTools=[{...liveTools[0],status:'completed',elapsed_ms:300},{call_id:'two',name:'run_command',status:'running',preview:{command:'python check.py'}}];sequence++;events.push({sequence,kind:'tool_start',run_id:'r',data:liveTools[1]});
  await page.locator('.active-tool').filter({hasText:'python check.py'}).waitFor();
  assert.equal(await page.evaluate(()=>window.motionRefs.tool.isConnected),true,'adding a tool retains the previous record');
  assert.equal(await page.locator('.live .tool-activity').count(),2);
  liveTools.push({call_id:'three',name:'write_file',path:'report.md',status:'completed',diff:'--- /dev/null\n+++ report.md\n@@ -0,0 +1 @@\n+新增内容',additions:1,deletions:0});sequence++;events.push({sequence,kind:'tool_result',run_id:'r',data:liveTools[2]});
  const file=page.locator('.live .tool-activity[data-tool-id="three"]');await file.locator('summary').click();await file.locator('.diff-row.added').waitFor();await file.locator('summary').click();
  liveTools=liveTools.map(tool=>tool.call_id==='three'?{...tool,elapsed_ms:500}:tool);activity='thinking';sequence++;events.push({sequence,kind:'tool_result',run_id:'r',data:liveTools[2]});await page.locator('.live .runtime-label').filter({hasText:'思考中'}).waitFor();
  await file.locator('summary').click();await file.locator('.diff-row.added').waitFor({timeout:2000});
  await page.screenshot({path:'/tmp/ignovate-runtime-motion.png'});
  await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.locator('.map-run-card').waitFor({state:'attached',timeout:2000}).catch(async e=>{throw Error(e.message+' Toast: '+await page.locator('#toast').textContent()+' Errors: '+JSON.stringify(errors));});
  await page.evaluate(()=>window.mapMotionRefs={map:document.querySelector('.conversation-map'),card:document.querySelector('.map-run-card'),excerpt:document.querySelector('.map-run-card .map-excerpt')});
  activity='thinking';sequence++;events.push({sequence,kind:'status',run_id:'r',data:{status:'thinking'}});
  await page.waitForFunction(async()=>{const response=await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}});return (await response.json()).sequence>=33;});await page.waitForTimeout(250);
  assert.equal(await page.evaluate(()=>Object.values(window.mapMotionRefs).every(n=>n.isConnected)),true,'map running card also survives nonstructural status updates');
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.locator('.live').waitFor();await page.evaluate(()=>window.finalMotionRefs={turn:document.querySelector('.live'),answer:document.querySelector('.live .answer')});
  committed=true;liveTools=liveTools.map(t=>({...t,status:'completed'}));sequence++;events.push({sequence,kind:'final',run_id:'r',data:{text:answer}});
  await page.locator('[data-node-id="n2"]').waitFor();assert.equal(await page.evaluate(()=>window.finalMotionRefs.turn===document.querySelector('[data-node-id="n2"]')&&window.finalMotionRefs.answer.isConnected),true,'native commit retains the live answer and turn');
  await page.screenshot({path:'/tmp/ignovate-motion-live.png'});assert.deepEqual(errors,[]);
  console.log('Status/tool/token updates preserve DOM, focus, selection and expanded tool history.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
