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

## Remaining #133 work

A later restart-metadata phase may preserve a TTL-bound, content-free mapping from delivered Discord
answer ids to their request/target/source ids. That phase is responsible for restoring assistant
reply targets, bounded turn provenance, multi-chunk logical turns, and restart-time causal visual
links.

Until that metadata exists, hydration must prefer missing attribution over incorrect cross-user
attribution. It must not infer a target from message adjacency, answer text, or surrounding tone.
