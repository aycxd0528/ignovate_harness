"""Lossless checkpoint compression across native saver and GUI readers."""
import asyncio
from contextlib import closing
import importlib
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zlib

import aiosqlite
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver as LegacySaver
from agent import SqliteSaver as AppSaver
from nailong.core.sqlite_saver import OwnedAsyncSqliteSaver
from nailong.core.branches import clone_checkpoint
import test_gui_native as gui_fixture
import test_gui_history_continuation as history_fixture

PREFIX='nailong.zlib.v1:'
PAYLOAD='complete historical analysis '*5000+' ORIGINAL_ANALYSIS_TAIL'
CONFIG={'configurable':{'thread_id':'one','checkpoint_ns':''}}


def checkpoint():
    value=empty_checkpoint()
    value['channel_values']={'messages':[HumanMessage(id='input',content='must retain ORIGINAL_REQUIREMENT'),AIMessage(id='answer',content=PAYLOAD)]}
    value['channel_versions']={'messages':'1'}
    return value


def put(saver,value=None,config=CONFIG):
    value=checkpoint() if value is None else value
    return saver.put(config,value,{'source':'loop','step':1},{'messages':'1'})


class CheckpointCodecTests(unittest.TestCase):
    def codec(self):return importlib.import_module('nailong.core.checkpoint_serde')

    def test_native_types_and_complete_message_fields_roundtrip_losslessly(self):
        codec=self.codec().CheckpointSerializer();native=JsonPlusSerializer()
        message=AIMessage(id='assistant',content=PAYLOAD,additional_kwargs={'reasoning_content':'preserve reasoning '*3000},
            tool_calls=[{'name':'read_file','args':{'path':'original.py'},'id':'call','type':'tool_call'}],
            usage_metadata={'input_tokens':100,'output_tokens':10,'total_tokens':110})
        values=[None,True,123,1.25,b'plain',bytearray(b'mutable'),{'message':message,'tool':ToolMessage(PAYLOAD,name='read_file',tool_call_id='call',status='error')}]
        for value in values:
            with self.subTest(kind=type(value).__name__):
                encoded=codec.dumps_typed(value);expected=native.dumps_typed(value)
                self.assertEqual(codec.loads_typed(encoded),native.loads_typed(expected))
                if encoded[0].startswith(PREFIX):
                    inner,size=encoded[0][len(PREFIX):].split(':')
                    self.assertEqual((inner,zlib.decompress(encoded[1])),expected)
                    self.assertEqual(int(size),len(expected[1]))

    def test_small_and_incompressible_records_keep_native_encoding(self):
        codec=self.codec().CheckpointSerializer();native=JsonPlusSerializer()
        for value in ({'small':'content'},b'x'*16383,random.Random(7).randbytes(65536)):
            with self.subTest(length=len(value)):
                self.assertEqual(codec.dumps_typed(value),native.dumps_typed(value))
        self.assertTrue(codec.dumps_typed(b'x'*16384)[0].startswith(PREFIX))

    def test_legacy_json_msgpack_and_bytes_still_decode(self):
        codec=self.codec().CheckpointSerializer();native=JsonPlusSerializer()
        for value in (checkpoint(),b'legacy',bytearray(b'legacy'),None):
            encoded=native.dumps_typed(value)
            self.assertEqual(codec.loads_typed(encoded),native.loads_typed(encoded))
        self.assertEqual(codec.loads_typed(('json',b'{"legacy":[1,2,3]}')),{'legacy':[1,2,3]})

    def test_corrupt_truncated_concatenated_and_wrong_size_records_are_rejected(self):
        codec=self.codec().CheckpointSerializer();kind,body=codec.dumps_typed(b'x'*20000)
        self.assertTrue(kind.startswith(PREFIX))
        inner,size=kind[len(PREFIX):].split(':');size=int(size)
        damaged=body[:-1]+bytes([body[-1]^1])
        cases=[(kind,body[:-1]),(kind,damaged),(kind,body+b'extra'),(kind,body+body),
               (PREFIX+inner+':'+str(size-1),body),(PREFIX+inner+':'+str(size+1),body)]
        for encoded in cases:
            with self.subTest(kind=encoded[0],length=len(encoded[1])),self.assertRaises(ValueError):codec.loads_typed(encoded)

    def test_unknown_noncanonical_and_excessive_headers_are_rejected(self):
        module=self.codec();codec=module.CheckpointSerializer()
        self.assertEqual(module.MAX_CHECKPOINT_BYTES,64*1024*1024)
        for kind in ('nailong.zlib.v2:bytes:4',PREFIX+'pickle:4',PREFIX+'bytes:-1',PREFIX+'bytes:04',
                     PREFIX+'bytes:４',PREFIX+'bytes:999999999999999999999',PREFIX+'bytes:67108865',
                     PREFIX+'bytes:4:extra',PREFIX+'bytes:'+'0'*100000,'future-unknown-codec'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):codec.loads_typed((kind,b'bad'))

    def test_decoding_enforces_declared_output_bound_and_encoding_preserves_oversized_native_records(self):
        module=self.codec();codec=module.CheckpointSerializer()
        with patch.object(module,'MAX_CHECKPOINT_BYTES',1024):
            data=b'x'*2048
            self.assertEqual(codec.dumps_typed(data),JsonPlusSerializer().dumps_typed(data))
            self.assertEqual(codec.loads_typed((PREFIX+'bytes:1024',zlib.compress(b'x'*1024))),b'x'*1024)
            with self.assertRaises(ValueError):codec.loads_typed((PREFIX+'bytes:1025',zlib.compress(b'x'*1025)))
            with self.assertRaises(ValueError):codec.loads_typed((PREFIX+'bytes:64',zlib.compress(b'x'*2000000)))


class SyncCheckpointCompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.database=Path(self.temp.name)/'checkpoint.sqlite'

    def test_sync_large_checkpoint_is_compressed_and_reopens_with_all_fields(self):
        value=checkpoint();native=JsonPlusSerializer().dumps_typed(value)
        with AppSaver.from_conn_string(str(self.database)) as saver:
            saved=put(saver,value)
            kind,blob=saver.conn.execute('SELECT type,checkpoint FROM checkpoints').fetchone()
            self.assertTrue(kind.startswith(PREFIX),kind)
            self.assertLess(len(blob),len(native[1])//4)
        with AppSaver.from_conn_string(str(self.database)) as reader:
            record=reader.get_tuple(saved)
            self.assertEqual(record.checkpoint,value)
            self.assertEqual(record.metadata['step'],1)

    def test_legacy_and_compressed_snapshots_mix_without_rewriting_old_rows(self):
        original=checkpoint()
        with LegacySaver.from_conn_string(str(self.database)) as old:
            saved=put(old,original)
            before=old.conn.execute('SELECT type,checkpoint FROM checkpoints').fetchone()
        with AppSaver.from_conn_string(str(self.database)) as saver:
            self.assertEqual(saver.get_tuple(saved).checkpoint,original)
            child=checkpoint();child['channel_values']['messages'].append(HumanMessage('later requirement'))
            current=put(saver,child,saved)
            original_row=saver.conn.execute('SELECT type,checkpoint FROM checkpoints WHERE checkpoint_id=?',(original['id'],)).fetchone()
            self.assertEqual(original_row,before)
            self.assertTrue(saver.conn.execute('SELECT type FROM checkpoints WHERE checkpoint_id=?',(child['id'],)).fetchone()[0].startswith(PREFIX))
            self.assertEqual(len(list(saver.list(CONFIG))),2)
            self.assertEqual(saver.get_tuple(current).parent_config,saved)

    def test_exact_historical_fork_excludes_later_source_and_retains_large_analysis(self):
        with AppSaver.from_conn_string(str(self.database)) as saver:
            first=checkpoint();saved=put(saver,first)
            later=checkpoint();later['channel_values']['messages'].append(HumanMessage('LATER_SOURCE_ONLY'))
            put(saver,later,saved)
            target=clone_checkpoint(saver,'one',first['id'],'fork')
            child=saver.get_tuple({'configurable':{'thread_id':'fork','checkpoint_id':target}})
            text='\n'.join(message.content for message in child.checkpoint['channel_values']['messages'])
            self.assertIn(PAYLOAD,text);self.assertIn('ORIGINAL_REQUIREMENT',text);self.assertNotIn('LATER_SOURCE_ONLY',text)

    def test_explicit_custom_serializer_is_preserved(self):
        custom=JsonPlusSerializer()
        with closing(sqlite3.connect(self.database)) as connection:
            saver=AppSaver(connection,serde=custom);saved=put(saver)
            self.assertIs(saver.serde,custom)
            self.assertEqual(connection.execute('SELECT type FROM checkpoints').fetchone()[0],'msgpack')
            self.assertIn(PAYLOAD,str(saver.get_tuple(saved).checkpoint))

    def test_failed_compression_does_not_publish_or_replace_checkpoint(self):
        with AppSaver.from_conn_string(str(self.database)) as saver:
            original=checkpoint();saved=put(saver,original)
            before=saver.conn.execute('SELECT type,checkpoint FROM checkpoints').fetchone()
            with patch('nailong.core.checkpoint_serde.zlib.compress',side_effect=MemoryError('injected compression failure')):
                with self.assertRaises(MemoryError):put(saver,checkpoint(),saved)
            self.assertEqual(saver.conn.execute('SELECT count(*) FROM checkpoints').fetchone()[0],1)
            self.assertEqual(saver.conn.execute('SELECT type,checkpoint FROM checkpoints').fetchone(),before)
            self.assertEqual(saver.get_tuple(CONFIG).checkpoint,original)


class AsyncCheckpointCompressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_async_reads_legacy_and_sync_compressed_snapshots_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'checkpoint.sqlite';legacy=checkpoint();modern=checkpoint()
            with LegacySaver.from_conn_string(str(database)) as saver:old=put(saver,legacy)
            with AppSaver.from_conn_string(str(database)) as saver:new=put(saver,modern,old)
            async with OwnedAsyncSqliteSaver.from_conn_string(str(database)) as reader:
                self.assertEqual((await reader.aget_tuple(old)).checkpoint,legacy)
                self.assertEqual((await reader.aget_tuple(new)).checkpoint,modern)
                self.assertEqual(len([row async for row in reader.alist(CONFIG)]),2)

    async def test_owned_async_large_checkpoint_and_pending_write_cross_to_sync_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'checkpoint.sqlite';value=checkpoint()
            async with OwnedAsyncSqliteSaver.from_conn_string(str(database)) as saver:
                saved=await saver.aput(CONFIG,value,{'source':'loop','step':1},{'messages':'1'})
                await saver.aput_writes(saved,[('large-result',PAYLOAD)],'writer')
                self.assertEqual((await saver.aget_tuple(saved)).checkpoint,value)
            with AppSaver.from_conn_string(str(database)) as reader:
                row=reader.get_tuple(saved)
                self.assertEqual(row.checkpoint,value)
                self.assertIn(('writer','large-result',PAYLOAD),row.pending_writes)
                self.assertTrue(all(row[0].startswith(PREFIX) for row in reader.conn.execute('SELECT type FROM checkpoints UNION ALL SELECT type FROM writes')))

    async def test_explicit_custom_async_serializer_is_preserved(self):
        custom=JsonPlusSerializer()
        async with aiosqlite.connect(':memory:') as connection:
            saver=OwnedAsyncSqliteSaver(connection,serde=custom)
            saved=await saver.aput(CONFIG,checkpoint(),{'source':'loop','step':1},{'messages':'1'})
            self.assertIs(saver.serde,custom)
            async with connection.execute('SELECT type FROM checkpoints') as cursor:self.assertEqual((await cursor.fetchone())[0],'msgpack')
            self.assertIn(PAYLOAD,str((await saver.aget_tuple(saved)).checkpoint))


class GUICompressedCheckpointTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=gui_fixture.NativeGUITests.asyncSetUp
    asyncTearDown=gui_fixture.NativeGUITests.asyncTearDown
    send=gui_fixture.NativeGUITests.send

    async def test_lazy_gui_readers_recover_full_compressed_answer_without_loading_model(self):
        from nailong.gui.controller import GUIController
        node=await self.send(self.root,'长回答')
        await self.controller.worker
        with closing(sqlite3.connect(self.sessions.database_path)) as connection:
            kind=connection.execute('SELECT type FROM checkpoints WHERE checkpoint_id=?',(node['checkpoint_id'],)).fetchone()[0]
        self.assertTrue(kind.startswith(PREFIX),kind)
        await self.controller.close();self.controller=GUIController(self.project,sessions=self.sessions)
        self.assertIsNone(self.controller.service)
        self.assertIsNotNone(self.controller._checkpoint(self.root['thread_id']))
        detail=await self.controller.command('node',{'node_id':node['id']})
        self.assertIn('正文末尾标记',detail['answer'])
        self.assertIsNone(self.controller.service)


class GUICompressedContinuationTests(unittest.TestCase):
    setUp=history_fixture.HistoryContinuationTests.setUp
    graph_turn=history_fixture.HistoryContinuationTests.graph_turn
    import_log=history_fixture.HistoryContinuationTests.import_log

    def test_readonly_imported_continuation_uses_compressed_checkpoint(self):
        from nailong.gui.history import continuation_state
        # Reuse the real graph/log fixture with the application saver instead
        # of its deliberately legacy saver. All validation remains production.
        with patch.object(history_fixture,'SqliteSaver',AppSaver):
            self.graph_turn(answer=AIMessage(PAYLOAD))
        branch=self.import_log(answer=PAYLOAD)
        with closing(sqlite3.connect(self.sessions.database_path)) as connection:
            self.assertTrue(connection.execute('SELECT type FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1').fetchone()[0].startswith(PREFIX))
        result=continuation_state(self.controller,branch)
        self.assertTrue(result['allowed'],result)
        with closing(sqlite3.connect(self.sessions.database_path)) as connection:
            connection.execute("UPDATE checkpoints SET type='nailong.zlib.v9:msgpack:1'")
            connection.commit()
        refused=continuation_state(self.controller,branch)
        self.assertFalse(refused['allowed'],refused)
        self.assertIn('无法核对',refused['reason'])
