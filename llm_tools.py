"""Minimal Ollama adapter; model and prompts live in a separate YAML file."""
import json
from string import Template
from urllib.parse import urlsplit

import httpx

from checkpoints import fingerprint
from wearable import Adapter, fields, names, require, strings, symbols


def build_registry(config, tools, *, transport=None):
    """Build adapters without contacting the server. transport is for offline tests."""
    fields(config, {'version', 'endpoint', 'model', 'adapters'}, set(), 'llm')
    require(config['version'] == '0.1', 'llm.version', 'expected "0.1"')
    for key in ('endpoint', 'model'):
        require(isinstance(config[key], str) and bool(config[key].strip()), 'llm.' + key, 'expected nonempty string')
    url = urlsplit(config['endpoint'])
    require(url.scheme in ('http', 'https') and bool(url.hostname) and not url.username
            and not url.password and not url.query and not url.fragment,
            'llm.endpoint', 'expected HTTP(S) chat URL without credentials/query/fragment')
    names(config['adapters'], 'llm.adapters')
    fields(tools, {'version', 'capabilities'}, set(), 'tools')
    names(tools['capabilities'], 'tools.capabilities')
    registry = {}
    for name, spec in config['adapters'].items():
        path = 'llm.adapters.' + name
        fields(spec, {'capability', 'system', 'prompt'}, set(), path)
        require(isinstance(spec['capability'], str) and spec['capability'] in tools['capabilities'], path, 'unknown capability')
        cap = tools['capabilities'][spec['capability']]
        fields(cap, {'inputs', 'outputs', 'events'}, set(), path)
        strings(cap['inputs'], path)
        strings(cap['outputs'], path)
        symbols(cap['events'], path)
        for key in ('system', 'prompt'):
            require(isinstance(spec[key], str) and bool(spec[key].strip()), path + '.' + key, 'expected nonempty string')
        template = Template(spec['prompt'])
        require(template.is_valid(), path + '.prompt', 'invalid $placeholder; use $$ for a literal dollar')
        require(set(template.get_identifiers()) <= set(cap['inputs']), path + '.prompt', 'unknown input placeholder')
        schema = {
            'type': 'object', 'required': ['event', 'output'], 'additionalProperties': False,
            'properties': {
                'event': {'type': 'string', 'enum': list(cap['events'])},
                'output': {'type': 'object', 'required': list(cap['outputs']),
                           'additionalProperties': False,
                           'properties': {k: {'type': 'string'} for k in cap['outputs']}},
            },
        }
        call = make_call(config['endpoint'], config['model'], spec['system'], template, schema, transport)
        registry[name] = Adapter(tuple(cap['inputs']), tuple(cap['outputs']), tuple(cap['events']), call,
                                 identity=fingerprint({'config': config, 'adapter': name, 'implementation': 1}))
    return registry


def make_call(endpoint, model, system, template, schema, transport):
    async def call(args):
        payload = {
            'model': model, 'stream': False, 'format': schema,
            'options': {'temperature': 0, 'num_predict': 512},
            'messages': [
                {'role': 'system', 'content': system + '\nReturn only JSON matching this schema: ' + json.dumps(schema)},
                {'role': 'user', 'content': template.substitute(args)},
            ],
        }
        # The executor owns the overall deadline; cancellation closes this client.
        async with httpx.AsyncClient(timeout=None, trust_env=False, transport=transport) as client:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()
        require(isinstance(body, dict) and body.get('done') is True, 'ollama', 'incomplete reply')
        return json.loads(body['message']['content'])
    return call
