# HARCH Autonomy Controller v9 (dry-run)

This is an isolated engineering workflow prototype, not part of hospital operations.
It has **no LLM calls, filesystem writes, network calls, persistence, or business actions**.

Run: `pytest tests/test_autonomy_controller.py -q`.

The event schema is a first step toward Mission Control visualization: each transition
records task, state, agent, evidence, result and human approval. Events currently live
in memory and are **not durable**.

## Safety

- Integration is gated on explicit `approved=True` passed by trusted caller; this is
  **not an authenticated approval mechanism**. Never wire untrusted agent output here.
- A failure or missing integration approval blocks the run.
- No direct deployment, merge, production credentials, or business workflow access.
- Next steps: durable event store, authenticated approval, idempotency, retries, real
  agent adapters, policy engine and telemetry API. Do not deploy as-is.
