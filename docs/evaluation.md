# Minimal evaluation and local traceability

This MVP provides observations and one protocol property. It does not yet provide task datasets, automatic answer grading, a tournament scheduler or cost-normalized leaderboards.

## Record and inspect

```sh
python -m wearable --request "Explain agents" --trace traces.sqlite --run-id trial-1
sqlite3 traces.sqlite "SELECT id,run_id,state,event,data FROM trace_events ORDER BY id;"
```

`--trace` and `--checkpoint` may use the same SQLite file. They use separate tables and transactions. Each event has an ordered ID, UTC timestamp, run ID, state, kind and JSON metadata. The application appends events; SQLite itself is not tamper-proof. Use unique run IDs for independent trials and reuse a run ID only for checkpoint resume.

Events include `run.started/completed/failed`, `action.started/completed`, `checkpoint.reused`, and `sdk.started/completed/failed`. Run starts record workflow/configuration fingerprints. SDK completion includes reported request count and input/output/total tokens. Action/run durations are elapsed wall time, not CPU time. Prompts, answers and exception messages are omitted from traces; checkpoints still contain input/output data.

Interrupted processes may leave a start with no completion. Trace storage failures stop execution; logs are not a replacement for recovery checkpoints. No exactly-once logging or side effects are claimed. Completed-run resume adds a new run-attempt record but no action or SDK calls.

Example SQL queries (SQLite JSON functions required):

```sql
-- Per-state observed latency, including errors; do not sum run and action timings.
SELECT state, COUNT(*) AS attempts,
       AVG(json_extract(data, '$.elapsed_ms')) AS mean_ms
FROM trace_events WHERE event = 'action.completed' GROUP BY state;

-- Completed SDK usage; this excludes failed/interrupted SDK runs.
SELECT run_id, SUM(json_extract(data, '$.requests')) AS model_requests,
       SUM(json_extract(data, '$.total_tokens')) AS reported_tokens
FROM trace_events WHERE event = 'sdk.completed' GROUP BY run_id;

-- Evidence at the SDK boundary; do not also count its outer propagated failure.
SELECT json_extract(data, '$.failure.category') AS category,
       json_extract(data, '$.failure.origin') AS origin, COUNT(*) AS occurrences
FROM trace_events WHERE event = 'sdk.failed' GROUP BY category, origin;
```

Usage on failed SDK calls is not captured reliably and is **unknown**, not zero. No prices are inferred. Tool-call counts inside the SDK are not logged individually in this MVP; aggregate SDK requests count model requests. Those limitations prevent a full cost/fairness comparison today.

## Failure evidence versus cause

Every failure descriptor records boundary, category, origin, exception class and `root_cause_proven: false`.

| Observation | Category / origin | What it establishes |
| --- | --- | --- |
| SDK rejects a generated envelope | invalid_model_output / model_output | Output violated a structural contract, not why it happened |
| OpenAI API exception | provider_error / provider_or_transport | API/network/authentication/rate-limit failure, not bad model reasoning |
| SDK turn budget expires | turn_budget_exhausted / undetermined | Internal run did not finish within the limit |
| Outer deadline expires | timeout / undetermined | Could be model/server/tool latency or budget choice |
| SDK pauses for unsupported approval | unsupported_interruption / adapter | This adapter does not implement that protocol |
| Unknown Python/runtime error | execution_error / undetermined | Boundary and exception are known; root cause is not |
| Workflow property violation | counterexample from checker | Declared/observed protocol violates the specified rule |

Invalid output can result from a weak model, conflicting instructions, schema design or integration issues. We retain evidence without claiming a proven causal diagnosis. Semantically incorrect but structurally valid answers require independent task-specific grading, which is not implemented. Harness attribution needs controlled reproduction with a known-good/scripted model. Unknown failures must remain unknown.

## One declarative ordering property

`examples/review-property.yaml` defines:

- `before`: state entry requiring approval.
- `grant`: an outgoing `(state,event)` pair granting approval.
- `revoke`: outgoing pairs invalidating it.

Approval starts false. Each edge updates approval (revocation first) before entering its target. The example requires a passing check before `done`; writing a new draft/revision invalidates prior approval. For this example's single `answer` value, every declared writer must appear in `revoke`. The checker does not infer that list or identify artifact versions automatically. With multiple artifacts, this single-bit property is insufficient; do not claim per-artifact review guarantees.

```sh
# Exhaustive graph check, no model calls:
python -m evaluate --property examples/review-property.yaml

# Check one recorded run against the same graph:
python -m evaluate --property examples/review-property.yaml \
  --trace traces.sqlite --run-id trial-1
```

The static checker explores the product of workflow state and one approval bit. Each pair is visited once, so the number of explored configurations is at most twice the number of workflow states. It returns `satisfied` or a `violation` with a counterexample path. Paths are structurally possible, not predictions of model behavior; runtime budgets and data-dependent feasibility are not modeled. The implementation retains counterexample paths for readability, so memory also includes those path copies.

Trace checking returns `satisfied`, `violation`, or `inconclusive`. A mismatch in workflow fingerprints, missing events, interrupted attempts or ambiguous reused run IDs is inconclusive. A completed checkpoint with no earlier action log is insufficient evidence. Exit codes are 0, 1 and 2 respectively (2 also denotes input errors). A satisfied trace only covers that execution. A failed task can satisfy a safety property by never entering the protected state; report task success separately.

## Fair harness comparisons

Keep tasks, grading, model/version, prompts, tool implementation and relevant budgets fixed when isolating a harness. If those differ, label the result a system comparison. Compare task quality separately from protocol compliance, latency and resource usage. Use independent deterministic graders where possible; model judges add their own uncertainty. Repeated trials and cold/warm conditions matter. The offline experiments in [experiments.md](experiments.md) validate mechanisms, not comparative model performance.
