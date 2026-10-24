const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const installedChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const browser = await chromium.launch({ headless: true, executablePath: fs.existsSync(installedChrome) ? installedChrome : undefined });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto('http://127.0.0.1:8767/#token=browser-test-only');
    await page.locator('#project-name').filter({ hasText: 'GUI 验证项目' }).waitFor();
    await page.getByRole('button', { name: '新会话', exact: true }).click();
    await page.locator('#composer-target').filter({ hasText: '发送到 新会话' }).waitFor({state:'attached'});
    await page.getByPlaceholder('向 Ignovate 描述你的任务…').fill('设计 GUI 共同要求');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.locator('.turn .answer').first().waitFor();
    await page.getByRole('button', { name: '会话地图', exact: true }).click();
    await page.locator('.map-card').first().waitFor();
    await page.locator('.map-card').first().getByRole('button', { name: '详情', exact: true }).click();
    await page.locator('#inspector .answer').waitFor();
    await page.getByRole('button', { name: '聊天', exact: true }).click();
    await page.locator('.turn .answer').first().evaluate(el => {
      const range = document.createRange(); range.selectNodeContents(el);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    });
    await page.getByRole('button', { name: '引用追问', exact: true }).click();
    assert.match(await page.locator('#composer').inputValue(), /引用/);
    const snapshot = () => page.evaluate(async () => (await fetch('/api/snapshot', { headers: { Authorization: 'Bearer browser-test-only' } })).json());
    assert.equal((await snapshot()).runs.length, 1, 'quote must not send');
    await page.getByRole('button', { name: '会话地图', exact: true }).click();
    await page.locator('.map-card').first().getByRole('button', { name: '分叉', exact: true }).click();
    await page.getByLabel('分支名称').fill('方案 B');
    await page.locator('#dialog').getByRole('button', { name: '创建分支', exact: true }).click();
    await page.locator('#dialog').waitFor({ state: 'hidden' });
    await page.locator('#composer-target').filter({ hasText: '发送到 方案 B' }).waitFor({state:'attached'});
    await page.getByRole('button',{name:'聊天',exact:true}).click();
    await page.locator('#composer').fill('B 方案要求');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.waitForFunction(async () => {
      const s = await (await fetch('/api/snapshot', { headers: { Authorization: 'Bearer browser-test-only' } })).json();
      return s.nodes.length === 2;
    });
    // The persisted node can precede the UI's final snapshot/React commit.
    // Wait for visible completion before clicking the morphing send/stop button.
    await page.waitForFunction(() =>
      document.querySelectorAll('.turn:not(.live)').length === 2 &&
      !document.querySelector('.turn.live') &&
      document.querySelector('#send')?.getAttribute('aria-label') === '发送');
    assert.equal(await page.getByRole('button',{name:'比较',exact:true}).count(),0);
    await page.locator('#composer').fill('<img src=x onerror=alert(1)>');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.locator('.prompt').filter({ hasText: '<img src=x' }).waitFor();
    await page.waitForFunction(() => document.querySelectorAll('.turn:not(.live)').length === 3);
    assert.equal(await page.locator('.prompt img').count(), 0);
    await page.screenshot({ path: '/tmp/ignovate-gui-chat.png', fullPage: true });
    await page.getByRole('button', { name: '会话地图', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.map-card').length === 3);
    await page.getByRole('button', { name: '适应视图', exact: true }).click();
    await page.screenshot({ path: '/tmp/ignovate-gui-map.png', fullPage: true });
    await page.setViewportSize({ width: 1024, height: 720 });
    await page.screenshot({ path: '/tmp/ignovate-gui-narrow.png', fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.reload();
    await page.locator('#project-name').filter({ hasText: 'GUI 验证项目' }).waitFor();
    assert.equal((await snapshot()).branches.length, 2);
    assert.deepEqual(errors, []);
    console.log('Browser GUI acceptance passed: chat, map, quote, fork, XSS, narrow layout and reload.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
