const {chromium}=require('playwright');const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),page=await browser.newPage({viewport:{width:1168,height:793}});
 const root=path.resolve(__dirname,'../nailong/gui/static'),branch={id:'b',conversation_id:'c',name:'Test',parent_id:null,is_default:true,archived:false,draft:'',recoverable:true,node_ids:[],head:null,permission_mode:'default'};
 let effort='default',release,started;const gate=new Promise(resolve=>release=resolve),begun=new Promise(resolve=>started=resolve),writes=[];
 const snapshot=()=>({sequence:0,project:{id:'p',name:'Effort',path:'/tmp/effort'},configuration:{configured:true,model:'fake',reasoning_effort:effort,reasoning_options:[['default','默认'],['off','关闭'],['low','低'],['high','高'],['max','最高']]},branches:[branch],nodes:[],runs:[],approvals:[],paused:false,layout:{positions:{},collapsed:[],viewport:{}}});
 try{await page.route('http://effort.invalid/**',async route=>{const url=new URL(route.request().url()),reply=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});if(url.pathname==='/api/snapshot')return reply(snapshot());if(url.pathname==='/api/events')return reply({sequence:0,events:[]});
 if(url.pathname==='/api/command'){const body=route.request().postDataJSON();if(body.name==='reasoning'){effort=body.payload.effort;writes.push(effort);if(writes.length===1){started();await gate;}return reply({reasoning_effort:effort});}return reply({});}
 const file=url.pathname==='/'?'index.html':url.pathname.slice(1);return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,file))});});
 await page.goto('http://effort.invalid/#token=test');await page.locator('#project-name').filter({hasText:'Effort'}).waitFor();await page.getByRole('button',{name:'推理等级',exact:true}).click();const slider=page.getByRole('slider',{name:'推理等级',exact:true});await slider.press('End');await begun;
 assert.equal(await slider.getAttribute('aria-disabled'),'true','an in-flight save serializes configuration changes');
 await slider.press('Home');assert.deepEqual(writes,['max'],'a pending save cannot be overwritten by another gesture');
 await page.locator('#composer').click();assert.equal(await page.getByRole('dialog',{name:'推理等级',exact:true}).count(),0);
 release();await page.waitForFunction(()=>!document.querySelector('#reasoning-control').disabled);
 await page.getByRole('button',{name:'推理等级',exact:true}).click();await slider.press('ArrowLeft');await page.waitForFunction(async()=>{const s=await(await fetch('/api/snapshot')).json();return s.configuration.reasoning_effort==='high'});
 assert.equal(effort,'high','reopening the panel must use the latest saved choice');assert.deepEqual(writes,['max','high']);console.log('PromptBar serializes saves and reopens with the authoritative reasoning choice.');
 }finally{release();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
