const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ignovate-workspace-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const userData = path.join(root, 'user-data');
  const cwd = path.join(root, 'source-project');
  const project = path.join(root, 'chosen-project');
  fs.mkdirSync(cwd);
  fs.mkdirSync(project);
  const create = overrides => require('../desktop/workspace.cjs').createWorkspaceManager({
    packaged: true, argv: [], cwd, userData, ...overrides,
  });
  return { root, userData, cwd, project, create };
}

// A fresh packaged launch must produce a usable private directory without a picker.
test('fresh packaged launch uses a private welcome workspace without remembering it', t => {
  const { userData, create } = fixture(t);
  const workspace = create();
  assert.equal(workspace.project, path.join(fs.realpathSync(userData), 'Welcome'));
  assert.equal(workspace.welcomeProject, workspace.project);
  assert.deepEqual(fs.readdirSync(workspace.project), []);
  assert.equal(fs.existsSync(path.join(userData, 'workspace.json')), false);
  if (process.platform !== 'win32') assert.equal(fs.statSync(workspace.project).mode & 0o777, 0o700);
});

// Remembered state may never silently replace an explicit command-line project.
test('explicit project wins over remembered state and is not automatically remembered', t => {
  const { cwd, project, userData, create } = fixture(t);
  create().rememberSelection({ canceled: false, filePaths: [project] });
  const workspace = create({ argv: ['app', '--project', cwd] });
  assert.equal(workspace.project, fs.realpathSync(cwd));
  assert.equal(create().project, fs.realpathSync(project));
  assert.throws(() => create({ argv: ['--project', path.join(cwd, 'missing')] }));
  assert.throws(() => create({ argv: ['--project'] }));
  assert.equal(fs.existsSync(path.join(userData, 'workspace.json')), true);
});

// Development startup must continue to use cwd and have no persistence side effect.
test('source startup uses cwd even when a packaged workspace was remembered', t => {
  const { cwd, project, userData, create } = fixture(t);
  create().rememberSelection({ canceled: false, filePaths: [project] });
  assert.equal(create({ packaged: false }).project, fs.realpathSync(cwd));
  const before = fs.readFileSync(path.join(userData, 'workspace.json'), 'utf8');
  assert.equal(create({ packaged: false, argv: ['--project', project] }).project, fs.realpathSync(project));
  assert.equal(fs.readFileSync(path.join(userData, 'workspace.json'), 'utf8'), before);
});

// Explicit chooser success must survive a new process; cancel must leave state untouched.
test('chosen valid directory is remembered across launches and cancel preserves it', t => {
  const { project, userData, create } = fixture(t);
  const workspace = create();
  assert.equal(workspace.rememberSelection({ canceled: false, filePaths: [project] }), fs.realpathSync(project));
  const marker = path.join(userData, 'workspace.json');
  const before = fs.readFileSync(marker, 'utf8');
  assert.deepEqual(JSON.parse(before), { version: 1, project: fs.realpathSync(project) });
  assert.equal(create().project, fs.realpathSync(project));
  assert.equal(workspace.rememberSelection({ canceled: true, filePaths: [project] }), null);
  assert.equal(workspace.rememberSelection({ canceled: false, filePaths: [] }), null);
  assert.equal(fs.readFileSync(marker, 'utf8'), before);
  if (process.platform !== 'win32') assert.equal(fs.statSync(marker).mode & 0o777, 0o600);
});

// A moved/deleted or non-directory saved project cannot prevent opening the guide.
test('missing and moved remembered directories fall back to welcome', t => {
  const { project, userData, create } = fixture(t);
  create().rememberSelection({ canceled: false, filePaths: [project] });
  fs.renameSync(project, `${project}-moved`);
  const workspace = create();
  assert.equal(workspace.project, path.join(fs.realpathSync(userData), 'Welcome'));
  fs.writeFileSync(project, 'file replacing directory');
  assert.equal(create().project, workspace.welcomeProject);
});

test('corrupt and unsupported workspace markers fall back without losing their contents', t => {
  const { project, userData, create } = fixture(t);
  create();
  const marker = path.join(userData, 'workspace.json');
  for (const contents of ['{', 'null', '[]', JSON.stringify({ version: 2, project }), JSON.stringify({ version: 1, project: 42 }), JSON.stringify({ version: 1, project: 'relative-project' })]) {
    fs.writeFileSync(marker, contents);
    const workspace = create();
    assert.equal(workspace.project, workspace.welcomeProject);
    assert.equal(fs.readFileSync(marker, 'utf8'), contents);
  }
});

// Invalid chooser results must not corrupt a previously usable marker.
test('file and missing directory selections fail without changing the remembered workspace', t => {
  const { root, project, userData, create } = fixture(t);
  const workspace = create();
  workspace.rememberSelection({ canceled: false, filePaths: [project] });
  const marker = path.join(userData, 'workspace.json');
  const before = fs.readFileSync(marker, 'utf8');
  const file = path.join(root, 'file');
  fs.writeFileSync(file, '');
  assert.throws(() => workspace.rememberSelection({ canceled: false, filePaths: [file] }));
  assert.throws(() => workspace.rememberSelection({ canceled: false, filePaths: [path.join(root, 'missing')] }));
  assert.equal(fs.readFileSync(marker, 'utf8'), before);
  assert.deepEqual(fs.readdirSync(userData).sort(), ['Welcome', 'workspace.json']);
});

// Electron and the subprocess are external boundaries; filesystem state stays real.
async function bootDesktop({ userData, cwd, argv = [], packaged = true, environment = {} }) {
  const vm = require('node:vm');
  const { EventEmitter } = require('node:events');
  const { PassThrough } = require('node:stream');
  const handlers = new Map();
  const state = { dialogs: [], children: [], errors: [], quit: false, selection: { canceled: true, filePaths: [] } };
  const app = {
    isPackaged: packaged, enableSandbox() {}, async whenReady() {}, on() {},
    getPath(name) { if (name === 'userData') return userData; if (name === 'documents') return cwd; throw new Error(`Unexpected path ${name}`); },
    quit() { state.quit = true; },
  };
  class BrowserWindow {
    constructor(options) {
      state.window = this;
      this.options = options;
      this.webContents = { setWindowOpenHandler() {}, on() {}, send() {} };
    }
    async loadURL(url) { state.url = url; }
    isDestroyed() { return false; }
  }
  const electron = {
    app, BrowserWindow, clipboard: { writeText() {} },
    dialog: {
      async showOpenDialog(...args) { state.dialogs.push(args); return state.selection; },
      showErrorBox(title, message) { state.errors.push({ title, message }); },
    },
    ipcMain: { handle(name, handler) { handlers.set(name, handler); } },
    session: { defaultSession: { setPermissionRequestHandler() {} } },
  };
  const childProcess = {
    spawn(command, args, options) {
      const child = new EventEmitter();
      child.stdin = new PassThrough();
      child.stdout = new PassThrough();
      child.stderr = new PassThrough();
      child.kill = () => {};
      state.children.push({ command, args, options, child });
      setImmediate(() => child.stdout.write('{"url":"http://127.0.0.1:4040"}\n'));
      return child;
    },
  };
  const desktop = path.resolve(__dirname, '../desktop');
  const processInput = { argv, cwd: () => cwd, env: environment, resourcesPath: path.join(cwd, 'resources'), platform: process.platform };
  const context = {
    require(name) {
      if (name === 'electron') return electron;
      if (name === 'node:child_process') return childProcess;
      return require(name.startsWith('./') ? path.join(desktop, name) : name);
    },
    __dirname: desktop, process: processInput, setTimeout, clearTimeout, URL,
  };
  vm.runInNewContext(fs.readFileSync(path.join(desktop, 'main.cjs'), 'utf8'), context, { filename: 'main.cjs' });
  for (let attempt = 0; attempt < 20 && !state.url && !state.quit; attempt++) await new Promise(setImmediate);
  state.invoke = (name, ...args) => handlers.get(name)({ sender: state.window.webContents, senderFrame: { url: state.url } }, ...args);
  return state;
}

test('packaged Electron startup reaches welcome UI before any folder picker and passes the private path to Python', async t => {
  const { cwd, userData } = fixture(t);
  const state = await bootDesktop({ cwd, userData, environment: { IGNOVATE_DESKTOP_WELCOME_PROJECT: '/untrusted/old-value' } });
  assert.equal(state.url, 'http://127.0.0.1:4040');
  assert.deepEqual(state.dialogs, []);
  assert.deepEqual(state.errors, []);
  assert.equal(state.children.length, 1);
  const { options, args } = state.children[0];
  const welcome = path.join(fs.realpathSync(userData), 'Welcome');
  assert.equal(args[args.indexOf('--project') + 1], welcome);
  assert.equal(options.cwd, welcome);
  assert.equal(options.env.IGNOVATE_DESKTOP_WELCOME_PROJECT, welcome);
  assert.equal(state.window.options.webPreferences.contextIsolation, true);
  assert.equal(state.window.options.webPreferences.sandbox, true);
});

test('Electron chooser cancel returns null, and an explicit selected workspace is reused on next launch', async t => {
  const { cwd, userData, project } = fixture(t);
  const state = await bootDesktop({ cwd, userData });
  assert.ok(state.url, 'the initial window must load before project selection');
  assert.equal(await state.invoke('choose-project'), null);
  assert.equal(fs.existsSync(path.join(userData, 'workspace.json')), false);
  state.selection = { canceled: false, filePaths: [project] };
  assert.equal(await state.invoke('choose-project'), fs.realpathSync(project));
  const next = await bootDesktop({ cwd, userData });
  assert.equal(next.children[0].options.cwd, fs.realpathSync(project));
  assert.equal(next.children[0].options.env.IGNOVATE_DESKTOP_WELCOME_PROJECT, path.join(fs.realpathSync(userData), 'Welcome'));
  assert.deepEqual(next.dialogs, []);
});

test('source Electron startup keeps cwd and explicit project behavior without an initial picker', async t => {
  const { cwd, userData, project } = fixture(t);
  const source = await bootDesktop({ cwd, userData, packaged: false });
  assert.ok(source.url);
  const first = source.children[0];
  assert.equal(first.args[first.args.indexOf('--project') + 1], fs.realpathSync(cwd));
  assert.deepEqual(source.dialogs, []);
  const explicit = await bootDesktop({ cwd, userData, argv: ['--project', project] });
  assert.ok(explicit.url);
  assert.equal(explicit.children[0].options.cwd, fs.realpathSync(project));
  assert.deepEqual(explicit.dialogs, []);
  assert.equal(fs.existsSync(path.join(userData, 'workspace.json')), false);
});
