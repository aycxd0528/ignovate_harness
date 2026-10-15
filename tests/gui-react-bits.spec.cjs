// Regression: swiping must not select the row or archive the active branch by mistake;
// warm hints must retain descriptions, and collapsing live steps must retain output.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:795},colorScheme:'light'}),errors=[],commands=[];
 page.on('pageerror',e=>errors.push(e.message));
 let sequence=0,changed=false;
 const tools=[{call_id:'read',name:'read_file',path:'README.md',status:'completed'},{call_id:'check',name:'run_command',preview:{command:'python check.py'},status:'running'}];
 const branches=[{id:'main',name:'主线方案',conversation_id:'c',parent_id:null,node_ids:[],head:null,draft:'',is_default:true,permission_mode:'default'},{id:'child',name:'界面探索',conversation_id:'c',parent_id:'main',node_ids:[],head:null,draft:'',permission_mode:'default'}];
 const snapshot=()=>({sequence,project:{id:'bits',name:'React Bits 适配',path:'/tmp/ignovate-react-bits'},configuration:{configured:true,model:'offline-model',reasoning_effort:'default',reasoning_options:[['default','默认']]},branches,nodes:[],runs:[{id:'run',branch_id:'main',message:'检查项目并验证界面',status:'running',activity:'thinking',elapsed_ms:1500,tools}],approvals:[],paused:false,layouts:{c:{revision:0,positions:{},collapsed:[],viewport:{x:40,y:40,zoom:1}}}});
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname,reply=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  if(path==='/api/snapshot')return reply(snapshot());
  if(path==='/api/events'){const reset=changed;changed=false;return reply({sequence,events:[],reset});}
  const data=route.request().postDataJSON();commands.push(data);
  if(data?.name==='branch'&&data.payload.archived!==undefined){branches.find(b=>b.id===data.payload.branch_id).archived=data.payload.archived;sequence++;}
  if(data?.name==='workspaces')return reply([]);
  if(data?.name==='trace')return reply({sequence,total_turns:0,turns:[],tool_calls:0,model_calls:0,elapsed_ms:null});
  return reply({});
 });
 try{
  await page.goto((process.env.GUI_TEST_ORIGIN||'http://127.0.0.1:8767')+'/#token=browser-test-only');
  await page.locator('.live .active-tool').waitFor();
  const main=page.locator('.session[data-branch-id="main"]'),child=page.locator('.session[data-branch-id="child"]');
  // WarmTooltip: one cold wait, then immediate sibling hints, with proper cleanup.
  await page.locator('#composer-add').hover();await page.locator('#control-tooltip:popover-open').waitFor();
  await page.locator('#permission-mode').hover();
  await page.waitForFunction(()=>document.querySelector('#control-tooltip').textContent.includes('权限'),{},{timeout:120});
  assert.ok((await page.locator('#permission-mode').getAttribute('aria-describedby')).includes('control-tooltip'));
  await page.keyboard.press('Escape');assert.equal(await page.locator('#permission-mode').getAttribute('aria-describedby'),null);
  // ThoughtLine folds tool steps only, and real refreshes retain the chosen fold.
  assert.equal(await page.locator('.live .thought-line-step').count(),1,'completed steps are visible alongside the current tool');
  assert.match(await page.locator('.live .thought-line-step').first().textContent(),/README.md/);
  await page.locator('.live').getByRole('button',{name:'收起执行过程',exact:true}).click();
  assert.equal(await page.locator('.live .thought-line__trace').getAttribute('aria-hidden'),'true');
  tools[1].elapsed_ms=2500;sequence++;changed=true;await page.waitForTimeout(350);
  assert.equal(await page.locator('.live .thought-line__trace').getAttribute('aria-hidden'),'true');
  await page.locator('.live').getByRole('button',{name:'展开执行过程',exact:true}).click();
  assert.equal(await page.locator('.live .active-tool').isVisible(),true);
  // Horizontal gesture reveals actions without navigating or issuing a command.
  const box=await child.boundingBox();await page.mouse.move(box.x+box.width-45,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width-120,box.y+box.height/2,{steps:12});await page.mouse.up();
  await page.waitForTimeout(350);
  assert.equal(await main.getAttribute('aria-current'),'page');
  assert.equal(commands.filter(c=>c?.name==='branch').length,0);
  const archive=page.getByRole('button',{name:'归档会话 界面探索',exact:true});await archive.click();
  await child.waitFor({state:'detached'});
  assert.ok(commands.some(c=>c?.name==='branch'&&c.payload.branch_id==='child'&&c.payload.archived===true));
  assert.equal(await main.getAttribute('aria-current'),'page');
  // Vertical scrolling and pointer cancellation never commit a full-swipe action.
  branches.push({...branches[1],id:'full',name:'滑动边界',archived:false});sequence++;changed=true;
  const full=page.locator('.session[data-branch-id="full"]');await full.waitFor();
  const swipeBox=await full.boundingBox(),y=swipeBox.y+swipeBox.height/2;
  await page.mouse.move(swipeBox.x+60,y);await page.mouse.down();await page.mouse.move(swipeBox.x+62,y+32,{steps:8});await page.mouse.up();
  const beforeCancel=commands.filter(c=>c?.name==='branch').length;
  await page.mouse.move(swipeBox.x+swipeBox.width-45,y);await page.mouse.down();await page.mouse.move(swipeBox.x+5,y,{steps:12});
  assert.equal(await page.locator('.swipe-dragging').count(),1);
  await full.evaluate(row=>row.parentElement.dispatchEvent(new PointerEvent('pointercancel',{pointerId:1,bubbles:true})));
  await page.mouse.up();assert.equal(commands.filter(c=>c?.name==='branch').length,beforeCancel);
  await full.evaluate(row=>new Promise(resolve=>{const settled=()=>{if(Math.abs(new DOMMatrix(getComputedStyle(row.parentElement).transform).e)<.5)resolve();else requestAnimationFrame(settled);};settled();}));
  await page.mouse.move(swipeBox.x+swipeBox.width-45,y);await page.mouse.down();await page.mouse.move(swipeBox.x-40,y,{steps:12});await page.mouse.up();await full.waitFor({state:'detached'});
  assert.ok(commands.some(c=>c?.name==='branch'&&c.payload.branch_id==='full'&&c.payload.archived===true));
  assert.equal(await main.getAttribute('aria-current'),'page');
  // Keyboard segmented navigation switches the actual view and keeps its thumb aligned.
  await page.getByRole('button',{name:'聊天',exact:true}).focus();await page.keyboard.press('End');
  await page.locator('.trace-page').waitFor();
  const selected=page.locator('.view-tab.active');assert.equal(await selected.textContent(),'轨迹');
  await page.setViewportSize({width:760,height:700});await page.waitForTimeout(200);
  await page.waitForFunction(()=>{const a=document.querySelector('.view-tab.active').getBoundingClientRect(),b=document.querySelector('.rubber-segment-thumb').getBoundingClientRect();return Math.abs(a.x-b.x)<2&&Math.abs(a.width-b.width)<2;});
  const a=await selected.boundingBox(),b=await page.locator('.rubber-segment-thumb').boundingBox();assert.ok(Math.abs(a.x-b.x)<2&&Math.abs(a.width-b.width)<2,'thumb follows selected view after resize');
  if(await page.locator('#sidebar-backdrop').isVisible())await page.locator('#sidebar-backdrop').click();
  await page.getByRole('button',{name:'聊天',exact:true}).click();
  await page.locator('#composer').fill('保留真实草稿');
  await page.setViewportSize({width:390,height:760});
  assert.equal(await page.locator('#composer').inputValue(),'保留真实草稿');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  branches[0].permission_mode='bypassPermissions';sequence++;changed=true;
  await page.waitForFunction(()=>document.querySelector('#permission-mode').dataset.value==='bypassPermissions');
  await page.setViewportSize({width:320,height:760});
  assert.equal(await page.locator('#permission-mode').evaluate(control=>{const box=control.getBoundingClientRect();return [...control.children].filter(child=>getComputedStyle(child).display!=='none').every(child=>{const rect=child.getBoundingClientRect();return rect.left>=box.left-.5&&rect.right<=box.right+.5;});}),true,'compact permission icons stay inside their own hit target');
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.locator('.live .thought-line__pulse').evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.setViewportSize({width:1168,height:795});await page.screenshot({path:'/tmp/ignovate-react-bits-light.png'});
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');await page.screenshot({path:'/tmp/ignovate-react-bits-dark.png'});
  assert.deepEqual(errors,[]);console.log('React Bits: warm hints, live fold persistence, swipe archive identity, keyboard views, resize, narrow composer and reduced motion passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
