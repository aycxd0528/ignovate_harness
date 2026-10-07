import json
import tempfile
from pathlib import Path
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from nailong.core.compact import compact_messages


def exchange(index, size=8000, name='read_file', payload=None):
    call = f'call-{index}'
    return [AIMessage(id=f'ai-{index}', content='', tool_calls=[{'id':call,'name':name,'args':{'path':f'{index}.py'},'type':'tool_call'}]),
            ToolMessage(id=f'tool-{index}', tool_call_id=call, name=name,
                        content=json.dumps(payload or {'ok':True,'path':f'{index}.py','content':'x'*size}))]


class CompactionBehaviorTests(unittest.TestCase):
    def test_plain_tool_error_retains_failure_status_and_excerpt(self):
        messages=[HumanMessage(content='调查')]+exchange(1)+exchange(2)
        messages[2]=messages[2].model_copy(update={'status':'error','content':'Error: validation failed '+ 'x'*1000})
        result=compact_messages(messages,min_gain=0)
        body=next(m.content for m in result.messages if m.id=='tool-1')
        self.assertIn('"status": "error"',body)
        self.assertIn('validation failed',body)

    def test_soft_l2_refuses_summary_that_increases_context(self):
        messages=[]
        for i in range(6): messages += [HumanMessage(id=f'u{i}',content=f'q{i}'),AIMessage(id=f'a{i}',content='a')]
        result=compact_messages(messages,level='L2')
        self.assertFalse(result.compacted)
        self.assertEqual(result.messages,messages)
        self.assertEqual(result.reason,'insufficient_savings')

    def test_large_requirements_use_compact_index_without_hiding_middle_references(self):
        from nailong.core.history_archive import HistoryArchive
        with tempfile.TemporaryDirectory() as directory:
            archive=HistoryArchive(Path(directory).resolve())
            messages=[]
            for i in range(24):
                messages += [HumanMessage(content=f'Requirement {i}: '+'x'*100000),AIMessage(content='ok')]
            messages += [HumanMessage(content='current')]
            result=compact_messages(messages,keep_turns=1,hard=True,archive=archive,thread_id='one')
            summary=next(m for m in result.messages if m.additional_kwargs.get('nailong_compact_summary'))
            reference=summary.additional_kwargs['nailong_compact_state']['index_reference']
            page=archive.read('one',reference,max_chars=6000)
            self.assertFalse(page['archive_truncated'])
            pages=[page['content']]
            while page['truncated']:
                page=archive.read('one',reference,offset=page['next_offset'])
                pages.append(page['content'])
            index=json.loads(''.join(pages))
            middle=next(item for item in index['request_evidence'] if item['excerpt'].startswith('Requirement 10:'))
            recovered=archive.read('one',middle['reference'])
            self.assertTrue(recovered['content'].startswith('Requirement 10:'))


    def test_l2_preserves_unfinished_parallel_exchange_across_user_turns(self):
        messages=[HumanMessage(id='first',content='调查')]
        pending=AIMessage(id='pending',content='',tool_calls=[
            {'id':'p1','name':'read_file','args':{},'type':'tool_call'},
            {'id':'p2','name':'read_file','args':{},'type':'tool_call'}])
        partial=ToolMessage(id='partial',name='read_file',tool_call_id='p1',content='x'*9000)
        messages += [pending,partial]
        for i in range(7): messages += [HumanMessage(content=f'继续{i}'),AIMessage(content='资料'*3000)]
        result=compact_messages(messages,keep_turns=1,level='L2',hard=True)
        self.assertIn(pending,result.messages)
        self.assertIn(partial,result.messages)

    def test_complete_task_index_remains_recoverable_after_repeated_summaries(self):
        from nailong.core.history_archive import HistoryArchive
        with tempfile.TemporaryDirectory() as directory:
            archive=HistoryArchive(Path(directory).resolve())
            messages=[]
            for i in range(60):
                messages += [HumanMessage(content=f'要求{i}：'+str(i)*400),AIMessage(content='调查'*3000)]
            messages += [HumanMessage(content='当前工作')]
            result=compact_messages(messages,keep_turns=1,archive=archive,thread_id='one')
            for i in range(10): result.messages.extend([HumanMessage(content=f'新要求{i}'),AIMessage(content='更多调查'*3000)])
            result=compact_messages(result.messages,keep_turns=1,archive=archive,thread_id='one')
            summary=next(m for m in result.messages if m.additional_kwargs.get('nailong_compact_summary'))
            reference=summary.additional_kwargs['nailong_compact_state'].get('index_reference')
            self.assertTrue(reference)
            self.assertIn(reference,summary.content)
            pages=[]; offset=0
            while True:
                page=archive.read('one',reference,offset=offset,max_chars=6000)
                pages.append(page['content'])
                if not page['truncated']: break
                offset=page['next_offset']
            index=json.loads(''.join(pages))
            self.assertTrue(any('要求25：' in request for request in index['requests']))
            self.assertTrue(any('新要求0' in request for request in index['requests']))
            self.assertGreaterEqual(len(index['evidence']),60)

    def test_single_turn_clears_old_tools_but_keeps_latest_exchange(self):
        messages=[HumanMessage(id='u',content='调查项目')]+exchange(1)+exchange(2)
        result=compact_messages(messages)
        self.assertTrue(result.compacted)
        by_id={m.id:m for m in result.messages}
        self.assertEqual(by_id['ai-1'].tool_calls[0]['id'],'call-1')
        self.assertLess(len(by_id['tool-1'].content),1000)
        self.assertEqual(by_id['tool-2'].content,messages[-1].content)

    def test_pending_parallel_exchange_is_preserved(self):
        messages=[HumanMessage(content='调查')]+exchange(1)
        messages.append(AIMessage(id='pending',content='',tool_calls=[{'id':'p1','name':'read_file','args':{},'type':'tool_call'}, {'id':'p2','name':'read_file','args':{},'type':'tool_call'}]))
        partial=ToolMessage(id='partial',name='read_file',tool_call_id='p1',content='x'*20000)
        messages.append(partial)
        result=compact_messages(messages,min_gain=0)
        self.assertIn(partial,result.messages)
        self.assertEqual(next(m for m in result.messages if m.id=='pending').tool_calls,messages[-2].tool_calls)

    def test_initial_constraint_survives_many_turns_and_repeated_compaction(self):
        messages=[HumanMessage(id='first',content='修复认证。整个任务禁止改动数据库结构。')]
        for i in range(15): messages += [AIMessage(content='调查资料'*2000),HumanMessage(content=f'继续步骤{i}')]
        result=compact_messages(messages)
        self.assertTrue(result.compacted)
        self.assertIn('禁止改动数据库结构','\n'.join(str(m.content) for m in result.messages))
        messages=result.messages
        for i in range(8): messages += [AIMessage(content='更多资料'*2000),HumanMessage(content=f'继续后续{i}')]
        result=compact_messages(messages)
        self.assertIn('禁止改动数据库结构','\n'.join(str(m.content) for m in result.messages))
        self.assertEqual(sum(bool(m.additional_kwargs.get('nailong_compact_summary')) for m in result.messages),1)

    def test_command_failure_retains_exit_code_error_and_archive_reference(self):
        class Archive:
            def save(self,thread_id,message,arguments=None): return 'result-ref'
        payload={'ok':False,'exit_code':7,'timed_out':False,'output_truncated':True,'output':'build log\n'*1000+'ImportError: missing dependency'}
        messages=[HumanMessage(content='检查')]+exchange(1,name='run_command',payload=payload)+exchange(2)
        result=compact_messages(messages,min_gain=0,archive=Archive(),thread_id='one')
        text=next(m.content for m in result.messages if m.id=='tool-1')
        self.assertIn('7',text); self.assertIn('ImportError',text); self.assertIn('result-ref',text)
        self.assertIn('output_truncated',text)

    def test_goal_and_plan_pins_keep_only_latest_version(self):
        messages=[HumanMessage(id=f'goal{i}',content=f'目标进度{i}',additional_kwargs={'nailong_pin':'goal'}) for i in range(8)]
        result=compact_messages(messages,min_gain=0)
        goals=[m for m in result.messages if m.additional_kwargs.get('nailong_pin')=='goal']
        self.assertEqual(len(goals),1); self.assertEqual(goals[0].content,'目标进度7')

    def test_recent_skill_body_becomes_loaded_state_without_replaying_setup(self):
        payload={'ok':True,'name':'probe','path':'skills/probe/SKILL.md','version':'v1','content':'一次性初始化后持续遵守约定。'*1000}
        messages=[HumanMessage(content='使用Skill')]+exchange(1,name='load_skill',payload=payload)+exchange(2)
        result=compact_messages(messages)
        text='\n'.join(str(m.content) for m in result.messages)
        self.assertIn('probe',text); self.assertIn('v1',text); self.assertIn('不要重复',text)
        self.assertNotIn(payload['content'],text)

    def test_hard_limit_can_compact_latest_completed_tool_and_ignore_gain_gate(self):
        messages=[HumanMessage(content='检查')]+exchange(1,size=3000)
        result=compact_messages(messages,hard=True)
        self.assertTrue(result.compacted)
        self.assertIn(messages[0],result.messages)
        self.assertLess(len(result.messages[-1].content),1000)
