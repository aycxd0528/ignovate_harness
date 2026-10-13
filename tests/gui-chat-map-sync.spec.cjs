const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'dark'}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 const snapshot=()=>page.evaluate(async()=>await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json());
 try{
  await page.goto('http://127.0.0.1:8767/#token=browser-test-only');await page.locator('#project-name').waitFor();
  await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.getByRole('button',{name:'新会话',exact:true}).click();await page.getByLabel('地图追问').waitFor();
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.locator('#composer').fill('聊天首轮，延迟地图追问');await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.locator('.map-run-card').waitFor();
  assert.equal(await page.locator('.map-draft').count(),0,'an empty root draft must not cover a chat run');
  await page.locator('.map-card:not(.map-run-card)').filter({hasText:'聊天首轮'}).waitFor();
  assert.equal(await page.locator('.map-draft').count(),0,'chat completion must replace the empty map state without a manual refresh');
  assert.match(await page.locator('.map-excerpt').textContent(),/先明确内核接口/);
  await page.reload();await page.locator('.map-card:not(.map-run-card)').waitFor();assert.equal(await page.locator('.map-draft').count(),0);
  await page.getByRole('button',{name:'新会话',exact:true}).click();await page.getByLabel('地图追问').fill('保留地图中尚未发送的问题');
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.locator('#composer').fill('聊天先开始');await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.locator('.turn:not(.live)').waitFor();await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.locator('.map-card:not(.map-run-card)').waitFor();
  assert.equal(await page.getByLabel('地图追问').inputValue(),'保留地图中尚未发送的问题','unsent map text survives a chat root turn');
  assert.match(await page.locator('.map-draft-heading').textContent(),/继续追问/);
  const placement=await page.locator('.conversation-map').evaluate(map=>({card:parseFloat(map.querySelector('.map-card').style.left),draft:parseFloat(map.querySelector('.map-draft').style.left)}));assert.ok(placement.draft>=placement.card+310,'retained draft cannot obscure the synced answer');
  const glyphs=new Map();
  for(const label of ['请求批准','帮我批准','完全访问权限']){
   await page.locator('.map-draft').getByRole('button',{name:'权限管理',exact:true}).click();await page.getByRole('menuitemradio',{name:label,exact:true}).click();
   glyphs.set(label,await page.locator('.map-draft button[aria-label="权限管理"] svg').evaluate(svg=>svg.innerHTML));
  }
  assert.equal(new Set(glyphs.values()).size,3,'map draft permission modes have distinct SVG glyphs');
  await page.getByRole('button',{name:'聊天',exact:true}).click();
  for(const label of glyphs.keys()){
   await page.locator('#permission-mode').click();await page.getByRole('menuitemradio',{name:label,exact:true}).click();await page.locator('#permission-mode').filter({hasText:label}).waitFor();
   assert.equal(await page.locator('#permission-mode>svg').first().evaluate(svg=>svg.innerHTML),glyphs.get(label),'chat and map permission icons agree');
  }
  await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.getByRole('button',{name:'发送追问',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.map-card:not(.map-run-card)').length===2);
  const state=await snapshot(),branch=state.branches.find(branch=>branch.name==='聊天先开始');const nodes=state.nodes.filter(node=>node.branch_id===branch.id);assert.equal(nodes[1].parent_id,nodes[0].id);assert.equal(state.runs.length,3,'retained draft is sent once as a continuation');
  await page.screenshot({path:'/tmp/ignovate-chat-map-synced.png'});assert.deepEqual(errors,[]);
  await page.getByRole('button',{name:'新会话',exact:true}).click();await page.getByLabel('地图追问').fill('失败后保留的问题');
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.locator('#composer').fill('失败聊天测试');await page.getByRole('button',{name:'发送',exact:true}).click();
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer browser-test-only'}})).json();return s.runs.at(-1).status==='failed';});
  await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.getByRole('button',{name:'移到新会话',exact:true}).waitFor();
  assert.equal(await page.getByLabel('地图追问').inputValue(),'失败后保留的问题');assert.equal(await page.getByRole('button',{name:'发送追问',exact:true}).isDisabled(),true);
  let creates=0;await page.route('**/api/command',async route=>{if(route.request().postDataJSON().name==='create'){creates++;await new Promise(resolve=>setTimeout(resolve,250));}return route.continue();});
  await page.getByRole('button',{name:'移到新会话',exact:true}).dblclick();await page.getByPlaceholder('从一个新问题开始…').waitFor();assert.equal(creates,1,'double-clicking draft move creates one conversation');
  assert.equal(await page.getByLabel('地图追问').inputValue(),'失败后保留的问题');assert.equal((await snapshot()).runs.length,4,'moving a retained draft must not execute it');
  await page.getByRole('button',{name:'显式恢复队列',exact:true}).click();await page.getByRole('button',{name:'发送追问',exact:true}).click();
  await page.locator('.map-card:not(.map-run-card)').filter({hasText:'失败后保留的问题'}).waitFor();assert.equal((await snapshot()).runs.length,5);assert.deepEqual(errors,[]);
  console.log('Chat root pending/completion/reload, retained map draft continuation and distinct permission glyphs passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
