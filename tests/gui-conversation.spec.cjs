const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:960},colorScheme:'dark'});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const snapshot=()=>page.evaluate(async()=>await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json());
  try{
    await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8767')+'/#token=browser-test-only');await page.locator('#project-name').filter({hasText:'GUI 验证项目'}).waitFor();
    await page.getByRole('button',{name:'新会话',exact:true}).click();
    await page.getByRole('button',{name:'权限管理',exact:true}).click();await page.getByRole('menuitemradio',{name:'帮我批准',exact:true}).click();
    await page.locator('#composer').fill('工具状态与产物测试');await page.getByRole('button',{name:'发送',exact:true}).click();
    await page.locator('#workspace-title').filter({hasText:'工具状态与产物测试'}).waitFor();
    await page.locator('.live .active-tool').filter({hasText:'等待批准'}).waitFor();
    await page.getByRole('button',{name:'批准一次',exact:true}).click();
    await page.locator('.live .active-tool').filter({hasText:'正在运行'}).waitFor();
    await page.locator('.live .task-elapsed').waitFor();await page.screenshot({path:'/tmp/ignovate-gui-tools-live.png'});
    await page.waitForFunction(()=>document.querySelectorAll('.turn:not(.live)').length===1);
    const user=await page.locator('.user-message .prompt').boundingBox(),answer=await page.locator('.assistant-message .answer').boundingBox();
    assert.ok(user.x>answer.x,'user bubble aligns right; assistant body aligns left');
    assert.match(await page.locator('.task-elapsed').textContent(),/\d+(?:\.\d+)?[sm]/);
    await page.locator('.turn').getByRole('button',{name:'详情',exact:true}).click();
    await page.locator('.artifact-card[data-kind=file]').filter({hasText:'report.md'}).waitFor();
    assert.equal(await page.locator('#inspector .answer').count(),0,'inspector must not repeat the model answer');
    await page.locator('.artifact-card[data-kind=file]').getByRole('button',{name:'预览产物',exact:true}).click();
    await page.locator('#dialog').filter({hasText:'测试产物'}).waitFor();await page.getByRole('button',{name:'取消',exact:true}).click();
    const before=await snapshot();await page.reload();await page.locator('.turn:not(.live)').waitFor();
    assert.equal((await snapshot()).runs[0].elapsed_ms,before.runs[0].elapsed_ms);
    await page.locator('.turn').getByRole('button',{name:'详情',exact:true}).click();
    await page.screenshot({path:'/tmp/ignovate-gui-conversation.png'});
    assert.deepEqual(errors,[]);console.log('Conversation bubbles, input title, live command, task time and artifact-only inspector passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
