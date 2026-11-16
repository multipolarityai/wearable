"""A small, sequential declarative agent interpreter (Python 3.11+)."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

import yaml


class ValidationError(ValueError):
    """Invalid declaration or input, with a location."""


def require(condition, path, message):
    if not condition:
        raise ValidationError(f"{path}: {message}")


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        require(not self.check_event(yaml.AliasEvent), "yaml", "aliases are unsupported")
        self.depth = getattr(self, "depth", 0) + 1
        require(self.depth <= 40, "yaml", "nesting exceeds 40 levels")
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            require(isinstance(key, str), "yaml", "mapping keys must be strings (quote 'on')")
            require(key not in result, "yaml", f"duplicate key {key!r}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def load_yaml(path):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(65537)
        require(len(raw) <= 65536, str(path), "document exceeds 64 KiB")
        return yaml.load(raw.decode("utf-8"), Loader=StrictLoader)
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ValidationError(f"{path}: invalid YAML: {exc}") from exc


def fields(value, required, optional, path):
    require(isinstance(value, Mapping), path, "expected a mapping")
    require(set(required) <= value.keys(), path, f"required fields: {sorted(required)}")
    require(value.keys() <= set(required) | set(optional), path, "unknown fields")


def names(value, path):
    require(isinstance(value, Mapping), path, "expected a mapping")
    require(all(isinstance(k, str) and k for k in value), path, "names must be nonempty strings")


def strings(value, path):
    names(value, path)
    require(all(v == "string" for v in value.values()), path, "only string fields are supported")


def symbols(value, path):
    require(isinstance(value, list) and bool(value), path, "expected a nonempty list")
    require(all(isinstance(v, str) and v for v in value), path, "expected nonempty strings")
    require(len(set(value)) == len(value), path, "duplicate symbols")


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(map(freeze, value))
    return value


@dataclass(frozen=True)
class Adapter:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    events: tuple[str, ...]
    call: Callable  # async function accepting a dict and returning an envelope


@dataclass(frozen=True)
class Plan:
    agent: Mapping
    capabilities: Mapping
    runtime: Mapping


def bind(plan, registry):
    bound = {}
    for name, cap in plan.capabilities.items():
        adapter_id = plan.runtime['bindings'][name]
        adapter = registry.get(adapter_id)
        path = f"runtime.bindings.{name}"
        require(isinstance(adapter, Adapter), path, f"unknown adapter {adapter_id!r}")
        require(inspect.iscoroutinefunction(adapter.call), path, "adapter must be async")
        for key in ('inputs', 'outputs', 'events'):
            require(set(getattr(adapter, key)) == set(cap[key]), path, f"incompatible {key}")
        bound[name] = adapter.call
    return bound


def compile_agent(agent, tools, runtime, registry):
    for doc, required, path in (
        (agent, {'version', 'name', 'inputs', 'values', 'alphabet', 'initial', 'states'}, 'agent'),
        (tools, {'version', 'capabilities'}, 'tools'),
        (runtime, {'version', 'backend', 'bindings', 'limits'}, 'runtime'),
    ):
        fields(doc, required, set(), path)
        require(doc['version'] == '0.1', path + '.version', 'expected "0.1"')
    require(isinstance(agent['name'], str) and agent['name'], 'agent.name', 'expected a name')
    strings(agent['inputs'], 'agent.inputs')
    strings(agent['values'], 'agent.values')
    symbols(agent['alphabet'], 'agent.alphabet')
    caps, states = tools['capabilities'], agent['states']
    names(caps, 'tools.capabilities')
    names(states, 'agent.states')
    require(isinstance(agent['initial'], str) and agent['initial'] in states, 'agent.initial', 'unknown state')
    require(runtime['backend'] == 'sequential', 'runtime.backend', 'only sequential is supported')
    names(runtime['bindings'], 'runtime.bindings')
    require(set(runtime['bindings']) == set(caps), 'runtime.bindings', 'must bind every capability exactly once')
    require(all(isinstance(v, str) for v in runtime['bindings'].values()), 'runtime.bindings', 'expected adapter IDs')
    fields(runtime['limits'], {'max_steps', 'action_timeout_ms'}, set(), 'runtime.limits')
    for key, value in runtime['limits'].items():
        require(type(value) is int and value > 0, 'runtime.limits.' + key, 'expected a positive integer')
    for name, cap in caps.items():
        path = 'tools.capabilities.' + name
        fields(cap, {'inputs', 'outputs', 'events'}, set(), path)
        strings(cap['inputs'], path + '.inputs')
        strings(cap['outputs'], path + '.outputs')
        symbols(cap['events'], path + '.events')
        require('error' not in cap['events'], path, 'error is reserved')
    terminals, accepts, needs = set(), set(), {}
    for name, state in states.items():
        path = 'agent.states.' + name
        names(state, path)
        needs[name] = set()
        if 'terminal' in state:
            fields(state, {'terminal'}, set(), path)
            require(state['terminal'] in ('accept', 'reject'), path, 'invalid terminal kind')
            terminals.add(name)
            if state['terminal'] == 'accept':
                accepts.add(name)
            continue
        fields(state, {'action', 'inputs', 'on'}, {'save'}, path)
        require(isinstance(state['action'], str) and state['action'] in caps, path + '.action', 'unknown capability')
        cap = caps[state['action']]
        fields(state['inputs'], set(cap['inputs']), set(), path + '.inputs')
        for key, ref in state['inputs'].items():
            rp = path + '.inputs.' + key
            names(ref, rp)
            require(set(ref) in ({'input'}, {'value'}, {'result', 'field'}), rp, 'invalid reference')
            require(all(isinstance(v, str) for v in ref.values()), rp, 'references must be strings')
            if 'input' in ref:
                require(ref['input'] in agent['inputs'], rp, 'unknown input')
            elif 'value' in ref:
                require(ref['value'] in agent['values'], rp, 'unknown value')
                needs[name].add(('value', ref['value']))
            else:
                source = states.get(ref['result'])
                require(isinstance(source, Mapping) and isinstance(source.get('action'), str) and source['action'] in caps, rp, 'unknown result state')
                require(ref['field'] in caps[source['action']]['outputs'], rp, 'unknown result field')
                needs[name].add(('result', ref['result']))
        fields(state['on'], set(cap['events']) | {'error'}, set(), path + '.on')
        require(set(state['on']) <= set(agent['alphabet']), path + '.on', 'event outside alphabet')
        for event, target in state['on'].items():
            require(isinstance(target, str) and target in states, path + '.on.' + event, 'unknown target')
        names(state.get('save', {}), path + '.save')
        for dest, src in state.get('save', {}).items():
            require(dest in agent['values'] and isinstance(src, str) and src in cap['outputs'], path + '.save', 'unknown value/output')
    require(bool(accepts), 'agent.states', 'at least one accept state is required')
    reachable = {agent['initial']}
    while True:
        expanded = reachable | {t for s in reachable for t in states[s].get('on', {}).values()}
        if expanded == reachable:
            break
        reachable = expanded
    require(reachable == set(states), 'agent.states', 'unreachable states')
    finishing = set(terminals)
    while True:
        expanded = finishing | {s for s, spec in states.items() if set(spec.get('on', {}).values()) & finishing}
        if expanded == finishing:
            break
        finishing = expanded
    require(finishing == set(states), 'agent.states', 'a state has no path to a terminal')
    universe = {('result', s) for s in states} | {('value', v) for v in agent['values']}
    facts = {s: set(universe) for s in states}
    while True:
        incoming = {s: [] for s in states}
        incoming[agent['initial']].append(set())
        for name, state in states.items():
            for event, target in state.get('on', {}).items():
                outgoing = set(facts[name])
                if event != 'error':
                    outgoing.add(('result', name))
                    outgoing.update(('value', v) for v in state.get('save', {}))
                incoming[target].append(outgoing)
        updated = {s: set.intersection(*edges) for s, edges in incoming.items()}
        if updated == facts:
            break
        facts = updated
    for name in states:
        require(needs[name] <= facts[name], 'agent.states.' + name + '.inputs', 'reference may be unassigned')
    plan = Plan(freeze(agent), freeze(caps), freeze(runtime))
    bind(plan, registry)
    return plan


def load_plan(agent, tools, runtime, registry):
    return compile_agent(load_yaml(agent), load_yaml(tools), load_yaml(runtime), registry)


async def run(plan, inputs, registry):
    """Run trusted, cooperative async adapters; cancellation is not rollback."""
    bound = bind(plan, registry)
    fields(inputs, set(plan.agent['inputs']), set(), 'input')
    require(all(isinstance(v, str) for v in inputs.values()), 'input', 'expected string values')
    context = {'input': dict(inputs), 'results': {}, 'values': {}}
    state, steps, trace = plan.agent['initial'], 0, []
    limits = plan.runtime['limits']
    while True:
        spec = plan.agent['states'][state]
        status = spec.get('terminal')
        if status or steps == limits['max_steps']:
            return dict(status=status or 'budget_exhausted', state=state, context=context, steps=steps, trace=trace)
        args = {}
        for key, ref in spec['inputs'].items():
            if 'input' in ref:
                args[key] = context['input'][ref['input']]
            elif 'value' in ref:
                args[key] = context['values'][ref['value']]
            else:
                args[key] = context['results'][ref['result']][ref['field']]
        steps += 1
        try:
            async with asyncio.timeout(limits['action_timeout_ms'] / 1000):
                reply = await bound[spec['action']](args)
            cap = plan.capabilities[spec['action']]
            fields(reply, {'event', 'output'}, set(), 'reply')
            require(reply['event'] in cap['events'], 'reply.event', 'unknown event')
            fields(reply['output'], set(cap['outputs']), set(), 'reply.output')
            require(all(isinstance(v, str) for v in reply['output'].values()), 'reply.output', 'expected strings')
            event, output = reply['event'], dict(reply['output'])
        except Exception as exc:
            event, output = 'error', None
            error = type(exc).__name__
        target = spec['on'][event]
        if output is not None:
            context['results'][state] = output
            context['values'].update({dest: output[src] for dest, src in spec.get('save', {}).items()})
        entry = dict(step=steps, state=state, event=event, next=target)
        if output is None:
            entry['error'] = error
        trace.append(entry)
        state = target


def main():
    from stub_tools import REGISTRY

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', default='examples/agent.yaml')
    parser.add_argument('--tools', default='examples/tools.yaml')
    parser.add_argument('--runtime', default='examples/runtime.yaml')
    parser.add_argument('--request', default='Explain finite-state agents')
    args = parser.parse_args()
    try:
        plan = load_plan(args.agent, args.tools, args.runtime, REGISTRY)
        result = asyncio.run(run(plan, {'request': args.request}, REGISTRY))
    except (ValidationError, OSError) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'accept' else 1


if __name__ == '__main__':
    from wearable import main as entrypoint

    raise SystemExit(entrypoint())
