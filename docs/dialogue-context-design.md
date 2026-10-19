# Speaker-focused Discord context and tone-drift mitigation

## Problem

A long exchange between Hina and user A can dominate the context of a new
message from user B. Even when IDs and reply targets are accurate, the model
sometimes attributes A's prior acts or stated circumstances to B, and carries
Hina's irritation across the speaker boundary. Repeated assistant text also
anchors subsequent answers in the same expressions and emotional intensity.

## Design principles

- **Current message first.** The present author and their actual utterance
  determine the response. Channel recency is not proof of personal participation.
- **Preserve causality.** An explicit reply chain retains priority and its
  original participant/source metadata. Same-speaker conversation history
  remains available for follow-ups.
- **Differentiate ownership from awareness.** Other users' exchanges can be
  useful as a *scene* but are not evidence of the current user's behavior,
  intent, presence throughout the scene, or emotional relationship with Hina.
- **Avoid a new classifier.** Use existing provenance, speaker bindings and
  reply-target metadata rather than text keywords or another model call.
- **Respect existing boundaries.** Apply the projection only after the existing
  egress policy; never alter raw Discord buffers, DB history or memory writes.

## Provider projection

The current answer-request builder already resolves `current_speaker`,
`current_interaction` and `active_reply_chain`, and approves allowed data at
the egress boundary. Right before forming the provider's reference blocks,
`project_dialogue_context` replaces the flat `channel_recent_messages` with:

| Field | Meaning |
| --- | --- |
| `speaker_recent_messages` | Current user's own turns and Hina replies demonstrably directed at them (`speaker_thread` or bound speaker/target flags). |
| `channel_ambient_messages` | Other participants' channel turns and Hina's replies to them; a scene reference only. Unknown-target assistant messages go here. |
| `target_user_history` | Explicitly sampled history of a requested target, never silently reclassified as current user's history. |
| `conversation_focus` | Whether current speaker has any direct recent-channel history and whether there is a causal reply chain. |

The original `active_reply_chain`, `personal_recent_conversation`,
structured memories, external-context policy and retrieval behavior are
unchanged. The original accepted rows remain available for provenance and
audit, while `context.dialogue_projection` reports how many ambient rows
reached the final provider request.

A message from a user with **no** direct recent speaker thread **and** no
explicit reply anchor gets at most eight most-recent ambient rows. This bounds
a preceding conversation's influence without blanking out the scene; e.g., B
can still react to A's conversation. If a direct same-speaker thread or an
explicit reply is present, the prior context budget applies unchanged.

## Tone mitigation

The character and output guidelines distinguish prior Hina messages as
historical events rather than templates for voice or emotional intensity.
Irritation in one reply must not automatically intensify in following replies.
React afresh to topic changes, kindness and changes of speaker rather than
repeating the same first phrase or increasingly adversarial refusal.

No banned-word list, random tone switch, additional LLM call, per-user anger
counter or new runtime configuration is introduced.

## Verification and limitations

Deterministic regression tests assert speaker attribution, new-speaker
ambient limiting, explicit-reply preservation, requested-target isolation,
unchanged original data and same-speaker context retention. Multi-turn
roleplay evaluations should use repeated samples to measure irritation,
verbal repetition, third-party misattribution and unsupported assertions that
new participants were observing earlier.

A deterministic projection cannot *guarantee* the model obeys instructions.
The first rollout should inspect the routing trace and compare response
samples before/after. If summarization requests concerning extensive ambient
history are under-supported, refine that use case separately rather than
restoring full ambient history for ordinary new-speaker replies.
