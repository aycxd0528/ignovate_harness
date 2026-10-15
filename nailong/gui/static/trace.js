import {el,button,icon,statusLabel} from './ui.js';
import {patchElement,synchronize} from './rendering.js';

const labels={user:'用户',assistant:'助手',tool:'工具',model:'模型'};
let clockInstalled=false;
const lane=role=>role==='user'?'input':role==='tool'?'tool':'model';
const pretty=value=>typeof value==='string'?value:JSON.stringify(value,null,2)||'';
const duration=ms=>Number.isFinite(ms)?(ms<1000?Math.round(ms)+' 毫秒':ms<60000?(ms/1000).toFixed(1)+' 秒':Math.floor(ms/60000)+' 分 '+Math.floor(ms%60000/1000)+' 秒'):'未知';
function preview(row){
  if(row.role!=='tool')return row.text||row.label;
  const data=row.data||{},args=data.preview||{};
  return (args.command||args.path||data.path||pretty(args))+' → '+(data.output_preview||data.output_snippet||data.summary||statusLabel(row.status));
}
function rowElement(row){
  const root=el('details','trace-row'),summary=el('summary'),body=el('div','trace-row-body');
  summary.setAttribute('aria-label',((labels[row.role]||'事件')+' '+(row.role==='tool'?row.label+' ':'')+preview(row)).slice(0,400));
  root.dataset.role=row.role;root.dataset.status=row.status||'';root.dataset.eventId=row.id;
  const mark=el('span','trace-role',labels[row.role]||'事件');
  summary.append(mark);
  if(row.role==='tool')summary.append(el('strong','trace-operation',row.label));
  summary.append(el('span','trace-preview',preview(row).replace(/\s+/g,' ')));
  if(row.status&&['queued','running','waiting_approval','completed','failed','stopped','stopping','interrupted','unknown'].includes(row.status))summary.append(el('span','trace-event-status',statusLabel(row.status)));
  if(Number.isFinite(row.elapsed_ms))summary.append(el('span','trace-duration',duration(row.elapsed_ms)));
  summary.append(icon('chevron'));
  if(row.role==='tool'){
    body.append(el('span','trace-body-label','参数预览'),el('pre','trace-data',pretty(row.data?.preview||{})),el('span','trace-body-label','执行结果'),el('pre','trace-data',pretty(row.data)));
  }else body.append(el('pre','trace-data',row.text||pretty(row.data)));
  if(row.timestamp){const time=el('time','trace-timestamp');time.dateTime=row.timestamp;const date=new Date(row.timestamp);time.textContent=Number.isFinite(date.getTime())?date.toLocaleTimeString():row.timestamp;body.append(time);}
  root.append(summary,body);return root;
}

// The host caches this view by branch. Native details/search/scroll survive
// live updates; requests are coalesced and obsolete branch replies are ignored.
export function createTrace({read,isCurrent,onChat}){
  if(!clockInstalled){clockInstalled=true;setInterval(()=>document.querySelector('.trace-page')?.tick?.(),1000);}
  const root=el('div','trace-page'),toolbar=el('div','trace-toolbar'),metrics=el('div','trace-metrics');
  const searchWrap=el('label','trace-search'),search=el('input');search.type='search';search.placeholder='搜索轨迹';search.setAttribute('aria-label','搜索轨迹');searchWrap.append(icon('search'),search);
  const overview=el('div','trace-overview'),heading=el('div','trace-overview-heading'),tracks=el('div','trace-tracks'),list=el('div','trace-list'),notice=el('div','trace-notice');
  const orderNote=el('span','muted','按事件顺序');heading.append(el('span','','执行概览'),orderNote);
  const segments=new Map(),trackRoots=new Map(),rounds=new Map(),rows=new Map();
  for(const [id,label] of [['input','输入'],['model','模型'],['tool','工具']]){const track=el('div','trace-track'),name=el('span','trace-track-label',label),bars=el('div','trace-bars');track.dataset.lane=id;track.append(name,bars);tracks.append(track);trackRoots.set(id,bars);}
  overview.append(heading,tracks);toolbar.append(metrics,searchWrap);root.append(toolbar,overview,notice,list);
  let report=null,limit=80,timer,inFlight=false,wanted=null,loaded=null,lastRead=0,epoch=0,receivedAt=0;
  const active=()=>root.isConnected&&isCurrent();
  const retry=()=>{loaded=null;root.update(wanted);};
  const empty=()=>{const section=el('div','trace-empty');section.append(icon('list'),el('h2','','暂无执行轨迹'),el('p','muted','发送一条消息后，这里会按轮次记录输入、模型和工具执行。'),button('开始对话',onChat,'secondary'));return section;};
  function render(){
    if(!report)return;
    const query=search.value.trim().toLocaleLowerCase(),matches=row=>!query||(row.label+' '+preview(row)+' '+pretty(row.data)).toLocaleLowerCase().includes(query);
    metrics.replaceChildren(el('span','',`时长 ${duration(report.elapsed_ms)}`),el('span','',`${report.total_turns} 轮`),el('span','',`${report.model_calls} 次模型 · ${report.tool_calls} 次工具`));
    orderNote.textContent=report.has_more?'最近 '+report.turns.length+' 轮 · 按事件顺序':'按事件顺序';
    const all=report.turns.flatMap(turn=>turn.rows),items=[],roundKeys=new Set(),segmentKeys=new Set();
    const byLane=new Map([...trackRoots.keys()].map(id=>[id,[]]));
    tracks.style.setProperty('--trace-width',Math.max(1,all.length)*18+'px');
    for(const [index,row] of all.entries()){
      const roleLane=lane(row.role),key=row.id;segmentKeys.add(key);let segment=segments.get(key);
      if(!segment){segment=button('',()=>{const row=rows.get(key)?.element;if(row){row.open=true;row.scrollIntoView({block:'center',behavior:'auto'});row.querySelector('summary').focus({preventScroll:true});}},'trace-segment');segments.set(key,segment);}
      segment.style.left=index*18+'px';segment.dataset.status=row.status||'';
      segment.title=labels[row.role]+' · '+row.label;segment.setAttribute('aria-label',`定位${labels[row.role]}：${row.label}`);segment.disabled=!matches(row);byLane.get(roleLane).push(segment);
    }
    for(const [id,bars] of trackRoots)synchronize(bars,byLane.get(id));
    for(const key of segments.keys())if(!segmentKeys.has(key))segments.delete(key);
    if(report.has_more){const load=button('加载更早的轨迹',()=>{limit=Math.min(500,limit+80);retry();},'text-button trace-load');load.disabled=limit>=500;items.push(load);}
    for(const turn of report.turns){
      const shown=turn.rows.filter(matches);if(!shown.length)continue;roundKeys.add(turn.id);
      let round=rounds.get(turn.id);if(!round){const section=el('section','trace-round'),header=el('div','trace-round-header'),body=el('div','trace-round-rows');section.dataset.turnId=turn.id;section.append(header,body);round={section,header,body};rounds.set(turn.id,round);}
      round.header.replaceChildren(el('span','','第 '+turn.round+' 轮'),el('span','',statusLabel(turn.status)),el('span','',duration(turn.elapsed_ms)));
      const children=[];
      for(const row of shown){
        let entry=rows.get(row.id);const signature=JSON.stringify(row);
        if(!entry){entry={element:rowElement(row)};rows.set(row.id,entry);}else if(entry.signature!==signature)patchElement(entry.element,rowElement(row));
        entry.signature=signature;children.push(entry.element);
      }
      synchronize(round.body,children);items.push(round.section);
    }
    // Keep filtered rows cached so restoring search also restores disclosures.
    const allKeys=new Set(all.map(row=>row.id));for(const key of rows.keys())if(!allKeys.has(key))rows.delete(key);
    for(const key of rounds.keys())if(!report.turns.some(turn=>turn.id===key))rounds.delete(key);
    if(!report.total_turns)items.push(empty());else if(!roundKeys.size)items.push(el('p','trace-no-results muted','没有匹配的事件。'));
    overview.hidden=!all.length;synchronize(list,items);
    root.tick();
  }
  root.tick=()=>{
    if(!active()||!report)return;
    const elapsed=Math.max(0,performance.now()-receivedAt),live=turn=>['running','waiting_approval','stopping'].includes(turn.status)&&Number.isFinite(turn.elapsed_ms);
    if(metrics.firstChild)metrics.firstChild.textContent='时长 '+duration(Number.isFinite(report.elapsed_ms)?report.elapsed_ms+report.turns.filter(live).length*elapsed:null);
    for(const turn of report.turns){const label=rounds.get(turn.id)?.header.lastChild;if(label&&live(turn))label.textContent=duration(turn.elapsed_ms+elapsed);}
  };
  search.addEventListener('input',render);
  async function load(){
    timer=null;if(!active()||inFlight)return;
    const requestedLimit=limit,requestedEpoch=epoch;inFlight=true;lastRead=performance.now();notice.textContent=report?'':'正在加载轨迹…';
    try{
      const next=await read(requestedLimit);if(!active()||limit!==requestedLimit||epoch!==requestedEpoch)return;
      report=next;receivedAt=performance.now();loaded=next.sequence;notice.replaceChildren();render();
    }catch(error){if(active()&&limit===requestedLimit&&epoch===requestedEpoch){notice.replaceChildren(el('span','',error.message),button('重试',retry,'text-button'));loaded=wanted;}}
    finally{inFlight=false;if(active()&&(loaded===null||loaded<wanted))root.update(wanted);}
  }
  root.invalidate=()=>{loaded=null;epoch++;};
  root.update=sequence=>{if(wanted!==null&&sequence<wanted)root.invalidate();wanted=sequence;if(!active()||inFlight||timer||loaded!==null&&loaded>=wanted)return;timer=setTimeout(load,Math.max(0,250-(performance.now()-lastRead)));};
  return root;
}
