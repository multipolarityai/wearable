"""Real SDK runner, scripted in-process model: no credentials or HTTP."""
import asyncio
from copy import deepcopy
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import unittest

try:
    from agents import Model, ModelProvider, ModelResponse, Usage
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText, ResponseFunctionToolCall
    from openai_wearable import build_registry, demo_tools
except ImportError:
    raise unittest.SkipTest('Install requirements-openai.txt for SDK integration tests')

from trace_log import SQLiteTrace
from wearable import ValidationError, compile_agent, load_yaml, run

ROOT = Path(__file__).resolve().parents[1] / 'examples'


def message(event, output):
    return ResponseOutputMessage(id='msg', type='message', role='assistant', status='completed',
                                 content=[ResponseOutputText(type='output_text', text=json.dumps({'event': event, 'output': output}), annotations=[])])


class ScriptedModel(Model):
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    async def get_response(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply == 'slow':
            await asyncio.sleep(1)
        return ModelResponse(output=[reply], usage=Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise AssertionError('streaming is not used')
        yield


class Provider(ModelProvider):
    def __init__(self, model):
        self.model, self.names = model, []

    def get_model(self, model_name):
        self.names.append(model_name)
        return self.model


class OpenAIWearableTests(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(ROOT / 'openai-agents.yaml')
        self.agent = load_yaml(ROOT / 'agent.yaml')
        self.tools = load_yaml(ROOT / 'tools.yaml')
        self.runtime = load_yaml(ROOT / 'runtime-llm.yaml')

    def execute(self, replies, trace_sink=None, tools=None):
        self.model = ScriptedModel(replies)
        self.provider = Provider(self.model)
        registry = build_registry(self.config, self.tools, tool_registry=tools or demo_tools(), model_provider=self.provider)
        plan = compile_agent(self.agent, self.tools, self.runtime, registry)
        return asyncio.run(run(plan, {'request': 'Say hello $text'}, registry, trace_sink=trace_sink))

    def test_real_sdk_tool_loop_inside_outer_state(self):
        result = self.execute([
            message('ok', {'text': 'Hello world'}),
            ResponseFunctionToolCall(id='tool1', type='function_call', call_id='call1', name='word_count', arguments='{"text":"Hello world"}'),
            message('pass', {'feedback': ''}),
        ])
        self.assertEqual((result['status'], result['steps']), ('accept', 2))
        self.assertEqual(len(self.model.calls), 3)
        self.assertEqual(self.model.calls[0]['input'], [{'role': 'user', 'content': 'Request: Say hello $text'}])
        self.assertEqual(self.provider.names, [self.config['model']] * 3)
        last_input = self.model.calls[-1]['input']
        self.assertTrue(any(isinstance(i, dict) and i.get('type') == 'function_call_output' and i.get('output') == '2' for i in last_input))
        self.assertEqual(self.model.calls[0]['tools'], [])
        self.assertEqual([t.name for t in self.model.calls[1]['tools']], ['word_count'])
        self.assertEqual(self.model.calls[0]['system_instructions'], self.config['adapters']['llm.draft']['system'])

    def test_sdk_turn_limit_maps_to_error(self):
        self.config['max_turns'] = 1
        result = self.execute([message('ok', {'text': 'Hello'}), ResponseFunctionToolCall(id='t', type='function_call', call_id='c', name='word_count', arguments='{"text":"Hello"}')])
        self.assertEqual(result['status'], 'reject')
        self.assertEqual(result['trace'][-1]['error'], 'MaxTurnsExceeded')

    def test_invalid_output_and_sdk_failure(self):
        for reply in (message('invented', {'text': 'x'}), message('ok', {'text': 3}), RuntimeError('provider failed')):
            with self.subTest(reply=reply):
                result = self.execute([reply])
                self.assertEqual(result['status'], 'reject')
                self.assertEqual(result['context']['values'], {})

    def test_outer_timeout(self):
        self.runtime['limits']['action_timeout_ms'] = 5
        result = self.execute(['slow'])
        self.assertEqual(result['trace'][0]['error'], 'TimeoutError')

    def test_config_errors_before_execution(self):
        original = deepcopy(self.config)
        for mutate in (lambda c: c.update(max_turns=True), lambda c: c.update(model=''),
                       lambda c: c.update(extra=1),
                       lambda c: c['adapters']['llm.draft'].update(prompt='$unknown'),
                       lambda c: c['adapters']['llm.check'].update(tools=['unknown'])):
            self.config = deepcopy(original)
            mutate(self.config)
            with self.assertRaises(ValidationError):
                build_registry(self.config, self.tools, tool_registry=demo_tools())

    def test_sqlite_sdk_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            self.execute([message('ok', {'text': 'Hello'}), message('pass', {'feedback': ''})], SQLiteTrace(path, 'sdk-test'))
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute("SELECT state,data FROM trace_events WHERE event='sdk.completed' ORDER BY id").fetchall()
            self.assertEqual([r[0] for r in rows], ['draft', 'check'])
            self.assertEqual(sum(json.loads(r[1])['total_tokens'] for r in rows), 30)

    def test_failure_attribution_is_evidence_based(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            self.execute([message('invalid', {'text': 'x'})], SQLiteTrace(path, 'bad-output'))
            self.execute([RuntimeError('unknown defect')], SQLiteTrace(path, 'unknown'))
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute("SELECT run_id,data FROM trace_events WHERE event='sdk.failed' ORDER BY id").fetchall()
            self.assertEqual(json.loads(rows[0][1])['failure']['origin'], 'model_output')
            self.assertEqual(json.loads(rows[1][1])['failure']['origin'], 'undetermined')

    def test_provider_failure_is_not_model_quality_failure(self):
        import httpx
        from openai import APIConnectionError
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            error = APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))
            self.execute([error], SQLiteTrace(path, 'provider'))
            with closing(sqlite3.connect(path)) as db:
                data = db.execute("SELECT data FROM trace_events WHERE event='sdk.failed'").fetchone()[0]
            self.assertEqual(json.loads(data)['failure']['origin'], 'provider_or_transport')

    def test_unsupported_approval_is_adapter_failure(self):
        from agents import function_tool
        @function_tool(needs_approval=True)
        async def word_count(text: str) -> int:
            self.fail('approval-required tool must not execute')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'trace.sqlite'
            result = self.execute([message('ok', {'text': 'Hello'}), ResponseFunctionToolCall(id='t', type='function_call', call_id='c', name='word_count', arguments='{"text":"Hello"}')], SQLiteTrace(path, 'approval'), tools={'word_count': word_count})
            self.assertEqual(result['status'], 'reject')
            with closing(sqlite3.connect(path)) as db:
                data = db.execute("SELECT data FROM trace_events WHERE event='adapter.failed'").fetchone()[0]
            self.assertEqual(json.loads(data)['failure']['origin'], 'adapter')

    def test_identity_tracks_config_and_host(self):
        def identity(host='v1'):
            return build_registry(self.config, self.tools, tool_registry=demo_tools(), host_version=host)['llm.draft'].identity
        first = identity()
        self.config['model'] = 'another-model'
        self.assertNotEqual(first, identity())
        self.assertNotEqual(identity(), identity('v2'))
