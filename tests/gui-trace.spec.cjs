const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.GUI_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'dark'}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto('http://127.0.0.1:8767/#token=browser-test-only');await page.locator('#project-name').waitFor();
  await page.getByRole('button',{name:'新会话',exact:true}).click();await page.getByRole('button',{name:'轨迹',exact:true}).click();
  await page.getByText('暂无执行轨迹', {exact:true}).waitFor();
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.locator('#permission-mode').click();await page.getByRole('menuitemradio',{name:'完全访问权限',exact:true}).click();
  await page.locator('#composer').fill('轨迹实时工具状态与产物测试，延迟地图追问');await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.getByRole('button',{name:'轨迹',exact:true}).click();
  const tool=page.locator('.trace-row[data-role=tool]').filter({hasText:'run_command'});await tool.waitFor();
  assert.equal(await tool.getAttribute('data-status'),'running');await tool.locator('summary').click();
  await tool.evaluate(row=>window.traceRetainedRow=row);
  await page.waitForFunction(()=>document.querySelector('.trace-row[data-role=tool][data-status=completed]'));
  assert.equal(await tool.getAttribute('open'),'','tool details remain expanded through status updates');
  assert.equal(await tool.evaluate(row=>row===window.traceRetainedRow),true,'live trace rows retain their DOM identity');
  await page.getByLabel('搜索轨迹').fill('report.md');await page.locator('.trace-row[data-role=tool]').filter({hasText:'report.md'}).waitFor();
  assert.equal(await page.getByLabel('搜索轨迹').inputValue(),'report.md','completion must preserve the search input');
  assert.equal(await page.locator('.trace-row[data-role=user]').count(),0,'search filters rows, not just round headings');
  await page.getByLabel('搜索轨迹').fill('');await page.locator('.trace-row[data-role=assistant]').filter({hasText:'命令已完成'}).waitFor();
  await page.locator('.trace-round-header').filter({hasText:'已结束'}).waitFor();
  assert.equal(await page.locator('.trace-row[data-role=tool]').count(),2,'tool start/end pairs render once');
  assert.match(await page.locator('.trace-metrics').textContent(),/1 轮/);assert.match(await page.locator('.trace-metrics').textContent(),/2 次工具/);
  assert.equal(await page.locator('.trace-track').count(),3);assert.ok(await page.locator('.trace-segment').count()>2);
  await tool.locator('summary').click();await page.getByLabel('搜索轨迹').blur();await page.locator('#startup-screen').waitFor({state:'hidden'});await page.screenshot({path:'/tmp/ignovate-trace-dark.png'});await tool.locator('summary').click();
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.getByRole('button',{name:'轨迹',exact:true}).click();
  assert.equal(await tool.getAttribute('open'),'','tab switching keeps expanded details');
  await page.reload();await page.getByRole('button',{name:'轨迹',exact:true}).click();await page.locator('.trace-row[data-role=tool]').filter({hasText:'report.md'}).waitFor();
  await page.locator('#startup-screen').waitFor({state:'hidden'});await page.getByRole('button',{name:'主题',exact:true}).click();await page.screenshot({path:'/tmp/ignovate-trace-light.png'});
  await page.setViewportSize({width:540,height:795});assert.equal(await page.locator('body').evaluate(body=>body.scrollWidth<=innerWidth),true,'the trace fits narrow windows');
  await page.getByRole('button',{name:'新会话',exact:true}).click();await page.getByRole('button',{name:'轨迹',exact:true}).click();await page.getByText('暂无执行轨迹',{exact:true}).waitFor();
  assert.equal(await page.locator('.trace-row').count(),0,'a new conversation cannot retain the previous trace');assert.deepEqual(errors,[]);
  console.log('Trace empty/live/completed/reload, search, row retention, theme and scope passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
