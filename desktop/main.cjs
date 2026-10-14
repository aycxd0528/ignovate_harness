const { app, BrowserWindow, dialog, ipcMain, session } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const { runtimePaths } = require('./runtime.cjs');
const root = path.resolve(__dirname, '..');
let child, origin, window, selectedProjects = new Set();
let shuttingDown = false, adapterExited = false;

function trusted(event) {
  if (!window || event.sender !== window.webContents || new URL(event.senderFrame.url).origin !== origin) throw new Error('Invalid native bridge origin');
}
async function launch() {
  app.enableSandbox();
  await app.whenReady();
  const index = process.argv.indexOf('--project');
  let projectPath=index >= 0 ? process.argv[index + 1] : process.cwd();
  if(app.isPackaged&&index<0){
    const result=await dialog.showOpenDialog({properties:['openDirectory'],title:'选择 Ignovate 工作区',defaultPath:app.getPath('documents')});
    if(result.canceled){app.quit();return;}projectPath=result.filePaths[0];
  }
  const project = fs.realpathSync(projectPath);
  selectedProjects.add(project);
  const {python,pythonArgs}=runtimePaths({packaged:app.isPackaged,resourcesPath:process.resourcesPath,sourceRoot:root});
  const environment=app.isPackaged?{...process.env,PATH:path.join(process.resourcesPath,'tools')+path.delimiter+(process.env.PATH||'')}:process.env;
  child = spawn(python, [...pythonArgs,'-m', 'nailong.gui', '--stdio', '--project', project], { cwd: app.isPackaged?project:root, env:environment, stdio: ['ignore','pipe','pipe'], windowsHide: true });
  const url = await new Promise((resolve,reject) => {
    let pending = '';
    const timer = setTimeout(() => reject(new Error('Python adapter did not start within 30 seconds')), 30000);
    child.on('error', reject);
    child.once('exit', code => reject(new Error('Python adapter exited: '+code)));
    child.stdout.on('data', data => { pending += data; const line = pending.split('\n')[0]; if (!pending.includes('\n')) return;
      try { const value=JSON.parse(line); if(value.url){clearTimeout(timer);resolve(value.url);} } catch(e){clearTimeout(timer);reject(e);} });
  });
  origin = new URL(url).origin;
  if (new URL(url).hostname !== '127.0.0.1') throw new Error('Adapter must bind loopback');
  session.defaultSession.setPermissionRequestHandler((_contents,_permission,callback) => callback(false));
  ipcMain.handle('choose-project', async event => {
    trusted(event);
    const result = await dialog.showOpenDialog(window, { properties: ['openDirectory'], title: '打开本地项目' });
    if(result.canceled)return null;
    const selected=fs.realpathSync(result.filePaths[0]);selectedProjects.add(selected);return selected;
  });
  ipcMain.handle('open-project-file', async (event, projectPath, relative) => {
    trusted(event);
    if(typeof projectPath!=='string'||typeof relative!=='string'||path.isAbsolute(relative))throw new Error('Invalid project file');
    const project=fs.realpathSync(projectPath);
    if(!selectedProjects.has(project))throw new Error('Project was not selected in this window');
    const target=fs.realpathSync(path.resolve(project,relative));
    const inside=path.relative(project,target);
    if(!inside||inside.startsWith('..')||path.isAbsolute(inside)||!fs.statSync(target).isFile())throw new Error('File must be inside selected project');
    if(inside.split(path.sep).some(part=>part==='.git'||part==='.env'||part.startsWith('.env.')))throw new Error('Protected file');
    // Invoke an actual text editor. OS default file associations can execute files.
    const editor = process.env.IGNOVATE_EDITOR || (process.platform === 'darwin' ? '/usr/bin/open' : process.platform === 'win32' ? 'notepad.exe' : null);
    if(!editor)throw new Error('请设置 IGNOVATE_EDITOR 为文本编辑器可执行文件路径');
    const args = !process.env.IGNOVATE_EDITOR && process.platform === 'darwin' ? ['-a','TextEdit',target] : [target];
    return new Promise((resolve,reject)=>{const editorChild=spawn(editor,args,{shell:false,stdio:'ignore',windowsHide:true});editorChild.once('error',reject);editorChild.once('spawn',()=>{editorChild.unref();resolve('');});});
  });
  window = new BrowserWindow({ width: 1450, height: 950, minWidth: 1024, minHeight: 720, title: 'Ignovate',
    backgroundColor: '#fbfcfd', webPreferences: { preload: path.join(__dirname,'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true } });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, target) => { if(new URL(target).origin!==origin)event.preventDefault(); });
  child.on('exit', () => { adapterExited=true;if(window&&!window.isDestroyed())window.webContents.send('kernel-exited'); });
  await window.loadURL(url);
}
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if(!child||adapterExited)return;
  event.preventDefault();
  if(shuttingDown)return;
  shuttingDown=true;
  // SIGTERM is handled by Python: cancellation joins tools before lease release.
  const timer=setTimeout(()=>{if(!adapterExited)child.kill('SIGKILL');},20000);
  child.once('exit',()=>{clearTimeout(timer);adapterExited=true;app.quit();});
  child.kill('SIGTERM');
});
launch().catch(async error => { await app.whenReady();dialog.showErrorBox('Ignovate 启动失败',error.message);if(child)child.kill();app.quit(); });
