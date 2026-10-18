"""Project/local runtime policy; stage limits are distinct from goal total costs."""
from nailong.core.preferences import read_config
from pathlib import Path


def child_options(root, *, model_output, context_window=None):
    root=Path(root).resolve()
    values={'max_concurrency':3,'stage_tokens':30000,'output_tokens':4096,'final_output_tokens':1024}
    ranges={'max_concurrency':(1,3),'stage_tokens':(4096,2_000_000),'output_tokens':(128,131072),'final_output_tokens':(128,16384)}
    for name in ('settings.json','settings.local.json'):
        config=read_config(root/'.nailong'/name,root).get('subagents',{})
        if not isinstance(config,dict) or set(config)-values.keys():raise ValueError('subagents 配置字段无效。')
        values.update(config)
    for key,value in values.items():
        lo,hi=ranges[key]
        if type(value) is not int or not lo<=value<=hi:raise ValueError(f'subagents.{key} 必须为 {lo}–{hi} 的整数。')
    cap=min(model_output,context_window//2 if context_window else model_output)
    values['output_tokens']=min(values['output_tokens'],cap,values['stage_tokens']//2)
    values['final_output_tokens']=min(values['final_output_tokens'],values['output_tokens'])
    return values
