# Windowed cross-channel recent conversations (#542)

## Purpose

A user switching from a public server channel to DM should not lose the
immediately preceding interaction just because the next message lacks a
keyword such as "전에" or "기억나". Automatic cross-channel conversation
context is **time-bound and deterministic**, not selected with lexical
matching or a second LLM/classifier call.

## Read path

1. When persistent memory reading is enabled, select the requesting user's
   `shared_calls` from the configured recent window
   (`RECENT_CONTEXT_WINDOW_MINUTES`, default 60, allowed 15–180).
   The cutoff is calculated once for each request using UTC; SQLite
   `created_at` is stored as UTC `YYYY-MM-DD HH:MM:SS`.
2. In DM, consider that user's eligible public text channels across accessible
   guilds, subject to `public_memory_in_dm`. In a server, consider only the
   same user's other public text channels from that server. Current-channel
   conversation already belongs to the normal speaker/channel context.
3. Filter source candidates by timestamp and owner **before** live Discord
   permission checks; verify the user's current guild membership and
   `view_channel`/`read_message_history`, the default role's public
   visibility, the guild allowlist, chatlog settings, and supported channel
   type before reading content.
4. Read at most **4 sources × 2 completed user–Hina exchanges**, limited to
   **4,000 combined message characters**. A stored assistant reply is included
   when the completed turn still exists; an absent reply remains absent.
   Returned context uses `public_server_context[].recent_exchanges` with
   source, author, UTC time, user text and (when available) Hina reply.
   Long individual messages are truncated to 800 characters per side.
5. Apply the usual final egress filtering and realm/owner scope checks. The
   existing current-channel-only policy still excludes other channels when the
   user explicitly asks about *this* channel.

## Limits and distinct retrieval paths

- This path only reads **public bot-call** records. It is not a raw guild-wide
  capture, a general chat search, or a means of carrying other users' private
  or unrelated conversations between spaces.
- `shared_summaries` have no generation-time timestamp. They therefore do not
  enter the automatic recent context; an old summary is not treated as a
  recent exchange merely because the source has a new call.
- The legacy `_PUBLIC_MEMORY_QUERY`/`_BROAD_SERVER_MEMORY_QUERY` lexical
  preflight gate no longer decides if this windowed context is read, including
  for third-party reference questions. Older history must use an explicit
  authorized retrieval path (e.g., `get_chat_history` where that tool can
  access the source, or P4 server recall). DM `get_chat_history` cannot search
  public server channels; restoring explicit cross-space historical lookup
  would require separate scope/permission design.
- No new schema, embedding, model decision, search index, or background job is
  added. Request provenance already records the number/size of public context
  rows so operating traces can confirm inclusion.

## Regression scenario

- At 20:23 in a public server channel the user and Hina discuss a joke.
- At 20:31 the same user says in DM "히나야 헛소리는 여기다 하는 게 나았겠네".
- The DM includes the bounded recent public exchange without the phrase
  matching any hand-maintained lexical trigger, provided the underlying
  completed turn and live channel permissions remain available.
