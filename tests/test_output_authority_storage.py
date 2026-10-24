"""Bounded native disk membership operations for active output replacement."""
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from nailong.core.verification_artifacts import artifact_index
from nailong.core.output_authority import remove_scope


class AuthorityMembershipTests(unittest.TestCase):
    def test_metadata_capacity_rejects_before_publishing_unreadable_marker(self):
        from nailong.core.output_authority import OutputAuthority
        with tempfile.TemporaryDirectory() as temporary,artifact_index() as index:
            root=Path(temporary).resolve()
            owner=OutputAuthority(root/'private',root/'project')
            with patch('nailong.core.output_authority.METADATA_BYTES',8,create=True):
                with self.assertRaises(ValueError):owner.load(index,lambda index:[])
            self.assertFalse(owner.required.exists())
            self.assertFalse((owner.directory/'head.json').exists())

    def test_scope_replacement_releases_capacity_before_inserting_new_members(self):
        with patch('nailong.core.verification_artifacts.MAX_BYTES',5000),artifact_index() as index:
            for i in range(80):index.add(Path('bundle')/str(i))
            remove_scope(index,Path('bundle'))
            self.assertEqual(list(index),[])
            for i in range(80):index.add(Path('bundle')/str(i))
            self.assertEqual(len(list(index)),80)

    def test_component_prefix_does_not_remove_similarly_named_siblings(self):
        with artifact_index() as index:
            for path in ('a','a/file','ab/file','a0','b/a/file'):index.add(Path(path))
            remove_scope(index,Path('a'))
            self.assertEqual([p.as_posix() for p in index],['a0','ab/file','b/a/file'])
