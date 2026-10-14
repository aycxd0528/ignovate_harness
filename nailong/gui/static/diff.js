import {el,button,icon} from './ui.js';

export function parseDiff(diff=''){
  const rows=[];let old=null,next=null,inHunk=false;
  for(const value of diff.split('\n')){
    const hunk=value.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if(hunk){old=Number(hunk[1]);next=Number(hunk[2]);inHunk=true;rows.push({kind:'hunk',text:value});continue;}
    if(/^\[(?:中间)?内容已截断\]$/.test(value)){old=next=null;rows.push({kind:'meta',text:value});continue;}
    if(!inHunk){if(value)rows.push({kind:'meta',text:value});continue;}
    const kind=value.startsWith('+')?'added':value.startsWith('-')?'removed':value.startsWith(' ')?'context':'meta';
    rows.push({kind,text:value.endsWith('\r')?value.slice(0,-1)+' ␍ (CRLF)':value,old:(kind==='context'||kind==='removed')&&old!==null?old++:null,next:(kind==='context'||kind==='added')&&next!==null?next++:null});
  }
  return rows;
}
export function fileChange(tool){
  if(!['write_file','edit_file'].includes(tool.name)||tool.status!=='completed')return null;
  const path=tool.path||tool.preview?.path;if(!path)return null;
  const actual=typeof tool.diff==='string',diff=actual?tool.diff:tool.preview?.diff||'';
  const truncated=Boolean(tool.diff_truncated)||/^\[(?:中间)?内容已截断\]$/m.test(diff);
  const complete=Number.isInteger(tool.additions)&&Number.isInteger(tool.deletions);
  const rows=complete?null:parseDiff(diff);
  return {path,diff,rows,preview:!actual,truncated,approximate:!complete&&truncated,
    additions:complete?tool.additions:rows.filter(row=>row.kind==='added').length,
    deletions:complete?tool.deletions:rows.filter(row=>row.kind==='removed').length};
}
export function diffStats(change){
  const root=el('span','diff-stats');root.title=change.preview?'执行前预览':'按记录的文件操作累计';
  const prefix=change.approximate?'≥':'';root.append(el('span','diff-add',prefix+'+'+change.additions),el('span','diff-remove',prefix+'-'+change.deletions));return root;
}
export function renderDiff(change){
  const root=el('div','recorded-diff');
  if(change.preview)root.append(el('p','diff-note muted','执行前预览；该回合未记录执行后的补丁。'));
  if(!change.diff){root.append(el('p','diff-note muted',change.preview?'该回合未记录文件差异。':'没有行增删。'));return root;}
  for(const row of change.rows||parseDiff(change.diff)){
    const line=el('div','diff-row '+row.kind);line.append(el('span','diff-number',row.old??''),el('span','diff-number',row.next??''),el('code','diff-code',row.text));root.append(line);
  }
  if(change.truncated)root.append(el('p','diff-note muted','差异预览已截断。'));return root;
}
export function fileChangesReview(tools){
  const files=new Map();
  for(const tool of tools){const change=fileChange(tool);if(!change)continue;if(!files.has(change.path))files.set(change.path,[]);files.get(change.path).push(change);}
  const root=el('div','file-changes-review'),summary=el('div','changes-summary'),content=el('div','changes-content'),main=el('div','changes-patches'),nav=el('aside','changes-files'),search=el('input','changes-search');
  const total={additions:0,deletions:0,preview:false,approximate:false};for(const changes of files.values())for(const change of changes){total.additions+=change.additions;total.deletions+=change.deletions;total.approximate||=change.approximate;total.preview||=change.preview;}
  summary.append(el('span','',`${files.size} 个文件`),diffStats(total));root.append(summary,content);content.append(main,nav);
  search.placeholder='筛选文件…';search.setAttribute('aria-label','筛选变更文件');nav.append(search);const tree=el('div','changes-tree');nav.append(tree);
  const sections=new Map();let selected=files.keys().next().value;
  for(const [path,changes] of files){const section=el('section','file-change-section');sections.set(path,section);const header=el('div','file-change-header'),counts={additions:changes.reduce((n,c)=>n+c.additions,0),deletions:changes.reduce((n,c)=>n+c.deletions,0),preview:changes.some(c=>c.preview),approximate:changes.some(c=>c.approximate)};
    header.append(icon('file'),el('strong','',path),diffStats(counts));section.append(header);
    changes.forEach((change,i)=>{if(changes.length>1)section.append(el('p','diff-operation muted',`第 ${i+1} 次文件操作`));section.append(renderDiff(change));});main.append(section);
  }
  function renderTree(){tree.replaceChildren();let directory=null;for(const [path,changes] of files){if(!path.toLocaleLowerCase().includes(search.value.toLocaleLowerCase()))continue;const parts=path.replaceAll('\\','/').split('/'),name=parts.pop(),folder=parts.join('/');if(folder!==directory){directory=folder;if(folder){const label=el('div','changes-folder');label.append(icon('folder'),el('span','',folder));tree.append(label);}}
    const row=button('',()=>{selected=path;renderTree();sections.get(path).scrollIntoView({block:'start',behavior:'instant'});},'file-change-entry'+(selected===path?' selected':''));row.title=path;row.append(icon('file'),el('span','',name),diffStats({additions:changes.reduce((n,c)=>n+c.additions,0),deletions:changes.reduce((n,c)=>n+c.deletions,0),preview:changes.some(c=>c.preview),approximate:changes.some(c=>c.approximate)}));tree.append(row);
  }if(!tree.childElementCount)tree.append(el('p','muted','没有匹配文件。'));}
  search.addEventListener('input',renderTree);renderTree();return root;
}
