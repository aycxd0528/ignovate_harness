const {_electron:electron}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {execFileSync}=require('node:child_process');

(async()=>{
  assert.equal(process.platform,'win32','Run the installed Windows smoke test on Windows');
  const source=process.env.IGNOVATE_APP;
  assert.ok(source,'Set IGNOVATE_APP to the Ignovate-win32-x64 directory');
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ignovate-installed-smoke-'));
  let app;
  try{
    const relocated=path.join(temp,'应用程序 空格','Ignovate');
    const project=path.join(temp,'项目 空格'),home=path.join(temp,'home');
    fs.cpSync(source,relocated,{recursive:true});fs.mkdirSync(project);fs.mkdirSync(home);
    fs.writeFileSync(path.join(project,'README.md'),'# 安装版验证\n');
    const resources=path.join(relocated,'resources'),python=path.join(resources,'runtime','python.exe');
    const env={...process.env,
      PATH:path.join(process.env.SystemRoot,'System32'),PYTHONPATH:path.join(temp,'invalid-python-path'),
      IGNOVATE_CONFIG_DIR:path.join(temp,'config'),NAILONG_DATA_DIR:path.join(temp,'data')};
    for(const name of ['IGNOVATE_PYTHON','DEEPSEEK_API_KEY','DEEPSEEK_API_BASE','DEEPSEEK_MODEL','VIRTUAL_ENV','PYTHONHOME','ELECTRON_RUN_AS_NODE'])delete env[name];
    const metadata=JSON.parse(execFileSync(python,['-I','-B','-c',
      'import sys,json,importlib.metadata,nailong.gui;print(json.dumps({"prefix":sys.prefix,"version":importlib.metadata.version("ignovate-harness"),"bytecode":sys.dont_write_bytecode}))'],{env,encoding:'utf8',windowsHide:true}));
    assert.equal(metadata.version,JSON.parse(fs.readFileSync(path.resolve(__dirname,'../desktop/package.json'),'utf8')).version);
    assert.equal(metadata.bytecode,true);
    assert.equal(fs.realpathSync(metadata.prefix),fs.realpathSync(path.join(resources,'runtime')));
    assert.match(execFileSync(path.join(resources,'tools','rg.exe'),['--version'],{env,encoding:'utf8',windowsHide:true}),/^ripgrep 15\.2\.0/);
    app=await electron.launch({executablePath:path.join(relocated,'Ignovate.exe'),args:['--project',project,'--user-data-dir='+path.join(home,'chromium')],env,timeout:60000});
    const page=await app.firstWindow();
    await page.locator('#project-name').filter({hasText:'项目 空格'}).waitFor({timeout:30000});
    await page.locator('#startup-screen').waitFor({state:'detached'});
    assert.equal(await app.evaluate(({app})=>app.isPackaged),true);
    assert.deepEqual(await page.evaluate(()=>({choose:typeof window.ignovateDesktop.chooseProject,
      copy:typeof window.ignovateDesktop.writeClipboardText,node:typeof require})),
      {choose:'function',copy:'function',node:'undefined'});
    const options=await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences());
    assert.equal(options.sandbox,true);assert.equal(options.contextIsolation,true);assert.equal(options.nodeIntegration,false);
    await page.evaluate(()=>window.ignovateDesktop.writeClipboardText('Windows 桌面剪贴板验证'));
    assert.equal(await app.evaluate(({clipboard})=>clipboard.readText()),'Windows 桌面剪贴板验证');
    if(process.env.IGNOVATE_SCREENSHOT)await page.screenshot({path:process.env.IGNOVATE_SCREENSHOT});
    await app.close();app=null;
    assert.ok(!fs.existsSync(path.join(resources,'runtime','Lib','site-packages','nailong','gui','__pycache__')));
    // Windows cannot remove a directory containing a running interpreter or
    // a still-open SQLite database. Relocation also guards against path leaks.
    fs.rmSync(relocated,{recursive:true});fs.rmSync(path.join(temp,'data'),{recursive:true,force:true});
    console.log('Windows installed app: relocated Unicode paths, embedded Python/ripgrep, sandbox, clipboard and clean shutdown passed.');
  }finally{
    if(app)await app.close();
    fs.rmSync(temp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
