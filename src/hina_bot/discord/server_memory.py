"""Live Discord authority for server memories and public self-disclosures."""
import asyncio
import json

import discord

from hina_bot.core.scope_overrides import memory_mode_capabilities
from hina_bot.core.server_memory_repository import ServerSourceRef
from hina_bot.core.server_memory_selection import select_server_rows


def _profiles(store, scope, related):
    repo = store._server_memories
    for uid in related - {str(scope.user_id)}:
        if repo.opted_out(scope.guild_id, uid) or not memory_mode_capabilities(store.user_memory_mode(uid))[0]:
            continue
        items = store.memory_items(uid, origin_realm=scope.realm)
        annotations = store.memory_annotations([item.id for item in items])
        for item in items:
            ann = annotations.get(item.id)
            if (not ann or ann.audience != "server" or ann.usage_mode not in ("contextual", "ambient")
                    or ann.item_revision != item.revision or not ann.evidence or not item.origin_public_at_capture
                    or item.disclosure == "reference_gated" or not item.source_message_ids):
                continue
            try:
                sources = [repo._source(scope.guild_id, ServerSourceRef(mid, "user"))
                           for mid in item.source_message_ids]
            except (ValueError, PermissionError):
                continue
            if not all(str(row["user_id"]) == uid and str(channel) == item.origin_channel_id
                       and src_scope.public_at_capture for row, channel, src_scope, _ in sources):
                continue
            if not any(ann.evidence in text for _, _, _, text in sources):
                continue
            yield {"id": item.id, "store": "member_profile", "usage_mode": "contextual",
                   "trust": "self_disclosed", "subject_user_ids": json.dumps([uid]), "content": item.content,
                   "keywords": json.dumps(ann.keywords), "importance": ann.importance,
                   "time_scope": ann.time_scope.value, "event_at": ann.event_at, "observed_at": ann.observed_at,
                   "origin_channel_id": item.origin_channel_id, "last_used_at": ann.last_used_at}


async def _authority(guild, scope, channels, authors):
    try:
        reader = await guild.fetch_member(scope.user_id)
    except discord.HTTPException:
        return set(), set(), {}
    private = set()
    allowed, members = set(), {str(reader.id): reader.display_name}
    for cid in sorted(channels):
        try:
            channel = await guild.fetch_channel(int(cid))
            thread = channel if isinstance(channel, discord.Thread) else None
            if thread is not None:
                channel = await guild.fetch_channel(thread.parent_id)
                if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                    continue
            elif not isinstance(channel, discord.TextChannel):
                continue
            perm = channel.permissions_for(reader)
            if not (perm.view_channel and perm.read_message_history):
                continue
            public = channel.permissions_for(guild.default_role)
            private_thread = thread is not None and thread.is_private()
            is_public = not private_thread and public.view_channel and public.read_message_history
            if not is_public:
                if cid != str(scope.channel_id):
                    continue
                if private_thread and not perm.manage_threads:
                    membership = await thread.fetch_member(reader.id)
                    if membership.id != reader.id:
                        continue
                private.add(cid)
            allowed.add(cid)
        except discord.HTTPException:
            continue
    for uid in sorted(authors - members.keys()):
        try:
            member = await guild.fetch_member(int(uid))
        except discord.HTTPException:
            continue
        members[uid] = member.display_name
    return allowed, private, members


def server_reader(client, message, scope):
    async def read(query, *, lookup=False, manage=False):
        guild = message.guild
        store = client.store

        def enabled():
            return (getattr(client.settings, "server_memory_read", "on") == "on"
                    and memory_mode_capabilities(store.memory_mode_chain(scope)["effective"])[0])

        if guild is None or not (manage or enabled()):
            return []
        repo = store._server_memories
        related = {str(m.id) for m in getattr(message, "mentions", ())} - {str(scope.user_id)}
        replied = getattr(getattr(message, "reference", None), "resolved", None)
        if getattr(replied, "author", None):
            related.add(str(replied.author.id))
        related = set(sorted(related)[:32])
        profiles = [] if manage else list(_profiles(store, scope, related))
        sources = [s for row in repo.items(scope.guild_id)
                   if row["space_key"] in ("guild", f"channel:{scope.channel_id}") for s in repo.sources(row["id"])]
        if not (sources or profiles):
            return []
        channels = {s["source_channel_id"] for s in sources} | {p["origin_channel_id"] for p in profiles}
        authors = {s["author_user_id"] for s in sources} | related
        try:
            async with asyncio.timeout(8):
                allowed, private, members = await _authority(guild, scope, channels, authors)
        except (TimeoutError, OSError):
            client.events.emit("memory.server_access_unavailable")
            return []
        if str(scope.user_id) not in members:
            return []
        if manage:
            return [r for r in repo.items(scope.guild_id)
                    if r["space_key"] in ("guild", f"channel:{scope.channel_id}")
                    and (refs := repo.sources(r["id"])) and all(s["source_channel_id"] in allowed for s in refs)]
        if not enabled():
            return []
        rows = store.server_memory_candidates(scope, readable_channel_ids=allowed, member_user_ids=members,
                                              related_user_ids=related)
        rows = [r for r in rows if r["space_key"] == "guild" or r["origin_channel_id"] in private]
        rows.extend(p for p in _profiles(store, scope, related & members.keys()) if p["origin_channel_id"] in allowed)
        result = select_server_rows(rows, query, lookup=lookup)
        for row in result:
            row["subjects"] = [{"user_id": uid, "name": members[uid]} for uid in row.pop("subject_user_ids")]
        client.events.emit("memory.server_selection", selected=[(r["store"], r["id"], r["tier"]) for r in result])
        return result
    return read
