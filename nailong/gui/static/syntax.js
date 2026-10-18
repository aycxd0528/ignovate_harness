import highlighter from './syntax-engine.js';

// Highlight only the sanitized code's text, never Markdown-provided HTML.
// Explicit grammars keep unknown languages plain and avoid auto-detection on streams.
export function highlightCode(code) {
  const language=code?.dataset.language?.toLowerCase();
  if(!language||!highlighter.getLanguage(language))return;
  const source=code.textContent;
  try{
    code.innerHTML=highlighter.highlight(source,{language,ignoreIllegals:true}).value;
    code.classList.add('hljs');
  }catch{
    // A partial/unsupported construct must leave its source readable and copyable.
    code.textContent=source;
  }
}
