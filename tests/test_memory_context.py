import tempfile
import unittest
import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace
import asyncio
from pathlib import Path

from nailong.core.memory import MemoryStore, load_project_memory


class MemoryFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.user_file = self.root / 'user/context.md'
        self.store = MemoryStore(self.root, user_file=self.user_file)

    def load(self, **kwargs):
        return load_project_memory(self.root, user_file=self.user_file, **kwargs)


class MemoryLoadingTests(MemoryFixtures):
    def test_loading_reports_truncated_content_and_full_version(self):
        self.store.commit(self.store.stage('project', 'first\nimportant tail'))
        result = self.load(max_chars=5)
        self.assertTrue(hasattr(result, 'reports'), '加载结果必须报告截断，不能只返回片段')
        report = result.reports['project']
        self.assertEqual(report['status'], 'truncated')
        self.assertEqual(report['total_chars'], 20)
        self.assertEqual(report['loaded_chars'], 5)
        self.assertTrue(report['version'])
        self.assertEqual(result[0].content, 'first')

    def test_loading_distinguishes_missing_empty_and_unreadable(self):
        self.store.commit(self.store.stage('project', ''))
        local_file = self.store.path('local')
        local_file.write_bytes(b'\xff')
        result = self.load()
        self.assertTrue(hasattr(result, 'reports'), '缺失、空文件与读取失败必须可区分')
        self.assertEqual(result.reports['user']['status'], 'missing')
        self.assertEqual(result.reports['project']['status'], 'empty')
        self.assertEqual(result.reports['local']['status'], 'error')
        self.assertIn('error', result.reports['local'])

    def test_loading_reports_unsafe_path_without_reading_target(self):
        self.store.path('project').parent.mkdir()
        outside = self.root / 'unrelated'
        outside.write_text('must never inject', encoding='utf-8', newline='\n')
        self.store.path('project').symlink_to(outside)
        result = self.load()
        self.assertTrue(hasattr(result, 'reports'), '拒绝链接的结果不能静默消失')
        self.assertEqual(result.reports['project']['status'], 'error')
        self.assertNotIn('must never inject', ''.join(entry.content for entry in result))


class MemoryCatalogTests(MemoryFixtures):
    def topic(self, scope, filename, content):
        directories = {'user': self.user_file.parent / 'memory',
                       'project': self.root / '.nailong/memory',
                       'local': self.root / '.nailong/memory.local'}
        target = directories[scope] / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8', newline='\n')
        return target

    def test_catalog_discovers_all_scopes_without_returning_topic_bodies(self):
        self.topic('user', 'style.md', '---\ndescription: 回答风格\n---\nBODY_USER')
        self.topic('project', 'build.md', '---\ndescription: 构建方式\n---\nBODY_PROJECT')
        self.topic('local', 'paths.md', 'BODY_LOCAL')
        self.assertTrue(hasattr(self.store, 'catalog'), '需要可发现的主题目录')
        result = self.store.catalog()
        self.assertEqual([item['document'] for item in result['documents']],
                         ['user/style.md', 'project/build.md', 'local/paths.md'])
        self.assertEqual(result['documents'][1]['description'], '构建方式')
        self.assertNotIn('BODY_', str(result))
        self.assertTrue(all(item['version'] for item in result['documents']))

    def test_read_document_paginates_entire_legacy_and_topic_files(self):
        self.store.commit(self.store.stage('local', 'legacy-tail'))
        self.topic('project', 'large.md', '头部\n末尾很重要')
        self.assertTrue(hasattr(self.store, 'read_document'), '需要按需分页读取')
        self.assertEqual(self.store.read_document('local/context.md')['content'], 'legacy-tail')
        offset, pages = 0, []
        while True:
            result = self.store.read_document('project/large.md', offset=offset, limit=3)
            self.assertTrue(result['ok'])
            pages.append(result['content'])
            if not result['truncated']:
                break
            self.assertGreater(result['next_offset'], offset)
            offset = result['next_offset']
        self.assertEqual(''.join(pages), '头部\n末尾很重要')

    def test_document_ids_and_linked_topics_cannot_escape_scope(self):
        target = self.topic('project', 'linked.md', 'placeholder')
        outside = self.root / 'secret.md'
        outside.write_text('not allowed', encoding='utf-8', newline='\n')
        target.unlink()
        target.symlink_to(outside)
        self.assertTrue(hasattr(self.store, 'read_document'), '需要受范围限制的读取')
        for document in ('../secret.md', '/secret.md', 'project/../secret.md',
                         'project/nested/secret.md', 'unknown/topic.md', 'project/linked.md'):
            with self.subTest(document=document):
                result = self.store.read_document(document)
                self.assertFalse(result['ok'])
                self.assertNotIn('not allowed', str(result))
        result = self.store.catalog()
        self.assertEqual(result['documents'][0]['status'], 'error')

    def test_catalog_and_reads_report_invalid_and_oversized_files(self):
        bad = self.topic('project', 'encoding.md', 'placeholder')
        bad.write_bytes(b'\xff')
        self.topic('project', 'large.md', 'x' * 160001)
        self.assertTrue(hasattr(self.store, 'catalog'), '目录必须报告读取失败')
        result = self.store.catalog()
        self.assertEqual([item['status'] for item in result['documents']], ['error', 'error'])
        for document in ('project/encoding.md', 'project/large.md'):
            self.assertFalse(self.store.read_document(document)['ok'])

    def test_directory_symlink_is_reported_and_never_followed(self):
        outside = self.root / 'unrelated-dir'
        outside.mkdir()
        (outside / 'hidden.md').write_text('not allowed', encoding='utf-8', newline='\n')
        parent = self.root / '.nailong'
        parent.mkdir()
        (parent / 'memory').symlink_to(outside, target_is_directory=True)
        self.assertTrue(hasattr(self.store, 'catalog'), '目录错误需要可见')
        result = self.store.catalog()
        self.assertTrue(result['diagnostics'])
        self.assertFalse(result['documents'])
        self.assertFalse(self.store.read_document('project/hidden.md')['ok'])

    def test_read_parameters_are_validated_and_empty_file_is_complete(self):
        self.topic('project', 'empty.md', '')
        self.assertTrue(hasattr(self.store, 'read_document'), '分页必须处理空文件与非法参数')
        result = self.store.read_document('project/empty.md')
        self.assertTrue(result['ok'])
        self.assertFalse(result['truncated'])
        self.assertIsNone(result['next_offset'])
        for offset, limit in ((-1, 3), (True, 3), (0, 0), (0, 20000)):
            self.assertFalse(self.store.read_document('project/empty.md', offset, limit)['ok'])

    @unittest.skipIf(os.name == 'nt', 'POSIX os.open race injection; native handle/junction races are tested separately.')
    def test_ancestor_swapped_during_open_cannot_return_outside_content(self):
        target=self.topic('project','file.md','allowed')
        outside=self.root/'outside'; outside.mkdir()
        (outside/'file.md').write_text('outside-secret', newline='\n')
        directory=target.parent; parked=directory.with_name('parked')
        original_open=os.open; swapped=False

        def racing_open(path,flags,*args,**kwargs):
            nonlocal swapped
            if not swapped and (str(path)=='memory' or str(path).endswith('/memory/file.md')):
                swapped=True
                directory.rename(parked); directory.symlink_to(outside,target_is_directory=True)
                try: return original_open(path,flags,*args,**kwargs)
                finally:
                    directory.unlink(); parked.rename(directory)
            return original_open(path,flags,*args,**kwargs)

        with patch('nailong.core.memory.os.open',side_effect=racing_open) as mocked_open:
            with patch('nailong.core.memory.os.supports_dir_fd',os.supports_dir_fd|{mocked_open}):
                result=self.store.read_document('project/file.md')
        self.assertTrue(swapped, '必须实际触发读取时的父目录替换')
        self.assertFalse(result['ok'])
        self.assertNotIn('outside-secret',str(result))

    @unittest.skipIf(os.name == 'nt', 'POSIX filesystem FIFO; Windows devices and reparse points are tested separately.')
    def test_nonregular_topic_is_reported_without_opening_pipe_stream(self):
        directory=self.root/'.nailong/memory'; directory.mkdir(parents=True)
        os.mkfifo(directory/'pipe.md')
        result=self.store.read_document('project/pipe.md')
        self.assertFalse(result['ok'])
        self.assertEqual(self.store.catalog()['documents'][0]['status'],'error')

    def test_topic_limit_is_visible_and_frontmatter_may_end_at_eof(self):
        self.topic('project','000.md','---\ndescription: EOF metadata\n---')
        for index in range(1,202): self.topic('project',f'{index:03}.md','body')
        result=self.store.catalog()
        self.assertEqual(len(result['documents']),200)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['documents'][0]['description'],'EOF metadata')

    def test_clipped_heading_index_is_explicitly_marked(self):
        self.store.commit(self.store.stage('project','# '+'题'*140+'\nbody'))
        row=self.store.catalog()['documents'][0]
        self.assertEqual(len(row['sections'][0]),128)
        self.assertTrue(row['sections_truncated'])


class MemoryBudgetTests(MemoryFixtures):
    def test_small_budget_reclaims_prompt_room_for_long_document_ids(self):
        self.store.commit(self.store.stage('project','摘要'*3000))
        (self.root/'.nailong/settings.json').write_text('{"memory":{"max_tokens":512}}', newline='\n')
        filename='题'*80+'.md'; topic=self.root/'.nailong/memory'/filename
        topic.parent.mkdir(); topic.write_text('长文件名正文', newline='\n')
        from nailong.core.memory_context import MemoryReadContext,estimate_memory_tokens
        snapshot=self.load(); context=MemoryReadContext(snapshot)
        for result in (context.list('project',offset=1),context.read('project/'+filename)):
            self.assertTrue(result['ok'],'合法 Unicode ID 不应永久阻塞分页')
            self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context)+
                                 estimate_memory_tokens(json.dumps(result,ensure_ascii=False)),512)

    def test_directory_diagnostics_cannot_prevent_listing_at_small_budget(self):
        self.store.commit(self.store.stage('project','摘要'*3000))
        (self.root/'.nailong/settings.json').write_text('{"memory":{"max_tokens":512}}', newline='\n')
        for scope in ('user','project','local'):
            directory=self.store.topic_directory(scope); directory.mkdir(parents=True,exist_ok=True)
            (directory/'context.md').write_text('reserved', newline='\n')
        from nailong.core.memory_context import MemoryReadContext
        result=MemoryReadContext(self.load()).list()
        self.assertTrue(result['ok'],'目录诊断应明确缩短，不能占尽第一页')
        self.assertTrue(result.get('diagnostics_truncated') or result['diagnostics'])

    def test_small_budget_supports_long_id_first_discovered_by_read(self):
        self.store.commit(self.store.stage('project','摘要'*3000))
        (self.root/'.nailong/settings.json').write_text('{"memory":{"max_tokens":512}}', newline='\n')
        from nailong.core.memory_context import MemoryReadContext
        context=MemoryReadContext(self.load())
        filename='题'*80+'.md'; topic=self.root/'.nailong/memory'/filename
        topic.parent.mkdir(); topic.write_text('not in initial catalog', newline='\n')
        self.assertTrue(context.read('project/'+filename)['ok'])

    def test_section_envelope_can_be_compacted_without_losing_read_progress(self):
        title='题'*120
        self.store.commit(self.store.stage('project','# '+title+'\n'+'中文正文'*1000))
        (self.root/'.nailong/settings.json').write_text('{"memory":{"max_tokens":512}}', newline='\n')
        from nailong.core.memory_context import MemoryReadContext,estimate_memory_tokens
        snapshot=self.load(); result=MemoryReadContext(snapshot).read_section('project',title)
        self.assertTrue(result['ok'])
        self.assertGreater(result['next_offset'],0)
        self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context)+
                             estimate_memory_tokens(json.dumps(result,ensure_ascii=False)),512)

    def test_large_heading_metadata_does_not_block_catalog_pagination(self):
        headings=''.join('# '+('长标题' * 50)+str(index)+'\n' for index in range(10))
        self.store.commit(self.store.stage('project',headings+'正文'*20000))
        from nailong.core.memory_context import MemoryReadContext,estimate_memory_tokens
        snapshot=self.load(); result=MemoryReadContext(snapshot).list('project')
        self.assertTrue(result['ok'], '大标题索引不能让第一个目录项始终无法读取')
        self.assertEqual(result['documents'][0]['document'],'project/context.md')
        self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context)+
                             estimate_memory_tokens(json.dumps(result,ensure_ascii=False)),snapshot.budget_tokens)

    def test_runtime_read_rejects_changed_version_until_snapshot_refresh(self):
        self.store.commit(self.store.stage('project','original version'))
        from nailong.core.memory_context import MemoryReadContext
        context=MemoryReadContext(self.load())
        self.assertEqual(context.read('project/context.md')['content'],'original version')
        self.store.commit(self.store.stage('project','changed version'))
        result=context.read('project/context.md')
        self.assertFalse(result['ok'], '同一运行不得把新版本正文静默接在旧摘要或旧分页后')
        self.assertEqual(result['status'],'changed')
        self.assertFalse(context.read_section('project')['ok'])
        self.assertEqual(MemoryReadContext(self.load()).read('project/context.md')['content'],'changed version')

    def test_minimum_budget_still_allows_paginated_reads_and_catalog_progress(self):
        self.store.commit(self.store.stage('project','摘要'*3000))
        (self.root/'.nailong/settings.json').write_text('{"memory":{"max_tokens":512}}', newline='\n')
        from nailong.core.memory_context import MemoryReadContext,estimate_memory_tokens
        snapshot=self.load(); context=MemoryReadContext(snapshot)
        for result in (context.list('project'),context.read('project/context.md')):
            self.assertTrue(result['ok'], '最小配置仍需支持一次读取或目录条目')
            self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context)+
                                 estimate_memory_tokens(json.dumps(result,ensure_ascii=False)),512)
        page=context.read('project/context.md')
        self.assertGreater(page['next_offset'],0)

    def test_topic_discovered_on_first_read_is_also_version_bound(self):
        from nailong.core.memory_context import MemoryReadContext
        context=MemoryReadContext(self.load())
        document=self.root/'.nailong/memory/new.md'
        document.parent.mkdir(parents=True); document.write_text('original', newline='\n')
        self.assertTrue(context.read('project/new.md')['ok'])
        document.write_text('changed', newline='\n')
        self.assertEqual(context.read('project/new.md').get('status'),'changed')

    def test_three_layers_share_budget_and_keep_topic_body_on_demand(self):
        for scope in ('user', 'project', 'local'):
            self.store.commit(self.store.stage(scope, '重要约定' * 3000))
        topic = self.root / '.nailong/memory/build.md'
        topic.parent.mkdir()
        topic.write_text('---\ndescription: 构建方式\n---\nTOPIC_BODY_NOT_IN_PROMPT', encoding='utf-8', newline='\n')
        snapshot = self.load()
        self.assertTrue(hasattr(snapshot, 'rendered_context'), '默认上下文必须受统一预算约束')
        from nailong.core.memory_context import estimate_memory_tokens
        self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context), 4000)
        self.assertIn('project/build.md', snapshot.rendered_context)
        self.assertNotIn('TOPIC_BODY_NOT_IN_PROMPT', snapshot.rendered_context)
        for scope in ('user', 'project', 'local'):
            self.assertEqual(snapshot.reports[scope]['status'], 'truncated')
            self.assertGreater(snapshot.reports[scope]['loaded_chars'], 0)

    def test_local_budget_overrides_project_and_invalid_values_are_rejected(self):
        config = self.root / '.nailong/settings.json'
        config.parent.mkdir()
        config.write_text('{"memory":{"max_tokens":2048}}', newline='\n')
        (config.parent / 'settings.local.json').write_text('{"memory":{"max_tokens":1024}}', newline='\n')
        snapshot = self.load()
        self.assertTrue(hasattr(snapshot, 'budget_tokens'), '预算配置必须有效')
        self.assertEqual(snapshot.budget_tokens, 1024)
        for value in (True, 0, 511, 16385, '4096'):
            config.write_text(json.dumps({'memory': {'max_tokens': value}}), newline='\n')
            (config.parent / 'settings.local.json').unlink(missing_ok=True)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load()

    def test_historical_and_concurrent_reads_cannot_overflow_model_memory_budget(self):
        self.store.commit(self.store.stage('project', 'summary'))
        topic = self.root / '.nailong/memory/large.md'
        topic.parent.mkdir()
        topic.write_text('长文本' * 4000, encoding='utf-8', newline='\n')
        snapshot = self.load()
        self.assertTrue(hasattr(snapshot, 'rendered_context'), '需要统一记忆输入预算')
        from langchain_core.messages import ToolMessage, HumanMessage
        from nailong.core.memory_context import MemoryReadContext, estimate_memory_tokens
        context = MemoryReadContext(snapshot)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda offset: context.read('project/large.md', offset, 4000), (0, 4000, 8000)))
        messages = [HumanMessage(content='actual user request')]
        for i, result in enumerate(results):
            messages.append(ToolMessage(content=json.dumps(result, ensure_ascii=False),
                                        name='memory_read', tool_call_id=str(i)))
        original = [message.content for message in messages]
        filtered = context.filter_messages(messages)
        usage = estimate_memory_tokens(snapshot.rendered_context)
        usage += sum(estimate_memory_tokens(message.content) for message in filtered if message.type == 'tool')
        self.assertLessEqual(usage, snapshot.budget_tokens)
        self.assertEqual(filtered[0].content, 'actual user request')
        self.assertEqual([message.content for message in messages], original, '不能改写持久历史')
        self.assertEqual([message.tool_call_id for message in filtered[1:]], ['0', '1', '2'])
        self.assertNotEqual(filtered[-1].content, '')

    def test_budget_limited_read_keeps_progress_pointer_to_unread_tail(self):
        topic = self.root / '.nailong/memory/large.md'
        topic.parent.mkdir(parents=True)
        topic.write_text('中文正文' * 3000 + 'TAIL', encoding='utf-8', newline='\n')
        snapshot = self.load()
        self.assertTrue(hasattr(snapshot, 'rendered_context'), '读取也必须计入 JSON 包装预算')
        from nailong.core.memory_context import MemoryReadContext, estimate_memory_tokens
        result = MemoryReadContext(snapshot).read('project/large.md', 0, 16000)
        self.assertTrue(result['ok'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['next_offset'], len(result['content']))
        self.assertGreater(result['next_offset'], 0)
        self.assertLessEqual(estimate_memory_tokens(snapshot.rendered_context) +
                             estimate_memory_tokens(json.dumps(result, ensure_ascii=False)), snapshot.budget_tokens)


class MemoryCommandTests(MemoryFixtures):
    def actions(self):
        from config import Settings
        from ui.actions import CommandActions
        from ui.controller import CommandController
        settings = Settings('test-api-key', 'https://api.invalid', 'deepseek-chat', self.root)
        factory = SimpleNamespace(settings=settings, memory_store=self.store,
                                  _memory_snapshot=self.load())
        service = SimpleNamespace(runtime_factory=factory)
        return CommandActions(CommandController(settings)), service

    def execute(self, argument):
        from ui.controller import CommandRequest
        actions, service = self.actions()
        return asyncio.run(actions.execute(CommandRequest('/memory', argument, '/memory ' + argument), service, 'thread'))

    def test_show_explains_truncation_without_false_stale_flag(self):
        self.store.commit(self.store.stage('project', '汉字约定' * 10000))
        result = self.execute('show project')
        self.assertIn('已截断', result.text)
        self.assertNotIn('待 reload', result.text)
        self.assertIn('memory_budget', result.data)
        self.assertEqual(result.data['scopes']['project']['status'], 'truncated')

    def test_show_reports_unreadable_scope_without_hiding_other_scopes(self):
        self.store.commit(self.store.stage('project', 'valid memory'))
        self.store.path('local').write_bytes(b'\xff')
        result = self.execute('show')
        self.assertIn('读取失败', result.text)
        self.assertIn('valid memory', result.text)
        self.assertEqual(result.data['scopes']['local']['status'], 'error')

    def test_commands_list_scope_and_read_complete_topic_by_pages(self):
        directory = self.root / '.nailong/memory'
        directory.mkdir(parents=True)
        (directory / 'build.md').write_text('---\ndescription: build\n---\n' + 'x' * 5000 + 'TAIL', newline='\n')
        result = self.execute('list project')
        self.assertEqual(result.data['documents'][0]['document'], 'project/build.md')
        page = self.execute('read project/build.md')
        self.assertTrue(page.data['truncated'])
        tail = self.execute('read project/build.md ' + str(page.data['next_offset']))
        self.assertTrue(tail.data['content'].endswith('TAIL'))


if __name__ == '__main__':
    unittest.main()
