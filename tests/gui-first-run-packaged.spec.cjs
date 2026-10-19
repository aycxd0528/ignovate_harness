const {_electron:electron}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {execFileSync}=require('node:child_process');

(async()=>{
 const source=process.env.IGNOVATE_APP;assert.ok(source,'Set IGNOVATE_APP to packaged app/directory');
 const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ignovate-first-run-')),home=path.join(temp,'home'),project=path.join(temp,'项目 空格');
 const windows=process.platform==='win32',relocated=path.join(temp,'应用程序',windows?'Ignovate':'Ignovate.app');let app;
 fs.mkdirSync(home);fs.mkdirSync(project);fs.mkdirSync(path.dirname(relocated));fs.writeFileSync(path.join(project,'README.md'),'# First-run test\n');
 try{
  if(windows)await fs.promises.cp(source,relocated,{recursive:true});else execFileSync('ditto',[source,relocated]);
  const executablePath=windows?path.join(relocated,'Ignovate.exe'):path.join(relocated,'Contents/MacOS/Ignovate');
  const env={...process.env,HOME:home,USERPROFILE:home,APPDATA:path.join(home,'AppData'),XDG_CONFIG_HOME:path.join(home,'.config'),IGNOVATE_CONFIG_DIR:path.join(temp,'config'),NAILONG_DATA_DIR:path.join(temp,'data'),PYTHONPATH:path.join(temp,'invalid'),PATH:windows?path.join(process.env.SystemRoot,'System32'):'/usr/bin:/bin:/usr/sbin:/sbin'};
  for(const name of ['IGNOVATE_PYTHON','DEEPSEEK_API_KEY','DEEPSEEK_API_BASE','DEEPSEEK_BASE_URL','DEEPSEEK_MODEL','VIRTUAL_ENV','PYTHONHOME','ELECTRON_RUN_AS_NODE'])delete env[name];
  const userData=path.join(home,'desktop'),args=['--user-data-dir='+userData];
  async function launch(){app=await electron.launch({executablePath,args,env,timeout:60000});return app.firstWindow();}
  async function snapshot(page){return page.evaluate(async()=>await(await fetch('/api/snapshot',{headers:{Authorization:'Bearer '+sessionStorage.getItem('ignovate-token')}})).json());}
  let page=await launch();
  const actualUserData=await app.evaluate(({app})=>app.getPath('userData'));assert.ok(path.relative(temp,actualUserData)&&!path.relative(temp,actualUserData).startsWith('..'),'test must use isolated desktop storage');
  const guide=page.getByRole('dialog',{name:'开始使用 Ignovate',exact:true});await guide.waitFor({timeout:30000});
  assert.equal((await snapshot(page)).onboarding.workspace_placeholder,true,'fresh no-argument launch starts with guide, before workspace picker');
  const effects=[];page.on('request',request=>{if(request.url().endsWith('/api/command')){try{effects.push(JSON.parse(request.postData()).name);}catch{}}});
  await page.screenshot({path:process.env.IGNOVATE_FIRST_RUN_SCREENSHOT||path.join(os.tmpdir(),'ignovate-first-run-installed.png')});
  await guide.getByRole('button',{name:'开始设置',exact:true}).click();
  await app.evaluate(({dialog})=>{dialog.showOpenDialog=async()=>({canceled:true,filePaths:[]});});
  await guide.getByRole('button',{name:'选择文件夹',exact:true}).click();
  await guide.locator('.onboarding-shell[aria-busy="false"]').waitFor();assert.equal((await snapshot(page)).onboarding.workspace_placeholder,true,'cancel does not confirm a workspace');
  await app.evaluate(({dialog},chosen)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[chosen]});},project);
  await guide.getByRole('button',{name:'选择文件夹',exact:true}).click();await guide.getByRole('heading',{name:'连接你的模型',exact:true}).waitFor();
  await guide.getByLabel('API 地址',{exact:true}).fill('https://example.invalid');await guide.getByLabel('模型 ID',{exact:true}).fill('offline-first-run');await guide.getByLabel('API Key',{exact:true}).fill('installed-test-key');
  await guide.getByRole('button',{name:'保存并继续',exact:true}).click();await guide.getByRole('heading',{name:'准备好开始了',exact:true}).waitFor();
  await guide.getByRole('button',{name:'进入工作台',exact:true}).click();await guide.waitFor({state:'detached'});
  assert.equal((await snapshot(page)).onboarding.status,'completed');assert.equal((await snapshot(page)).runs.length,0);
  assert.equal(effects.some(name=>['send','verify','test_connection'].includes(name)),false,'setup cannot invoke a model task');
  assert.equal(JSON.parse(fs.readFileSync(path.join(actualUserData,'workspace.json'),'utf8')).project,fs.realpathSync(project));
  await app.close();app=null;
  page=await launch();await page.locator('#startup-screen').waitFor({state:'detached'});const resumed=await snapshot(page);
  assert.equal(resumed.project.path,fs.realpathSync(project));assert.equal(resumed.onboarding.required,false);assert.equal(await page.locator('#onboarding-dialog').count(),0);
  await app.close();app=null;fs.renameSync(project,path.join(temp,'moved-project'));
  page=await launch();await page.getByRole('dialog',{name:'开始使用 Ignovate',exact:true}).getByRole('heading',{name:'选择你的工作区',exact:true}).waitFor();
  assert.equal((await snapshot(page)).onboarding.workspace_placeholder,true,'missing remembered workspace enters recovery');
  await app.close();app=null;
  if(!windows)execFileSync('codesign',['--verify','--deep','--strict',relocated]);
  console.log('Installed first-run: no-argument welcome, native picker cancel/select, local model setup, persistence, missing-workspace recovery and clean close passed.');
 }finally{if(app)await app.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
