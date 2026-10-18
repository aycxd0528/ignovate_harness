# highlight.js 11.11.1

Vendored from the official `highlight.js@11.11.1` npm package. Project: https://github.com/highlightjs/highlight.js/tree/11.11.1

Only core and these grammars are bundled: bash, c, cpp, csharp, css, diff, go, java, javascript, json, markdown, powershell, python, rust, sql, typescript, xml, yaml. No CDN, external runtime dependency or automatic language detection is used.

## Rebuild

Extract `npm pack highlight.js@11.11.1` and bundle this entry from the package directory with esbuild (`--bundle --format=esm --minify`):

```js
import hljs from './lib/core.js';
import language0 from './lib/languages/bash.js';
hljs.registerLanguage('bash',language0);
import language1 from './lib/languages/c.js';
hljs.registerLanguage('c',language1);
import language2 from './lib/languages/cpp.js';
hljs.registerLanguage('cpp',language2);
import language3 from './lib/languages/csharp.js';
hljs.registerLanguage('csharp',language3);
import language4 from './lib/languages/css.js';
hljs.registerLanguage('css',language4);
import language5 from './lib/languages/diff.js';
hljs.registerLanguage('diff',language5);
import language6 from './lib/languages/go.js';
hljs.registerLanguage('go',language6);
import language7 from './lib/languages/java.js';
hljs.registerLanguage('java',language7);
import language8 from './lib/languages/javascript.js';
hljs.registerLanguage('javascript',language8);
import language9 from './lib/languages/json.js';
hljs.registerLanguage('json',language9);
import language10 from './lib/languages/markdown.js';
hljs.registerLanguage('markdown',language10);
import language11 from './lib/languages/powershell.js';
hljs.registerLanguage('powershell',language11);
import language12 from './lib/languages/python.js';
hljs.registerLanguage('python',language12);
import language13 from './lib/languages/rust.js';
hljs.registerLanguage('rust',language13);
import language14 from './lib/languages/sql.js';
hljs.registerLanguage('sql',language14);
import language15 from './lib/languages/typescript.js';
hljs.registerLanguage('typescript',language15);
import language16 from './lib/languages/xml.js';
hljs.registerLanguage('xml',language16);
import language17 from './lib/languages/yaml.js';
hljs.registerLanguage('yaml',language17);
export default hljs;
```

Preserve this license and the version/license banner when rebuilding.

## License

BSD 3-Clause License

Copyright (c) 2006, Ivan Sagalaev.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
