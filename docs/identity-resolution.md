# Discord identity resolution

Identity resolution connects a current name reference to a Discord-known user id. It is independent
of history and memory retrieval: resolving `2_718281 핑해줘` does not authorize that person's history
or public summaries.

## Current member source

Set `DISCORD_MEMBERS_INTENT=true` and enable **Server Members Intent** in the Discord Developer
Portal, then restart the bot. This setting is startup-only, not a `/config` runtime setting. The
default is false so existing deployments do not unexpectedly request an unconfigured privileged
intent. Portal permission and any required intent review must be in place before enabling it;
otherwise Discord may close the Gateway connection with code 4014.

The client uses discord.py's members-intent cache and startup chunking. A directory is complete
only with that intent enabled and `guild.chunked` true. Startup time and memory use therefore grow
with the guilds' member counts. No complete member fetch or chunk runs in the message path.
If startup chunking fails or the guild is unavailable, a partial cache is not treated as a complete
directory. Gateway member updates maintain current metadata; no separate persistent alias database
is introduced. Presences intent is not required.

For ordinary guild text channels, candidates are current human members with
`channel.permissions_for(member).view_channel`. The caller must also be able to view the channel.
The Hina account and other bots are excluded. Private threads have separate membership semantics;
textual resolution is deferred there, while actual current-message mention reuse remains available.
The caller's own username can also be resolved; this does not change the current-speaker identity.

Current aliases are ordered `display_name`, `global_name`, `name`, deduplicated and bounded. These
aliases take priority over old display names and cannot be crowded out by four historical names.

## Local matching and semantic fallback

Every nonempty triggered guild request may be checked for local name evidence. The old profile-only
regex no longer gates identity resolution; adding verbs is unnecessary.

With a complete directory, local matching checks the entire visible candidate set before any
provider-sized limit is applied. It uses NFKC, case folding and whitespace normalization, preserving
username separators such as underscores. A match must cover a full alias with word/username or
Korean particle boundaries. Short aliases (fewer than three alphanumeric characters), bare numeric
aliases and arbitrary substrings cannot automatically resolve. Duplicate normalized aliases and
requests naming multiple people are ambiguous, never resolved by choosing the first member.
The result keeps the original reference span from the current request.

When no unique current exact match exists, local lexical/phonetic search narrows the candidates.
Hangul syllable romanization, display-name tokens and conservative spelling similarity are search
keys only: they do not authorize a user id. For example, `센돌` can shortlist `sendol`, but the
existing semantic resolver still decides whether the names refer to the same person. This is not
a complete transliteration system; unfamiliar variants may remain unresolved.

Only a relevant shortlist is sent to the semantic resolver, with at most 32 users and four names
per user. An oversized shortlist is ambiguous; competing candidates are not silently truncated.
No shortlist means no identity provider call. The final answer never receives the member directory.

## Historical fallback

`shared_calls` remains bounded historical name evidence (256 recent rows, at most 32 users), not
the current guild directory. Each old name is filtered using its own source channel: that channel
must currently be public/readable and readable by the caller. A visible record for the same user
does not authorize aliases from a different, hidden source channel.

With a complete directory, historical aliases may supplement currently visible members for
semantic matching, but current aliases are tested first. Departed or hidden members cannot enter
the fallback.

Without a complete directory, only locally narrowed historical identities can use the legacy
semantic path. At most eight are revalidated with `fetch_member(id)` under a timeout and the normal
request concurrency limit. Their current membership, bot status and current-channel visibility
must pass before a provider sees them. Historical candidates missing from the cache are not silently
assumed to be current members. This path is intentionally less complete than a maintained directory;
it cannot discover every inactive member or guarantee guild-wide nickname uniqueness.

## Answer context and retrieval

The request-scoped `resolved_identities` field carries at most two selected identities, each with
`user_id`, the request's `reference` and at most four `names`. It is separate from
`current_interaction.mentions`, which continues to mean actual Discord mentions. It is available
even with memory or chat logging disabled, and is reset when the turn completes or raises.
Routing context-size accounting includes the same admitted identity data as the answer request.

- Calling/pinging: resolved identity only; no extra target history or target public memory.
- Recent speech: identity plus bounded current-channel target history, without target public memory.
- Profile/deep history/public recall: independently selected history and authorized public memory.

Existing normal recent-context hydration and the current speaker's own memory are unchanged. They
are not needed to convey the resolved identity. Resolving someone does not change `current_speaker`.

## Privacy, mentions and observability

`EXTERNAL_CONTEXT_POLICY=bot_interactions_only` still blocks textual cross-user resolution before
reading a directory or invoking its provider. The final `apply_context_policy` boundary independently
removes `resolved_identities`, including accidental adapter injections. Explicit third-party Discord
mentions bypass textual lookup and keep the existing #250 reuse policy. Ordinary mentions remain
controlled by `ALLOW_USER_MENTIONS`; everyone, here and role mentions remain inert.

`identity.resolution` telemetry records `resolution_method` (`exact`, `semantic` or `policy`) and
`directory_complete`, plus existing outcomes/counts and HMAC groups. Exact resolution has
`resolver_invoked=false` and `evidence_source=member_directory`; semantic resolution keeps
`resolver_invoked=true` and `evidence_source=resolver_derived`. A local result is not a provider call.
Final context provenance records selected/blocked identity counts without raw names or references.
These observations do not learn aliases from assistant output.

## Discord references

- [discord.py intents, member cache and retrieval](https://discordpy.readthedocs.io/en/stable/intents.html)
- [Discord privileged intent configuration](https://docs.discord.com/developers/events/gateway#privileged-intents)
- [Request Guild Members](https://docs.discord.com/developers/events/gateway-events#request-guild-members)
- [Full member request rate limit](https://docs.discord.com/developers/change-log#introducing-rate-limit-when-requesting-all-guild-members)

Whole-guild Gateway member requests are limited to one per guild per bot every 30 seconds. Prefix
queries and `fetch_member(id)` serve different purposes and cannot substitute for a complete
normalized-alias directory. The bot does not issue whole-guild requests for individual user messages.
