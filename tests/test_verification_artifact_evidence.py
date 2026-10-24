"""Native verification evidence must survive big builds and bind delivered bytes."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import threading
from unittest.mock import patch

from test_managed_workflow import WorkflowFixture


class ArtifactEvidenceTests(WorkflowFixture):
    async def test_late_delivery_observation_cannot_erase_new_verification(self):
        from types import SimpleNamespace
        from agent_service import AgentService
        self.configure_build()
        await self.verify()
        factory = SimpleNamespace(settings=SimpleNamespace(project_root=self.root),task_store=self.tasks)
        service = AgentService(factory,permission_engine=self.engine,session_store=self.store)
        (self.root/'bundle/000.txt').write_text('unverified output')
        started, release = asyncio.Event(), asyncio.Event()
        native = service._task_input_fingerprint
        async def observe_then_wait(task):
            current = await native(task)
            started.set()
            await release.wait()
            return current
        with patch.object(service,'_task_input_fingerprint',side_effect=observe_then_wait):
            pending = asyncio.create_task(service.delivery_report('thread'))
            try:
                await asyncio.wait_for(started.wait(),3)
                self.assertEqual((await self.verify())['status'],'passed')
                fresh = self.tasks.snapshot('thread')
            finally:
                release.set()
                await pending
        self.assertEqual(self.tasks.snapshot('thread'),fresh)

    async def test_healthy_latest_manifest_survives_corrupt_superseded_receipt(self):
        from nailong.core.verification import current_evidence
        self.configure_build()
        first = await self.verify()
        second = await self.verify()
        directory = self.store.root/'verification-artifacts-v1'
        (directory/(first['artifact_manifest']['id']+'.json')).unlink()
        self.assertEqual(current_evidence(self.root,second,self.store.root),second['input_after'])
        third = await self.verify()
        self.assertEqual(third['status'],'passed')
        # Active ownership is independently sealed. Modern builds no longer
        # read superseded event manifests merely to recover output rights.
        self.assertEqual(third['artifact_authority_warnings'], [])

    async def test_missing_output_parent_directories_can_be_built_and_reverified(self):
        self.steps = [{'name':'build','kind':'build','generated_paths':['build/release/output.txt'],
            'command':self.command("from pathlib import Path;p=Path('build/release/output.txt');"
                "p.parent.mkdir(parents=True,exist_ok=True);p.write_text('artifact')")}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        first = await self.verify()
        self.assertEqual(first['status'],'passed')
        self.assertEqual(first['generated_count'],3)
        self.assertEqual((await self.verify())['status'],'passed')
        (self.root/'build/unregistered-source.py').write_text('new input')
        from nailong.core.verification import current_evidence
        self.assertNotEqual(current_evidence(self.root,first,self.store.root),first['input_after'])

    async def test_mutable_input_directory_permissions_cannot_change_and_restore(self):
        if os.name == 'nt':
            self.skipTest('native POSIX chmod contract')
        (self.root/'sources').mkdir(mode=0o700)
        (self.root/'sources/input.txt').write_text('input')
        self.steps = [{'name':'build','kind':'build','generated_paths':['bundle','sources/generated.txt'],
            'command':self.command("from pathlib import Path;import stat;p=Path('sources');p.chmod(0o755);"
                "assert stat.S_IMODE(p.stat().st_mode)==0o755;p.chmod(0o700);"
                "(p/'generated.txt').write_text('artifact');Path('bundle').mkdir()")}]
        self.configure(self.steps)
        self.begin(scope=['sources/input.txt'])
        with self.assertRaises(ValueError):
            await self.verify()
        self.assertEqual(self.store.read_events('thread')[-1]['data']['status'],'invalidated')

    async def test_legacy_file_only_receipt_allows_directory_reverification(self):
        from nailong.core.sessions import ProjectSessionStore
        from nailong.core.verification import VerificationService, current_evidence
        self.configure_build()
        original = await self.verify()
        legacy_store = ProjectSessionStore(self.root, base_dir=Path(self.data.name)/'legacy')
        legacy = {**original, 'generated_paths':['bundle/000.txt']}
        legacy.pop('artifact_manifest')
        legacy.pop('output_authority_version', None)
        legacy.pop('output_authority_token', None)
        legacy_store.append_event('thread', 'verification', legacy)
        with self.assertRaises(ValueError):
            current_evidence(self.root, legacy, legacy_store.root)
        service = VerificationService(self.root, legacy_store)
        result = await service.run('thread', approval=lambda *args:'approve_once', permission_engine=self.engine)
        self.assertEqual(result['status'], 'passed')

    async def test_malformed_or_missing_manifest_is_unknown_without_external_reads(self):
        from nailong.core.verification import current_evidence
        self.configure_build()
        record = await self.verify()
        directory = self.store.root/'verification-artifacts-v1'
        valid = json.loads((directory/(record['artifact_manifest']['id']+'.json')).read_bytes())
        for damaged in ([], {**valid, 'parts':[None]}, {**valid,'parts':[[None, 1]]}, {**valid,'count':True}):
            data = json.dumps(damaged).encode()
            identity = hashlib.sha256(data).hexdigest()
            (directory/(identity+'.json')).write_bytes(data)
            with self.subTest(damaged=damaged), self.assertRaises(ValueError):
                current_evidence(self.root, {**record,'artifact_manifest':{'version':1,'id':identity}}, self.store.root)
        (directory/(record['artifact_manifest']['id']+'.json')).unlink()
        with self.assertRaises((ValueError, OSError)):
            current_evidence(self.root, record, self.store.root)

    async def test_delivery_report_and_auto_verification_bind_artifacts(self):
        from types import SimpleNamespace
        from agent_service import AgentService
        from nailong.core.verification import verify_goal_if_configured
        goal = self.configure_build()
        await self.verify()
        factory = SimpleNamespace(settings=SimpleNamespace(project_root=self.root, api_key=''),
            goal_store=self.goals, task_store=self.tasks)
        service = AgentService(factory, permission_engine=self.engine, session_store=self.store)
        self.assertEqual((await service.delivery_report('thread'))['status'], 'verified')
        self.assertIsNone(await verify_goal_if_configured(service, factory, goal, 'thread'))
        (self.root / 'bundle/000.txt').write_text('changed delivered bytes')
        self.assertNotEqual((await service.delivery_report('thread'))['status'], 'verified')
        repeated = await verify_goal_if_configured(service, factory, goal, 'thread', approval=lambda *args:'approve_once')
        self.assertIsNotNone(repeated)
        self.assertEqual(repeated['status'], 'passed')

    async def test_manifest_bigger_than_task_document_survives_restart(self):
        from nailong.core.verification import current_evidence
        from nailong.core.sessions import ProjectSessionStore
        self.steps = [{'name': 'build', 'kind': 'build', 'generated_paths': ['bundle'],
            'command': self.command("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
                "[(p/('x'*190+str(i)+'.txt')).write_text('artifact') for i in range(5000)]")}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        first = await self.verify()
        self.assertEqual(first['status'], 'passed')
        directory = self.store.root / 'verification-artifacts-v1'
        header = json.loads((directory/(first['artifact_manifest']['id']+'.json')).read_bytes())
        self.assertGreater(header['bytes'], 1024*1024)
        self.assertGreater(len(header['parts']), 1)
        reopened = ProjectSessionStore(self.root, base_dir=self.data.name)
        record = reopened.read_events('thread')[-1]['data']
        self.assertEqual(record['generated_count'], 5001)
        self.assertEqual(current_evidence(self.root, record, reopened.root), first['input_after'])
        self.assertLess(len(json.dumps(self.tasks.snapshot('thread'))), 100_000)
        self.assertEqual((await self.verify())['status'], 'passed')

    async def test_declared_link_root_can_be_verified_repeatedly(self):
        if os.name == 'nt':
            self.skipTest('native POSIX symlink contract')
        self.steps = [{'name':'build', 'kind':'build', 'generated_paths':['alias'],
            'command':self.command("from pathlib import Path;p=Path('alias');p.unlink(missing_ok=True);p.symlink_to('source.py')")}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        self.assertEqual((await self.verify())['status'], 'passed')
        self.assertEqual((await self.verify())['status'], 'passed')

    async def test_validation_result_is_frozen_before_initial_observation(self):
        self.configure_build()
        await self.verify()
        service = self.verification()
        validate = service._validate_generated
        def validate_then_change(*args):
            result = validate(*args)
            (self.root / 'bundle/user-source.txt').write_text('input added after validation')
            return result
        with patch.object(service, '_validate_generated', side_effect=validate_then_change):
            record = await service.run('thread', approval=lambda *args: 'approve_once', permission_engine=self.engine)
        self.assertEqual(record['status'], 'invalidated')
        self.assertFalse(record['steps'][0]['started'])

    async def test_feedback_observes_inputs_and_artifacts_in_one_snapshot(self):
        import nailong.core.verification as verification
        self.configure_build()
        record = await self.verify()
        artifact = self.root / 'bundle/000.txt'
        original = artifact.read_bytes()
        artifact.write_text('changed artifact')
        native = verification.snapshot_identity
        def observe_then_change(*args, **kwargs):
            result = native(*args, **kwargs)
            (self.root / 'source.py').write_text('changed source after observation')
            artifact.write_bytes(original)
            return result
        with patch.object(verification, 'snapshot_identity', side_effect=observe_then_change) as scan:
            with self.assertRaises(ValueError):
                verification.current_evidence(self.root, record, self.store.root, ['feedback.txt'])
        self.assertEqual(scan.call_count, 1)

    async def test_mutable_output_parent_cannot_be_replaced_and_restored(self):
        (self.root / 'sources').mkdir()
        (self.root / 'sources/input.txt').write_text('original untested input')
        (self.root / '__pycache__').mkdir()
        self.steps = [{'name': 'build', 'kind': 'build',
            'generated_paths': ['bundle', 'sources/generated.txt'], 'command': self.command(
            "from pathlib import Path;import shutil;p=Path('sources');h=Path('__pycache__/hold');"
            "p.rename(h);p.mkdir();(p/'input.txt').write_text('temporary passing input');"
            "assert (p/'input.txt').read_text()=='temporary passing input';shutil.rmtree(p);h.rename(p);"
            "(p/'generated.txt').write_text('output');Path('bundle').mkdir()")}]
        self.configure(self.steps)
        self.begin(scope=['sources/input.txt'])
        if os.name == 'nt':
            self.assertEqual((await self.verify())['status'], 'invalidated')
        else:
            with self.assertRaises(ValueError):
                await self.verify()
            self.assertEqual(self.store.read_events('thread')[-1]['data']['status'], 'invalidated')

    async def test_approval_cannot_reclassify_new_input_as_generated_output(self):
        self.configure_build()
        await self.verify()
        def approve(*args):
            (self.root / 'bundle/user-source.txt').write_text('new user input')
            return 'approve_once'
        record = await self.verify(approval=approve)
        self.assertEqual(record['status'], 'invalidated')
        self.assertFalse(record['steps'][0]['started'])

    async def test_directory_swap_and_restore_during_command_invalidates_verification(self):
        source = self.root / 'sources'
        source.mkdir()
        (source / 'input.txt').write_text('original untested input')
        (self.root / '__pycache__').mkdir()
        self.steps = [{'name': 'test', 'kind': 'test', 'command': self.command(
            "from pathlib import Path;import shutil;p=Path('sources');h=Path('__pycache__/hold');"
            "p.rename(h);p.mkdir();(p/'input.txt').write_text('temporary passing input');"
            "assert (p/'input.txt').read_text()=='temporary passing input';shutil.rmtree(p);h.rename(p)")}]
        self.configure(self.steps)
        self.begin(scope=['sources/input.txt'])
        self.assertEqual((await self.verify())['status'], 'invalidated')

    async def test_chunked_manifest_is_complete_private_and_corruption_fails_closed(self):
        from nailong.core.verification import current_evidence
        self.configure_build(101)
        with patch('nailong.core.verification_artifacts.CHUNK_BYTES', 1024):
            record = await self.verify()
            reference = record['artifact_manifest']
            self.assertLess(len(json.dumps(reference)), 128)
            self.assertEqual(record['generated_count'], 102)
            self.assertFalse(record['generated_preview_complete'])
            self.assertEqual(len(record['generated_paths']), 64)
            directory = self.store.root / 'verification-artifacts-v1'
            header = json.loads((directory / (reference['id'] + '.json')).read_bytes())
            self.assertGreater(len(header['parts']), 1)
            for identity, size in header['parts']:
                path = directory / (identity + '.part')
                self.assertLessEqual(size, 1024)
                if os.name != 'nt':
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(current_evidence(self.root, record, self.store.root), record['input_after'])
            task = self.tasks.snapshot('thread')
            self.assertEqual(current_evidence(self.root, task, self.store.root), record['input_after'])
            # GoalStore lives under a different fixed private boundary.
            self.assertTrue((self.goals.path.parent / 'verification-artifacts-v1' / (reference['id'] + '.json')).is_file())
            last = directory / (header['parts'][-1][0] + '.part')
            last.write_bytes(last.read_bytes()[:-1])
            with self.assertRaises(ValueError):
                current_evidence(self.root, task, self.store.root)
            goal = self.goals.active()
            self.assertFalse(self.goals.update(goal.id, state='complete', thread_id='thread')[0])

    async def test_manifest_publication_failure_never_registers_partial_proof(self):
        import nailong.core.verification_artifacts as artifacts
        self.configure_build(101)
        original = artifacts._publish
        def publish(boundary, data, suffix):
            if suffix == '.json' and json.loads(data).get('run_id') != 'preflight':
                raise OSError('native publication failure')
            return original(boundary, data, suffix)
        with patch.object(artifacts, '_publish', side_effect=publish):
            with self.assertRaises(OSError):
                await self.verify()
        self.assertFalse(self.goals.active().verification_succeeded)
        self.assertFalse(self.tasks.snapshot('thread').get('verification_manifest'))
        self.assertEqual(self.store.read_events('thread')[-1]['data']['status'], 'invalidated')

    async def test_complete_manifest_over_limit_is_rejected_without_clipping(self):
        self.configure_build(101)
        with patch('nailong.core.verification_artifacts.MAX_BYTES', 1024):
            with self.assertRaises(ValueError):
                await self.verify()
        self.assertFalse(self.goals.active().verification_succeeded)

    async def test_generated_internal_links_are_covered_and_reverified(self):
        if os.name == 'nt':
            self.skipTest('POSIX link contract; native Windows reparse points remain rejected')
        from nailong.core.verification import current_evidence
        self.steps = [{'name': 'build', 'kind': 'build', 'generated_paths': ['bundle'],
            'command': self.command("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
                "(p/'product').write_text('artifact');l=p/'current';l.unlink(missing_ok=True);l.symlink_to('product')")}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        first = await self.verify()
        self.assertEqual(first['status'], 'passed')
        self.assertEqual((await self.verify())['status'], 'passed')
        (self.root / 'bundle/current').unlink()
        (self.root / 'bundle/current').symlink_to('missing')
        with self.assertRaises(ValueError):
            current_evidence(self.root, first, self.store.root)

    async def test_output_enumeration_and_publication_run_off_event_loop(self):
        import nailong.core.verification_artifacts as artifacts
        self.configure_build(101)
        main = threading.get_ident()
        threads = []
        original = artifacts.enumerate_generated
        def enumerate_native(*args):
            threads.append(threading.get_ident())
            return original(*args)
        with patch.object(artifacts, 'enumerate_generated', side_effect=enumerate_native):
            self.assertEqual((await self.verify())['status'], 'passed')
        self.assertGreaterEqual(len(threads), 2)
        self.assertTrue(all(identity != main for identity in threads))

    async def test_recap_checks_current_artifacts_and_survives_reopen(self):
        from nailong.core.session_actions import SessionActions
        from nailong.core.sessions import ProjectSessionStore
        self.configure_build(101)
        await self.verify()
        actions = SessionActions(self.root, ProjectSessionStore(self.root, base_dir=self.data.name))
        self.assertIn('已验证：', await actions.recap_async('thread'))
        (self.root / 'bundle/100.txt').unlink()
        self.assertIn('未验证：', await actions.recap_async('thread'))

    async def test_input_changed_and_restored_during_command_invalidates_verification(self):
        self.steps = [{'name': 'test', 'kind': 'test', 'command': self.command(
            "from pathlib import Path;p=Path('source.py');original=p.read_bytes();"
            "p.write_text('temporary input');assert p.read_text()=='temporary input';p.write_bytes(original)")}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        self.assertEqual((await self.verify())['status'], 'invalidated')

    def configure_build(self, count=1):
        self.steps = [{'name': 'build', 'kind': 'build',
            'command': self.command("from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
                f"[(p/f'{{i:03}}.txt').write_text('tested artifact') for i in range({count})]"),
            'generated_paths': ['bundle']}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'artifact', '交付构建产物', kind='build')
        self.tasks.bind_verification('thread', 'artifact', 'build', ['source.py'])
        return self.goals.create('修复功能', thread_id='thread')

    async def test_mutated_generated_artifact_cannot_complete_goal(self):
        goal = self.configure_build()
        self.assertEqual((await self.verify())['status'], 'passed')
        (self.root / 'bundle/000.txt').write_text('modified after verification')
        completed, reason, _ = self.goals.update(goal.id, state='complete', thread_id='thread')
        self.assertFalse(completed, reason)
        self.assertNotEqual(self.tasks.snapshot('thread')['lifecycle'], 'completed')

    async def test_more_than_100_generated_outputs_can_be_verified_again_after_restart(self):
        self.configure_build(101)
        self.assertEqual((await self.verify())['status'], 'passed')
        from nailong.core.verification import VerificationService
        from nailong.core.sessions import ProjectSessionStore
        reopened = ProjectSessionStore(self.root, base_dir=self.data.name)
        verifier = VerificationService(self.root, reopened, self.goals, task_store=self.tasks)
        record = await verifier.run('thread', approval=lambda *args: 'approve_once', permission_engine=self.engine)
        self.assertEqual(record['status'], 'passed')

    async def test_removed_required_empty_directory_cannot_complete_goal(self):
        (self.root / 'assets').mkdir()
        self.steps = [{'name': 'test', 'kind': 'test',
            'command': self.command("from pathlib import Path;assert Path('assets').is_dir()")}]
        self.configure(self.steps)
        self.begin(scope=['source.py'])
        self.tasks.add_acceptance('thread', 'assets', '需要资源目录', kind='test')
        self.tasks.bind_verification('thread', 'assets', 'test', ['source.py'])
        goal = self.goals.create('修复功能', thread_id='thread')
        self.assertEqual((await self.verify())['status'], 'passed')
        (self.root / 'assets').rmdir()
        self.assertFalse(self.goals.update(goal.id, state='complete', thread_id='thread')[0])
