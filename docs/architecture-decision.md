# Decision: finite-state control, ordinary evaluation data

Status: adopted for the MVP.

Wearable has two separate responsibilities: constrain an outer workflow, and record evidence for evaluation. They do not need the same machinery.

## Keep a finite-state graph where it pays for itself

The declarative workflow already has finite named states and event-labelled edges. Treating that graph explicitly enables checks for unknown transitions, unreachable states and references that might be unassigned. A small product-state check also answers an actionable question: can any declared path reach completion after a revision without a fresh review? The counterexample identifies the route to fix before spending money on model runs.

This is ordinary finite-state workflow analysis, not a claim of novel formal verification. An imperative program or another graph framework could implement the same checks. Our value is making this restricted contract explicit and reusable across adapters.

## Use simpler mechanisms elsewhere

| Need | MVP choice | Reason |
| --- | --- | --- |
| Historical latency, failures, usage | Typed event metadata and SQL | Simple, inspectable, no new query language |
| Answer correctness | Future task-specific tests/graders | A finite-state graph cannot establish factual correctness |
| Tool execution and inner planning | Existing SDK runner | Reuse its tested execution loop |
| Outer protocol ordering | Finite graph plus one-bit property monitor | Exhaustive finite exploration with counterexamples |
| Durable recovery | SQLite checkpoints | Operational concern with explicit interrupted-call semantics |

A fixed linear pipeline could be implemented more cheaply as a few Python functions. If users do not need configurable branching or protocol checks, Wearable may not justify its extra layer. A DAG is simpler for acyclic work, but the current revision example deliberately contains a loop. General-purpose code is more expressive but harder to inspect exhaustively. Temporal-logic/model-checking tools may become appropriate for richer concurrency or liveness requirements; building one now is unnecessary.

## What we are not claiming

There is no Turing-machine executor, general formal-language DSL, proof of model correctness, proof of eventual success, or measured speed advantage. Unbounded strings and inner SDK programs mean the entire system is not a finite automaton. Only the abstract outer control graph and the explicit property monitor are finite. The graph can show a path to a terminal without guaranteeing the model takes it; budgets impose an operational stop.

One-bit review semantics work only when grant/revoke events correctly represent review of the current artifact. They cannot express arbitrary data lineage or prove the review is truthful. Keep that limitation visible rather than hiding it behind formal notation.

Future work should be driven by concrete evaluation questions. Extend the semantics only when an existing test or SQL query cannot answer a useful question reliably. General-language and Turing-machine extensions are outside the roadmap.
