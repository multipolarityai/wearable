"""Check one review/revision ordering property over a graph or SQLite trace."""
import argparse
from collections import deque
from contextlib import closing
import json
import sqlite3

from checkpoints import fingerprint
from wearable import Adapter, ValidationError, compile_agent, fields, load_yaml, require


def validate_rule(agent, rule):
    fields(rule, {'version', 'name', 'before', 'grant', 'revoke'}, set(), 'property')
    require(rule['version'] == '0.1', 'property.version', 'expected "0.1"')
    require(isinstance(rule['name'], str) and bool(rule['name']), 'property.name', 'expected name')
    require(isinstance(rule['before'], str) and rule['before'] in agent['states'], 'property.before', 'unknown state')
    require(isinstance(rule['revoke'], list), 'property.revoke', 'expected list')
    for item in [rule['grant'], *rule['revoke']]:
        fields(item, {'state', 'event'}, set(), 'property.trigger')
        require(isinstance(item['state'], str) and item['state'] in agent['states'], 'property.trigger.state', 'unknown state')
        require(isinstance(item['event'], str) and item['event'] in agent['states'][item['state']].get('on', {}), 'property.trigger.event', 'unknown outgoing event')
    require(rule['grant'] not in rule['revoke'], 'property', 'grant cannot also revoke')


def advance(approved, state, event, rule):
    trigger = {'state': state, 'event': event}
    if trigger in rule['revoke']:
        return False
    return approved or trigger == rule['grant']


def check_graph(agent, rule):
    validate_rule(agent, rule)
    queue, seen = deque([(agent['initial'], False, [])]), set()
    while queue:
        state, approved, path = queue.popleft()
        if state == rule['before'] and not approved:
            return {'status': 'violation', 'property': rule['name'], 'counterexample': path}
        if (state, approved) in seen:
            continue
        seen.add((state, approved))
        for event, target in agent['states'][state].get('on', {}).items():
            edge = {'state': state, 'event': event, 'next': target}
            queue.append((target, advance(approved, state, event, rule), path + [edge]))
    return {'status': 'satisfied', 'property': rule['name'], 'scope': 'all paths in declared graph'}


def check_trace(agent, rule, path, run_id):
    validate_rule(agent, rule)
    # Read-only URI prevents accidental database creation.
    from pathlib import Path
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT event,data FROM trace_events WHERE run_id=? ORDER BY id', (run_id,)).fetchall()
    rows = [(kind, json.loads(data)) for kind, data in rows]
    starts = [data for kind, data in rows if kind == 'run.started']
    expected = fingerprint(agent)
    if not starts or any(s.get('workflow_fingerprint') != expected for s in starts):
        return {'status': 'inconclusive', 'reason': 'missing trace or workflow fingerprint mismatch'}
    state, approved, witness, step = agent['initial'], False, [], 0
    if state == rule['before']:
        return {'status': 'violation', 'counterexample': []}
    for kind, edge in rows:
        if kind != 'action.completed':
            continue
        if edge['step'] != step + 1 or edge['state'] != state or agent['states'][state].get('on', {}).get(edge['event']) != edge['next']:
            return {'status': 'inconclusive', 'reason': 'trace gap, reused run ID, or invalid transition'}
        approved = advance(approved, state, edge['event'], rule)
        state, step = edge['next'], edge['step']
        witness.append(edge)
        if state == rule['before'] and not approved:
            return {'status': 'violation', 'property': rule['name'], 'counterexample': witness}
    started = sum(kind == 'action.started' for kind, _ in rows)
    if not rows or rows[-1][0] != 'run.completed' or started != step or rows[-1][1].get('steps') != step:
        return {'status': 'inconclusive', 'reason': 'incomplete or interrupted trace'}
    return {'status': 'satisfied', 'property': rule['name'], 'scope': 'recorded execution only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', default='examples/agent.yaml')
    parser.add_argument('--tools', default='examples/tools.yaml')
    parser.add_argument('--runtime', default='examples/runtime.yaml')
    parser.add_argument('--property', dest='property_path', default='examples/review-property.yaml')
    parser.add_argument('--trace')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if bool(args.trace) != bool(args.run_id):
        parser.error('--trace and --run-id must be supplied together')
    try:
        agent, tools, runtime = map(load_yaml, (args.agent, args.tools, args.runtime))
        async def never_called(args):
            raise AssertionError('Static validation never invokes tools')
        # Bind contract-only placeholders for the existing compiler; no SDK/model loading.
        registry = {adapter: Adapter(tuple(tools['capabilities'][cap]['inputs']),
                                     tuple(tools['capabilities'][cap]['outputs']),
                                     tuple(tools['capabilities'][cap]['events']), never_called)
                    for cap, adapter in runtime['bindings'].items()}
        compile_agent(agent, tools, runtime, registry)
        rule = load_yaml(args.property_path)
        result = check_trace(agent, rule, args.trace, args.run_id) if args.trace else check_graph(agent, rule)
    except (ValueError, KeyError, TypeError, AttributeError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps(result, indent=2))
    return {'satisfied': 0, 'violation': 1, 'inconclusive': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
