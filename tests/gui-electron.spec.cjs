const {_electron:electron}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
(async()=>{
  const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ignovate-desktop-test-'));
  const project=path.join(temp,'Electron 验证项目');fs.mkdirSync(project);
  let app;
  try {
    app=await electron.launch({executablePath:require('../desktop/node_modules/electron'),args:[path.join(root,'desktop'),'--project',project],
      env:{...process.env,IGNOVATE_PYTHON:path.join(root,'.venv/bin/python'),NAILONG_DATA_DIR:path.join(temp,'data')}});
    const page=await app.firstWindow();await page.locator('#project-name').filter({hasText:'Electron 验证项目'}).waitFor();
    const bridge=await page.evaluate(()=>({choose:typeof window.ignovateDesktop.chooseProject,node:typeof require}));
    assert.deepEqual(bridge,{choose:'function',node:'undefined'});
    const options=await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
    assert.equal(options.sandbox,true);assert.equal(options.contextIsolation,true);assert.equal(options.nodeIntegration,false);
    await page.screenshot({path:'/tmp/ignovate-gui-electron.png'});
    console.log('Electron smoke passed: local window, sandbox, context isolation, bounded bridge.');
  }finally{if(app)await app.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
