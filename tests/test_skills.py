"""Local Skill discovery, activation, and resource boundary tests."""

import json
import tempfile
import unittest
from pathlib import Path

from nailong.core.skills import SkillRegistry
from nailong.tools.files import FileSession
from tools import build_tools


class SkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.project = base / "project"
        self.project.mkdir()
        self.user_skills = base / "user-skills"
        self.user_skills.mkdir()

    def write_skill(self, directory: Path, name: str, body: str):
        skill = directory / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Review source files.\n---\n\n{body}\n",
            encoding="utf-8",
        )
        return skill

    def registry(self):
        return SkillRegistry(self.project, user_root=self.user_skills)

    def test_project_skill_overrides_same_named_user_skill(self):
        self.write_skill(self.user_skills, "review-code", "user version")
        self.write_skill(self.project / ".agents" / "skills", "review-code", "project version")
        registry = self.registry()

        self.assertEqual(len(registry.list_skills()), 1)
        self.assertEqual(registry.list_skills()[0].source, "项目")
        loaded = registry.load_skill("review-code")
        self.assertTrue(loaded["ok"])
        self.assertIn("project version", loaded["content"])
        self.assertNotIn("user version", loaded["content"])

    def test_resource_read_stays_inside_skill_and_blocks_protected_paths(self):
        skill = self.write_skill(self.project / ".agents" / "skills", "review-code", "body")
        (skill / "references").mkdir()
        (skill / "references" / "guide.md").write_text("safe reference", encoding="utf-8")
        (skill / ".env").write_text("private", encoding="utf-8")
        (skill / "references" / "linked.md").symlink_to(skill / ".env")
        registry = self.registry()

        self.assertEqual(registry.read_resource("review-code", "references/guide.md")["content"], "safe reference")
        self.assertFalse(registry.read_resource("review-code", "../outside.txt")["ok"])
        self.assertFalse(registry.read_resource("review-code", "references/linked.md")["ok"])

    def test_explicit_invocation_and_registered_tools(self):
        self.write_skill(self.project / ".agents" / "skills", "review-code", "Review source files.")
        registry = self.registry()
        prompt, activated = registry.prepare_invocation("$review-code 审查 main.py")
        self.assertTrue(activated)
        self.assertIn('load_skill(name="review-code")', prompt)
        self.assertIn("审查 main.py", prompt)
        with self.assertRaises(ValueError):
            registry.prepare_invocation("$unknown 审查 main.py")

        tools = {tool.name: tool for tool in build_tools(
            file_session=FileSession(self.project), skill_registry=registry
        )}
        result = json.loads(tools["load_skill"].invoke({"name": "review-code"}))
        self.assertTrue(result["ok"])
        self.assertIn("Review source files.", result["content"])


if __name__ == "__main__":
    unittest.main()
