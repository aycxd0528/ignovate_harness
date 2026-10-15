const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.GUI_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'dark'}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const snapshot=()=>page.evaluate(async()=>await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json());
 try{
  await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8767')+'/#token=browser-test-only');await page.getByRole('button',{name:'新会话',exact:true}).click();
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.locator('#permission-mode').click();await page.getByRole('menuitemradio',{name:'完全访问权限',exact:true}).click();
  await page.evaluate(()=>{window.toolDisplayMax={current:0,groups:0,records:0};new MutationObserver(()=>{for(const [key,selector] of Object.entries({current:'.live .active-tool',groups:'.live .tool-group',records:'.live .tool-activity'}))window.toolDisplayMax[key]=Math.max(window.toolDisplayMax[key],document.querySelectorAll(selector).length);}).observe(document.querySelector('#content'),{subtree:true,childList:true});});
  await page.locator('#composer').fill('并行工具展示测试');await page.locator('#send').click();await page.locator('.live .active-tool').first().waitFor();
  assert.equal(await page.locator('.live .tool-group').count(),0,'a running task must not render the full history alongside its status');
  await page.evaluate(()=>window.progressRef=document.querySelector('.live .active-tool'));
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json();return s.runs.at(-1).tools.filter(t=>t.status==='running').length===2;});
  assert.equal(await page.locator('.live .active-tool').count(),1,'parallel tool calls occupy one live status row');
  assert.match(await page.locator('.live .active-tool').textContent(),/2.*同时/);
  assert.equal(await page.evaluate(()=>window.progressRef===document.querySelector('.live .active-tool')),true,'parallel updates retain the original status row');
  await page.locator('#startup-screen').waitFor({state:'hidden'});await page.screenshot({path:'/tmp/ignovate-tools-single-live.png'});
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json();return s.runs.at(-1).status==='running'&&s.runs.at(-1).tools.length===2&&s.runs.at(-1).tools.every(t=>t.status==='completed');});
  assert.equal(await page.locator('.live .active-tool').count(),1,'the row remains between tool completion and the final answer');
  await page.locator('.turn:not(.live)').filter({hasText:'并行工具已完成'}).waitFor();
  const group=page.locator('.tool-group');assert.equal(await group.getAttribute('open'),null,'completion folds the full history');
  assert.match(await group.locator('summary').first().textContent(),/调用了工具/);
  assert.deepEqual(await page.evaluate(()=>window.toolDisplayMax),{current:1,groups:0,records:0},'live snapshots must never burst into multiple tool rows');
  await group.locator('summary').first().click();await page.locator('.tool-activity-list').waitFor({state:'visible'});
  assert.equal(await page.locator('.tool-activity').count(),2,'completion preserves both actual tool records');
  assert.match(await page.locator('.tool-activity').first().textContent(),/结果一/);assert.match(await page.locator('.tool-activity').last().textContent(),/结果二/);
  await group.locator('summary').first().click();await page.screenshot({path:'/tmp/ignovate-tools-single-finished.png'});
  await page.locator('#composer').fill('并行工具展示测试，停止');await page.locator('#send').click();await page.locator('.live .active-tool').waitFor();await page.locator('#stop').click();
  await page.waitForFunction(()=>!document.querySelector('.live'));
  assert.equal(await page.locator('.tool-group').last().getAttribute('open'),null,'stopping also leaves a folded tool history');
  assert.equal((await snapshot()).runs.at(-1).status,'stopped');assert.deepEqual(errors,[]);
  console.log('Parallel tools use one retained row; thinking gaps, completion, output history and stop passed.');
 }catch(error){console.error('Live tools page errors:',errors);console.error((await page.locator('body').innerText()).slice(0,800));await page.screenshot({path:'/tmp/ignovate-live-tools-debug.png'});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
