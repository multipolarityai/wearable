import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from evaluate import check_graph, check_trace
from stub_tools import REGISTRY
from trace_log import SQLiteTrace
from wearable import compile_agent, load_yaml

ROOT = Path(__file__).resolve().parents[1] / 'examples'


class PropertyTests(unittest.TestCase):
    def setUp(self):
        self.agent = load_yaml(ROOT / 'agent.yaml')
        self.rule = load_yaml(ROOT / 'review-property.yaml')

    def test_static_valid_and_counterexample(self):
        self.assertEqual(check_graph(self.agent, self.rule)['status'], 'satisfied')
        self.agent['states']['draft']['on']['ok'] = 'done'
        result = check_graph(self.agent, self.rule)
        self.assertEqual(result['status'], 'violation')
        self.assertEqual(result['counterexample'], [{'state': 'draft', 'event': 'ok', 'next': 'done'}])

    def test_revision_invalidates_prior_review(self):
        self.agent['states']['check']['on']['pass'] = 'revise'
        self.agent['states']['revise']['on']['ok'] = 'done'
        self.assertEqual(check_graph(self.agent, self.rule)['status'], 'violation')
        # A second graph where revision is reachable only after a passing review.
        self.agent['states']['check']['on']['revise'] = 'failed'
        without_revocation = deepcopy(self.rule)
        without_revocation['revoke'] = []
        self.assertEqual(check_graph(self.agent, without_revocation)['status'], 'satisfied')
        self.assertEqual(check_graph(self.agent, self.rule)['status'], 'violation')

    def test_recorded_trace_and_mismatch(self):
        from wearable import run
        plan = compile_agent(self.agent, load_yaml(ROOT / 'tools.yaml'), load_yaml(ROOT / 'runtime.yaml'), REGISTRY)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            asyncio.run(run(plan, {'request': 'Hello'}, REGISTRY, trace_sink=SQLiteTrace(path, 'run')))
            self.assertEqual(check_trace(self.agent, self.rule, path, 'run')['status'], 'satisfied')
            self.assertEqual(check_trace(self.agent, self.rule, path, 'absent')['status'], 'inconclusive')
            self.agent['name'] = 'changed'
            self.assertEqual(check_trace(self.agent, self.rule, path, 'run')['status'], 'inconclusive')

    def test_checkpoint_result_without_original_log_is_inconclusive(self):
        from checkpoints import Checkpoint
        from wearable import run
        plan = compile_agent(self.agent, load_yaml(ROOT / 'tools.yaml'), load_yaml(ROOT / 'runtime.yaml'), REGISTRY)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.sqlite'
            asyncio.run(run(plan, {'request': 'Hello'}, REGISTRY, checkpoint=Checkpoint(path, 'saved')))
            asyncio.run(run(plan, {'request': 'Hello'}, REGISTRY, checkpoint=Checkpoint(path, 'saved'), resume=True, trace_sink=SQLiteTrace(path, 'saved')))
            self.assertEqual(check_trace(self.agent, self.rule, path, 'saved')['status'], 'inconclusive')

    def test_incomplete_trace_is_not_pass(self):
        from checkpoints import fingerprint
        from trace_log import emit, trace_context
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            with trace_context(SQLiteTrace(path, 'partial')):
                emit('run.started', workflow_fingerprint=fingerprint(self.agent))
                emit('action.started', step=1)
            self.assertEqual(check_trace(self.agent, self.rule, path, 'partial')['status'], 'inconclusive')
