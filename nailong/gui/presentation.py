"""GUI projections of recorded execution, without inferring generated files."""


def artifact_cards(node):
    artifacts=[]
    files=set()
    for tool in node.get('tools',[]):
        name=tool.get('name')
        preview=tool.get('preview') or {}
        path=tool.get('path') or preview.get('path')
        if name in {'write_file','edit_file'} and tool.get('status')=='completed' and path and path not in files:
            files.add(path)
            artifacts.append({'kind':'file','path':path,'operation':name,'call_id':tool.get('call_id'),
                              'diff':tool.get('diff'),'diff_truncated':tool.get('diff_truncated',False),
                              'additions':tool.get('additions'),'deletions':tool.get('deletions'),
                              'title':str(path).replace('\\','/').rsplit('/',1)[-1]})
        if name=='run_command':
            artifacts.append({'kind':'command','title':preview.get('command') or '命令结果',
                              'status':tool.get('status'),'exit_code':tool.get('exit_code'),
                              'content':tool.get('output_preview') or tool.get('output_snippet') or tool.get('summary') or '',
                              'call_id':tool.get('call_id')})
    verification=node.get('delivery',{}).get('verification')
    if verification:
        artifacts.append({'kind':'verification','title':'验证报告','status':node['delivery'].get('status'),'report':verification})
    return artifacts
