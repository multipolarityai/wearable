import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from checkpoints import Checkpoint, CheckpointError
from stub_tools import REGISTRY
from wearable import load_plan, run

ROOT = Path(__file__).resolve().parents[1] / 'examples'


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'runs.sqlite'
        self.registry = dict(REGISTRY)
        self.plan = load_plan(ROOT / 'agent.yaml', ROOT / 'tools.yaml', ROOT / 'runtime.yaml', self.registry)

    def execute(self, **kwargs):
        checkpoint = kwargs.pop('checkpoint', Checkpoint(self.path, 'test'))
        return asyncio.run(run(self.plan, {'request': 'Hello'}, self.registry, checkpoint=checkpoint, **kwargs))

    def test_completed_run_is_returned_without_calls(self):
        result = self.execute()
        async def should_not_run(args):
            self.fail('completed run invoked a tool')
        self.registry['llm.draft'] = replace(REGISTRY['llm.draft'], call=should_not_run)
        self.assertEqual(result, self.execute(resume=True))
        with self.assertRaises(CheckpointError):
            self.execute()

    def test_resume_after_committed_transition(self):
        class CrashAfterSave(Checkpoint):
            def save(self, snapshot):
                super().save(snapshot)
                if snapshot['phase'] == 'ready':
                    raise RuntimeError('process stopped after commit')
        with self.assertRaises(RuntimeError):
            self.execute(checkpoint=CrashAfterSave(self.path, 'test'))
        async def should_not_run(args):
            self.fail('draft was repeated')
        self.registry['llm.draft'] = replace(REGISTRY['llm.draft'], call=should_not_run)
        result = self.execute(resume=True)
        self.assertEqual((result['status'], result['steps']), ('accept', 4))

    def test_interrupted_call_requires_explicit_retry(self):
        async def interrupted(args):
            raise asyncio.CancelledError()
        self.registry['llm.check'] = replace(REGISTRY['llm.check'], call=interrupted)
        with self.assertRaises(asyncio.CancelledError):
            self.execute()
        self.registry = dict(REGISTRY)
        with self.assertRaisesRegex(CheckpointError, 'interrupted call'):
            self.execute(resume=True)
        result = self.execute(resume=True, retry_pending=True)
        self.assertEqual((result['status'], result['steps']), ('accept', 5))
        self.assertEqual([e['step'] for e in result['trace']], [1, 3, 4, 5])

    def test_fingerprint_mismatch(self):
        self.execute()
        with self.assertRaisesRegex(CheckpointError, 'differs'):
            asyncio.run(run(self.plan, {'request': 'Changed'}, self.registry,
                            checkpoint=Checkpoint(self.path, 'test'), resume=True))
        self.registry['llm.check'] = replace(REGISTRY['llm.check'], identity='changed-model')
        with self.assertRaisesRegex(CheckpointError, 'differs'):
            self.execute(resume=True)

    def test_missing_run_and_stale_writer(self):
        with self.assertRaises(CheckpointError):
            self.execute(resume=True)
        first, second = Checkpoint(self.path, 'other'), Checkpoint(self.path, 'other')
        snap = {'phase': 'ready'}
        first.start('signature', snap)
        second.start('signature', snap, resume=True)
        first.save({'phase': 'pending'})
        with self.assertRaisesRegex(CheckpointError, 'another runner'):
            second.save({'phase': 'pending'})

    def test_retry_keeps_budget(self):
        from wearable import compile_agent, load_yaml
        runtime = load_yaml(ROOT / 'runtime.yaml')
        runtime['limits']['max_steps'] = 1
        self.plan = compile_agent(load_yaml(ROOT / 'agent.yaml'), load_yaml(ROOT / 'tools.yaml'), runtime, self.registry)
        async def interrupted(args):
            raise asyncio.CancelledError()
        self.registry['llm.draft'] = replace(REGISTRY['llm.draft'], call=interrupted)
        with self.assertRaises(asyncio.CancelledError):
            self.execute()
        self.registry = dict(REGISTRY)
        result = self.execute(resume=True, retry_pending=True)
        self.assertEqual((result['status'], result['steps']), ('budget_exhausted', 1))


if __name__ == '__main__':
    unittest.main()
