"""Map follow-ups remain available between execution and pump retirement."""
import asyncio
import types
import unittest

import test_gui_native as fixture


class MapHandoffTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.NativeGUITests.asyncSetUp
    async def asyncTearDown(self):
        for gate in getattr(self,'_release_gates',[]):gate.set()
        await fixture.NativeGUITests.asyncTearDown(self)
    send=fixture.NativeGUITests.send

    async def completed_handoff(self):
        first=await self.send(self.root,'shared origin')
        await self.controller.worker
        retired=asyncio.Event();release=asyncio.Event()
        controller=self.controller
        # Preserve the real execute task; delay only its awaiting pump's
        # retirement to make the real done-task scheduling window deterministic.
        async def pump(owner):
            while owner.queue and not owner.paused:
                run=owner.runs[owner.queue.pop(0)]
                owner.current_task=asyncio.create_task(owner._execute(run))
                try:
                    await owner.current_task
                    retired.set()
                    await release.wait()
                except asyncio.CancelledError:
                    pass
                finally:
                    owner.current_task=None
        controller._pump=types.MethodType(pump,controller)
        self._release_gates=getattr(self,'_release_gates',[])+[release]
        await controller.command('send',{'branch_id':self.root['id'],'message':'later source only','request_id':'source-later'})
        await asyncio.wait_for(retired.wait(),5)
        self.assertTrue(controller.current_task.done())
        self.assertFalse(controller.worker.done())
        self.assertFalse(controller.queue)
        return first,release

    def payload(self,first):
        return {'branch_id':self.root['id'],'node_id':first['id'],'kind':'continue',
                'message':'new branch only','request_id':'map-completed-handoff'}

    async def test_done_execution_allows_exact_fork_and_pump_continues_once(self):
        first,release=await self.completed_handoff()
        payload=self.payload(first)
        result=await self.controller.command('map_send',payload)
        self.assertEqual(await self.controller.command('map_send',payload),result)
        self.assertEqual(len(self.controller.branches.branches()),2)
        release.set()
        await asyncio.wait_for(self.controller.worker,5)
        run=self.controller.runs[result['run_id']]
        self.assertEqual(run['status'],'completed',run.get('error'))
        branch=self.controller.branches.branch(run['branch_id'])
        history=str(self.controller.service.get_history(branch['thread_id']))
        self.assertIn('shared origin',history)
        self.assertIn('new branch only',history)
        self.assertNotIn('later source only',history)
        self.assertEqual([n['prompt'] for n in self.controller.branches.path(branch['id'])],['shared origin','new branch only'])
        with self.assertRaisesRegex(ValueError,'请求 ID'):
            await self.controller.command('map_send',{**payload,'message':'different'})

    async def test_done_execution_with_queued_work_remains_busy_without_fork(self):
        first,release=await self.completed_handoff()
        await self.controller.command('send',{'branch_id':self.root['id'],'message':'queued source','request_id':'queued-source'})
        with self.assertRaisesRegex(ValueError,'等待'):
            await self.controller.command('map_send',self.payload(first))
        self.assertEqual(len(self.controller.branches.branches()),1)
        self.assertIsNone(self.controller.branches.db.execute('SELECT * FROM map_requests WHERE id=?',('map-completed-handoff',)).fetchone())
        release.set()
        await asyncio.wait_for(self.controller.worker,5)

    async def test_unfinished_execution_still_blocks_map_without_fork(self):
        first=await self.send(self.root,'shared origin')
        await self.controller.worker
        native=self.controller._execute
        finished=asyncio.Event();release=asyncio.Event()
        async def finishing(run):
            await native(run)
            finished.set()
            await release.wait()
        self.controller._execute=finishing
        self._release_gates=getattr(self,'_release_gates',[])+[release]
        await self.controller.command('send',{'branch_id':self.root['id'],'message':'finishing source','request_id':'finishing-source'})
        try:
            await asyncio.wait_for(finished.wait(),5)
            self.assertFalse(self.controller.current_task.done())
            with self.assertRaisesRegex(ValueError,'等待'):
                await self.controller.command('map_send',self.payload(first))
            with self.assertRaisesRegex(ValueError,'等待'):
                await self.controller.command('fork',{'branch_id':self.root['id'],'node_id':first['id'],'name':'blocked'})
            self.assertEqual(len(self.controller.branches.branches()),1)
        finally:
            release.set()
        await asyncio.wait_for(self.controller.worker,5)
