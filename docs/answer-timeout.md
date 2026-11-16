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
This setting is independent of `OUTPUT_REPAIR_TIMEOUT_SECONDS` and
`ROUTING_CLASSIFIER_TIMEOUT_SECONDS`. An expired deadline is logged as
`status=timeout`, `timeout_source=request_deadline`, and
`timeout_seconds`; existing answer error handling applies.
