# MVP tests and experiments

Validated on 2026-11-16 with Python 3.13, OpenAI Agents SDK 0.24.0 and the declared dependencies. Python 3.11+ is required by the code; other Python versions were not exercised in this session.

## Reproduce

```sh
python -m pip install -r requirements-openai.txt
python -W error::ResourceWarning -m unittest discover -s tests -v
```

All **42 tests pass** with the SDK installed. The base dependency-only environment also passes its suite, skipping the optional SDK test module. The tests make no hosted API calls and require no credentials. They exercise the actual SDK `Runner` with a scripted `Model` provider, not a replacement runner.

## Execution experiments

| Experiment | Observed result | Meaning |
| --- | --- | --- |
| Offline CLI, SQLite trace, graph check, recorded-run check | Accepted; both property checks satisfied | Basic runnable path and trace integration work |
| Real SDK runner with scripted model and internal `word_count` call | 2 outer steps, 3 SDK model requests, accepted | SDK tool loop runs inside the Wearable state; metrics preserve both levels |
| Schema-invalid model output | `model_output` evidence category and outer error transition | Structural output failure is separated from an unknown runtime defect |
| Simulated provider connection error | `provider_or_transport` category | Connection errors are not labelled bad model reasoning |
| Approval-required SDK tool | Adapter limitation, no tool execution, outer rejection | Unsupported pause protocol cannot silently proceed |
| Unknown Python error | `undetermined` origin | No unsupported causal blame |
| Review bypass in graph | Counterexample returned | Static analysis finds an illegal route before execution |
| Revision after a granted review | Property violation until another grant | Revocation semantics matter, not merely presence of a historical review |
| Missing/interrupted trace or old checkpoint without original log | Inconclusive | Missing evidence is not a passing evaluation |
| Checkpoint resume, stale writers, cancellation, deadline and budgets | Expected recovery/rejection/stop behaviors | Operational boundaries remain tested |

SDK usage in the scripted experiment is synthetic test data. The three requests are real runner invocations of a fake model, not three billable OpenAI calls. These experiments do not establish a speed, cost or quality advantage over other harnesses. Comparing different scripted paths would not be a fair harness benchmark.

## Earlier local-model experiments

The earlier Ollama implementation was live-tested against installed local models. A one-state LFM2.5 1.2B run returned `Hello!`, accepted, and resumed from a checkpoint without another inference. A longer revision workflow exhausted its budget after repeated revision requests. A Ministral run exercised malformed-JSON rejection. These were functional checks, not statistically meaningful model evaluations. The bundled Qwen example was not downloaded or live-tested.

## Not yet validated

No live OpenAI-hosted model call was made for this SDK change. Account/model access, real token costs, answer quality and provider-specific latency remain deployment checks. There is no benchmark dataset, independent semantic grader, inner-tool telemetry or complete accounting for tokens spent before SDK failures. There is no general temporal-logic engine or proof of inner-agent correctness.
