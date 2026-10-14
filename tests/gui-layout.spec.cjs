const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:960}});
  const root=path.resolve(__dirname,'../nailong/gui/static');
  let layout={revision:0,positions:{n1:{x:0,y:0}},collapsed:[],viewport:{x:40,y:40,zoom:1}};
  const branch={id:'b1',conversation_id:'c1',name:'布局测试',parent_id:null,is_default:true,archived:false,draft:'',recoverable:true,node_ids:['n1'],head:'n1',permission_mode:'default'};
  const node={id:'n1',parent_id:null,branch_id:'b1',prompt:'测试问题',answer:'测试回答',tools:[],status:'completed',recoverable:true,usage:{input_tokens:10,output_tokens:10,complete:true,cost_usd:null},delivery:{status:'unknown'}};
  const snapshot=()=>({schema_version:1,sequence:0,project:{id:'p1',name:'布局测试',path:'/tmp/layout-test'},configuration:{configured:true,model:'fake',reasoning_effort:'default',reasoning_options:[['default','default']]},branches:[branch],nodes:[node],runs:[],approvals:[],paused:false,layouts:{c1:layout}});
  const saves=[],errors=[];let notifyFirst;
  const first=new Promise(resolve=>notifyFirst=resolve);
  page.on('pageerror',error=>errors.push(error.message));
  try {
    // Serve the actual UI with an isolated, delayed backend. No user data or API.
    await page.route('http://layout.invalid/**',async route=>{
      const url=new URL(route.request().url());
      const answer=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
      if(url.pathname==='/api/snapshot')return answer(snapshot());
      if(url.pathname==='/api/events')return answer({sequence:0,reset:false,events:[]});
      if(url.pathname==='/api/command'){
        const body=route.request().postDataJSON();
        if(body.name==='layout'){
          const payload=body.payload;saves.push({revision:payload.revision,x:payload.layout.positions.n1.x});
          if(payload.revision!==layout.revision)return answer({error:'地图布局版本冲突，请重新加载。'},400);
          layout={...payload.layout,revision:layout.revision+1};const saved=structuredClone(layout);
          if(saves.length===1){notifyFirst();await new Promise(resolve=>setTimeout(resolve,800));}
          return answer(saved);
        }
        return answer({});
      }
      const filename=url.pathname==='/'?'index.html':url.pathname.slice(1);
      return route.fulfill({contentType:filename.endsWith('.js')?'text/javascript':filename.endsWith('.css')?'text/css':'text/html',body:fs.readFileSync(path.join(root,filename))});
    });
    await page.goto('http://layout.invalid/#token=test');await page.locator('.session').waitFor();
    await page.locator('.session').focus();await page.keyboard.press('Shift+F10');
    await page.getByRole('menuitem',{name:'重命名',exact:true}).waitFor();await page.keyboard.press('Escape');
    assert.equal(await page.locator('#branch-menu').count(),0);
    const focusRestored=await page.locator('.session').evaluate(element=>element===document.activeElement);
    await page.getByRole('button',{name:'会话地图',exact:true}).click();
    await page.locator('.map-handle').focus();await page.keyboard.press('ArrowRight');await first;
    await page.keyboard.press('ArrowRight');await page.waitForTimeout(1400);
    assert.equal(layout.positions.n1.x,32,'latest move must persist after the previous response');
    assert.deepEqual(saves,[{revision:0,x:16},{revision:1,x:32}]);
    assert.equal(await page.locator('.map-card').evaluate(element=>element.style.left),'32px');
    assert.equal(focusRestored,true,'Escape must restore focus to the invoking session');
    assert.deepEqual(errors,[]);
    console.log('Map save serialization and context menu keyboard focus passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
