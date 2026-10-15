const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.GUI_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto('http://127.0.0.1:8767/#token=browser-test-only');await page.locator('#project-name').waitFor();
  await page.getByRole('button',{name:'新会话',exact:true}).click();await page.getByRole('button',{name:'会话地图',exact:true}).click();
  await page.getByLabel('地图追问').fill('拖动测试起点');await page.getByRole('button',{name:'发送追问',exact:true}).click();
  await page.locator('.map-card:not(.map-run-card)').waitFor();await page.getByRole('button',{name:'继续追问',exact:true}).click();
  await page.getByLabel('地图追问').fill('工具状态与产物测试，延迟地图追问');
  await page.locator('.map-draft').getByRole('button',{name:'权限管理',exact:true}).click();await page.getByRole('menuitemradio',{name:'完全访问权限',exact:true}).click();
  await page.getByRole('button',{name:'发送追问',exact:true}).click();await page.locator('.map-run-card').waitFor();
  const card=page.locator('.map-run-card'),before=await card.evaluate(c=>({x:parseFloat(c.style.left),y:parseFloat(c.style.top)}));
  const rect=await card.locator('.map-card-header').boundingBox(),edge=await page.locator('.draft-link').getAttribute('d');
  await page.mouse.move(rect.x+70,rect.y+25);await page.mouse.down();await page.mouse.move(rect.x+130,rect.y+60,{steps:4});
  assert.deepEqual(await card.evaluate(c=>({x:parseFloat(c.style.left),y:parseFloat(c.style.top)})),{x:before.x+60,y:before.y+35},'the sending card can be dragged by its heading');
  assert.notEqual(await page.locator('.draft-link').getAttribute('d'),edge,'the live connector follows before release');
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json();return s.runs.at(-1).status==='completed';});
  await page.mouse.move(rect.x+160,rect.y+80);await page.mouse.up();
  const finished=page.locator('.map-card').filter({hasText:'延迟地图追问'});await finished.waitFor();
  const expected={x:before.x+90,y:before.y+55};
  assert.deepEqual(await finished.evaluate(c=>({x:parseFloat(c.style.left),y:parseFloat(c.style.top)})),expected,'completion during a held drag keeps the latest pointer position');
  await page.reload();await page.getByRole('button',{name:'会话地图',exact:true}).click();await finished.waitFor();
  assert.deepEqual(await finished.evaluate(c=>({x:parseFloat(c.style.left),y:parseFloat(c.style.top)})),expected,'the completed position survives reloading');
  assert.deepEqual(errors,[]);console.log('Heading drag, live connector and completion during capture passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
