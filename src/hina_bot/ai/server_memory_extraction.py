"""Extract only from new, explicitly addressed interactions in one channel."""
import json

from hina_bot.ai.memory_model_routing import build_memory_model_plan
from hina_bot.ai.prompts import load_prompt
from hina_bot.core.server_memory_repository import ServerSourceRef

POLICY = load_prompt("server_extraction.md", section="memory")
PENDING = """json_valid(context_provenance)
    AND json_extract(context_provenance,'$.source_trigger_kind')='explicit'
    AND json_extract(context_provenance,'$.server_memory_processed') IS NULL"""


def pending_spaces(store):
    rows = store.db.execute(f"""SELECT DISTINCT realm,scope FROM turns
        WHERE realm LIKE 'guild:%' AND {PENDING}""")
    return {(int(realm.split(':')[1]), int(scope.split(':')[3])) for realm, scope in rows}


def batch(store, guild, channel):
    repo = store._server_memories
    rows = store.db.execute(f"""SELECT message_id,name,user_id,created_at,
        created_at <= datetime('now','-8 hours') AS stale FROM turns
        WHERE realm=? AND scope LIKE ? AND {PENDING} ORDER BY id""",
        (f"guild:{guild}", f"guild:{guild}:channel:{channel}:user:%"))
    turns = []
    for row in rows:
        ref = ServerSourceRef(row["message_id"], "user")
        try:
            source, _, scope, content = repo._source(guild, ref)
        except (ValueError, PermissionError):
            continue
        turn = dict(row)
        turn.update(content=content[:3000], public=scope.public_at_capture)
        provenance = json.loads(source["context_provenance"])
        if not any(provenance.get(k) for k in ("history_lookup", "memory_lookup", "server_memory")):
            turn["reply"] = source["reply"][:2000]
        turns.append(turn)
        if len(turns) == 16:
            break
    return turns


def _candidates(repo, guild, channel, space_key):
    return {r["id"]: r["content"] for r in repo.items(guild, limit=32)
            if r["space_key"] == space_key and (refs := repo.sources(r["id"]))
            and all(s["source_channel_id"] == str(channel) for s in refs)}


async def extract_server_batch(llm, store, guild, channel, *, stale=False, admin_verifier=None):
    key = (guild, channel)
    running = store._server_extractions
    if key in running:
        return
    running.add(key)
    try:
        turns = batch(store, guild, channel)
        if len(turns) < 16 and not (stale and len(turns) >= 2 and turns[0]["stale"]):
            return
        ids = {r["message_id"] for r in turns}
        accepted = 0
        for public in (False, True):
            eligible = {r["message_id"]: r for r in turns if r["public"] == public}
            if not eligible:
                continue
            space_key = "guild" if public else f"channel:{channel}"
            repo = store._server_memories
            existing = _candidates(repo, guild, channel, space_key)
            payload = {"turns": list(eligible.values()), "existing": existing}
            plan = build_memory_model_plan(llm.settings, "", payload, shared=True)
            response = await llm._memory_request("extract_server_memory", POLICY, payload, plan)
            if response.status != "completed":
                return
            proposals = json.loads(response.output_text)
            if not isinstance(proposals, list) or len(proposals) > 8:
                raise ValueError("Invalid server memory proposal list")
            # Withdrawal or deletion while awaiting the provider invalidates its whole input.
            live = {r["message_id"] for r in batch(store, guild, channel)}
            if not eligible.keys() <= live or _candidates(repo, guild, channel, space_key) != existing:
                return
            for proposal in proposals:
                try:
                    fields = dict(proposal)
                    relation = fields.pop("relation", None)
                    target = fields.pop("relation_target", None)
                    if relation is not None and (type(target) is not int or target not in existing):
                        continue
                    if fields.keys() - {"content", "evidence", "sources", "subject_user_ids", "time_scope",
                                        "stability", "event_at", "recurrence", "usage_mode", "importance", "keywords"}:
                        continue
                    sources = [ServerSourceRef(str(s["message_id"]), s["role"], str(s.get("evidence", "")))
                               for s in fields.pop("sources")]
                    if not sources or not all(s.message_id in eligible and (
                        s.role == "user" or "reply" in eligible[s.message_id]) for s in sources):
                        continue
                    admin_ids = set()
                    if admin_verifier and fields.get("usage_mode") == "directive":
                        for ref in sources:
                            uid = eligible[ref.message_id]["user_id"]
                            if ref.role == "user" and await admin_verifier(guild, int(uid)):
                                admin_ids.add(uid)
                        if not eligible.keys() <= {r["message_id"] for r in batch(store, guild, channel)}:
                            return
                    store.add_server_memory(guild, sources=sources, relation=relation, relation_target=target, verified_admin_ids=frozenset(admin_ids), **fields)
                    accepted += 1
                except (ValueError, PermissionError, KeyError, TypeError):
                    continue
        with store.db:
            store.db.executemany("""UPDATE turns SET context_provenance=json_set(
                context_provenance,'$.server_memory_processed',1) WHERE realm=? AND message_id=?""",
                [(f"guild:{guild}", mid) for mid in ids])
        llm.usage.routing_event("memory.server_extraction", input_turns=len(turns), accepted=accepted)
    finally:
        running.remove(key)
