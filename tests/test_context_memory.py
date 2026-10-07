import importlib
import tempfile
import unittest
from pathlib import Path

from nailong.core.memory import MemoryStore, load_project_memory


class ContextMemoryTests(unittest.TestCase):
    maxDiff = 800

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='nailong-context-memory-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'project'
        self.root.mkdir()
        self.user_file = Path(temporary.name) / 'user/.nailong/context.md'
        self.store = MemoryStore(self.root, user_file=self.user_file, api_key='sentinel-secret')

    def write(self, scope, content):
        path = self.store.path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def load(self, **kwargs):
        if not kwargs:
            try:
                module = importlib.import_module('nailong.core.memory_selection')
            except ImportError:
                self.fail('The independent core-memory selector is missing')
            return module.select_core_memory(self.store)
        return load_project_memory(
            self.root, user_file=self.user_file, api_key='sentinel-secret', **kwargs
        )

    def index(self):
        try:
            module = importlib.import_module('nailong.core.memory_selection')
        except ImportError:
            self.fail('The independent memory heading index is missing')
        return module.memory_index(self.store)

    def test_default_core_excludes_large_body_and_prefers_core_heading(self):
        self.write('project', '# Archive\n' + 'old detail\n' * 1000
                   + '## 核心约定\nKeep tests offline.\n'
                   + '## Implementation\nnever-injected-body\n' * 1000)
        entries = self.load()
        self.assertEqual(len(entries), 1)
        self.assertIn('Keep tests offline.', entries[0].content)
        self.assertFalse('never-injected-body' in entries[0].content)
        self.assertNotIn('old detail', entries[0].content)
        self.assertLessEqual(len(entries[0].content), 2048)
        self.assertIn('截断', entries[0].content)
        self.assertIn('read_memory', entries[0].content)

    def test_each_layer_has_its_own_bounded_core(self):
        for scope in ('user', 'project', 'local'):
            self.write(scope, f'{scope} opening\n' + '规则' * 4000 + f'{scope}-tail')
        entries = self.load()
        self.assertEqual([entry.scope for entry in entries], ['user', 'project', 'local'])
        for entry in entries:
            with self.subTest(scope=entry.scope):
                self.assertTrue(entry.content.startswith(f'{entry.scope} opening'))
                self.assertLessEqual(len(entry.content), 2048)
                self.assertNotIn(f'{entry.scope}-tail', entry.content)

    def test_short_memory_and_explicit_legacy_limit_are_preserved(self):
        self.write('project', '  # Notes\nsmall body\n  ')
        self.assertEqual(self.load()[0].content, '# Notes\nsmall body')
        self.write('project', 'first\nimportant tail')
        result = self.load(max_chars=5)
        self.assertEqual(result[0].content, 'first')
        self.assertTrue(hasattr(result, 'reports'), 'Explicit truncation must be reported')
        self.assertEqual(result.reports['project']['status'], 'truncated')

    def test_whitespace_only_memory_is_empty_even_when_over_core_budget(self):
        self.write('project', ' \n' * 5000)
        result = self.load()
        self.assertEqual(result, [])
        self.assertEqual(result.reports['project']['status'], 'empty')

    def test_catalog_indexes_allowed_scopes_titles_versions_without_bodies(self):
        self.write('user', '# Personal\nuser-only-body')
        self.write('project', '# Core\nproject-only-body\n## Evidence\n' + 'large-body\n' * 1000)
        self.write('local', '# Local\nlocal-only-body')
        catalog = self.index()
        for text in ('user', 'project', 'local', 'Personal', 'Core', 'Evidence', 'Local'):
            self.assertIn(text, catalog)
        for body in ('user-only-body', 'project-only-body', 'local-only-body', 'large-body'):
            self.assertNotIn(body, catalog)
        page = self.store.read_section('project')
        self.assertIn(page['version'], catalog)

    def test_exact_heading_read_includes_nested_headings_but_not_sibling(self):
        self.write('project', '# Handbook\nopening\n## Build\nrun tests\n'
                   + '### Flags\n--offline\n## Deploy\nnever here\n')
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        result = method('project', section='Build')
        self.assertEqual(result['content'], '## Build\nrun tests\n### Flags\n--offline\n')
        self.assertEqual(result['scope'], 'project')
        self.assertEqual(result['section'], 'Build')
        self.assertEqual(result['path'], str(self.store.path('project')))
        self.assertEqual(result['offset'], 0)
        self.assertIsNone(result['next_offset'])
        self.assertFalse(result['truncated'])

    def test_full_body_pages_are_capped_and_reassemble_without_gaps(self):
        body = '甲' * 6000 + '乙' * 6000 + '终'
        self.write('local', body)
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        first = method('local', max_chars=99999)
        self.assertEqual(first['content'], '甲' * 6000)
        self.assertEqual(first['next_offset'], 6000)
        self.assertTrue(first['truncated'])
        second = method('local', offset=first['next_offset'])
        self.assertEqual(second['content'], '乙' * 6000)
        self.assertEqual(second['next_offset'], 12000)
        last = method('local', offset=second['next_offset'])
        self.assertEqual(last['content'], '终')
        self.assertIsNone(last['next_offset'])
        self.assertFalse(last['truncated'])
        self.assertEqual(first['content'] + second['content'] + last['content'], body)

    def test_scope_title_and_pagination_are_validated(self):
        self.write('project', '# Allowed\nbody\n')
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        for scope in ('../secret', str(self.user_file), 'other'):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                method(scope)
        for section in ('Allow', 'missing', '../secret'):
            with self.subTest(section=section), self.assertRaises(ValueError):
                method('project', section=section)
        for kwargs in ({'offset': -1}, {'offset': True}, {'max_chars': 0}, {'max_chars': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                method('project', **kwargs)

    def test_secret_is_redacted_before_page_boundaries_and_in_catalog_titles(self):
        self.write('project', '# sentinel-secret\n' + 'x' * 5990 + 'sentinel-secret' + 'end')
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        first = method('project')
        second = method('project', offset=first['next_offset'])
        joined = first['content'] + second['content']
        self.assertNotIn('sentinel-secret', joined)
        self.assertIn('[密钥已隐藏]', joined)
        self.assertNotIn('sentinel-secret', self.index())

    def test_versions_follow_file_changes_even_outside_core(self):
        self.write('project', '# Core\nfixed rule\n# Details\nold')
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        before = method('project', section='Core')
        self.write('project', '# Core\nfixed rule\n# Details\nnew')
        after = method('project', section='Core')
        self.assertEqual(before['content'], after['content'])
        self.assertNotEqual(before['version'], after['version'])
        self.assertIn(after['version'], self.index())
        self.assertNotIn(before['version'], self.index())

    def test_oversized_core_section_keeps_notice_within_default_budget(self):
        self.write('project', '# Core\n' + 'rule\n' * 1000 + '# Appendix\nbody')
        result = self.load()
        self.assertLessEqual(len(result[0].content), 2048)
        self.assertIn('截断', result[0].content)
        self.assertIn('read_memory', result[0].content)
        self.assertNotIn('Appendix', result[0].content)

    def test_explicit_small_core_budget_keeps_source_and_reports_omission(self):
        self.write('project', 'first\nimportant tail')
        module = importlib.import_module('nailong.core.memory_selection')
        result = module.select_core_memory(self.store, max_chars=5)
        self.assertEqual(result[0].content, 'first')
        self.assertEqual(result.reports['project']['status'], 'truncated')
        self.assertTrue(result.reports['project']['truncated'])

    def test_fenced_examples_do_not_create_sections_or_override_real_core(self):
        self.write('project', '```markdown\n# Core\nexample-only\n```\n'
                   + '# Core\nreal rule\n# Details\n' + 'body\n' * 1000)
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        self.assertEqual(method('project', 'Core')['content'], '# Core\nreal rule\n')
        self.assertIn('real rule', self.load()[0].content)
        self.assertNotIn('example-only', self.load()[0].content)

    def test_ambiguous_heading_is_rejected_instead_of_choosing_unrelated_body(self):
        self.write('project', '# Repeated\nfirst\n# Repeated\nsecond\n')
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        with self.assertRaises(ValueError):
            method('project', 'Repeated')

    def test_read_section_stays_in_requested_layer(self):
        for scope in ('user', 'project', 'local'):
            self.write(scope, f'# Rules\n{scope}-only\n')
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        for scope in ('user', 'project', 'local'):
            with self.subTest(scope=scope):
                self.assertEqual(method(scope, 'Rules')['content'], f'# Rules\n{scope}-only\n')

    def test_unreadable_and_oversized_bodies_are_reported_without_injection(self):
        path = self.write('project', 'placeholder')
        path.write_bytes(b'\xff')
        self.write('local', 'x' * 160001)
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        with self.assertRaises(UnicodeError):
            method('project')
        with self.assertRaises(ValueError):
            method('local')
        catalog = self.index()
        self.assertIn('error', catalog)
        self.assertNotIn('x' * 100, catalog)
        self.assertEqual(self.load(), [])

    def test_read_and_catalog_reject_file_and_parent_symlinks(self):
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        outside = self.root.parent / 'unrelated'
        outside.mkdir()
        (outside / 'context.md').write_text('never expose this', encoding='utf-8')
        project_dir = self.root / '.nailong'
        project_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            method('project')
        self.assertNotIn('never expose this', self.index())
        self.assertEqual(self.load().reports['project']['status'], 'error')
        project_dir.unlink()
        project_dir.mkdir()
        self.store.path('project').symlink_to(outside / 'context.md')
        with self.assertRaises(ValueError):
            method('project')
        self.assertNotIn('never expose this', self.index())

    def test_missing_memory_page_reports_no_version(self):
        method = getattr(self.store, 'read_section', None)
        self.assertTrue(callable(method), 'MemoryStore.read_section is missing')
        result = method('user')
        self.assertEqual(result['content'], '')
        self.assertIsNone(result['version'])
        self.assertIsNone(result['next_offset'])
        self.assertFalse(result['truncated'])


if __name__ == '__main__':
    unittest.main()
