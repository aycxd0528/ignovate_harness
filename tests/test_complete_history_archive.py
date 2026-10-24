"""Compaction references preserve complete originals rather than capped previews."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from langchain_core.messages import AIMessage,HumanMessage,ToolMessage,message_to_dict
from nailong.core.compact import compact_messages
from nailong.core.history_archive import HistoryArchive,MAX_ARCHIVE_BYTES


def wire(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode()


def legacy_partial(archive,thread,message,arguments=None):
    """An authentic v1 partial record whose full identity cannot be recovered."""
    archive.save(thread,HumanMessage(content='ensure private directory'))
    key=hashlib.sha256(thread.encode()).hexdigest();serialized=message_to_dict(message)
    reference='hist_'+hashlib.sha256(wire({'thread_key':key,'message':serialized,'arguments':arguments})).hexdigest()
    content=serialized['data']['content'];serialized['data']['content']=str(content)[:500]
    record={'schema_version':1,'thread_key':key,'reference':reference,'message':serialized,
        'kind':message.type,'name':message.name,'message_id':message.id,'arguments':arguments,
        'version':{'historical':True,'content_sha256':hashlib.sha256(str(content).encode()).hexdigest()},
        'archive_truncated':True,'original_content_chars':len(str(content)),'evidence':None}
    path=archive.root/key/(reference+'.json');path.write_bytes(wire(record))
    return reference


class CompleteHistoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.archive=HistoryArchive(self.root/'history')

    def header(self,reference):
        path=next(self.archive.root.rglob(reference+'.json'))
        return path,json.loads(path.read_bytes())

    def test_large_human_tail_is_recoverable_after_native_l2_and_repeated_summary(self):
        marker='TAIL_REQUIREMENT_94a1';prefix='x'*(MAX_ARCHIVE_BYTES+4096)
        original=HumanMessage(id='original',content=prefix+marker)
        messages=[original,AIMessage(content='historical conclusion'),HumanMessage(content='current')]
        result=compact_messages(messages,level='L2',keep_turns=1,hard=True,archive=self.archive,thread_id='one')
        self.assertTrue(result.compacted)
        summary=next(m for m in result.messages if m.additional_kwargs.get('nailong_compact_summary'))
        refs=summary.additional_kwargs['nailong_compact_state']['evidence']
        reference=next(ref for ref in refs if self.archive.read('one',ref)['message_id']=='original')
        page=self.archive.read('one',reference,offset=len(prefix))
        self.assertFalse(page['archive_truncated']);self.assertEqual(page['content'],marker)
        result.messages.extend([AIMessage(content='more progress'),HumanMessage(content='next')])
        repeated=compact_messages(result.messages,level='L2',keep_turns=1,hard=True,
            archive=self.archive,thread_id='one')
        summary=next(m for m in repeated.messages if m.additional_kwargs.get('nailong_compact_summary'))
        self.assertIn(reference,summary.additional_kwargs['nailong_compact_state']['evidence'])
        self.assertEqual(self.archive.read('one',reference,offset=len(prefix))['content'],marker)

    def test_large_unicode_content_crosses_part_boundaries_without_losing_tail(self):
        content='中'*(MAX_ARCHIVE_BYTES//3+100)+'🚀结束'
        reference=self.archive.save('one',HumanMessage(content=content))
        page=self.archive.read('one',reference,offset=len(content)-3)
        self.assertFalse(page['archive_truncated']);self.assertEqual(page['content'],'🚀结束')
        path,header=self.header(reference)
        self.assertEqual(header['schema_version'],2);self.assertGreater(len(header['parts']),1)
        self.assertLessEqual(path.stat().st_size,MAX_ARCHIVE_BYTES)
        for part in header['parts']:
            blob=path.parent/('histpart_'+part['sha256']+'.bin')
            self.assertLessEqual(blob.stat().st_size,1024*1024)

    def test_complete_message_and_arguments_have_explicit_paginated_sections(self):
        message=AIMessage(content='short answer',additional_kwargs={'reasoning_content':'r'*3_000_000+'REASON_END'})
        arguments={'content':'a'*3_000_000+'ARG_END'}
        reference=self.archive.save('one',message,arguments)
        expected_message=wire(message_to_dict(message)).decode();expected_arguments=wire(arguments).decode()
        reasoning=self.archive.read('one',reference,section='message',offset=expected_message.index('REASON_END'),max_chars=10)
        args=self.archive.read('one',reference,section='arguments',offset=expected_arguments.index('ARG_END'),max_chars=7)
        self.assertEqual(reasoning['content'],'REASON_END');self.assertEqual(args['content'],'ARG_END')
        self.assertEqual(reasoning['section'],'message');self.assertFalse(reasoning['archive_truncated'])
        self.assertTrue(self.archive.read('one',reference)['arguments_truncated'],'preview is still bounded')
        self.assertEqual(self.archive.load('one',reference)['arguments'],arguments)

    def test_native_history_tool_exposes_sections_and_enforces_thread_scope(self):
        from tools import build_tools
        from nailong.tools.files import FileSession
        message=AIMessage(content='',additional_kwargs={'reasoning_content':'historical reasoning'})
        reference=self.archive.save('one',message,{'requested_path':'a.txt'})
        tools={tool.name:tool for tool in build_tools(file_session=FileSession(self.root),
            history_archive=self.archive,thread_id='one')}
        body=json.loads(tools['read_history_result'].invoke({'reference':reference,'section':'arguments'}))
        self.assertTrue(body['ok'],body);self.assertEqual(json.loads(body['content']),{'requested_path':'a.txt'})
        other={tool.name:tool for tool in build_tools(file_session=FileSession(self.root),
            history_archive=self.archive,thread_id='other')}
        self.assertFalse(json.loads(other['read_history_result'].invoke({'reference':reference}))['ok'])

    def test_legacy_partial_human_and_tool_cannot_support_destructive_compaction(self):
        human=HumanMessage(id='old',content='original request '*1000)
        tool=ToolMessage(id='result',name='read_file',tool_call_id='read',content='original result '*1000)
        for message in (human,tool):
            with self.subTest(kind=message.type):
                reference=legacy_partial(self.archive,'one',message)
                original_save=self.archive.save
                def save(thread,value,arguments=None):
                    return reference if value.id==message.id else original_save(thread,value,arguments)
                messages=([human,AIMessage(content='answer'),HumanMessage(content='current')]
                    if message is human else [HumanMessage(content='request'),
                        AIMessage(content='',tool_calls=[{'name':'read_file','args':{},'id':'read'}]),tool])
                with patch.object(self.archive,'save',side_effect=save):
                    result=compact_messages(messages,level='L2' if message is human else 'L1',keep_turns=1,
                        hard=True,archive=self.archive,thread_id='one')
                self.assertFalse(result.compacted)
                self.assertEqual(result.messages,messages)

    def test_inherited_partial_reference_cannot_become_a_complete_new_index(self):
        reference=legacy_partial(self.archive,'one',HumanMessage(content='missing old requirement '*1000))
        previous=HumanMessage(content='old summary',additional_kwargs={'nailong_compact_summary':True,
            'nailong_compact_state':{'requests':['old request'],'evidence':[reference],'index_truncated':False}})
        messages=[previous,HumanMessage(content='new requirement'),AIMessage(content='new answer'),HumanMessage(content='current')]
        result=compact_messages(messages,level='L2',keep_turns=1,hard=True,archive=self.archive,thread_id='one')
        self.assertFalse(result.compacted);self.assertEqual(result.messages,messages)

    def test_large_save_is_idempotent_under_concurrent_writers(self):
        message=HumanMessage(id='same',content='x'*(MAX_ARCHIVE_BYTES+4096))
        with ThreadPoolExecutor(max_workers=4) as pool:
            references=list(pool.map(lambda _:HistoryArchive(self.archive.root).save('one',message),range(8)))
        self.assertEqual(len(set(references)),1)
        self.assertFalse(self.archive.read('one',references[0])['archive_truncated'])
        self.assertEqual(len(list(self.archive.root.rglob('*.json'))),1)
        self.assertEqual(list(self.archive.root.rglob('*.tmp')),[])

    def test_corrupt_or_missing_chunk_is_rejected_without_replacing_source_archive(self):
        message=HumanMessage(content='x'*(MAX_ARCHIVE_BYTES+4096))
        reference=self.archive.save('one',message);path,header=self.header(reference)
        part=path.parent/('histpart_'+header['parts'][0]['sha256']+'.bin');original=part.read_bytes()
        part.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.archive.read('one',reference)
        with self.assertRaises(ValueError):self.archive.save('one',message)
        self.assertEqual(part.read_bytes(),b'corrupt')
        part.unlink()
        with self.assertRaises(ValueError):self.archive.read('one',reference)
        part.write_bytes(original)
        self.assertFalse(self.archive.read('one',reference)['archive_truncated'])

    def test_superseded_pin_retains_original_until_discoverable_complete_compaction(self):
        for pin in ('goal','plan'):
            with self.subTest(pin=pin):
                old=HumanMessage(id='old-'+pin,content='x'*(MAX_ARCHIVE_BYTES+4096)+'PIN_TAIL',
                    additional_kwargs={'nailong_pin':pin})
                messages=[old,AIMessage(content='earlier progress'),HumanMessage(id='new-'+pin,
                    content='current snapshot',additional_kwargs={'nailong_pin':pin})]
                released=compact_messages(messages,level='L1',hard=True,archive=self.archive,thread_id='one')
                retained=next(m for m in released.messages if m.id==old.id)
                self.assertEqual(retained.content,old.content)
                self.assertNotIn('nailong_pin',retained.additional_kwargs)
                self.assertNotIn(old.id,released.removed_ids)
                reduced=compact_messages(released.messages,level='L2',keep_turns=1,hard=True,
                    archive=self.archive,thread_id='one')
                summary=next(m for m in reduced.messages if m.additional_kwargs.get('nailong_compact_summary'))
                refs=summary.additional_kwargs['nailong_compact_state']['evidence']
                reference=next(ref for ref in refs if self.archive.read('one',ref)['message_id']==old.id)
                self.assertEqual(self.archive.read('one',reference,offset=len(old.content)-8)['content'],'PIN_TAIL')

    def test_superseded_pin_with_legacy_partial_archive_retains_original(self):
        old=HumanMessage(id='old-pin',content='old requirement '*1000,additional_kwargs={'nailong_pin':'goal'})
        # The compacted candidate sees the demoted message; bind that exact old
        # immutable v1 identity to a genuine partial archive.
        demoted=old.model_copy(update={'additional_kwargs':{}})
        reference=legacy_partial(self.archive,'one',demoted);original_save=self.archive.save
        def save(thread,value,arguments=None):
            return reference if value.id==old.id else original_save(thread,value,arguments)
        messages=[old,AIMessage(content='progress'),HumanMessage(content='latest',additional_kwargs={'nailong_pin':'goal'})]
        with patch.object(self.archive,'save',side_effect=save):
            result=compact_messages(messages,level='L2',keep_turns=1,hard=True,archive=self.archive,thread_id='one')
        self.assertEqual(next(m for m in result.messages if m.id==old.id).content,old.content)
        self.assertFalse(any(m.additional_kwargs.get('nailong_compact_summary') for m in result.messages))


if __name__=='__main__':unittest.main()
