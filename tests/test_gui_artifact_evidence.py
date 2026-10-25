"""Real process-produced artifact receipts through GUI read/decision boundaries."""
import json
import sys
import unittest

import test_gui_controller as fixture
from platform_fixtures import shell_join
from nailong.core.permissions import PermissionEngine
from nailong.core.task_state import TaskStore
from nailong.core.verification import VerificationService, current_evidence


class GUIArtifactEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ControllerTests.asyncSetUp(self)
        self.tasks = TaskStore(self.sessions)
        self.controller.service.task_store = self.tasks
        self.thread = self.branch['thread_id']
        (self.project/'source.py').write_text('source input')
        (self.project/'.nailong').mkdir(exist_ok=True)
        command = shell_join([sys.executable, '-B', '-c',
            "from pathlib import Path;p=Path('bundle');p.mkdir(exist_ok=True);"
            "[(p/f'{i:03}.txt').write_text('artifact') for i in range(101)]"])
        (self.project/'.nailong/settings.json').write_text(json.dumps({'verification':{'steps':[
            {'name':'build','kind':'build','command':command,'generated_paths':['bundle']}]}}))
        self.tasks.begin(self.thread, 'deliver artifacts', scope=['source.py'])
        self.tasks.add_acceptance(self.thread,'human','inspect artifact',kind='manual')
        self.record = await VerificationService(self.project,self.sessions,task_store=self.tasks).run(self.thread,
            approval=lambda *args:'approve_once', permission_engine=PermissionEngine(self.project,rules={}))
        self.assertEqual(self.record['status'],'passed')

    async def asyncTearDown(self):
        await fixture.ControllerTests.asyncTearDown(self)

    async def test_manual_projection_and_confirmation_reject_modified_artifact(self):
        from nailong.gui.work_control import projection_async
        current = current_evidence(self.project, self.tasks.snapshot(self.thread), self.sessions.root)
        self.tasks.user_decision(self.thread, 'human', 'inspected', current_input_fingerprint=current)
        value = await projection_async(self.controller)
        self.assertFalse(any(row.get('acceptance_id')=='human' for row in value['attention']))
        (self.project/'bundle/100.txt').write_text('changed artifact outside preview')
        value = await projection_async(self.controller)
        self.assertTrue(any(row.get('acceptance_id')=='human' for row in value['attention']))
        task = self.tasks.snapshot(self.thread)
        with self.assertRaises(ValueError):
            await self.controller.command('work_control', {'operation':'confirm_acceptance',
                'branch_id':self.branch['id'], 'expected_revision':task['revision'],
                'acceptance_id':'human', 'reason':'confirm'})

    async def test_node_detail_uses_complete_receipt_after_output_mutation(self):
        node = self.controller.branches.commit_turn(self.branch['id'],0,None,
            task_snapshot=self.tasks.snapshot(self.thread))
        value = await self.controller.command('node',{'node_id':node['id']})
        self.assertEqual(value['delivery']['status'],'passed')
        self.assertFalse(value['delivery']['verification']['generated_preview_complete'])
        (self.project/'bundle/100.txt').unlink()
        value = await self.controller.command('node',{'node_id':node['id']})
        self.assertIn(value['delivery']['status'], {'unknown','stale'})
