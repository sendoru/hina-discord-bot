# Answer request timeout

`ANSWER_TIMEOUT_SECONDS` (default 45.0, range 1.0–300.0) can be edited
in Dashboard → Runtime Config → Models & Generation or with
`/config set ANSWER_TIMEOUT_SECONDS <seconds>`. The startup value comes
from `.env`, and SQLite overrides take effect on the next provider request
without a bot restart.

The setting is a **wall-clock deadline for each answer provider request**.
It covers provider SDK retries and provider-managed tools within the request.
A subsequent provider request after a local tool result, or an explicit
transient retry, gets a fresh deadline. It does **not** cap the entire
Discord turn, local tool execution, context assembly, message delivery,
or output repair.

The primary provider transport timeout is above the configurable maximum
so overrides exceeding the former 45-second HTTP timeout work.
Both the production `hina-bot` entrypoint (which injects its provider client)
and the default `LLM` constructor use the same 305-second transport timeout.
Changing the answer deadline does not require rebuilding that client.
This setting is independent of `OUTPUT_REPAIR_TIMEOUT_SECONDS` and
`ROUTING_CLASSIFIER_TIMEOUT_SECONDS`. An expired deadline is logged as
`status=timeout`, `timeout_source=request_deadline`, and
`timeout_seconds`; existing answer error handling applies.

If a 120-second override still fails after about 45 seconds with `ReadTimeout`,
check that the deployed entrypoint includes the shared transport timeout above
and restart the bot after deploying the fix. Older entrypoints injected a client
with the provider factory's default 45-second transport timeout, bypassing the
longer timeout in the default `LLM` constructor. The runtime override could be
correctly applied while the inner HTTP read timeout fired first.

`runtime_config_startup` records startup environment values, not effective
values after DB overrides. A startup snapshot of 45 alongside an override of
120 is expected. Dashboard updates are applied through the bot's admin command
queue; check `admin_commands.status` and `result_json` for confirmation.
For per-turn timing and build identity, correlate `turn_id` in the configured
events/usage JSONL logs; these logs are not stored in the conversation SQLite DB.
