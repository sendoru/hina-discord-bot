"""Request-scoped numeric Hina–member relationship lookup."""
import asyncio

import discord

from hina_bot.ai.local_tools import LocalToolError, LocalToolSpec
from hina_bot.ai.prompts import load_prompt
from hina_bot.core.memory_items import MemoryAccess, MemoryDisclosure, MemoryKind, memory_access
from hina_bot.core.relationship_profile import aggregate_shared_relationship_evidence
from hina_bot.core.routing import Scope
from hina_bot.core.scope_overrides import memory_mode_capabilities
from hina_bot.core.server_memory_repository import ServerSourceRef

from .private_thread_access import check_private_thread_access, private_thread_member_ids
from .server_memory import _authority


MEMBER_RELATIONSHIP = LocalToolSpec(
    name="get_member_relationship",
    description=load_prompt("member_relationship.md", section="memory").strip(),
    parameters={"type": "object", "properties": {"user_id": {"type": "string"}},
                "required": ["user_id"], "additionalProperties": False},
)


def _observations(store, target, cross_scope, public_channels):
    repo = store._server_memories
    for item in store.memory_items(target.user_id):
        if item.kind != MemoryKind.RELATIONSHIP or not item.source_message_ids:
            continue
        local = item.origin_realm == target.realm and item.origin_public_at_capture
        if local:
            if item.origin_channel_id not in public_channels or item.disclosure == MemoryDisclosure.REFERENCE_GATED:
                continue
        elif not (cross_scope and memory_access(item, target) == MemoryAccess.IMPLICIT):
            continue
        if item.origin_realm == f"dm:{target.user_id}":
            conversation = f"{item.origin_realm}:channel:{item.origin_channel_id}:user:{target.user_id}"
            if all(store.db.execute(
                """SELECT 1 FROM turns WHERE message_id=? AND scope=? AND user_id=?
                   AND json_valid(context_provenance)
                   AND coalesce(json_extract(context_provenance,'$.source_trigger_kind'),'') != 'withdrawn'""",
                (mid, conversation, str(target.user_id)),
            ).fetchone() for mid in item.source_message_ids):
                yield item
        elif item.origin_realm.startswith("guild:"):
            try:
                gid = int(item.origin_realm.removeprefix("guild:"))
                sources = [repo._source(gid, ServerSourceRef(mid, "user")) for mid in item.source_message_ids]
            except (ValueError, PermissionError):
                continue
            if all(origin.user_id == target.user_id and str(channel) == item.origin_channel_id
                   and (not local or origin.public_at_capture) for _, channel, origin, _ in sources):
                yield item


def register_member_relationship(registry, client, message, scope, confirmed_ids):
    async def lookup(arguments):
        uid = arguments.get("user_id")
        empty = {"profile": {}}
        if (set(arguments) != {"user_id"} or not isinstance(uid, str) or not uid.isascii()
                or not uid.isdecimal() or uid not in confirmed_ids or uid == str(scope.user_id)):
            return empty
        guild, channel, store = message.guild, message.channel, client.store
        target = Scope(scope.guild_id, scope.channel_id, int(uid))
        def enabled():
            return (client.settings.server_memory_read == "on"
                    and memory_mode_capabilities(store.memory_mode_chain(scope)["effective"])[0]
                    and memory_mode_capabilities(store.user_memory_mode(uid))[0]
                    and not store._server_memories.opted_out(scope.guild_id, uid))

        if not enabled() or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return empty
        origins = {item.origin_channel_id for item in store.memory_items(uid, origin_realm=scope.realm)
                   if item.kind == MemoryKind.RELATIONSHIP and item.origin_public_at_capture}
        try:
            async with asyncio.timeout(8):
                member = await guild.fetch_member(int(uid))
                if member.bot or guild.unavailable or not channel.permissions_for(member).view_channel:
                    return empty
                if isinstance(channel, discord.Thread) and channel.is_private():
                    ids = await private_thread_member_ids(channel)
                    check_private_thread_access(channel, guild, scope.user_id, client.user.id, ids)
                    if member.id not in ids:
                        return empty
                allowed, private, _ = await _authority(guild, scope, origins | {str(scope.channel_id)}, set())
        except (discord.HTTPException, LocalToolError, TimeoutError, OSError):
            return empty
        if str(scope.channel_id) not in allowed or not enabled():
            return empty
        shared = client.settings.personal_memory_cross_scope == "on" or store.cross_scope_opt_in(uid)
        items = list(_observations(store, target, shared, allowed - private))
        return {"user_id": uid, "profile": aggregate_shared_relationship_evidence(items, target)}

    registry.register(MEMBER_RELATIONSHIP, lookup)
