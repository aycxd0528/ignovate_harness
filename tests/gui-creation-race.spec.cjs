const {chromium}=require('playwright');const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),page=await browser.newPage();
 const root=path.resolve(__dirname,'../nailong/gui/static'),branches=[{id:'old',conversation_id:'old-c',name:'OLD CONVERSATION',parent_id:null,is_default:true,archived:false,draft:'',recoverable:true,node_ids:['n'],head:'n',permission_mode:'default'}],node={id:'n',parent_id:null,branch_id:'old',prompt:'OLD QUESTION',answer:'old answer',tools:[],status:'completed',recoverable:true,usage:{},delivery:{}};
 let release,started;const gate=new Promise(resolve=>release=resolve),begun=new Promise(resolve=>started=resolve);
 const snapshot=()=>({sequence:0,project:{id:'p',name:'Race',path:'/tmp/race'},configuration:{configured:true,model:'fake',reasoning_effort:'default',reasoning_options:[['default','默认']]},branches,nodes:[node],runs:[],approvals:[],paused:false,layout:{revision:0,positions:{},collapsed:[],viewport:{}}});
 try{await page.route('http://race.invalid/**',async route=>{const url=new URL(route.request().url()),reply=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
 if(url.pathname==='/api/snapshot')return reply(snapshot());if(url.pathname==='/api/events')return reply({sequence:0,events:[]});
 if(url.pathname==='/api/command'){const body=route.request().postDataJSON();if(body.name==='create'){started();await gate;const next={...branches[0],id:'new',conversation_id:'new-c',name:'新会话',node_ids:[],head:null};branches.push(next);return reply(next);}return reply({});}
 const file=url.pathname==='/'?'index.html':url.pathname.slice(1);return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,file))});});
 await page.goto('http://race.invalid/#token=test');await page.locator('#workspace-title').filter({hasText:'OLD CONVERSATION'}).waitFor();await page.getByRole('button',{name:'新会话',exact:true}).click();await begun;
 await page.getByRole('button',{name:'会话地图',exact:true}).click();await page.waitForTimeout(100);assert.equal(await page.locator('.map-card').count(),0,'pending new conversation must not expose old map actions');
 await page.locator('#composer').fill('new conversation only');release();await page.getByPlaceholder('从一个新问题开始…').waitFor();assert.equal(await page.locator('#workspace-title strong').textContent(),'新会话');assert.equal(branches[0].draft,'');console.log('Existing-conversation create/navigation boundary passed.');
 }finally{release();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
