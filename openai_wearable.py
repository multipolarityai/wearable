"""An optional OpenAI Agents SDK harness inside a Wearable state."""
import json
from string import Template

from agents import Agent, AgentOutputSchemaBase, ModelBehaviorError, Runner, RunConfig, function_tool
from agents import FunctionTool

from trace_log import emit
from failures import classify
from openai import APIError
from checkpoints import fingerprint
from wearable import Adapter, ValidationError, fields, names, require, strings, symbols


class UnsupportedSDKInterruption(ValidationError):
    pass


class Envelope(AgentOutputSchemaBase):
    def __init__(self, outputs, events):
        self.outputs, self.events = tuple(outputs), tuple(events)

    def is_plain_text(self):
        return False

    def name(self):
        return 'wearable_result'

    def is_strict_json_schema(self):
        return True

    def json_schema(self):
        return {'type': 'object', 'required': ['event', 'output'], 'additionalProperties': False,
                'properties': {'event': {'type': 'string', 'enum': list(self.events)},
                               'output': {'type': 'object', 'required': list(self.outputs),
                                          'additionalProperties': False,
                                          'properties': {k: {'type': 'string'} for k in self.outputs}}}}

    def validate_json(self, json_str):
        try:
            reply = json.loads(json_str)
            fields(reply, {'event', 'output'}, set(), 'sdk.reply')
            require(reply['event'] in self.events, 'sdk.reply.event', 'unknown event')
            fields(reply['output'], set(self.outputs), set(), 'sdk.reply.output')
            require(all(isinstance(v, str) for v in reply['output'].values()), 'sdk.reply.output', 'expected strings')
            return reply
        except (ValueError, TypeError) as exc:
            raise ModelBehaviorError('Invalid Wearable result envelope') from exc


def build_registry(config, tools, *, tool_registry=None, host_version='v1', model_provider=None):
    """Only host-registered FunctionTools can be selected by YAML. No imports/eval."""
    fields(config, {'version', 'model', 'max_turns', 'adapters'}, set(), 'openai_agents')
    require(config['version'] == '0.1', 'openai_agents.version', 'expected "0.1"')
    require(isinstance(config['model'], str) and bool(config['model'].strip()), 'openai_agents.model', 'expected model name')
    require(type(config['max_turns']) is int and config['max_turns'] > 0, 'openai_agents.max_turns', 'expected positive integer')
    names(config['adapters'], 'openai_agents.adapters')
    fields(tools, {'version', 'capabilities'}, set(), 'tools')
    names(tools['capabilities'], 'tools.capabilities')
    available, registry = tool_registry or {}, {}
    for name, spec in config['adapters'].items():
        path = 'openai_agents.adapters.' + name
        fields(spec, {'capability', 'system', 'prompt'}, {'tools'}, path)
        require(isinstance(spec['capability'], str) and spec['capability'] in tools['capabilities'], path, 'unknown capability')
        cap = tools['capabilities'][spec['capability']]
        fields(cap, {'inputs', 'outputs', 'events'}, set(), path)
        strings(cap['inputs'], path)
        strings(cap['outputs'], path)
        symbols(cap['events'], path)
        require('error' not in cap['events'], path, 'error is reserved')
        for key in ('system', 'prompt'):
            require(isinstance(spec[key], str) and bool(spec[key].strip()), path + '.' + key, 'expected nonempty string')
        template = Template(spec['prompt'])
        require(template.is_valid() and set(template.get_identifiers()) <= set(cap['inputs']), path + '.prompt', 'invalid input placeholders')
        selected = spec.get('tools', [])
        require(isinstance(selected, list), path + '.tools', 'expected a list')
        if selected:
            symbols(selected, path + '.tools')
        require(all(t in available and isinstance(available[t], FunctionTool) for t in selected), path + '.tools', 'unknown or unsupported host tool')
        require(all(available[t].name == t for t in selected), path + '.tools', 'tool name must match registry key')
        schema = Envelope(cap['outputs'], cap['events'])
        agent = Agent(name=name, model=config['model'], instructions=spec['system'],
                      output_type=schema, tools=[available[t] for t in selected])
        run_config = RunConfig(tracing_disabled=True)
        if model_provider is not None:
            run_config.model_provider = model_provider
        call = make_call(agent, template, schema, config['max_turns'], run_config)
        registry[name] = Adapter(tuple(cap['inputs']), tuple(cap['outputs']), tuple(cap['events']), call,
                                 identity=fingerprint({'config': config, 'adapter': name, 'host': host_version, 'implementation': 1}))
    return registry


def make_call(agent, template, schema, max_turns, run_config):
    async def call(args):
        emit('sdk.started', harness='openai-agents', agent=agent.name, model=agent.model, max_turns=max_turns)
        try:
            result = await Runner.run(agent, template.substitute(args), max_turns=max_turns, run_config=run_config)
        except BaseException as exc:
            failure = classify(exc, 'openai-agents')
            if isinstance(exc, APIError):
                failure.update(category='provider_error', origin='provider_or_transport')
            emit('sdk.failed', harness='openai-agents', failure=failure)
            raise
        usage = result.context_wrapper.usage
        emit('sdk.completed', harness='openai-agents', requests=usage.requests,
             input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, total_tokens=usage.total_tokens)
        if result.interruptions:
            error = UnsupportedSDKInterruption('SDK approvals/interruptions are unsupported by this adapter')
            emit('adapter.failed', failure=classify(error, 'openai-adapter'))
            raise error
        # Enforce the boundary even when a custom provider returns unexpected output.
        return schema.validate_json(json.dumps(result.final_output))
    return call


def demo_tools():
    @function_tool
    async def word_count(text: str) -> int:
        """Count whitespace-separated words in text."""
        return len(text.split())
    return {'word_count': word_count}
