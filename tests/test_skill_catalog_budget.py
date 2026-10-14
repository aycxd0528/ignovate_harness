"""Selection catalogs keep every enabled skill while full instructions stay intact."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from nailong.core.model import HarnessChatDeepSeek
from nailong.core.prompts import build_prompt_parts
from nailong.core.skills import SkillRegistry


class SkillCatalogBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.user = self.root / 'user-skills'
        self.user.mkdir()

    def write_skill(self, directory, name, description, body='Full instructions.\n'):
        path = directory / name / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ('---\nname: ' + name + '\ndescription: '
                   + json.dumps(description, ensure_ascii=False) + '\n---\n\n' + body)
        path.write_text(content, encoding='utf-8', newline='\n')
        return path, content

    def registry(self):
        return SkillRegistry(self.project, user_root=self.user)

    def test_all_100_maximum_length_names_reach_native_sdk_system_payload(self):
        names = ['skill-%03d-' % index + 'x' * 54 for index in range(100)]
        for name in names:
            self.write_skill(self.user, name, '查阅官方资料并完整核实相关功能。' * 40)
        registry = self.registry()
        self.assertEqual(len(registry.list_skills()), 100)
        catalog = registry.catalog()
        rows = [json.loads(line) for line in catalog.splitlines()]
        parts = build_prompt_parts(project_root=self.project, tool_names={'load_skill'},
                                   skill_catalog=catalog)
        model = HarnessChatDeepSeek(model='deepseek-flash', api_key='fixture-key',
                                    base_url='https://example.invalid', max_retries=0)
        payload = model._get_request_payload([
            SystemMessage(content=parts['base_system'] + parts['fixed_memory'] + parts['skill_catalog']),
            HumanMessage(content='Choose the relevant local skill.'),
        ])
        system = payload['messages'][0]['content']
        self.assertIn(names[-1], system)
        for name in names:
            self.assertIn(name, system)
        self.assertEqual([row['name'] for row in rows], names)
        self.assertLessEqual(len(catalog), 16000)
        self.assertTrue(all(row['source'] == '用户' for row in rows))
        self.assertTrue(all(len(row['description']) <= 80 for row in rows))

    def test_long_description_is_marked_but_load_preserves_source_body_and_version(self):
        description = 'Read the complete specification and check every boundary. ' * 16
        path, content = self.write_skill(self.user, 'complete-guide', description,
                                        body='Detailed body.\n' * 200)
        registry = self.registry()
        row = json.loads(registry.catalog())
        self.assertLessEqual(len(row['description']), 80)
        self.assertIn('[摘录]', row['description'])
        self.assertTrue(description.startswith(row['description'].split('…[摘录]')[0]))
        loaded = registry.load_skill('complete-guide')
        self.assertTrue(loaded['ok'])
        self.assertEqual(loaded['content'], content)
        self.assertEqual(loaded['source'], '用户')
        self.assertEqual(loaded['path'], str(path.resolve()))
        self.assertEqual(loaded['version'], hashlib.sha256(content.encode('utf-8')).hexdigest())
        self.assertEqual(registry.list_skills()[0].description, description.strip())
        self.assertIn('权限与审批', loaded['message'])

    def test_global_budget_counts_actual_quote_and_backslash_escaping(self):
        names = ['skill-%03d-' % index + 'q' * 54 for index in range(100)]
        for index, name in enumerate(names):
            description = 'ok' if index == 0 else ('"\\' * 512)
            self.write_skill(self.user, name, description)
        registry = self.registry()
        catalog = registry.catalog()
        rows = [json.loads(line) for line in catalog.splitlines()]
        self.assertEqual([row['name'] for row in rows], names)
        self.assertLessEqual(len(catalog), 16000)
        self.assertEqual(rows[0]['description'], 'ok')
        self.assertTrue(all(len(row['description']) <= 80 for row in rows))
        self.assertTrue(all('[摘录]' in row['description'] for row in rows[1:]))
        # With 100 names of length 64, 80 escaped description characters each
        # would exceed the budget even after removing JSON's separator spaces.
        self.assertTrue(all(len(row['description']) < 80 for row in rows[1:]))
        self.assertEqual(len(registry.list_skills()), 100)
        self.assertEqual(registry.load_skill(names[-1])['content'],
                         (self.user / names[-1] / 'SKILL.md').read_text())

    def test_short_descriptions_stay_exact_and_catalog_json_is_compact(self):
        for length in (79, 80, 81):
            self.write_skill(self.user, 'length-%d' % length, '甲' * length)
        registry = self.registry()
        rows = {row['name']: row for row in map(json.loads, registry.catalog().splitlines())}
        self.assertEqual(rows['length-79']['description'], '甲' * 79)
        self.assertEqual(rows['length-80']['description'], '甲' * 80)
        self.assertLessEqual(len(rows['length-81']['description']), 80)
        self.assertIn('[摘录]', rows['length-81']['description'])
        self.assertNotIn('"name": ', registry.catalog())
        self.assertNotIn(', "source"', registry.catalog())

    def test_disabled_skill_stays_registered_but_is_absent_from_catalog_and_load(self):
        for name in ('alpha', 'disabled-guide', 'omega'):
            self.write_skill(self.user, name, 'Complete guide. ' * 40)
        registry = self.registry()
        registry.set_enabled('disabled-guide', False)
        rows = [json.loads(line) for line in registry.catalog().splitlines()]
        self.assertEqual([row['name'] for row in rows], ['alpha', 'omega'])
        self.assertEqual(len(registry.list_skills(include_disabled=True)), 3)
        self.assertFalse(registry.load_skill('disabled-guide')['ok'])
        self.assertTrue(all(len(row['description']) <= 80 for row in rows))
        restored = self.registry()
        self.assertEqual([skill.name for skill in restored.list_skills()], ['alpha', 'omega'])

    def test_project_override_keeps_its_full_body_and_source_in_selection_catalog(self):
        self.write_skill(self.user, 'shared-guide', 'User description.', body='User body.\n')
        _, project_content = self.write_skill(self.project / '.agents/skills',
            'shared-guide', 'Project description.', body='Project body.\n')
        registry = self.registry()
        self.assertEqual(json.loads(registry.catalog()),
            {'name': 'shared-guide', 'source': '项目', 'description': 'Project description.'})
        loaded = registry.load_skill('shared-guide')
        self.assertEqual(loaded['content'], project_content)
        self.assertEqual(loaded['source'], '项目')


if __name__ == '__main__':
    unittest.main()
