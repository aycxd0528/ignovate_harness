"""Interrupted complete archives never authorize replacement of source messages."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from nailong.core import history_chunks
from nailong.core.compact import compact_messages
from nailong.core.history_archive import HistoryArchive, MAX_ARCHIVE_BYTES


class HistoryArchiveFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'history'
        self.archive = HistoryArchive(self.root)
        self.message = HumanMessage(id='original', content='x' * (MAX_ARCHIVE_BYTES + 4000) + 'TAIL')

    def manifest(self, reference):
        path = next(self.root.rglob(reference + '.json'))
        return path, json.loads(path.read_bytes())

    def test_write_failure_retains_original_cleans_uncommitted_parts_and_retries(self):
        original_publish = self.archive._publish
        published = []
        def fail(directory, name, payload):
            if published:
                raise OSError('injected disk write failure')
            original_publish(directory, name, payload)
            published.append(name)
        messages = [self.message, AIMessage(content='old answer'), HumanMessage(content='current')]
        before = copy.deepcopy(messages)
        with patch.object(self.archive, '_publish', side_effect=fail):
            with self.assertRaises(ValueError):
                compact_messages(messages, level='L2', hard=True, keep_turns=1,
                                 archive=self.archive, thread_id='one')
        self.assertEqual(messages, before)
        self.assertEqual(list(self.root.rglob('hist_*.json')), [])
        self.assertFalse(list(self.root.rglob(published[0])), 'failed publication fragment must be reclaimed')
        reference = self.archive.save('one', self.message)
        self.assertEqual(self.archive.read('one', reference, offset=len(self.message.content)-4)['content'], 'TAIL')

    def test_hard_exit_before_manifest_and_after_manifest_are_restart_safe(self):
        script = '''
import os, sys
from pathlib import Path
from langchain_core.messages import HumanMessage
from nailong.core.history_archive import HistoryArchive, MAX_ARCHIVE_BYTES
archive = HistoryArchive(Path(sys.argv[1]))
publish = archive._publish
def exit_after_publish(directory, name, payload):
    publish(directory, name, payload)
    if (sys.argv[2] == 'part' and name.endswith('.bin')) or (sys.argv[2] == 'manifest' and name.endswith('.json')):
        os._exit(73)
archive._publish = exit_after_publish
archive.save('one', HumanMessage(id='original', content='x' * (MAX_ARCHIVE_BYTES + 4000) + 'TAIL'))
'''
        for stage in ('part', 'manifest'):
            with self.subTest(stage=stage):
                root = self.root / stage
                process = subprocess.run([sys.executable, '-c', script, str(root), stage],
                                         capture_output=True, text=True, timeout=15)
                self.assertEqual(process.returncode, 73, process.stderr)
                self.assertEqual(len(list(root.rglob('hist_*.json'))), int(stage == 'manifest'))
                archive = HistoryArchive(root)
                reference = archive.save('one', self.message)
                self.assertFalse(archive.read('one', reference)['archive_truncated'])
                self.assertEqual(archive.read('one', reference, offset=len(self.message.content)-4)['content'], 'TAIL')

    def test_malformed_manifests_and_body_identity_are_rejected_without_repair(self):
        reference = self.archive.save('one', self.message)
        path, header = self.manifest(reference)
        original = path.read_bytes()
        malformed = []
        for field, value in [('size_bytes', True), ('size_bytes', history_chunks.MAX_RECORD_BYTES+1),
                             ('sha256', '../outside'), ('parts', []), ('schema_version', True)]:
            row = copy.deepcopy(header); row[field] = value; malformed.append(row)
        for field, value in [('size', True), ('size', 1), ('sha256', '../outside')]:
            row = copy.deepcopy(header); row['parts'][0][field] = value; malformed.append(row)
        for row in malformed:
            with self.subTest(row=row):
                encoded = json.dumps(row).encode(); path.write_bytes(encoded)
                with self.assertRaises(ValueError): self.archive.read('one', reference)
                with self.assertRaises(ValueError): self.archive.save('one', self.message)
                self.assertEqual(path.read_bytes(), encoded)
        path.write_bytes(original)
        self.assertEqual(self.archive.load('one', reference)['message']['data']['content'], self.message.content)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink/FIFO semantics')
    def test_link_and_fifo_parts_are_rejected_without_following_or_blocking(self):
        reference = self.archive.save('one', self.message)
        path, header = self.manifest(reference)
        part = path.parent / ('histpart_' + header['parts'][0]['sha256'] + '.bin')
        original = part.read_bytes(); outside = self.root.parent / 'outside'; outside.write_bytes(original)
        part.unlink(); part.symlink_to(outside)
        with self.assertRaises(ValueError): self.archive.read('one', reference)
        self.assertEqual(outside.read_bytes(), original)
        part.unlink(); os.mkfifo(part)
        with self.assertRaises(ValueError): self.archive.read('one', reference)

    def test_limit_failure_precedes_publication_and_preserves_source(self):
        before = copy.deepcopy(self.message)
        with patch.object(history_chunks, 'MAX_RECORD_BYTES', 2048):
            with self.assertRaisesRegex(ValueError, '未截断或替换'):
                self.archive.save('one', self.message)
        self.assertEqual(self.message, before)
        self.assertFalse(self.root.exists())

    def test_secret_crossing_chunk_boundary_is_absent_from_every_published_object(self):
        secret = 'private-secret-0123456789'; archive = HistoryArchive(self.root, api_key=secret)
        message = HumanMessage(content='x' * (history_chunks.PART_BYTES-9) + secret + 'x' * MAX_ARCHIVE_BYTES,
                               additional_kwargs={'private': secret})
        reference = archive.save('one', message, {'credential': secret})
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(secret.encode(), path.read_bytes())
        record = archive.load('one', reference)
        self.assertNotIn(secret, json.dumps(record, ensure_ascii=False))
        self.assertIn('[密钥已隐藏]', record['message']['data']['content'])

    def test_small_reference_and_default_pages_remain_v1_compatible(self):
        message = HumanMessage(content='legacy small text')
        from langchain_core.messages import message_to_dict
        key = hashlib.sha256(b'one').hexdigest()
        payload = json.dumps({'thread_key':key,'message':message_to_dict(message),'arguments':None},
                             ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
        expected = 'hist_' + hashlib.sha256(payload).hexdigest()
        reference = self.archive.save('one', message)
        self.assertEqual(reference, expected)
        self.assertEqual(self.manifest(reference)[1]['schema_version'], 1)
        page = self.archive.read('one', reference, 7, 5)
        self.assertEqual(page['content'], 'small'); self.assertEqual(page['next_offset'], 12)
        for section in ('unknown', [], None, True):
            with self.subTest(section=section), self.assertRaises(ValueError):
                self.archive.read('one', reference, section=section)


if __name__ == '__main__': unittest.main()
