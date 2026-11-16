import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest

import httpx

from llm_tools import build_registry
from wearable import ValidationError, compile_agent, load_yaml, run

ROOT = Path(__file__).resolve().parents[1] / 'examples'


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.config = load_yaml(ROOT / 'llm.yaml')
        self.tools = load_yaml(ROOT / 'tools.yaml')
        self.agent = load_yaml(ROOT / 'agent.yaml')
        self.runtime = load_yaml(ROOT / 'runtime-llm.yaml')

    def execute(self, handler):
        registry = build_registry(self.config, self.tools, transport=httpx.MockTransport(handler))
        plan = compile_agent(self.agent, self.tools, self.runtime, registry)
        return asyncio.run(run(plan, {'request': 'Say hello $text'}, registry))

    def test_request_and_response(self):
        requests = []
        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(str(request.url), self.config['endpoint'])
            self.assertEqual(payload['model'], self.config['model'])
            self.assertFalse(payload['stream'])
            self.assertIn('schema', payload['messages'][0]['content'])
            event = payload['format']['properties']['event']['enum'][0]
            output = {k: 'Hello' for k in payload['format']['properties']['output']['properties']}
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({'event': event, 'output': output})}})
        result = self.execute(handler)
        self.assertEqual((result['status'], result['steps']), ('accept', 2))
        self.assertEqual(requests[0]['messages'][1]['content'], 'Request: Say hello $text')
        self.assertEqual(requests[1]['messages'][1]['content'], 'Answer: Hello')

    def test_http_and_malformed_errors(self):
        for body, code in (({}, 503), ({'done': False}, 200),
                           ({'done': True, 'message': {'content': 'not json'}}, 200),
                           ({'done': True, 'message': {'content': '{"event":"invented","output":{}}'}}, 200)):
            with self.subTest(body=body):
                result = self.execute(lambda request: httpx.Response(code, json=body))
                self.assertEqual(result['status'], 'reject')
                self.assertEqual(result['trace'][0]['event'], 'error')
                self.assertEqual(result['context']['values'], {})

    def test_network_failure(self):
        def handler(request):
            raise httpx.ConnectError('offline', request=request)
        self.assertEqual(self.execute(handler)['trace'][0]['error'], 'ConnectError')

    def test_cancel_request_at_deadline(self):
        self.runtime['limits']['action_timeout_ms'] = 5
        async def handler(request):
            await asyncio.sleep(1)
            return httpx.Response(200, json={})
        self.assertEqual(self.execute(handler)['trace'][0]['error'], 'TimeoutError')

    def test_config_validation(self):
        original = deepcopy(self.config)
        for mutate in (lambda c: c.update(model=''), lambda c: c.update(typo=True),
                       lambda c: c.update(endpoint='file:///tmp/x'),
                       lambda c: c['adapters']['llm.draft'].update(prompt='$unknown'),
                       lambda c: c['adapters']['llm.draft'].update(prompt='broken $'),
                       lambda c: c['adapters']['llm.draft'].update(capability='absent')):
            with self.subTest(mutation=mutate):
                self.config = deepcopy(original)
                mutate(self.config)
                with self.assertRaises(ValidationError):
                    build_registry(self.config, self.tools)
