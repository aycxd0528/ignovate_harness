const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});try{
 for(const failure of [false,true]){
  const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'dark'});const errors=[];page.on('pageerror',e=>errors.push(e.message));let release;const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/snapshot',async route=>{await gate;if(failure)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'启动测试：本地连接暂不可用'})});return route.continue();});
  await page.goto('http://127.0.0.1:8767/#token=browser-test-only');await page.locator('#startup-screen').waitFor();
  assert.match(await page.locator('#startup-screen').textContent(),/Ignovate/);assert.ok(await page.locator('.startup-letter').count()>=8);
  assert.equal(await page.locator('#startup-screen').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(22, 22, 22)','system dark appearance applies before the snapshot resolves');
  await page.waitForTimeout(650);
  await page.screenshot({path:failure?'/tmp/ignovate-startup-pending-error.png':'/tmp/ignovate-startup-dark.png'});
  await page.emulateMedia({colorScheme:'light'});assert.equal(await page.locator('#startup-screen').evaluate(n=>getComputedStyle(n).backgroundColor),'rgb(255, 255, 255)');
  await page.setViewportSize({width:390,height:844});assert.equal(await page.locator('#startup-screen').evaluate(n=>n.scrollWidth>n.clientWidth),false);
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.startup-letter').first().evaluate(n=>getComputedStyle(n).animationName),'none');
  release();
  if(failure){await page.locator('#startup-screen').filter({hasText:'本地连接暂不可用'}).waitFor();assert.equal(await page.locator('#startup-screen').getByRole('button',{name:'重新加载',exact:true}).count(),1);assert.equal(await page.locator('#startup-screen').getAttribute('data-state'),'error');}
  else{await page.locator('#reasoning-control').waitFor();assert.equal(await page.locator('#startup-screen').evaluate(n=>n.classList.contains('is-ready')),false,'a fast connection keeps the startup visible for two seconds');await page.locator('#startup-screen').waitFor({state:'detached'});assert.ok(await page.evaluate(()=>performance.now())>=2000,'startup does not finish before two seconds');}
  assert.deepEqual(errors,[]);await page.close();
 }
 const slow=await browser.newPage();let releaseSlow;const slowGate=new Promise(resolve=>releaseSlow=resolve);
 await slow.route('**/api/snapshot',async route=>{await slowGate;return route.continue();});await slow.goto('http://127.0.0.1:8767/#token=browser-test-only');await slow.waitForTimeout(2100);assert.equal(await slow.locator('#startup-screen').isVisible(),true,'a slow connection still waits for readiness');
 const releasedAt=Date.now();releaseSlow();await slow.locator('#startup-screen').waitFor({state:'detached'});assert.ok(Date.now()-releasedAt<1000,'a connection taking over two seconds adds no second delay');await slow.close();
 console.log('Startup two-second minimum, slow readiness, error/retry/mobile/reduced-motion passed.');
 }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
