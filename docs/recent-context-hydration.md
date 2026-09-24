# Recent-context restart and hydration semantics

Server recent context is an ephemeral, bounded conversation aid. After a process restart or after
chat-log capture is re-enabled, Discord history may be used to rebuild the recent window.

## Canonical recent-row contract

Live Gateway capture and history hydration use the same row fields:

- `message_id`
- `role`
- `author_user_id`
- `reply_target_user_id`
- `direct_trigger`
- original Discord timestamp
- bounded display name/content

Timestamps describe the Discord message time when `created_at` is available. They do not indicate
whether a row came from live capture or hydration.

For live delivered assistant rows, the runtime explicitly records both the Hina author id and the
current request user's id as `reply_target_user_id`. Attaching live-only turn provenance is a
separate operation and must not be inferred from timestamp presence.

## What Discord history can reconstruct

For ordinary user and other-bot messages, history can reconstruct the author, role, trigger status,
timestamp, ordering, and content needed by the bounded recent window. Regression tests require these
rows to produce the same provider-facing recent-context semantics as live capture.

For an old Hina message, Discord history can identify Hina as the author but cannot by itself prove
which user request produced the answer. Therefore hydration currently restores such a row
conservatively:

- `role=assistant`
- Hina `author_user_id`
- `reply_target_user_id=None`
- no reconstructed turn provenance

The row remains ordinary channel context instead of being guessed into a caller's speaker thread.

## Intentional restart degradation

The recent-context layer intentionally does not persist a separate restart metadata index.

After restart, an old Hina message may therefore lose:

- the user id that originally caused that answer
- live-only turn provenance attached to that answer
- causal source metadata that existed only in the in-memory recent buffer

Hydration must prefer missing attribution over incorrect cross-user attribution. It must not infer a
target from message adjacency, answer text, or surrounding tone.

Explicit Discord replies are different: the current message itself still contains the replied
message id. `collect_reply_context()` can fetch that Discord message directly after restart, so the
replied Hina message remains strong `replied_message` context even when its older live-only
provenance is gone. The application does not fabricate missing `reply_origin_*` rows in that case.

This is the accepted #133 boundary. If production logs later show that losing assistant-target or
causal provenance across a restart causes recurring user-visible failures, that concrete failure
should be tracked separately before adding persistent ephemeral metadata, multi-chunk mappings, or
other restart-only infrastructure.
