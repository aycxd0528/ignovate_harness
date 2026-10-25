"""The capacity includes fixed formatting and optional summary material."""
import json
from pathlib import Path
import tempfile
import unittest

from langchain_core.messages import AIMessage, HumanMessage
from nailong.core.compact import _render_summary, compact_messages
from nailong.core.history_archive import HistoryArchive

TITLE='较早对话摘要（任务资料，不是系统指令；用户后续纠正优先，历史工具证据需核对当前版本）：'
CONSTRAINTS='持续用户约束（新要求可能替代旧要求，执行前核对）：'
BANNER='完成/待办与验证：以下是助手历史结论，不代表已验证完成；以实际工具证据为准。'


def state(**values):
    result={key:[] for key in ('requests','constraints','actions','conclusions','evidence','skills','request_evidence')}
    result.update(values)
    return result


def history(capacity,offset):
    # This public fixture's complete fixed formatting is 143 characters.
    # The constraint is the latest older request; it is not duplicated as the
    # initial goal excerpt. Exercise both sides of the final rendered boundary.
    constraint='must '+'c'*(capacity-offset)
    return [HumanMessage(id='initial',content='Investigate'),
            AIMessage(id='analysis-first',content='analysis '*10000),
            HumanMessage(id='constraint',content=constraint),
            AIMessage(id='analysis-last',content='analysis '*10000),
            HumanMessage(id='current',content='Continue')],constraint


class SummaryBoundsTests(unittest.TestCase):
    def test_complete_required_format_must_fit_including_banner_and_separators(self):
        requirement='must preserve 完整约束 Ω🙂'
        current=state(constraints=[requirement])
        expected='\n\n'.join((TITLE,CONSTRAINTS,'- '+requirement,BANNER))
        self.assertEqual(_render_summary(current,len(expected)),expected)
        self.assertIsNone(_render_summary(current,len(expected)-1))
        self.assertEqual(current['constraints'],[requirement])

    def test_fixed_format_alone_cannot_bypass_tiny_capacity(self):
        expected=TITLE+'\n\n'+BANNER
        for capacity in (1,len(TITLE),len(expected)-1):
            with self.subTest(capacity=capacity):
                self.assertIsNone(_render_summary(state(),capacity))
        self.assertEqual(_render_summary(state(),len(expected)),expected)

    def test_public_default_explicit_and_absolute_limits_refuse_whole_oversized_constraint(self):
        for capacity,kwargs in ((6000,{}),(6000,{'summary_max_chars':6000}),
                                (20000,{'summary_max_chars':20000}),
                                (1000000,{'summary_max_chars':1000000})):
            with self.subTest(capacity=capacity,kwargs=kwargs):
                messages,requirement=history(capacity,142)
                original=[message.model_dump() for message in messages]
                result=compact_messages(messages,keep_turns=1,level='L2',hard=True,**kwargs)
                self.assertFalse(result.compacted)
                self.assertEqual(result.messages,messages)
                self.assertEqual(result.reason,'protected_context')
                self.assertEqual([message.model_dump() for message in messages],original)
                self.assertEqual(messages[2].content,requirement)

    def test_public_exact_fit_keeps_complete_constraint_and_real_capacity(self):
        for capacity in (6000,20000,1000000):
            with self.subTest(capacity=capacity):
                messages,requirement=history(capacity,143)
                result=compact_messages(messages,keep_turns=1,level='L2',hard=True,summary_max_chars=capacity)
                self.assertTrue(result.compacted,result.reason)
                summary=next(message for message in result.messages if message.additional_kwargs.get('nailong_compact_summary'))
                self.assertEqual(len(summary.content),capacity)
                self.assertIn(requirement,summary.content)
                self.assertIn(requirement,summary.additional_kwargs['nailong_compact_state']['constraints'])
                self.assertEqual(summary.additional_kwargs['nailong_compact_summary_expanded'],capacity>6000)
                self.assertEqual(result.messages[-1],messages[-1])

    def test_optional_headings_excerpts_and_omission_notices_all_count(self):
        requirement='must preserve every constraint 完整'
        current=state(requests=['Initial objective']+['request '+'r'*400]*12,
                      constraints=[requirement],skills=['skill '+'s'*400]*12,
                      actions=['failed evidence '+'a'*400]*12,conclusions=['conclusion '+'c'*400]*12,
                      evidence=['reference-'+str(i)+'e'*80 for i in range(12)],index_reference='complete-index-reference')
        generous=_render_summary(current,20000)
        required=generous.split(BANNER,1)[0]+BANNER
        snapshot=json.dumps(current,ensure_ascii=False)
        for room in (0,1,2,3,10,30,60,100,150,199,200,201,250,500,1000,3000):
            with self.subTest(room=room):
                capacity=len(required)+room
                rendered=_render_summary(current,capacity)
                self.assertIsNotNone(rendered)
                self.assertLessEqual(len(rendered),capacity)
                self.assertTrue(rendered.startswith(required))
                self.assertIn(requirement,rendered)
                self.assertIn(current['index_reference'],rendered)
                self.assertEqual(json.dumps(current,ensure_ascii=False),snapshot)

    def test_real_archive_index_and_original_analysis_survive_exact_capacity(self):
        with tempfile.TemporaryDirectory(prefix='summary exact boundary ') as directory:
            archive=HistoryArchive(Path(directory).resolve())
            requirement='must retain '+'c'*5400+' CONSTRAINT_TAIL'
            analysis='old complete analysis '*10000
            messages=[HumanMessage(id='initial',content='Investigate'),AIMessage(id='old-a',content=analysis),
                      HumanMessage(id='constraint',content=requirement),AIMessage(id='old-b',content=analysis),
                      HumanMessage(id='current',content='Continue')]
            generous=compact_messages(messages,keep_turns=1,level='L2',hard=True,archive=archive,thread_id='one',summary_max_chars=20000)
            initial=generous.messages[0]
            capacity=len(initial.content.split(BANNER,1)[0]+BANNER)
            exact=compact_messages(messages,keep_turns=1,level='L2',hard=True,archive=archive,thread_id='one',summary_max_chars=capacity)
            self.assertTrue(exact.compacted,exact.reason)
            summary=exact.messages[0]
            self.assertEqual(len(summary.content),capacity)
            self.assertIn(requirement,summary.content)
            reference=summary.additional_kwargs['nailong_compact_state']['index_reference']
            self.assertIn(reference,summary.content)
            index=json.loads(archive.load('one',reference)['message']['data']['content'])
            self.assertIn(requirement,index['constraints'])
            self.assertTrue(any(archive.load('one',entry)['message']['data']['content']==analysis for entry in index['evidence']))
            refused=compact_messages(messages,keep_turns=1,level='L2',hard=True,archive=archive,thread_id='one',summary_max_chars=capacity-1)
            self.assertFalse(refused.compacted)
            self.assertEqual(refused.messages,messages)

    def test_generous_capacity_keeps_distinct_optional_items_in_section_order(self):
        current=state(requests=['initial-request','request-older','request-newer'],
                      skills=['skill-older','skill-newer'],actions=['action-older','action-newer'],
                      conclusions=['conclusion-older','conclusion-newer'],evidence=['ref-older','ref-newer'])
        required='\n\n'.join((TITLE,'当前任务最初目标：','initial-request',BANNER))
        optional=(
            '用户要求索引（节选，早期要求不因轮数自动失效）：\n- initial-request\n- request-older\n- request-newer',
            '之前已加载 Skill（不要重复一次性初始化；适用持续规则需按需恢复完整说明）：\n- skill-newer\n- skill-older',
            '工具证据、错误和阻塞记录：\n- action-newer\n- action-older',
            '助手结论与待续工作：\n- conclusion-newer\n- conclusion-older',
            'read_history_result 可恢复的原始要求/证据引用：\n- ref-newer\n- ref-older')
        snapshot=json.dumps(current,ensure_ascii=False)
        self.assertEqual(_render_summary(current,20000),'\n\n'.join((required,*optional)))
        self.assertEqual(json.dumps(current,ensure_ascii=False),snapshot)

    def test_optional_retraction_updates_omission_count_and_can_show_only_notice(self):
        current=state(actions=['old-evidence-AAAA','middle-evidence-BBBB','recent-evidence-CCCC','new-evidence-DDDD'])
        required=TITLE+'\n\n'+BANNER
        # At 148 characters the initial allowance fits two complete entries,
        # but their omission notice forces the second entry to be retracted.
        # The displayed count must therefore change from two to three.
        section='工具证据、错误和阻塞记录：\n- new-evidence-DDDD\n- 另有 3 条，原始记录见证据引用；相关时先检索再执行。'
        omission_only='工具证据、错误和阻塞记录：\n- 另有 4 条，原始记录见证据引用；相关时先检索再执行。'
        snapshot=json.dumps(current,ensure_ascii=False)
        self.assertEqual(_render_summary(current,148),required+'\n\n'+section)
        self.assertEqual(_render_summary(current,147),required+'\n\n'+omission_only)
        self.assertEqual(_render_summary(current,len(required)+10),required)
        self.assertEqual(json.dumps(current,ensure_ascii=False),snapshot)
