const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const path=require('node:path');
const {once}=require('node:events');

(async()=>{
 const root=path.resolve(__dirname,'..');
 const server=spawn(process.env.GUI_TEST_PYTHON||path.join(root,'.venv/bin/python'),['-u','-c',`
import json, signal, tempfile, threading
from pathlib import Path
from types import SimpleNamespace
from nailong.core.sessions import ProjectSessionStore
from nailong.gui.controller import GUIController
from nailong.gui.server import GUIServer
signal.signal(signal.SIGTERM, lambda *_: exit(0))
with tempfile.TemporaryDirectory() as temporary:
    project = Path(temporary) / 'project'
    project.mkdir()
    def controller_factory(project):
        sessions = ProjectSessionStore(project, base_dir=Path(temporary)/'data')
        service = SimpleNamespace(task_store=None, permission_mode='default', runtime_factory=SimpleNamespace(settings=SimpleNamespace(model='offline', api_base='https://example.test', api_key='test-only')))
        controller = GUIController(project, sessions=sessions, service=service)
        if project.name == 'empty-project': return controller
        for identity in ('history','blank-a','blank-b','draft','map-draft'):
            controller.branches.create('已有会话' if identity=='history' else '新会话', identity=identity)
        sessions.append_event('history','user',{'text':'已经发过的问题'})
        sessions.append_event('history','final',{'text':'已有回答'})
        controller.branches.draft('draft','已有未发送草稿')
        controller._sync_history()
        return controller
    server = GUIServer(project, token='empty-conversation-test', controller_factory=controller_factory)
    empty_project = Path(temporary) / 'empty-project'
    empty_project.mkdir()
    empty_server = GUIServer(empty_project, token='empty-conversation-test', controller_factory=controller_factory)
    threading.Thread(target=empty_server.serve_forever, daemon=True).start()
    print(json.dumps({'origin':server.origin,'emptyOrigin':empty_server.origin,'projectId':server.controller.sessions.root.name}), flush=True)
    try: server.serve_forever()
    finally:
        server.server_close()
        empty_server.shutdown()
        empty_server.server_close()
`],{cwd:root,stdio:['ignore','pipe','pipe']});
 let stderr='';server.stderr.on('data',chunk=>{stderr+=chunk;});
 let browser;
 try{
  const config=await new Promise((resolve,reject)=>{
   let output='';const timeout=setTimeout(()=>reject(Error('Fixture did not start: '+stderr)),10000);
   server.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n')){clearTimeout(timeout);resolve(JSON.parse(output.split('\n')[0]));}});
   server.on('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code+': '+stderr));});
  });
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage({viewport:{width:1168,height:795}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(({projectId})=>{
   if(!localStorage.getItem('ignovate-branch-'+projectId))localStorage.setItem('ignovate-branch-'+projectId,'blank-a');
   localStorage.setItem('ignovate-map-draft-'+projectId+':map-draft',JSON.stringify({kind:'root',branchId:'map-draft',text:'地图中未发送的问题'}));
  },config);
  const snapshot=()=>page.evaluate(async()=>{
   const r=await fetch('/api/snapshot',{headers:{Authorization:'Bearer empty-conversation-test'}});return r.json();
  });
  const waitForSnapshot=async predicate=>{
   const deadline=Date.now()+5000;
   while(Date.now()<deadline){
    if(predicate(await snapshot()))return;
    await new Promise(resolve=>setTimeout(resolve,25));
   }
   assert.fail('Expected conversation state was not persisted');
  };
  const waitForDraft=text=>waitForSnapshot(value=>value.branches.some(b=>b.draft===text));
  let startupBusy=0;
  await page.route('**/api/command',async route=>{
   const {name}=route.request().postDataJSON();
   if(name==='prune_empty'&&!startupBusy++)return route.fulfill({contentType:'application/json',body:JSON.stringify({removed:[],retry:true})});
   return route.continue();
  });
  await page.goto(config.origin+'/#token=empty-conversation-test');
  await page.getByText('已有回答',{exact:true}).waitFor({timeout:4000});
  await page.unroute('**/api/command');
  await page.waitForFunction(()=>!document.querySelector('.session[data-branch-id="blank-a"]'),{},{timeout:4000});
  assert.deepEqual((await snapshot()).branches.map(b=>b.id).sort(),['draft','history','map-draft']);
  await page.locator('.new-conversation').click();
  const current=()=>page.locator('.session[aria-current="page"]').getAttribute('data-branch-id');
  await page.waitForFunction(()=>document.querySelectorAll('.session').length===4);
  let blankId=await current();
  await page.locator('.new-conversation').click();
  assert.equal(await current(),blankId,'repeated New Chat reuses the blank window');
  assert.equal((await snapshot()).branches.length,4);
  let busyReplies=0;
  await page.route('**/api/command',async route=>{
   const {name}=route.request().postDataJSON();
   if(name==='prune_empty'&&!busyReplies++)return route.fulfill({contentType:'application/json',body:JSON.stringify({removed:[],retry:true})});
   return route.continue();
  });
  await page.locator('.session[data-branch-id="history"]').click();
  await waitForSnapshot(value=>!value.branches.some(b=>b.id===blankId));
  await page.unroute('**/api/command');
  await page.locator('.new-conversation').click();
  await page.waitForFunction(()=>document.querySelectorAll('.session').length===4);
  blankId=await current();
  await page.locator('#composer').fill('刚刚输入的草稿');
  await page.locator('.new-conversation').click();
  await page.waitForFunction(old=>document.querySelector('.session[aria-current="page"]')?.dataset.branchId!==old,blankId);
  assert.equal((await snapshot()).branches.find(b=>b.id===blankId).draft,'刚刚输入的草稿');
  const discardedId=await current();
  await page.locator('.session[data-branch-id="history"]').click();
  await page.waitForFunction(id=>!document.querySelector('.session[data-branch-id="'+id+'"]'),discardedId);
  assert.equal((await snapshot()).branches.some(b=>b.id===discardedId),false);
  await page.reload();await page.getByText('已有回答',{exact:true}).waitFor();
  assert.equal((await snapshot()).branches.length,4,'discarded placeholders stay gone after reload');
  await page.locator('.new-conversation').click();
  await page.waitForFunction(()=>document.querySelectorAll('.session').length===5);
  const emptyAtShutdown=await current();
  await page.reload();await page.getByText('已有回答',{exact:true}).waitFor();
  await page.waitForFunction(id=>!document.querySelector('.session[data-branch-id="'+id+'"]'),emptyAtShutdown);
  assert.equal((await snapshot()).branches.some(b=>b.id===emptyAtShutdown),false,'reopening does not retain the old active blank');
  // Typing while the old draft is being flushed belongs to the new conversation.
  await page.locator('.session[data-branch-id="draft"]').click();
  let releaseDraft,markHeld;
  const held=new Promise(resolve=>{markHeld=resolve;}),gate=new Promise(resolve=>{releaseDraft=resolve;});
  await page.route('**/api/command',async route=>{
   const {name,payload}=route.request().postDataJSON();
   if(name==='draft'&&payload.branch_id==='draft'){markHeld();await gate;}
   await route.continue();
  });
  await page.locator('#composer').fill('旧草稿待保存');
  await page.locator('.new-conversation').click();await held;
  await page.locator('#composer').fill('保存期间新输入的问题');releaseDraft();
  await page.waitForFunction(()=>document.querySelector('.session[aria-current="page"]')?.dataset.branchId!=='draft');
  assert.equal(await page.locator('#composer').inputValue(),'保存期间新输入的问题');
  await waitForDraft('保存期间新输入的问题');
  assert.equal((await snapshot()).branches.find(b=>b.id==='draft').draft,'旧草稿待保存');
  await page.unroute('**/api/command');
  await page.goto(config.emptyOrigin+'/#token=empty-conversation-test');
  await page.locator('#startup-screen').waitFor({state:'detached'});
  assert.equal((await snapshot()).branches.length,0,'an unused workspace has no saved blank');
  await page.locator('#composer').fill('第一个未发送的草稿');
  await page.waitForFunction(()=>document.querySelectorAll('.session').length===1,{},{timeout:4000});
  await waitForDraft('第一个未发送的草稿');
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('#composer')?.value==='第一个未发送的草稿',{},{timeout:4000});
  assert.equal(await page.locator('#composer').inputValue(),'第一个未发送的草稿');
  assert.deepEqual(errors,[]);
  console.log('Empty conversations: real server cleanup, reuse, chat/map draft protection, navigation and reload passed.');
 }finally{
  if(browser)await browser.close();
  if(server.exitCode===null){server.kill('SIGTERM');await once(server,'exit');}
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
