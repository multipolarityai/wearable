"""Deterministic offline tools: one revision, then acceptance. No LLM calls."""
from wearable import Adapter


async def draft(args):
    return {'event': 'ok', 'output': {'text': 'Draft: ' + args['request']}}


async def check(args):
    passed = args['text'].startswith('Revised: ')
    return {'event': 'pass' if passed else 'revise',
            'output': {'feedback': '' if passed else 'Add a clear conclusion.'}}


async def revise(args):
    return {'event': 'ok', 'output': {'text': 'Revised: ' + args['text'] + '\n' + args['feedback']}}


REGISTRY = {
    'llm.draft': Adapter(('request',), ('text',), ('ok',), draft),
    'llm.check': Adapter(('text',), ('feedback',), ('pass', 'revise'), check),
    'llm.revise': Adapter(('text', 'feedback'), ('text',), ('ok',), revise),
}
