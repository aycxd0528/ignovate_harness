const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'light'}),errors=[],commands=[],reviewIssues=[];
 await page.addInitScript(()=>localStorage.setItem('ignovate-workspaces',JSON.stringify([{name:'其他工作区',path:'/tmp/bits-other'}])));
 page.setDefaultTimeout(7000);page.on('pageerror',e=>errors.push(e.message));
 let sequence=0,changed=false,failSend=false,delaySend=false,finishSend,onSendStart;
 const conf={configured:true,model:'deepseek-flash',model_name:'default',models:[{name:'default',model:'deepseek-flash'},{name:'备用',model:'custom-model'}],reasoning_effort:'default',reasoning_options:[['default','模型默认'],['none','关闭'],['low','低'],['high','高'],['max','最高']]};
 const branches=[{id:'main',name:'主线方案',conversation_id:'c',parent_id:null,node_ids:[],head:null,draft:'',is_default:true,recoverable:true,permission_mode:'default'},{id:'child',name:'界面探索',conversation_id:'c',parent_id:'main',node_ids:[],head:null,draft:'',recoverable:true,permission_mode:'default'}];
 let nodes=[],runs=[];
 const snapshot=()=>({sequence,project:{id:'fidelity',name:'React Bits 组件验收',path:'/tmp/bits-fidelity'},configuration:conf,branches,nodes,runs,approvals:[],paused:false,layouts:{c:{revision:0,positions:{},collapsed:[],viewport:{x:40,y:40,zoom:1}}}});
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname,reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  if(path==='/api/snapshot')return reply(snapshot());if(path==='/api/events'){const reset=changed;changed=false;return reply({sequence,events:[],reset});}
  const data=route.request().postDataJSON();commands.push(data);
  if(data.name==='reasoning'){conf.reasoning_effort=data.payload.effort;sequence++;return reply(conf);}
  if(data.name==='model'){conf.model_name=data.payload.name;conf.model=conf.models.find(m=>m.name===data.payload.name).model;conf.reasoning_effort='default';conf.reasoning_options=[['default','模型默认']];sequence++;return reply(conf);}
  if(data.name==='draft'){branches.find(b=>b.id===data.payload.branch_id).draft=data.payload.text;return reply({});}
  if(data.name==='send'&&failSend)return reply({error:'测试：消息未发送'},500);
  if(data.name==='send'&&delaySend)return new Promise(resolve=>{finishSend=()=>resolve(reply({}));onSendStart();});
  return reply(data.name==='workspaces'?[{id:'other-project',name:'其他工作区',path:'/tmp/bits-other',branches:[{...branches[0],id:'other',name:'其他会话',conversation_id:'other-conversation'}]}]:{});
 });
 try{
  await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8767')+'/#token=browser-test-only');await page.locator('#startup-screen').waitFor({state:'detached'});
  const selected=page.locator('.session[data-branch-id=main]'),other=page.locator('.session[data-branch-id=child]');
  assert.equal(await selected.evaluate(e=>getComputedStyle(e).color),'rgb(49, 92, 255)','selected branch text uses the accent blue');
  assert.notEqual(await other.evaluate(e=>getComputedStyle(e).color),'rgb(49, 92, 255)');
  assert.ok(await page.locator('.branched-menu__reach').count()>0,'official menu draws the selection path');
  await page.locator('.session[data-branch-id=other]').waitFor();
  if(!await page.locator('.workspace-group[data-workspace-path="/tmp/bits-other"] .branched-menu__reach').evaluateAll(paths=>paths.every(p=>Number.parseFloat(p.style.strokeDashoffset)>0)))reviewIssues.push('inactive workspace must not select a blue path');
  const switchMotion=await page.evaluate(async()=>{
   const row=document.querySelector('.session[data-branch-id=child]'),family=row.closest('.conversation-group');
   const [previous,next]=family.querySelectorAll('.branched-menu__reach'),menu=family.firstElementChild;
   const length=Number.parseFloat(next.style.strokeDasharray);
   row.focus();row.click();
   await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   window.bitsMenu=menu;window.bitsPath=next;
   return {retained:menu===document.querySelector('.session[data-branch-id=child]').closest('.branched-menu'),
    pathRetained:next.isConnected,focusRetained:document.activeElement===row,
    drawing:next.getAnimations().some(animation=>animation.transitionProperty==='stroke-dashoffset'),
    retracting:previous.getAnimations().some(animation=>animation.transitionProperty==='stroke-dashoffset'),
    offset:Number.parseFloat(getComputedStyle(next).strokeDashoffset),length};
  });
  assert.equal(switchMotion.retained,true,'branch selection must preserve the mounted BranchedMenu');
  assert.equal(switchMotion.pathRetained,true,'selection keeps the original SVG path');
  assert.equal(switchMotion.focusRetained,true,'selection keeps keyboard focus on the native row');
  assert.equal(switchMotion.drawing,true,'the selected path draws using the official CSS transition');
  assert.equal(switchMotion.retracting,true,'the previous selected path retracts');
  assert.ok(switchMotion.offset>0&&switchMotion.offset<switchMotion.length,'the selected path has an intermediate visible animation frame');
  await page.waitForFunction(()=>Number.parseFloat(getComputedStyle(window.bitsPath).strokeDashoffset)===0);
  await selected.focus();await selected.press('Enter');
  assert.equal(await selected.getAttribute('aria-current'),'page');
  assert.equal(await page.evaluate(()=>window.bitsMenu===document.querySelector('.session[data-branch-id=main]').closest('.branched-menu')),true,'keyboard navigation also preserves the menu');
  const rapidSwitch=await page.evaluate(async()=>{
   await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   document.querySelector('.session[data-branch-id=child]').click();
   await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
   return window.bitsPath.isConnected&&window.bitsPath.getAnimations().some(animation=>animation.transitionProperty==='stroke-dashoffset');
  });
  assert.equal(rapidSwitch,true,'rapidly reversing a selection continues the original path animation');
  await page.emulateMedia({reducedMotion:'reduce'});await selected.click();
  assert.equal(await page.locator('.branched-menu__reach').evaluateAll(paths=>paths.every(path=>path.getAnimations().length===0)),true,'reduced motion skips path transitions');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.locator('.prompt-bar__field').waitFor();
  await page.getByRole('button',{name:'推理等级',exact:true}).click();
  const slider=page.getByRole('slider',{name:'推理等级',exact:true});await slider.waitFor();
  assert.equal(await page.locator('.effort-fire,.reasoning-popover').count(),0);
  await slider.press('End');await page.waitForFunction(()=>document.querySelector('#reasoning-control').textContent.includes('最高'));
  assert.ok(commands.some(c=>c.name==='reasoning'&&c.payload.effort==='max'&&c.payload.model==='deepseek-flash'));
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'选择模型',exact:true}).click();
  await page.getByRole('option',{name:/custom-model/}).click();
  await page.waitForFunction(()=>document.querySelector('#model-control').textContent.includes('custom-model'));
  assert.ok(commands.some(c=>c.name==='model'&&c.payload.action==='select'&&c.payload.name==='备用'));
  assert.equal(await page.locator('.prompt-bar').getAttribute('data-max'),null);
  await page.locator('#composer').fill('发送失败时保留这段草稿');failSend=true;await page.locator('#send').click();
  await page.locator('#toast').filter({hasText:'测试：消息未发送'}).waitFor();assert.equal(await page.locator('#composer').inputValue(),'发送失败时保留这段草稿');
  await other.click();await page.locator('#composer').fill('分支草稿');await selected.click();assert.equal(await page.locator('#composer').inputValue(),'发送失败时保留这段草稿');
  failSend=false;delaySend=true;const sending=new Promise(resolve=>onSendStart=resolve);
  await page.locator('#composer').fill('提交中的消息');await page.locator('#send').click();await sending;
  await page.locator('#composer').fill('等待提交时写下的新草稿');finishSend();
  await page.waitForFunction(()=>document.querySelector('#send').getAttribute('aria-busy')==='false');
  assert.equal(await page.locator('#composer').inputValue(),'等待提交时写下的新草稿','completing a send preserves text entered while waiting');
  const tools=[{call_id:'read',name:'read_file',path:'README.md',status:'completed'},{call_id:'check',name:'run_command',preview:{command:'python check.py'},status:'running'}];
  runs=[{id:'run',branch_id:'main',message:'检查组件',status:'running',activity:'thinking',elapsed_ms:1500,tools}];sequence++;changed=true;
  await page.locator('.live .thought-line[data-working]').waitFor();await page.locator('.live .active-tool').filter({hasText:'python check.py'}).waitFor();
  const thought=page.locator('.live .thought-line__head');await thought.click();assert.equal(await thought.getAttribute('aria-expanded'),'false');
  runs[0].activity='tools';sequence++;changed=true;await page.waitForTimeout(250);assert.equal(await thought.getAttribute('aria-expanded'),'false');
  await thought.click();assert.equal(await thought.getAttribute('aria-expanded'),'true');
  runs[0].status='waiting_approval';sequence++;changed=true;
  await page.waitForFunction(()=>document.querySelector('.live .runtime-label').textContent==='等待批准');
  if(await page.locator('.live .thought-line__sr').textContent()!=='等待批准')reviewIssues.push('approval transition must update the accessible status');
  await page.evaluate(()=>window.fidelityTurn=document.querySelector('.live'));
  tools[1].status='completed';nodes=[{id:'n1',branch_id:'main',parent_id:null,prompt:'检查组件',answer:'组件已核对。',status:'completed',recoverable:true,tools,timing:{elapsed_ms:2300},usage:{}}];branches[0].node_ids=['n1'];branches[0].head='n1';runs[0].node_id='n1';runs[0].status='completed';sequence++;changed=true;
  await page.locator('[data-node-id=n1] .thought-line').waitFor();assert.equal(await page.evaluate(()=>window.fidelityTurn===document.querySelector('[data-node-id=n1]')),true);
  assert.equal(await page.locator('[data-node-id=n1] .thought-line[data-working]').count(),0);
  assert.equal(await page.locator('.tool-group').evaluate(e=>e.open),false);
  await page.locator('.tool-group>summary').click();assert.equal(await page.locator('.tool-activity').count(),2);
  await page.setViewportSize({width:390,height:760});if(await page.locator('#sidebar-backdrop').isVisible())await page.getByRole('button',{name:'收起侧栏',exact:true}).click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);assert.deepEqual(reviewIssues,[]);console.log('Official React Bits: animated branch selection/keyboard/reversal/reduced motion, PromptBar config/drafts and ThoughtLine lifecycle passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
