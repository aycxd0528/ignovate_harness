const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:960}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const nodes=Array.from({length:500},(_,i)=>({id:'n'+i,branch_id:i<471?'root':'b'+(i-470),parent_id:i?i<471?'n'+(i-1):'n100':null,
    prompt:'需求 '+i,answer:'这是规模测试中的回答正文，保留来源和当前路径。',status:'completed',recoverable:false,checkpoint_id:null,
    tools:[],usage:{input_tokens:12,output_tokens:8,cost_usd:null,complete:true},delivery:{status:'unverified'}}));
  const common=nodes.slice(0,101).map(n=>n.id);
  const branches=[{id:'root',name:'主分支',node_ids:nodes.slice(0,471).map(n=>n.id),head:'n470',is_default:1},
    ...Array.from({length:29},(_,i)=>({id:'b'+(i+1),name:'分支 '+(i+1),node_ids:[...common,'n'+(471+i)],head:'n'+(471+i),parent_id:'root'}))]
    .map(b=>({...b,thread_id:b.id,conversation_id:'conversation',recoverable:false,archived:0,draft:'',summary:''}));
  const snapshot={schema_version:1,sequence:0,project:{id:'scale',path:'/scale-fixture',name:'500 节点 / 30 分支'},branches,nodes,
    runs:[],approvals:[],paused:false,configuration:{configured:true,model:'offline',reasoning_effort:'default',permission_mode:'default'},
    layout:{revision:0,positions:{},collapsed:[],viewport:{x:40,y:40,zoom:1}}};
  await page.route('**/api/snapshot',route=>route.fulfill({json:snapshot}));
  await page.route('**/api/events?**',route=>route.fulfill({json:{sequence:0,events:[],reset:false}}));
  await page.route('**/api/command',async route=>{const value=route.request().postDataJSON();
    if(value.name==='layout'){snapshot.layout={...value.payload.layout,revision:snapshot.layout.revision+1};await route.fulfill({json:snapshot.layout});}
    else await route.fulfill({json:{}});
  });
  try {
    await page.goto('http://127.0.0.1:8767/#token=browser-test-only');
    await page.locator('.session').nth(29).waitFor();
    assert.equal(await page.locator('.turn').count(),80,'chat initially renders a bounded page');
    const measurements=[];
    for(let i=0;i<20;i++)measurements.push(await page.evaluate(async index=>{
      const started=performance.now();document.querySelectorAll('.session')[index%30].click();
      await new Promise(requestAnimationFrame);return performance.now()-started;
    },i+1));
    await page.getByLabel('查找当前对话').fill('需求 50');
    await page.waitForFunction(()=>document.querySelectorAll('.turn').length===1);
    assert.equal(await page.locator('.turn').count(),1);
    await page.getByRole('button',{name:'会话地图',exact:true}).click();
    assert.equal(await page.locator('.map-card').count(),500);
    await page.getByRole('button',{name:'适应视图',exact:true}).click();
    await page.locator('.map-card').first().getByRole('button',{name:'折叠',exact:true}).click();
    assert.equal(await page.locator('.map-card').count(),1);
    await page.locator('.map-card').first().getByRole('button',{name:'展开',exact:true}).click();
    assert.equal(await page.locator('.map-card').count(),500);
    await page.locator('.map-handle').first().focus();
    await page.keyboard.press('ArrowRight');
    await page.getByRole('button',{name:'主题',exact:true}).click();
    await page.getByRole('button',{name:'主题',exact:true}).click();
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
    await page.getByRole('button',{name:'聊天',exact:true}).click();
    await page.locator('#composer').dispatchEvent('keydown',{key:'Enter',isComposing:true});
    const sorted=measurements.sort((a,b)=>a-b),p95=sorted[Math.ceil(sorted.length*.95)-1];
    const result={nodes:500,branches:30,switch_p95_ms:p95,js_heap_bytes:await page.evaluate(()=>performance.memory?.usedJSHeapSize||null)};
    assert.deepEqual(errors,[]);
    fs.writeFileSync('/tmp/ignovate-gui-scale.json',JSON.stringify(result,null,2));
    assert.ok(p95<=300,'loaded branch switching should meet the PRD provisional 300ms budget');
    console.log('GUI scale acceptance:',result);
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
