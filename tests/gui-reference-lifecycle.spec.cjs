const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const page=await browser.newPage({viewport:{width:1168,height:793},colorScheme:'light'});
 const root=path.resolve(__dirname,'../nailong/gui/static');
 const branch={id:'b',conversation_id:'c',name:'新会话',parent_id:null,is_default:true,archived:false,draft:'',recoverable:true,node_ids:[],head:null,permission_mode:'default'};
 let effort='default';const writes=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 const snapshot=()=>({sequence:0,project:{id:'p',name:'Workspace',path:'/tmp/lifecycle'},configuration:{configured:true,model:'deepseek-flash',reasoning_effort:effort,reasoning_options:[['default','模型默认'],['off','关闭推理'],['low','低'],['high','高'],['max','最高']]},branches:[branch],nodes:[],runs:[],approvals:[],paused:false,layout:{revision:0,positions:{},collapsed:[],viewport:{x:40,y:40,zoom:1}}});
 try{
  await page.route('http://lifecycle.invalid/**',async route=>{
   const url=new URL(route.request().url()),respond=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(url.pathname==='/api/snapshot')return respond(snapshot());
   if(url.pathname==='/api/events')return respond({sequence:0,events:[]});
   if(url.pathname==='/api/command'){const body=route.request().postDataJSON();if(body.name==='reasoning'){writes.push(body.payload.effort);await new Promise(resolve=>setTimeout(resolve,100));effort=body.payload.effort;return respond({reasoning_effort:effort});}return respond({});}
   const file=url.pathname==='/'?'index.html':url.pathname.slice(1);return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,file))});
  });
  await page.goto('http://lifecycle.invalid/#token=test');await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.getByPlaceholder('从一个新问题开始…').fill('保留未发送的地图草稿');
  await page.reload();await page.getByPlaceholder('从一个新问题开始…').waitFor();assert.equal(await page.getByPlaceholder('从一个新问题开始…').inputValue(),'保留未发送的地图草稿');
  await page.getByRole('button',{name:'聊天',exact:true}).click();await page.getByRole('button',{name:'推理等级',exact:true}).click();const slider=page.getByRole('slider',{name:'推理等级',exact:true});await slider.press('End');await page.waitForFunction(()=>document.querySelector('#reasoning-control').textContent.includes('最高')&&!document.querySelector('#reasoning-control').disabled);assert.deepEqual(writes,['max'],'a keyboard gesture must perform one native write');
  await page.emulateMedia({reducedMotion:'reduce'});await page.screenshot({path:'/tmp/ignovate-gui-effort-reduced-motion.png'});await slider.press('Escape');assert.equal(await page.getByRole('dialog',{name:'推理等级',exact:true}).count(),0,'closing the panel removes its dialog');
  for(let index=0;index<4;index++){await page.getByRole('button',{name:'推理等级',exact:true}).click();await slider.press('Escape');}
  assert.equal(await page.locator('.prompt-bar__sparks').count(),1,'reopening the panel must not accumulate particle canvases');
  assert.deepEqual(errors,[]);console.log('Map draft reload, serialized effort gesture and closed/reduced-motion renderer lifecycle passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
