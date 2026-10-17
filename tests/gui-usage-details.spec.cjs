const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../nailong/gui/static');
const chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:fs.existsSync(chrome)?chrome:undefined});
  const page=await browser.newPage({viewport:{width:1100,height:850}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const actual={input_tokens:100,output_tokens:10,cache_hit_tokens:80,total_tokens:110,calls:1,usage_complete:true};
  const estimate={method:'estimated',estimated_tokens:9000,categories:{base_system:{tokens:4000},tool_definitions:{tokens:5000}}};
  const detail={id:'n',thread_id:'thread',branch_id:'b',parent_id:null,prompt:'你好',answer:'你好。',tools:[],status:'completed',recoverable:true,
    usage:{...actual,complete:true,cost_usd:null},usage_details:{last_turn:{actual,estimate},cumulative:actual},
    session_usage:{...actual,input_tokens:350,total_tokens:380},delivery:{status:'unverified'},events:[],artifacts:[],timing:{elapsed_ms:1000}};
  const branch={id:'b',conversation_id:'c',name:'输入统计',parent_id:null,is_default:true,archived:false,draft:'',recoverable:true,node_ids:['n'],head:'n',permission_mode:'default'};
  const snapshot={sequence:0,project:{id:'p',name:'统计测试',path:'/tmp/offline-usage'},configuration:{configured:true,model:'fake',reasoning_effort:'default',reasoning_options:[['default','默认']]},branches:[branch],nodes:[detail],runs:[],approvals:[],paused:false,layouts:{c:{revision:0,positions:{},collapsed:[],viewport:{x:40,y:40,zoom:1}}}};
  try{
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!=='http://usage.invalid')return route.abort();
      const answer=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      if(url.pathname==='/api/snapshot')return answer(snapshot);
      if(url.pathname==='/api/events')return answer({sequence:0,reset:false,events:[]});
      if(url.pathname==='/api/command')return answer(route.request().postDataJSON().name==='node'?detail:{});
      if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html data-theme="light"><head><link rel="stylesheet" href="/style.css"></head><body><aside id="inspector"></aside></body></html>'});
      const file=path.join(root,url.pathname==='/workbench'?'index.html':url.pathname.slice(1));
      if(!fs.existsSync(file))return route.fulfill({status:404,body:'Missing fixture asset'});
      return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'application/octet-stream',body:fs.readFileSync(file)});
    });
    await page.goto('http://usage.invalid/');
    await page.evaluate(async data=>{
      window.renderUsageDetails=(await import('/usage-details.js')).renderUsageDetails;
      document.querySelector('#inspector').replaceChildren(window.renderUsageDetails(data));
    },detail);
    const component=page.locator('.usage-breakdown');
    await component.locator('summary').focus();await page.keyboard.press('Space');
    assert.equal(await component.getAttribute('open'),'','native disclosure must be keyboard operable');
    const text=await component.innerText();
    for(const label of ['本轮实际输入','其中缓存命中','本轮实际输出','本轮合计','当前会话累计','分类估算','系统','工具定义'])assert.ok(text.includes(label),label);
    assert.ok(text.includes('9000'));assert.ok(text.includes('380'));assert.ok(text.includes('不是实际分项计费'));
    await page.evaluate(data=>{
      data.usage_details.last_turn.actual.usage_complete=false;
      data.usage_details.last_turn.estimate.categories['<img src=x onerror="window.injected=true">']={tokens:1};
      document.querySelector('#inspector').replaceChildren(window.renderUsageDetails(data));
    },detail);
    assert.ok((await page.locator('.usage-breakdown').textContent()).includes('部分调用用量未知'));
    assert.equal(await component.locator('img,script').count(),0);
    await page.goto('http://usage.invalid/workbench#token=test');
    await page.locator('.session').waitFor();
    await page.getByRole('button',{name:'详情',exact:true}).click();
    await page.locator('#inspector .usage-breakdown').waitFor();
    await page.locator('#inspector .usage-breakdown > summary').click();
    for(const theme of ['light','dark']){
      await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.locator('body').evaluate(body=>body.scrollWidth<=innerWidth),true,'usage details must fit narrow screens');
    }
    assert.deepEqual(errors,[]);
    console.log('Actual/estimate/cache/cumulative, unknown usage, keyboard, injection, real inspector and narrow themes passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
