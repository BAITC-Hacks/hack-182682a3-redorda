# Backend execution

The HTTP layer calls `start_run(run_id, *, idempotency_key)` and
`request_cancel(run_id)`. Both return `CampaignRun` and raise
`ExecutionConflict` for an invalid transition. `start_run` raises
`ExecutionUnavailable` when publication fails. They do not return DRF objects.

`start_run` locks the run row and changes `draft` to `queued` exactly once. A
deterministic Celery task ID derived from the run UUID and idempotency key is
stored in `task_id`. A retry with the same key returns the existing run; a
different key conflicts. The task is published only after commit. If broker
publication raises, the run becomes `failed` with `queue_unavailable`. A late
delivery of that message cannot claim it.

The worker claims only `queued` runs with its matching task ID under a row
lock. A duplicate delivery sees `running` or a terminal state and exits before
calling the agent. Celery automatic retries are disabled. Events are saved for
`queued`, `running`, `cancel_requested`, each `pilot_completed`, each
`campaign_result`, `result_ready`, and the terminal status. Pilots and campaign
results are saved as soon as the real engine provides them. Terminal states
are immutable under late deliveries and timeout guards.

The worker checks the cancellation flag before each public environment access,
before and after each pilot, and before saving each campaign. `queued` runs
cancel immediately; `running` runs stop cooperatively. The task has a 570
second soft limit, a 600 second hard limit, and a one-shot guard scheduled for
610 seconds to mark runs whose worker was killed. A worker must not restart a
`running` calculation: a pilot may have happened before the process died.

Production requires `REDORDA_ENVIRONMENT_FACTORY` to be a dotted import path
for a callable accepting `EngineContext` and returning an implementation of
the public `AgentEnvironment` protocol. The context contains `dataset_path`,
`budget`, `max_contacts`, `max_pilots`, `seed`, and `strategy`. The factory must
use only public organizer APIs. It must return actual pilot responses with
`cost` and `n_customers` so each pilot can be persisted. The worker calls the
shared `campaign_engine.Agent.act(env)`; there is no backend strategy or fake
result. The API remains disabled until `REDORDA_RUN_EXECUTION_ENABLED=1` and a factory
are configured. It returns 503 `engine_unavailable` without queueing. If an
operator enables an incomplete integration, the worker ends `failed` with
`engine_unavailable`.

`CampaignResult.metrics` and `RunResult.summary` contain only data returned by
the engine. If its current `list[dict]` output contains campaigns alone, these
fields are empty; the backend does not invent predicted uplift or simulation
results. The test runner is injected only by tests.

PostgreSQL row locks provide the concurrency guarantee in deployment.
`backend/tests/integration/` tests eight concurrent HTTP starts, duplicate claims,
live Redis delivery, cancellation, worker exceptions, and a separate prefork
worker. Run `make check-integration` against a dedicated PostgreSQL database.
Successful pipeline tests inject a runner exclusively in tests; they do not
validate the unfinished competition strategy.

The adapter checks requested pilot count, contacts and public channel cost before
calling the environment, and validates saved output before completing a run.
Unknown final metrics remain unknown; the AI adapter must enforce the real
environment limits. macOS spawn children initialize Celery task tracing in
`config/celery.py`; otherwise Celery 5.6 can fail before calling the task body.
