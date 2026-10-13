const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const page=await browser.newPage();
  const failures=[];page.on('console',message=>{if(message.type()==='error')failures.push(message.text());});
  try {
    await page.goto(pathToFileURL(path.resolve(__dirname,'../nailong/gui/static/index.html')).href);
    const body=await page.locator('body').innerText();
    console.log('Local file entry:',body.trim());
    console.log('Resource errors:',failures.map(message=>message.slice(0,220)));
    assert.ok(body.includes('需要启动本地服务'),'opening the HTML file must explain how to start the backend');
    assert.ok(body.includes('python -m nailong.gui'),'the page should show an actionable startup command');
    assert.ok(!body.includes('正在连接 Ignovate Harness'),'unsupported file entry must not hang on a connection spinner');
    assert.deepEqual(failures,[],'file entry should load its guidance without broken module or stylesheet requests');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
