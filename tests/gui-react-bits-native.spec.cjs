const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1280,height:820},colorScheme:'light'}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const snapshot=()=>page.evaluate(async()=>await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json());
 try{
  await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8768')+'/#token=browser-test-only');
  await page.locator('#composer').waitFor();
  await page.getByRole('button',{name:'新会话',exact:true}).click();
  await page.locator('#permission-mode').click();await page.getByRole('menuitemradio',{name:'完全访问权限',exact:true}).click();
  await page.locator('#composer').fill('工具状态与产物测试');await page.locator('#send').click();
  await page.locator('.live .active-tool').waitFor();
  await page.screenshot({path:'/tmp/ignovate-react-bits-native-thought.png',animations:'disabled'});
  await page.locator('.live .thought-line__head').click();
  assert.equal(await page.locator('.live .thought-line__trace').getAttribute('aria-hidden'),'true');
  await page.locator('.turn:not(.live)').waitFor();
  assert.equal(await page.locator('.assistant-status .thought-line').getAttribute('data-working'),null);
  assert.equal(await page.locator('.tool-group').evaluate(e=>e.open),false);
  await page.locator('.tool-group>summary').click();await page.locator('.tool-activity').filter({hasText:'report.md'}).waitFor();
  const currentId=await page.locator('.session[aria-current=page]').getAttribute('data-branch-id');
  const first=(await snapshot()).branches.find(b=>b.id===currentId),node=(await snapshot()).nodes.find(n=>n.id===first.head);assert.ok(node.answer);
  await page.locator('.turn-actions').getByRole('button',{name:'分叉',exact:true}).click();
  await page.getByLabel('分支名称',{exact:true}).fill('交互适配探索');await page.getByRole('button',{name:'创建分支',exact:true}).click();
  const child=page.locator('.session').filter({hasText:'交互适配探索'});await child.waitFor();
  assert.equal(await child.getAttribute('aria-current'),'page');
  await page.locator('.session[data-branch-id="'+first.id+'"]').click();
  await page.locator('#composer-add').click();await page.getByRole('option',{name:/项目文件 引用文件内容/}).click();
  await page.getByLabel('项目相对路径',{exact:true}).fill('README.md');await page.getByRole('button',{name:'加入草稿',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#composer').value.includes('临时项目'));
  await page.locator('#reasoning-control').click();await page.getByRole('slider',{name:'推理等级',exact:true}).waitFor();
  await page.getByRole('slider',{name:'推理等级',exact:true}).press('End');
  await page.waitForFunction(()=>document.querySelector('#reasoning-control').textContent.includes('最高'));
  assert.equal((await snapshot()).configuration.reasoning_effort,'max');
  await page.screenshot({path:'/tmp/ignovate-react-bits-native-effort.png',animations:'disabled'});await page.keyboard.press('Escape');
  await page.locator('#composer').fill('继续完善交互细节');
  await page.screenshot({path:'/tmp/ignovate-react-bits-native-light.png'});
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.waitForTimeout(220);await page.screenshot({path:'/tmp/ignovate-react-bits-native-dark.png'});
  // The new row action must mutate the intended native branch, leaving this branch selected.
  const childId=await child.getAttribute('data-branch-id');
  await child.focus();await page.keyboard.press('ArrowLeft');await page.getByRole('button',{name:'归档会话 交互适配探索',exact:true}).click();
  await child.waitFor({state:'detached'});
  assert.equal(Boolean((await snapshot()).branches.find(b=>b.id===childId).archived),true);
  assert.equal(await page.locator('.session[data-branch-id="'+first.id+'"]').getAttribute('aria-current'),'page');
  assert.deepEqual(errors,[]);console.log('Native GUI: real tools, live fold → completed status, branch creation, file references, reasoning and swipe archive passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
