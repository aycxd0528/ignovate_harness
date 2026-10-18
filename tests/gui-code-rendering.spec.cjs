const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {test,before,after,beforeEach,afterEach}=require('node:test');
const fs=require('node:fs'),path=require('node:path');

const root=path.resolve(__dirname,'../nailong/gui/static');
const installedChrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
let browser,page,errors,requests;
before(async()=>{browser=await chromium.launch({headless:true,executablePath:fs.existsSync(installedChrome)?installedChrome:undefined});});
after(async()=>{await browser?.close();});
beforeEach(async()=>{
  page=await browser.newPage({viewport:{width:1100,height:900},colorScheme:'light'});
  errors=[];requests=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());requests.push(url.href);
    if(url.origin!=='http://code-rendering.invalid')return route.abort();
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<!doctype html><html data-theme="light"><head><link rel="stylesheet" href="/style.css"></head><body><main id="samples" style="max-width:850px;padding:20px;margin:auto"></main><div id="toast"></div></body></html>'});
    const file=path.join(root,url.pathname.slice(1));
    return route.fulfill({contentType:file.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(file)});
  });
  await page.goto('http://code-rendering.invalid/');
  await page.evaluate(async()=>{
    window.prose=(await import('/ui.js')).prose;window.copied=[];
    window.ignovateDesktop={writeClipboardText:async text=>window.copied.push(text)};
  });
});
afterEach(async()=>{
  try{
    assert.deepEqual(errors,[],'Markdown rendering must not throw in the browser');
    assert.ok(requests.every(url=>url.startsWith('http://code-rendering.invalid/')),'rendering must use only locally shipped assets');
  }finally{await page?.close();}
});
const render=markdown=>page.evaluate(text=>document.querySelector('#samples').replaceChildren(window.prose(text)),markdown);

test('named code fences display distinct keyword and string colors',async()=>{
  await render('```javascript\nconst answer = "hello"; // readable\n```');
  assert.ok(await page.locator('pre code .hljs-keyword').count()>0,'JavaScript keywords must be highlighted');
  const colors=await page.locator('pre code').evaluate(code=>({base:getComputedStyle(code).color,keyword:getComputedStyle(code.querySelector('.hljs-keyword')).color,string:getComputedStyle(code.querySelector('.hljs-string')).color}));
  assert.notEqual(colors.keyword,colors.base);assert.notEqual(colors.keyword,colors.string);
  assert.equal(await page.locator('.code-header').innerText(),'javascript');
  for(const [language,source] of [['py','def greet():\n    return "你好"'],['ts','const value: string = "ok";'],['json','{"name": "hello", "enabled": true}'],['sh','echo "hello"']]){
    await render('```'+language+'\n'+source+'\n```');
    assert.ok(await page.locator('pre code [class^="hljs-"]').count()>0,language+' must resolve its local grammar');
    assert.equal(await page.locator('pre code').textContent(),source+'\n');
  }
});

test('unknown and unlabelled fences remain readable plain code',async()=>{
  const source='<img src=x onerror="window.pwned=true">\n\tconst untouched = "&<>";';
  for(const language of ['not-a-language','']){
    await render('```'+language+'\n'+source+'\n```');
    assert.equal(await page.locator('pre code').textContent(),source+'\n');
    assert.equal(await page.locator('pre code span').count(),0,'unknown code must not trigger automatic detection');
    assert.equal(await page.locator('img,script').count(),0);
  }
  assert.equal(await page.evaluate(()=>window.pwned),undefined);
});

test('punctuated and uppercase fence labels resolve common grammar aliases',async()=>{
  for(const [language,source] of [['c++','int main() { return 0; }'],['c#','public string Name = "hello";'],['PYTHON','def greet():\n    return "hello"']]){
    await render('```'+language+'\n'+source+'\n```');
    assert.ok(await page.locator('pre code [class^="hljs-"]').count()>0,language+' must retain its fence language');
    assert.equal(await page.locator('pre code').textContent(),source+'\n');
    assert.equal(await page.locator('.code-header').innerText(),language);
  }
});

test('HTML stays literal text before and inside highlighted fences',async()=>{
  await render('<img src=x onerror="window.pwned=true">\n\n<script>window.pwned=true</script>\n\n```html\n<img src=x onerror="window.pwned=true">\n```\n\n[unsafe](javascript:alert(1))');
  assert.equal(await page.locator('#samples img,#samples script,#samples [onerror],#samples a[href^="javascript:"]').count(),0);
  assert.ok((await page.locator('#samples').innerText()).includes('<img src=x onerror="window.pwned=true">'));
  assert.ok(await page.locator('pre code .hljs-tag').count()>0,'literal HTML tokens must still receive syntax colors');
  assert.equal(await page.evaluate(()=>window.pwned),undefined);
});

test('copying highlighted code preserves Unicode, spacing and literal markup',async()=>{
  const source='const markup = "<b>你好 & goodbye</b>";\n\t// keep tabs and  two spaces\n';
  await render('```js\n'+source+'```');
  await page.getByRole('button',{name:'复制代码',exact:true}).click();
  await page.waitForFunction(()=>window.copied.length===1);
  assert.deepEqual(await page.evaluate(()=>window.copied),[source]);
});

test('partial streaming fences re-render safely and copy the current text',async()=>{
  const partials=['const','const text = "','const text = "<em>你好 &','const text = "<em>你好 & welcome</em>";\n'];
  for(const source of partials){
    await render('```js\n'+source);
    assert.equal(await page.locator('pre code').textContent(),source.endsWith('\n')?source:source+'\n');
    assert.ok(await page.locator('pre code .hljs-keyword').count()>0,'an unclosed fence must keep its syntax colors');
  }
  await render('```js\n'+partials.at(-1)+'```');
  assert.equal(await page.locator('.code-frame').count(),1);
  assert.equal(await page.locator('pre code em').count(),0);
  await page.getByRole('button',{name:'复制代码',exact:true}).click();
  await page.waitForFunction(()=>window.copied.length===1);
  assert.deepEqual(await page.evaluate(()=>window.copied),[partials.at(-1)]);
});

test('code uses readable Windows fonts and accessible colors in light and dark themes',async()=>{
  await render('Inline `const value`\n\n```js\nconst answer = "hello"; // readable\n```');
  const typography=await page.locator('pre code').evaluate(code=>{const css=getComputedStyle(code);return {font:css.fontFamily,size:parseFloat(css.fontSize),height:parseFloat(css.lineHeight)};});
  assert.match(typography.font,/Consolas/);assert.match(typography.font,/Cascadia Mono/);
  assert.ok(typography.size>=14,'code must not shrink to the browser default monospace size');
  assert.ok(typography.height/typography.size>=1.5,'code lines need readable spacing');
  assert.equal(await page.locator('p code').evaluate(code=>getComputedStyle(code).fontFamily),typography.font);
  for(const [theme,scheme] of [['light','light'],['dark','dark'],['system','dark'],['system','light']]){
    await page.emulateMedia({colorScheme:scheme});await page.evaluate(value=>document.documentElement.dataset.theme=value,theme);
    const contrast=await page.locator('pre').evaluate(pre=>{
      const luminance=color=>{const channels=color.match(/[\d.]+/g).slice(0,3).map(value=>{const n=Number(value)/255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;});return channels[0]*.2126+channels[1]*.7152+channels[2]*.0722;};
      const background=luminance(getComputedStyle(pre).backgroundColor);
      return [...pre.querySelectorAll('code,.hljs-keyword,.hljs-string,.hljs-comment')].map(node=>{const foreground=luminance(getComputedStyle(node).color);return (Math.max(background,foreground)+.05)/(Math.min(background,foreground)+.05);});
    });
    assert.ok(contrast.length>=4,'theme checks must include rendered syntax tokens');
    assert.ok(contrast.every(value=>value>=4.5),theme+'/'+scheme+' syntax text needs at least 4.5:1 contrast: '+contrast);
    if(theme!=='system')await page.screenshot({path:'/tmp/ignovate-code-'+theme+'.png'});
  }
  await page.setViewportSize({width:390,height:844});
  await render('```js\nconst longLine = "'+('a'.repeat(200))+'";\n```');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'long code should scroll within its frame');
});
