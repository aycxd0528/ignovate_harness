const {_electron:electron}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
(async()=>{
 const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ignovate-copy-test-'));
 let app;
 try{
  app=await electron.launch({executablePath:process.env.GUI_ELECTRON||require('../desktop/node_modules/electron'),args:[path.join(root,'desktop'),'--project',temp],env:{...process.env,IGNOVATE_PYTHON:process.env.IGNOVATE_PYTHON||path.join(root,'.venv/bin/python'),NAILONG_DATA_DIR:path.join(temp,'data')}});
  await app.evaluate(async({clipboard})=>{global.copyTestOriginal=await clipboard.read();});
  const page=await app.firstWindow();await page.locator('#project-name').waitFor();
  const answer='中文回答 **格式**\n\n```python\nprint("复制测试")\n```',prompt='请介绍这个项目';
  await page.evaluate(async({answer,prompt})=>{
   for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
   const {renderTurn}=await import('./conversation.js');
   const node=renderTurn({id:'copy-fixture',prompt,answer,status:'completed',tools:[],usage:{},timing:{elapsed_ms:1000}});node.id='copy-fixture';document.querySelector('#content').replaceChildren(node);
  },{answer,prompt});
  // Keep the desktop's real deny-by-default Chromium permission policy.
  // Reading via Electron verifies the actual OS clipboard, not an API mock.
  const check=async(control,expected)=>{
   await app.evaluate(({clipboard})=>clipboard.writeText('copy-test-sentinel'));
   await control.click();let actual;
   for(let n=0;n<40;n++){actual=await app.evaluate(({clipboard})=>clipboard.readText());if(actual===expected)break;await page.waitForTimeout(25);}
   assert.equal(actual,expected,'a copy button writes its full current text to the native clipboard');
  };
  const turn=page.locator('#copy-fixture');
  await check(turn.locator('.turn-actions').getByRole('button',{name:'复制',exact:true}),answer);
  await check(turn.getByRole('button',{name:'复制输入',exact:true}),prompt);
  await check(turn.getByRole('button',{name:'复制代码',exact:true}),'print("复制测试")\n');
  assert.equal(await page.locator('#toast').textContent(),'已复制');
  await assert.rejects(()=>page.evaluate(()=>window.ignovateDesktop.writeClipboardText(42)),/Invalid clipboard text/);
  assert.equal(await app.evaluate(({clipboard})=>clipboard.readText()),'print("复制测试")\n');
  console.log('Desktop answer/input/code copy writes the native clipboard with browser permissions denied; invalid payload is rejected.');
 }finally{
  if(app){await app.evaluate(async({clipboard})=>{if(global.copyTestOriginal?.length)await clipboard.write(global.copyTestOriginal);else clipboard.clear();}).catch(()=>{});await app.close();}
  fs.rmSync(temp,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
