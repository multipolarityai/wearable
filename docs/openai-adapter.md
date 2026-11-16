# OpenAI Agents SDK wearable

The outer YAML graph calls a registered adapter once per state. That adapter creates an SDK `Agent` and invokes `Runner.run`. The SDK owns its internal model/tool loop; Wearable validates the final `{event, output}` envelope and follows the declared transition.

## Install and run

Python 3.11+, from the repository root:

```sh
python -m pip install -r requirements-openai.txt
# Supply OPENAI_API_KEY through your environment or secret manager.
python -m wearable --openai-agents examples/openai-agents.yaml \
  --runtime examples/runtime-llm.yaml --request "Explain finite-state agents" \
  --checkpoint runs.sqlite --trace runs.sqlite --run-id sdk-demo
```

The optional dependency is pinned to the tested `openai-agents==0.24.0`. The example selects `gpt-4.1-mini`; change `model` for a model accessible to your account that supports tool calling and structured outputs. Hosted calls require an API key and may incur charges. The MVP tests do not require credentials and make no hosted calls. Ollama and default offline stubs still work without installing this extra dependency.

Use the same flags plus `--resume` to recover the outer run. `--llm` (Ollama) and `--openai-agents` are mutually exclusive CLI choices. In Python, a host can compose compatible adapter registries explicitly.

## Configuration

`examples/openai-agents.yaml` contains a model, an SDK `max_turns` limit, and adapters with capability, system instructions and a `$placeholder` input prompt. Optional `tools` lists names from the host's allowlisted SDK `FunctionTool` registry. The CLI provides only a read-only `word_count` tool; YAML cannot import Python functions. The checker is instructed to call it, allowing multiple SDK model turns inside one outer state.

The output schema is generated from `tools.yaml`. Both the SDK output parser and outer executor validate the envelope. Unknown events, invalid fields, SDK errors and exceeded SDK turn limits produce the outer `error` event. Cancellation propagates. SDK approval interruptions are explicitly unsupported and reported as an adapter limitation; they are never treated as successful completion.

For programmatic construction:

```python
from openai_wearable import build_registry, demo_tools
from wearable import load_yaml, load_plan, run

registry = build_registry(load_yaml("examples/openai-agents.yaml"),
                          load_yaml("examples/tools.yaml"),
                          tool_registry=demo_tools(), host_version="v1")
plan = load_plan("examples/agent.yaml", "examples/tools.yaml",
                 "examples/runtime-llm.yaml", registry)
# In async application code:
# result = await run(plan, {"request": "Explain agents"}, registry)
```

Bump `host_version` when registered tool implementations change. It contributes to checkpoint identity alongside YAML configuration. Each invocation starts a fresh SDK run: no conversation session, handoffs, resumable SDK `RunState`, MCP or hosted tools are configured by this first adapter.

## Boundary guarantees

- `max_steps`: outer adapter invocations, not internal SDK turns.
- `max_turns`: SDK model turns per invocation, not a hard limit on individual tool calls or spend.
- `action_timeout_ms`: outer deadline for the whole invocation, including the SDK loop. Trusted cooperative tools must honor cancellation.
- SQLite checkpoints: state boundaries only. An interrupted SDK invocation restarts from its input only after explicit pending-call retry; completed internal tool effects are not rolled back.
- SDK cloud tracing is disabled for these runs. Optional local SQLite logs capture metadata and reported aggregate SDK usage, not full prompts or outputs.

These are control boundaries, not a sandbox or correctness proof. See the [official SDK guidance](https://developers.openai.com/cookbook/examples/agents_sdk/migrate-from-claude-agent-sdk/readme) for `Agent`, `Runner`, tools and the distinction between conversation continuation and interrupted-run recovery.
