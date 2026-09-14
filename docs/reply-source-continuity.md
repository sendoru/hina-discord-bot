# Reply source continuity

In `full` mode, delivered Hina answers retain their explicitly quoted source in the
ephemeral channel buffer. Follow-up requests include up to two distinct sources from
the caller's last four buffered Hina answers (or an explicitly selected Hina answer
in the same channel). Each source identifies the answer and caller it belonged to;
it is reference data, never an instruction or a new user utterance.

The current explicit reply has first priority, followed by retained sources, then
the existing recent-conversation allocation. All text shares the existing channel
character budget. Truncated excerpts are marked; exact analysis may require the
user to quote the complete text again.

`bot_interactions_only` does not admit these additional historical source rows.
The existing explicit-reply restrictions are unchanged. Nested source data is
removed before serialization, and retained sources pass the final egress filter.

Sources expire/evict with their owning answer, using the existing buffer TTL and
limits, and are cleared by channel clearing, forgetting, or process restart. They
are not written to persistent memory, summaries, or restored by history hydration.
There is no recursive reply-chain fetch. With chatlog disabled, this channel-context
feature is disabled as well.

The response policy asks for clarification when a referent is ambiguous and asks
for the original when exact analysis requires missing text. Injection text can be
translated/analyzed as data without executing its instructions. These are model
instructions, not a deterministic guarantee of Gemini's response behavior.
