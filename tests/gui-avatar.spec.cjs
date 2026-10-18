// Catches missing welcome/avatar integration, stale run expressions after polling,
// animation restarted by clock updates, and motion continuing in reduced mode.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let sequence=0,changed=false;
 const branch={id:'main',name:'头像验证',conversation_id:'c',node_ids:[],head:null,draft:'',is_default:true,permission_mode:'default'};
 const runs=[];
 const snapshot=()=>({sequence,project:{id:'avatar',name:'Ignovate',path:'/tmp/avatar'},configuration:{configured:true,model:'offline',reasoning_options:[['default','默认']]},branches:[branch],nodes:[],runs,approvals:[],paused:false,layouts:{c:{revision:0,positions:{}}}});
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname,reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  if(path==='/api/snapshot')return reply(snapshot());
  if(path==='/api/events'){const reset=changed;changed=false;return reply({sequence,events:[],reset});}
  const {name}=route.request().postDataJSON();return reply(name==='workspaces'?[]:{});
 });
 const update=async change=>{change();sequence++;changed=true;await page.waitForResponse(r=>r.url().includes('/api/snapshot'));};
 const geometry=locator=>locator.evaluate(svg=>[...svg.querySelectorAll('path,circle')].map(n=>[n.getAttribute('d'),n.getAttribute('transform'),n.getAttribute('r')].join('|')).join('\n'));
 try{
  await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8768')+'/#token=browser-test-only');
  const welcome=page.getByRole('img',{name:'Ignovate 机器人头像'});
  await welcome.waitFor({timeout:5000});
  assert.ok((await welcome.boundingBox()).width>=100,'welcome avatar has a visible presence');
  const before=await geometry(welcome);await page.mouse.move(1000,100);await page.waitForTimeout(700);
  assert.notEqual(await geometry(welcome),before,'idle eyes follow the pointer');
  // Test where the eyes actually look, not merely that their geometry changes.
  // Screen Y grows downwards; rendered eye centers must follow that direction.
  const box=await welcome.boundingBox(),cx=box.x+box.width/2,cy=box.y+box.height/2;
  for(const [direction,x,y,axis,sign] of [
   ['down',cx,780,'y',1],['up',cx,5,'y',-1],
   ['left',5,cy,'x',-1],['right',1160,cy,'x',1]
  ]){
   await page.mouse.move(x,y);await page.waitForTimeout(700);
   const center=await welcome.evaluate(svg=>{
    const eyes=[...svg.querySelectorAll('mask path[transform]')].map(p=>new DOMMatrix(p.getAttribute('transform')));
    return {x:eyes.reduce((s,m)=>s+m.e,0)/eyes.length,y:eyes.reduce((s,m)=>s+m.f,0)/eyes.length};
   });
   assert.ok(center[axis]*sign>8,`pointer ${direction}: eyes must look ${direction}, got ${JSON.stringify(center)}`);
  }
  await page.locator('#composer').fill('保留我的草稿');
  await update(()=>runs.push({id:'run',branch_id:'main',message:'验证头像状态',status:'running',activity:'thinking',elapsed_ms:1000,tools:[]}));
  const avatar=page.locator('.live .ignovate-avatar');await avatar.waitFor();
  await page.waitForFunction(()=>document.querySelector('.live .ignovate-avatar')?.dataset.avatarState==='thinking');
  await page.waitForTimeout(650);const thinking=await geometry(avatar);
  await page.waitForTimeout(350);assert.notEqual(await geometry(avatar),thinking,'thinking dots animate across clock updates');
  await update(()=>Object.assign(runs[0],{activity:'tools',tools:[{call_id:'cmd',name:'run_command',status:'running',preview:{command:'python check.py'}}]}));
  await page.waitForFunction(()=>document.querySelector('.live .ignovate-avatar')?.dataset.avatarState==='orbit');
  // Upstream's orbit is a finite entrance. A long tool call must continue
  // showing an active pose instead of silently settling into the idle ball.
  await page.waitForTimeout(4100);
  await page.waitForFunction(()=>[...document.querySelectorAll('.live .ignovate-avatar g[fill="none"] path')].some(p=>Number(p.getAttribute('opacity'))>.1),{},{timeout:2500});
  await page.locator('.live').getByRole('button',{name:'收起执行过程',exact:true}).click();
  await update(()=>runs[0].elapsed_ms+=1000);
  assert.equal(await page.locator('.live .thought-line__trace').getAttribute('aria-hidden'),'true','avatar updates preserve tool expansion');
  await update(()=>runs[0].status='waiting_approval');
  await page.waitForFunction(()=>document.querySelector('.live .ignovate-avatar')?.dataset.avatarState==='notify');
  assert.match(await page.locator('.live .runtime-label').textContent(),/等待批准/);
  await page.emulateMedia({reducedMotion:'reduce',colorScheme:'dark'});
  await page.waitForFunction(()=>document.querySelector('.live .ignovate-avatar')?.dataset.avatarMotion==='still');
  await page.waitForTimeout(100);const frozen=await geometry(avatar);
  await page.waitForTimeout(450);assert.ok((await geometry(avatar))===frozen,'reduced motion freezes animation');
  await update(()=>Object.assign(runs[0],{status:'running',activity:'thinking',tools:[]}));
  await page.waitForFunction(()=>document.querySelector('.live .ignovate-avatar')?.dataset.avatarState==='thinking');
  assert.notEqual(await geometry(avatar),frozen,'reduced motion still changes pose when run status changes');
  await page.setViewportSize({width:390,height:760});
  assert.equal(await page.locator('#composer').inputValue(),'保留我的草稿');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await avatar.evaluate(svg=>/NaN|Infinity/.test(svg.outerHTML)),false);
  // A cancelled first run returns to the cached empty screen. Its island was
  // unmounted while hidden, so the returned welcome must mount an avatar again.
  await update(()=>runs.splice(0));
  await welcome.waitFor({timeout:3000});
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForTimeout(100);const restored=await geometry(welcome);
  await page.mouse.move(350,80);await page.waitForTimeout(650);
  assert.notEqual(await geometry(welcome),restored,'welcome resumes after a cancelled first run');
  // Exercise completed/failed historical headers with the real rendering adapter.
  await page.evaluate(async()=>{
   const {renderTurn}=await import('./conversation.js');
   for(const status of ['completed','failed'])document.querySelector('#content').append(renderTurn({id:status,prompt:'检查',answer:'结果',status,tools:[],timing:{},usage:{}}));
  });
  assert.equal(await page.locator('[data-node-id="completed"] .ignovate-avatar').getAttribute('data-avatar-state'),'idle');
  assert.equal(await page.locator('[data-node-id="failed"] .ignovate-avatar').getAttribute('data-avatar-state'),'exclaim');
  assert.deepEqual(errors,[]);
  if(process.env.AVATAR_SCREENSHOT)await page.screenshot({path:process.env.AVATAR_SCREENSHOT,fullPage:true});
  console.log('Avatar UI: welcome, gaze, run transitions, preserved fold/draft, reduced motion, dark/mobile and historical headers passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
