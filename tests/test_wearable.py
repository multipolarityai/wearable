import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from wearable import ValidationError, compile_agent, load_yaml, run
from stub_tools import REGISTRY

ROOT = Path(__file__).resolve().parents[1]


class PrototypeTests(unittest.TestCase):
    def setUp(self):
        self.agent, self.tools, self.runtime = [load_yaml(ROOT / 'examples' / f'{n}.yaml')
                                               for n in ('agent', 'tools', 'runtime')]
        self.registry = dict(REGISTRY)

    def plan(self):
        return compile_agent(self.agent, self.tools, self.runtime, self.registry)

    def execute(self):
        return asyncio.run(run(self.plan(), {'request': 'Hello'}, self.registry))

    def tool(self, name, fn):
        self.registry[name] = replace(self.registry[name], call=fn)

    def test_revision_and_isolated_runs(self):
        first, second = self.execute(), self.execute()
        self.assertEqual(first, second)
        self.assertEqual((first['status'], first['steps']), ('accept', 4))
        self.assertEqual([x['event'] for x in first['trace']], ['ok', 'revise', 'ok', 'pass'])
        self.assertTrue(first['context']['values']['answer'].startswith('Revised:'))

    def test_immediate_accept_at_budget(self):
        async def passed(args):
            return {'event': 'pass', 'output': {'feedback': ''}}
        self.tool('llm.check', passed)
        self.runtime['limits']['max_steps'] = 2
        self.assertEqual(self.execute()['status'], 'accept')

    def test_budget(self):
        async def again(args):
            return {'event': 'revise', 'output': {'feedback': 'again'}}
        self.tool('llm.check', again)
        result = self.execute()
        self.assertEqual((result['status'], result['steps'], len(result['trace'])), ('budget_exhausted', 8, 8))

    def test_invalid_replies(self):
        for reply in (None, {}, {'event': 'unknown', 'output': {'text': 'x'}},
                      {'event': 'ok', 'output': {'text': 42}},
                      {'event': 'ok', 'output': {}},
                      {'event': 'error', 'output': {'text': 'x'}}):
            with self.subTest(reply=reply):
                async def invalid(args):
                    return reply
                self.tool('llm.draft', invalid)
                result = self.execute()
                self.assertEqual(result['status'], 'reject')
                self.assertEqual(result['context']['values'], {})

    def test_exception_and_timeout(self):
        async def broken(args):
            raise RuntimeError('broken')
        async def slow(args):
            await asyncio.sleep(1)
        self.runtime['limits']['action_timeout_ms'] = 5
        for fn, error in ((broken, 'RuntimeError'), (slow, 'TimeoutError')):
            with self.subTest(error=error):
                self.tool('llm.draft', fn)
                result = self.execute()
                self.assertEqual(result['status'], 'reject')
                self.assertEqual(result['trace'][0]['error'], error)

    def test_failure_preserves_answer(self):
        async def broken(args):
            raise RuntimeError()
        self.tool('llm.revise', broken)
        result = self.execute()
        self.assertEqual(result['context']['values']['answer'], 'Draft: Hello')
        self.assertNotIn('revise', result['context']['results'])

    def test_cancellation_propagates(self):
        async def cancelled(args):
            raise asyncio.CancelledError()
        self.tool('llm.draft', cancelled)
        with self.assertRaises(asyncio.CancelledError):
            self.execute()

    def test_invalid_declarations(self):
        original = deepcopy(self.agent)
        mutations = [
            lambda a: a.update(version='2'),
            lambda a: a.update(typo=True),
            lambda a: a.update(initial='absent'),
            lambda a: a['states']['draft']['on'].update(ok='absent'),
            lambda a: a['states']['draft']['on'].pop('error'),
            lambda a: a['states'].update(unreachable={'terminal': 'reject'}),
            lambda a: a['states']['draft']['inputs'].update(request={'value': 'answer'}),
            lambda a: a['states']['draft']['on'].update(error='check'),
            lambda a: a['states']['done'].update(action='draft_text'),
            lambda a: a['states']['check']['inputs'].update(text={'result': 'draft', 'field': 'absent'}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.agent = deepcopy(original)
                mutate(self.agent)
                with self.assertRaises(ValidationError):
                    self.plan()

    def test_nonterminating_region(self):
        self.agent['states']['revise']['on'] = {'ok': 'revise', 'error': 'revise'}
        with self.assertRaisesRegex(ValidationError, 'no path'):
            self.plan()

    def test_bad_limits(self):
        for limit in (True, 0, -1, '8'):
            self.runtime['limits']['max_steps'] = limit
            with self.assertRaises(ValidationError):
                self.plan()

    def test_binding_contract_and_rebinding(self):
        plan = self.plan()
        self.registry.pop('llm.check')
        with self.assertRaises(ValidationError):
            self.plan()
        with self.assertRaises(ValidationError):
            asyncio.run(run(plan, {'request': 'x'}, self.registry))
        self.registry = dict(REGISTRY)
        self.registry['llm.check'] = replace(REGISTRY['llm.check'], outputs=('wrong',))
        with self.assertRaises(ValidationError):
            self.plan()

    def test_plan_is_frozen_and_independent(self):
        plan = self.plan()
        self.agent['states']['draft']['on']['ok'] = 'failed'
        with self.assertRaises(TypeError):
            plan.agent['states']['draft']['on']['ok'] = 'failed'
        self.assertEqual(asyncio.run(run(plan, {'request': 'x'}, self.registry))['status'], 'accept')

    def test_input_validation(self):
        for inputs in ({}, {'request': 1}, {'request': 'x', 'extra': 'x'}):
            with self.assertRaises(ValidationError):
                asyncio.run(run(self.plan(), inputs, self.registry))

    def test_strict_yaml(self):
        for content in ('x: 1\nx: 2', 'x: !!python/object:bad {}',
                        'x: &x [1]\ny: *x', '[' * 45 + ']' * 45, 'x' * 65537):
            with self.subTest(content=content[:40]), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'bad.yaml'
                path.write_text(content)
                with self.assertRaises(ValidationError):
                    load_yaml(path)


if __name__ == '__main__':
    unittest.main()
