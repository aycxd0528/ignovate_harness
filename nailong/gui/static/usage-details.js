// Recorded usage and request-size estimates deliberately have separate labels.
const labels={base_system:'系统',fixed_memory:'固定记忆',skill_catalog:'Skill 目录',tool_definitions:'工具定义',loaded_skills:'已加载 Skills',tool_results:'工具结果',history:'历史',task_context:'任务投影',reasoning:'推理',framing:'消息封装'};
function element(tag,text,className=''){
  const node=document.createElement(tag);node.className=className;
  if(text!==undefined)node.textContent=String(text);
  return node;
}
function amount(value){return typeof value==='number'&&Number.isFinite(value)&&value>=0?String(value):'未知';}
function fact(list,label,value){list.append(element('dt',label),element('dd',value));}

export function renderUsageDetails(detail){
  const root=element('details',undefined,'tool-details usage-breakdown');
  root.append(element('summary','Token 用量明细'));
  const body=element('div',undefined,'tool-output');
  const turn=detail.usage_details?.last_turn;
  const actual=turn?.actual||detail.usage||{};
  const facts=element('dl',undefined,'facts');
  fact(facts,'本轮实际输入',amount(actual.input_tokens));
  fact(facts,'其中缓存命中',amount(actual.cache_hit_tokens));
  fact(facts,'本轮实际输出',amount(actual.output_tokens));
  fact(facts,'本轮合计',amount(actual.total_tokens));
  fact(facts,'模型调用',amount(actual.calls));
  if(detail.session_usage)fact(facts,'当前会话累计',amount(detail.session_usage.total_tokens));
  body.append(facts,element('p','缓存命中属于输入，不额外加到合计。','source-label'));
  if((actual.usage_complete??actual.complete)!==true){
    body.append(element('p','部分调用用量未知；显示已知部分，不能当作完整消耗。','source-label'));
  }
  const estimate=turn?.estimate;
  if(estimate){
    body.append(element('p','分类估算（不是实际分项计费）','source-label'));
    const categories=element('dl',undefined,'facts');
    fact(categories,'模型输入估算',amount(estimate.estimated_tokens));
    for(const [name,row] of Object.entries(estimate.categories||{}))fact(categories,labels[name]||name,amount(row?.tokens));
    body.append(categories);
  }else body.append(element('p','这轮没有可用的输入分类估算。','source-label'));
  root.append(body);
  return root;
}
