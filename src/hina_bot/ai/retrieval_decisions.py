"""Turn context, lore packing and content-free metrics for Decisions retrieval."""

import asyncio
import re
from time import perf_counter

from hina_bot.core.compact_lore import render_compact_lore
from hina_bot.core.config import DECISION_MODES
from hina_bot.core.memory_annotations import normalize_text
from hina_bot.core.memory_selection import SelectedMemory, decision_memory_candidates

from .decisions import MIN_PROBABILITY, DecisionResult
from .discord_emoji_input import normalize_custom_emoji_text
from .egress_policy import filter_channel_context
from .structured_memory_context import (
    REPLY_CHAIN_KINDS,
    personal_memory_fields,
    personal_memory_selection,
    recent_own_texts,
    structured_memory_context,
)

_DISCORD_ID = re.compile(r"<(?:@!?|@&|#)\d+>|\b\d{17,20}\b")
_REPLY_CHARS = 1800
_REPLY_ROWS = 4


def decision_query(
    message, rows, *, user_id, policy, recent_own=(), owner_only=False, now="",
) -> dict:
    def clean(text):
        return _DISCORD_ID.sub("[Discord ID]", normalize_custom_emoji_text(str(text))).strip()

    reply = []
    same_speaker = []
    budget = _REPLY_CHARS
    for row in reversed(filter_channel_context(rows, user_id, policy)):
        kind = row.get("context_kind")
        role = row.get("role")
        own = str(row.get("author_user_id") or row.get("user_id") or "") == str(user_id)
        if kind == "speaker_thread" and role == "user" and own:
            text = clean(row.get("content") or "")[:500]
            if text and len(same_speaker) < 2:
                same_speaker.append(text)
        if kind not in REPLY_CHAIN_KINDS or len(reply) >= _REPLY_ROWS or not budget:
            continue
        if role == "assistant":
            # A reply derived from a lookup can repeat private or third-party context.
            reply_target = str(row.get("reply_target_user_id") or "")
            if row.get("provenance_class") == "reference_derived" or (
                owner_only and reply_target and reply_target != str(user_id)
            ):
                continue
            speaker = "assistant"
        elif role == "user" and (own or not owner_only):
            speaker = "current_user" if own else "other_speaker"
        else:
            continue
        text = clean(row.get("content") or "")[:min(800, budget)]
        if text:
            reply.append({
                "speaker": speaker, "kind": kind, "text": text,
                "at": str(row.get("at") or "")[:32],
            })
            budget -= len(text)
    own_text = [clean(text)[:500] for text in recent_own[-2:]]
    return {
        "current_message": clean(message)[:4000],
        "explicit_reply": list(reversed(reply)),
        "recent_own": list(dict.fromkeys([*reversed(same_speaker), *own_text]))[-2:],
        "current_time": now,
    }


def lore_candidate_data(candidate) -> dict:
    return {
        "content": candidate.content,
        "kind": candidate.fact_type,
        "subjects": candidate.subjects,
        "awareness": candidate.awareness,
        "timeline": candidate.time,
        "certainty": candidate.confidence,
        "usage": candidate.retrieval_usages,
    }


def memory_candidate_data(entry: SelectedMemory) -> dict:
    return {
        "content": _DISCORD_ID.sub("[Discord ID]", entry.item.content),
        "kind": entry.item.kind.value,
        "state": entry.status.status.value,
        "date": entry.status.date or entry.status.noted,
        "usage": entry.annotation.usage_mode.value,
    }


def eligible_scores(result: DecisionResult) -> dict[str, float]:
    return {
        item_id: score for item_id, score in (result.scores or {}).items()
        if score >= MIN_PROBABILITY
    }


def pack_decision_lore(candidates, rows, result, *, max_items, max_chars):
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    ranked = sorted(eligible_scores(result).items(), key=lambda entry: (-entry[1], entry[0]))
    def supported_ids(item_id, chain=frozenset()):
        # A reviewed inference with explicit dependencies travels with those sources.
        if item_id in chain or item_id not in by_id:
            return None
        candidate = by_id[item_id]
        support = [item_id]
        if candidate.fact_type == "inference":
            for dependency in candidate.evidence_ids:
                ids = supported_ids(dependency, chain | {item_id})
                if ids is None:
                    return None
                support.extend(ids)
        return tuple(dict.fromkeys(support))

    packed = {}
    seen = set()
    for item_id, _ in ranked:
        if len(packed) >= max_items:
            break
        group = supported_ids(item_id)
        if group is None:
            continue
        additions = {}
        keys = set()
        for candidate_id in group:
            candidate = by_id[candidate_id]
            key = (candidate.lane, candidate.awareness, candidate.time,
                   normalize_text(candidate.content))
            if key not in seen and key not in keys:
                additions[candidate_id] = rows[candidate_id]
                keys.add(key)
        texts = [*packed.values(), *additions.values()]
        if len(texts) > max_items or render_compact_lore(texts).chars > max_chars:
            continue
        packed.update(additions)
        seen.update(keys)
    return render_compact_lore(list(packed.values())), list(packed)


def decision_observation(
    result, *, mode, applied, selected_ids=(), selected_chars=0, baseline_chars=0,
    fallback="", elapsed_ms=0,
) -> dict:
    if fallback:
        status = "fallback"
    elif not result.reason:
        status = "completed"
    else:
        status = "skipped" if result.request_ms is None else "error"
    return {
        **result.metrics(),
        "decision_mode": mode,
        "decision_applied": applied,
        "decision_selected": len(selected_ids),
        "decision_selected_chars": selected_chars,
        "decision_baseline_chars": baseline_chars,
        "decision_fallback": fallback,
        "decision_elapsed_ms": elapsed_ms,
        "status": status,
    }


async def select_with_decisions(llm, store, scope, rows, memory_kwargs, *, lore_mode):
    """Evaluate the two eligible pools in parallel before assembling the answer."""
    memory_mode = memory_kwargs["read_mode"]
    message = memory_kwargs["message"]
    started = perf_counter()
    policy = llm.settings.external_context_policy
    key = llm.settings.api_key_for("gemini")
    evaluate = llm.decisions_client.evaluate
    use_memory = memory_kwargs["use_memory"]
    mode_reader = getattr(store, "memory_mode", None)
    now = memory_kwargs["now"]
    include_community = llm.settings.community_lore

    def memory_snapshot():
        return personal_memory_selection(
            store, scope, message=message, settings=llm.settings, now=now,
            authorized_factual_items=memory_kwargs["authorized_factual_items"],
            explicit_call=memory_kwargs["explicit_call"], select_tiered=False,
        )

    lore_candidates = []
    memory_run = None
    requests = {}
    results = {}
    if lore_mode in DECISION_MODES:
        lore_candidates = [
            candidate for candidate in llm.static_lore_candidates
            if candidate.candidate_id in llm.static_lore_rows
            and (include_community or candidate.lane != "community_meme")
        ]
        requests["lore"] = evaluate(
            api_key=key, domain="lore",
            query=decision_query(
                message, rows, user_id=scope.user_id, policy=policy,
                recent_own=recent_own_texts(store, scope, now) if use_memory else (),
                now=now.isoformat(timespec="minutes"),
            ),
            candidates={c.candidate_id: lore_candidate_data(c) for c in lore_candidates},
        )
    if memory_mode in DECISION_MODES:
        memory_run = memory_snapshot() if use_memory else None
        if memory_run is None:
            results["memory"] = DecisionResult(reason="memory_disabled")
        else:
            memory_candidates = decision_memory_candidates(
                memory_run.items, memory_run.annotations, memory_run.ctx,
            )
            requests["memory"] = evaluate(
                api_key=key, domain="memory",
                query=decision_query(
                    message, rows, user_id=scope.user_id, policy=policy,
                    recent_own=memory_run.recent_own_texts, owner_only=True,
                    now=now.isoformat(timespec="minutes"),
                ),
                candidates={str(c.item.id): memory_candidate_data(c) for c in memory_candidates},
            )

    if requests:
        results.update(zip(requests, await asyncio.gather(*requests.values()), strict=True))

    # Permissions or memory revisions can change while the network requests are in flight.
    # Refresh the snapshot and discard scores if their candidates or policy changed.
    invalidated = ""
    if use_memory and callable(mode_reader) and mode_reader(scope) in {"off", "write_only"}:
        memory_run = None
        invalidated = "memory_disabled"
        memory_kwargs = {
            **memory_kwargs, "use_memory": False, "authorized_factual_items": (),
        }
    elif memory_run is not None:
        refreshed = memory_snapshot()
        if refreshed is None or (
            memory_run.items != refreshed.items
            or memory_run.annotations != refreshed.annotations
            or memory_run.ctx != refreshed.ctx
            or policy != llm.settings.external_context_policy
        ):
            invalidated = "memory_context_changed"
            # Do not carry a T4 approval across edits to its source item.
            memory_kwargs = {
                **memory_kwargs,
                "authorized_factual_items": tuple(
                    item for item in memory_kwargs["authorized_factual_items"]
                    if refreshed is not None and item in refreshed.items
                ),
            }
            refreshed = memory_snapshot() if refreshed is not None else None
        memory_run = refreshed

    memory_result = results.get("memory")
    memory_scores = (
        eligible_scores(memory_result)
        if memory_result is not None and memory_result.scores is not None and not invalidated
        else None
    )
    personal = personal_memory_fields(
        store, scope, **memory_kwargs, decision_run=memory_run, decision_scores=memory_scores,
    )

    observations = {}
    compact = None
    if (result := results.get("lore")) is not None:
        baseline = (
            llm.compact_lore_with_community if llm.settings.community_lore
            else llm.compact_lore_without_community
        )
        fallback = ""
        ids = []
        selected = None
        if (
            policy != llm.settings.external_context_policy
            or include_community != llm.settings.community_lore
        ):
            fallback = "lore_context_changed"
        elif result.scores is not None:
            selected, ids = pack_decision_lore(
                lore_candidates, llm.static_lore_rows, result,
                max_items=llm.settings.lore_max_items, max_chars=llm.settings.lore_max_chars,
            )
            if lore_mode == "decision":
                compact = selected
        if lore_mode == "decision" and compact is None:
            fallback = fallback or "full"
        observations["lore"] = decision_observation(
            result, mode=lore_mode, applied=compact is not None,
            selected_ids=ids, selected_chars=selected.chars if selected else 0,
            baseline_chars=baseline.chars, fallback=fallback,
            elapsed_ms=round((perf_counter() - started) * 1000),
        ), ids
        llm.usage.routing_event(
            "rag.mode", rag_mode=lore_mode,
            rag_selected_context="decision" if compact is not None else "full",
        )
    if memory_result is not None:
        selected_notes = [
            entry for entry in personal["selection"].selected if entry.via == "decision"
        ] if personal["selection"] else []
        ids = [entry.item.id for entry in selected_notes]
        baseline_fields = (
            structured_memory_context(
                store, scope, use_memory=memory_kwargs["use_memory"],
                allow_cross_space=memory_kwargs["allow_cross_space"],
                authorized_factual_items=memory_kwargs["authorized_factual_items"],
            )
            if memory_mode == "decision" and memory_run is not None
            else personal["fields"]
        )
        fallback = invalidated
        if memory_mode == "decision" and memory_run is not None and memory_scores is None:
            fallback = fallback or "scope_safe_legacy"
        observations["memory"] = decision_observation(
            memory_result, mode=memory_mode, applied=memory_mode == "decision"
            and memory_scores is not None, selected_ids=ids,
            selected_chars=sum(len(entry.item.content) for entry in selected_notes),
            baseline_chars=sum(
                len(entry["content"])
                for field in ("structured_owner_memory", "structured_full_memory")
                for entry in baseline_fields.get(field, [])
            ),
            fallback=fallback, elapsed_ms=round((perf_counter() - started) * 1000),
        ), ids
    for domain, (metrics, _) in observations.items():
        llm.usage.record_decision(domain, metrics)
    return personal, compact, {
        domain: {**metrics, "selected_ids": ids}
        for domain, (metrics, ids) in observations.items()
    }, memory_kwargs["use_memory"], memory_kwargs["authorized_factual_items"]
