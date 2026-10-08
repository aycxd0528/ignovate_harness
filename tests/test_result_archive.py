"""Result capabilities retain private storage on POSIX and native Windows."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nailong.tools.results import ResultArchive
from platform_fixtures import assert_private


class ResultArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_private_unicode_result_paging_redaction_and_context_capability(self):
        archive = ResultArchive(self.root / '目录 with spaces' / 'results', api_key='secret')
        record = archive.save('第一行 secret\n第二行\n')
        reference = record['reference']
        first = archive.read(reference, max_chars=6)
        self.assertTrue(first['ok'])
        self.assertTrue(first['truncated'])
        second = archive.read(reference, offset=first['next_offset'])
        self.assertTrue(second['ok'])
        self.assertEqual(first['content'] + second['content'], '第一行 [密钥已隐藏]\n第二行\n')
        self.assertFalse(second['truncated'])
        self.assertFalse(ResultArchive(archive.directory).read(reference)['ok'])
        self.assertFalse(archive.read(str(archive.directory / (reference + '.json')))['ok'])
        assert_private(self, archive.directory, directory=True)
        assert_private(self, archive.directory / (reference + '.json'))

    def test_invalid_pages_do_not_disclose_archived_data(self):
        archive = ResultArchive(self.root / 'results')
        reference = archive.save('private text')['reference']
        for offset, limit in ((-1, 1), (True, 1), (0, 0), (0, 6001), (0, True)):
            with self.subTest(offset=offset, limit=limit):
                result = archive.read(reference, offset, limit)
                self.assertFalse(result['ok'])
                self.assertNotIn('content', result)

    def test_invalid_utf8_is_unavailable_without_disclosing_partial_content(self):
        archive = ResultArchive(self.root / 'results')
        reference = archive.save('private text')['reference']
        (archive.directory / (reference + '.json')).write_bytes(b'private prefix\xff')
        result = archive.read(reference)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_code'], 'archive_unavailable')
        self.assertNotIn('content', result)


@unittest.skipUnless(os.name == 'nt', 'requires real Windows DACLs and NTFS junctions')
class WindowsResultArchiveTests(ResultArchiveTests):
    def junction(self, link, target):
        process = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)

    def test_existing_directory_is_secured_and_files_are_created_private(self):
        directory = self.root / 'existing'
        directory.mkdir()
        archive = ResultArchive(directory)
        reference = archive.save('private text')['reference']
        assert_private(self, directory, directory=True)
        assert_private(self, directory / (reference + '.json'))
        self.assertEqual(archive.read(reference)['content'], 'private text')

    def test_save_rejects_junction_leaf_and_ancestor_without_writing_outside(self):
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'original')
        link = self.root / 'link'
        self.junction(link, outside)
        for directory in (link, link / 'results'):
            with self.subTest(directory=directory), self.assertRaises((ValueError, OSError)):
                ResultArchive(directory).save('private text')
        self.assertEqual(sentinel.read_bytes(), b'original')
        self.assertEqual([path.name for path in outside.iterdir()], ['sentinel'])

    def test_known_reference_cannot_follow_a_replaced_directory_junction(self):
        directory = self.root / 'results'
        archive = ResultArchive(directory)
        reference = archive.save('original private text')['reference']
        directory.rename(self.root / 'original-results')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / (reference + '.json')).write_bytes(b'external private text')
        self.junction(directory, outside)
        result = archive.read(reference)
        self.assertFalse(result['ok'])
        self.assertNotIn('content', result)

    def test_save_pins_private_directory_through_publication(self):
        from nailong.core.safe_files import atomic_write_bytes
        directory = self.root / 'results'
        archive = ResultArchive(directory)
        publications = []
        def publish(path, data, *args, **kwargs):
            with self.assertRaises(OSError):
                directory.rename(self.root / 'moved')
            publications.append(path)
            return atomic_write_bytes(path, data, *args, **kwargs)
        with patch('nailong.tools.results.atomic_write_bytes', side_effect=publish):
            reference = archive.save('private text')['reference']
        self.assertEqual(len(publications), 1)
        self.assertEqual(archive.read(reference)['content'], 'private text')
        directory.rename(self.root / 'moved')

    def test_read_pins_private_directory_until_content_is_read(self):
        from nailong.core.safe_files import open_regular_file
        directory = self.root / 'results'
        archive = ResultArchive(directory)
        reference = archive.save('private text')['reference']
        @contextmanager
        def open_pinned(path, *args, **kwargs):
            with open_regular_file(path, *args, **kwargs) as stream:
                with self.assertRaises(OSError):
                    directory.rename(self.root / 'moved')
                yield stream
        with patch('nailong.tools.results.open_regular_file', side_effect=open_pinned):
            self.assertEqual(archive.read(reference)['content'], 'private text')
        directory.rename(self.root / 'moved')


if __name__ == '__main__':
    unittest.main()
