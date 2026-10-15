const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.GUI_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 let unblock;
 try{
  await page.goto('http://127.0.0.1:8767/#token=browser-test-only');await page.getByRole('button',{name:'轨迹',exact:true}).waitFor();
  await page.getByRole('button',{name:'轨迹',exact:true}).click();await page.getByRole('button',{name:'加载更早的轨迹',exact:true}).waitFor();
  assert.equal(await page.locator('.trace-round').count(),80);
  let announce;const held=new Promise(resolve=>announce=resolve),release=new Promise(resolve=>unblock=resolve);let delayed=false;
  await page.route('**/api/command',async route=>{if(route.request().postDataJSON().name==='trace'&&!delayed){delayed=true;announce();await release;if(process.env.TRACE_REJECT_OLD==='1')return route.abort('failed');}await route.continue();});
  await page.getByRole('button',{name:'主题',exact:true}).click();await held;
  await page.getByRole('button',{name:'加载更早的轨迹',exact:true}).click();unblock();
  await page.locator('.trace-row[data-role=user] .trace-preview').filter({hasText:/^历史问题 1$/}).waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('.trace-round').length===81);
  assert.equal(await page.getByRole('button',{name:'加载更早的轨迹',exact:true}).count(),0,'load-earlier intent survives an older in-flight request');
  let signal;const oldRequest=new Promise(resolve=>signal=resolve),oldRelease=new Promise(resolve=>unblock=resolve);delayed=false;
  await page.unroute('**/api/command');await page.route('**/api/command',async route=>{if(route.request().postDataJSON().name==='trace'&&!delayed){delayed=true;signal();await oldRelease;}await route.continue();});
  await page.getByRole('button',{name:'主题',exact:true}).click();await oldRequest;
  await page.getByRole('button',{name:'新会话',exact:true}).click();unblock();await page.getByText('暂无执行轨迹',{exact:true}).waitFor();
  assert.equal(await page.locator('.trace-row').count(),0,'a delayed old-branch trace cannot paint into a new conversation');
  assert.deepEqual(errors,[]);console.log('Load-earlier during refresh and stale branch reply isolation passed.');
 }catch(error){console.error('Trace lifecycle page errors:',errors);console.error((await page.locator('body').innerText()).slice(0,600));throw error;}
 finally{unblock?.();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
