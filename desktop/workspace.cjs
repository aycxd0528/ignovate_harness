const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

function directory(project) {
  if (typeof project !== 'string' || !project) throw new Error('A workspace directory is required');
  const resolved = fs.realpathSync(project);
  if (!fs.statSync(resolved).isDirectory()) throw new Error('Workspace must be a directory');
  return resolved;
}

function createWorkspaceManager({ packaged, argv, cwd, userData }) {
  fs.mkdirSync(userData, { recursive: true, mode: 0o700 });
  const privateDirectory = fs.realpathSync(userData);
  const welcomeProject = path.join(privateDirectory, 'Welcome');
  const marker = path.join(privateDirectory, 'workspace.json');
  const index = argv.indexOf('--project');
  let project;
  if (index >= 0) {
    // Invalid explicit arguments must surface instead of selecting another project.
    project = directory(argv[index + 1]);
  } else if (!packaged) {
    project = directory(cwd);
  } else {
    try {
      const saved = JSON.parse(fs.readFileSync(marker, 'utf8'));
      if (saved && saved.version === 1 && typeof saved.project === 'string' && path.isAbsolute(saved.project)) {
        project = directory(saved.project);
      }
    } catch {
      // Missing, unreadable, corrupt and moved workspaces all enter recovery UI.
    }
    if (!project) {
      fs.mkdirSync(welcomeProject, { recursive: true, mode: 0o700 });
      project = directory(welcomeProject);
    }
  }

  function rememberSelection(result) {
    if (result.canceled || !result.filePaths.length) return null;
    const selected = directory(result.filePaths[0]);
    const temporary = `${marker}.${randomUUID()}.tmp`;
    try {
      fs.writeFileSync(temporary, `${JSON.stringify({ version: 1, project: selected })}\n`, { mode: 0o600, flag: 'wx' });
      fs.renameSync(temporary, marker);
    } finally {
      fs.rmSync(temporary, { force: true });
    }
    return selected;
  }

  return { project, welcomeProject, rememberSelection };
}

module.exports = { createWorkspaceManager };
