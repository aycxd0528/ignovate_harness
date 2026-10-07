import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from nailong.core.sessions import ProjectSessionStore, StreamingRedactor


class ProjectSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name) / "agent-state"
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_store_path_is_project_hashed_and_separated(self):
        store = ProjectSessionStore(self.project, base_dir=self.base)
        expected = hashlib.sha1(str(self.project.resolve()).encode()).hexdigest()[:12]
        self.assertEqual(store.root, self.base.resolve() / "projects" / expected)
        self.assertEqual(store.database_path, store.root / "checkpoints.sqlite")
        self.assertEqual(store.sessions_dir, store.root / "sessions")
        self.assertNotEqual(
            store.root,
            ProjectSessionStore(self.base, base_dir=self.base).root,
        )

    def test_jsonl_events_are_redacted_and_never_store_raw_arguments(self):
        store = ProjectSessionStore(self.project, base_dir=self.base, api_key="secret-sentinel")
        store.append_event(
            "thread-1",
            "approval_needed",
            {
                "reason": "key=secret-sentinel",
                "args": {"content": "raw file body secret-sentinel"},
                "preview": {"diff": "-old\n+new"},
            },
        )

        payload = json.loads(store.session_path("thread-1").read_text(encoding="utf-8"))
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("secret-sentinel", encoded)
        self.assertNotIn("raw file body", encoded)
        self.assertNotIn('"args"', encoded)
        self.assertIn("key=[密钥已隐藏]", encoded)
        self.assertIn("-old", encoded)

    def test_lists_sessions_by_latest_event_with_redacted_summary(self):
        store = ProjectSessionStore(self.project, base_dir=self.base, api_key="private-key")
        store.append_event("old", "final", {"text": "较早回答"}, timestamp="2026-09-30T00:00:00Z")
        store.append_event("new", "final", {"text": "回答含 private-key"}, timestamp="2026-10-01T00:00:00Z")

        sessions = store.list_sessions()

        self.assertEqual([item["thread_id"] for item in sessions], ["new", "old"])
        self.assertEqual(sessions[0]["summary"], "回答含 [密钥已隐藏]")

    def test_resolves_session_id_or_one_based_recent_index(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectSessionStore(directory, base_dir=Path(directory) / "state")
            store.append_event("older", "final", {"text": "old"}, timestamp="2026-09-01T00:00:00+00:00")
            store.append_event("newer", "final", {"text": "new"}, timestamp="2026-09-02T00:00:00+00:00")

            self.assertEqual(store.resolve_session("1"), "newer")
            self.assertEqual(store.resolve_session("older"), "older")
            with self.assertRaises(ValueError):
                store.resolve_session("missing")

    def test_session_id_is_validated_before_building_paths(self):
        store = ProjectSessionStore(self.project, base_dir=self.base)
        with self.assertRaises(ValueError):
            store.session_path("../../outside")

    def test_streaming_redactor_hides_a_key_split_across_chunks(self):
        redactor = StreamingRedactor("secret-sentinel")
        chunks = [redactor.feed("前缀 secret-se"), redactor.feed("ntinel 后缀", final=True)]
        self.assertEqual("".join(chunks), "前缀 [密钥已隐藏] 后缀")

    def test_event_log_can_discard_events_after_a_turn_boundary(self):
        store = ProjectSessionStore(self.project, base_dir=self.base)
        store.append_event("thread", "final", {"text": "上一轮"})
        store.append_event("thread", "turn_start", {"message_ids": ["human-1"]})
        store.append_event("thread", "token", {"text": "本轮回答"})
        store.append_event("thread", "final", {"text": "本轮完成"})
        boundary = store.last_turn_boundary("thread")
        self.assertEqual(boundary[0], 1)

        removed = store.truncate_events("thread", boundary[0] - 1)

        self.assertEqual(removed, 3)
        self.assertEqual([event["data"].get("text") for event in store.read_events("thread")], ["上一轮"])


if __name__ == "__main__":
    unittest.main()
