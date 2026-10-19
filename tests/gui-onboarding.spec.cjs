const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.IGNOVATE_GUI_TEST_URL||'http://127.0.0.1:18868';
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 try{
  const page=await browser.newPage({viewport:{width:1168,height:795}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/#token=browser-test-only');
  const guide=page.getByRole('dialog',{name:'开始使用 Ignovate',exact:true});
  await guide.waitFor({timeout:5000});
  await guide.getByRole('heading',{name:'让第一个任务，从这里开始',exact:true}).waitFor();
  await guide.getByRole('button',{name:'开始设置',exact:true}).click();
  await guide.getByRole('button',{name:'使用当前工作区',exact:true}).click();
  await guide.getByRole('heading',{name:'连接你的模型',exact:true}).waitFor();
  await guide.getByRole('button',{name:'使用已有连接',exact:true}).click();
  await guide.getByRole('heading',{name:'准备好开始了',exact:true}).waitFor();
  await guide.getByRole('button',{name:'进入工作台',exact:true}).click();
  await guide.waitFor({state:'detached'});await page.reload();
  await page.locator('#project-name').waitFor();await page.locator('#startup-screen').waitFor({state:'detached'});
  assert.equal(await guide.count(),0,'native completion survives browser reload');
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await page.getByRole('button',{name:'重新查看使用引导',exact:true}).click();await guide.waitFor();
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({colorScheme:'dark',reducedMotion:'reduce'});
  assert.equal(await guide.evaluate(n=>n.scrollWidth>n.clientWidth),false);
  await page.screenshot({path:'/tmp/ignovate-onboarding-dark-narrow.png'});
  await guide.getByRole('button',{name:'稍后设置',exact:true}).click();await guide.waitFor({state:'detached'});
  await page.reload();await page.locator('#composer').waitFor();await page.locator('#startup-screen').waitFor({state:'detached'});assert.equal(await guide.count(),0);
  assert.deepEqual(errors,[]);console.log('First-run guide, native persistence, existing connection, reopen, skip and reduced-motion narrow layout passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
