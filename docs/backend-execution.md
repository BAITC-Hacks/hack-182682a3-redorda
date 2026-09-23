# Backend execution

The HTTP layer calls `start_run(run_id, *, idempotency_key, execution_available)` and
`request_cancel(run_id)`. Both return `CampaignRun` and raise
`ExecutionConflict` for an invalid transition. `start_run` raises
`ExecutionUnavailable` when publication fails. A new draft with execution disabled
raises `EngineNotReady`; a retry with the same key is returned before that gate.
They do not return DRF objects.

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
`campaign_result`, `result_ready`, and the terminal status, alongside events from
the shared runner. Pilots and campaign results are persisted as they become
available. Terminal states are immutable under late deliveries and timeout guards.

`pilot_completed` is appended once, immediately after persisting the public
environment response. Its payload contains `sequence`, `channel`,
`requested_customers`, `n_customers`, `cost`, `request`, and the public fields of
`observation`. The runner's subsequent belief update becomes a separate
`pilot_estimate_updated` event with `{sequence, posterior}`. Previously returned
events are never rewritten, so a client using `after` cannot miss an update.
The runner event `run_completed` means its portfolio is ready; the web run remains
`running` until result persistence, validation, and the task's final transition.

The worker checks the cancellation flag before each public environment access,
before and after each pilot, and before saving each campaign. `queued` runs
cancel immediately; `running` runs stop cooperatively. The task has a 570
second soft limit, a 600 second hard limit, and a one-shot guard scheduled for
610 seconds to mark runs whose worker was killed. A worker must not restart a
`running` calculation: a pilot may have happened before the process died.

`REDORDA_ENVIRONMENT_FACTORY` is a dotted import path for a callable accepting
`EngineContext` and returning the public `AgentEnvironment` protocol. The context
contains `dataset_path`, `budget`, `max_contacts`, `max_pilots`, `seed`, and
`strategy`. The implemented factory is
`apps.campaigns.services.participant_environment.create_environment`. It loads the
complete kit at the saved `Dataset.source_dir` and calls public
`make_mock_env(seed)`. It returns only the environment; model internals are not
passed to the agent. It verifies the profile against the selected dataset, tariff
codes, channel costs, fresh counters, and empty pilot history. CSV and module
paths are resolved inside the factory call and restored afterward; workers use
prefork.

The worker calls shared `run_campaigns` with a `RunConfig` built from the saved run,
history loaded from that dataset, an event observer, and cooperative cancellation.
`Agent.act` calls the same runner for direct use. HTTP `baseline` and `openai`
select the hypothesis source; pilot selection and the portfolio optimizer are
shared. An unavailable OpenAI response causes the runner to record `fallback_used`
and continue with deterministic hypotheses.

`meta.features.run_execution` is true only when `REDORDA_RUN_EXECUTION_ENABLED=1`
and a factory path is configured. `openai_strategy` additionally requires a
nonempty server `OPENAI_API_KEY`. Creating an unavailable OpenAI draft returns
400 with `fields.strategy`; starting one returns 503 `openai_unavailable`.
A new baseline draft with execution disabled returns 503 `engine_unavailable`.
Neither failure queues a task or changes the draft status. A same-key retry of
an already-started run is returned before the feature gate. These flags check
configuration, not worker health, dataset completeness, or validity of the key.
`manage.py check_agent` checks the dataset and fresh environment without pilots
or model requests; deployment steps are in [deployment.md](deployment.md).

`CampaignResult.metrics` persists the runner's `n_contacts`, `cost`, and
`estimated_incremental_net`. `RunResult.summary.predicted_effect` contains
`source="posterior_forecast_not_official_score"`, `net_arpu_gain_mean`,
`lower_tail_mean_10`, and `objective`. The lower-tail estimate is a model scenario
statistic, not a confidence interval. Warnings, limits of the estimate, resource
usage, metadata, and stop reason are saved in the summary. Separate final simulator
evaluation is not part of this web path, so `simulator_result` stays `null`.
Historical results with missing metrics keep those fields unknown. The legacy
`runner` argument in `run_engine` remains a test seam only.

PostgreSQL row locks provide the concurrency guarantee in deployment.
`backend/tests/integration/` tests eight concurrent HTTP starts, duplicate claims,
live Redis delivery, cancellation, worker exceptions, and a separate prefork
worker. Run `make check-integration` against a dedicated PostgreSQL database.
Those infrastructure tests use controlled runners. `test_engine_integration.py`
exercises the real shared runner on a controlled public environment, while
`test_participant_pipeline.py` covers HTTP start, the actual Celery task body,
results and CSV against an installed participant kit (skipped when it is absent).
These checks establish integration behavior, not an algorithm performance advantage.

The adapter validates request sizes and pilot count, then computes achievable
contacts from audience filters, public resource counters and channel price before
calling the environment. The actual response must match this count and cost;
small audiences can yield fewer contacts than requested. Both the shared runner
and backend validate resource limits and saved results before completing a run.
macOS spawn children initialize Celery task tracing in
`config/celery.py`; otherwise Celery 5.6 can fail before calling the task body.
