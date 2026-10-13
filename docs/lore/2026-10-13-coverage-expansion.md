# Lore coverage expansion — 2026-10-13

This change addresses gaps in `src/hina_bot/data/lore.jsonl` without changing the retrieval implementation or the runtime prompt. It preserves the existing schema, accepted status, canonical lane and KR release boundary.

## Evidence and rows
| Lore ID | Basis | Notes |
|---|---|---|
| `canon.iori.prefect_team.field_role` | [Official anime Iori profile](https://sh-anime.shochiku.co.jp/bluearchive-anime/character/shiromi-iori/) | Official adaptation profile; 2nd-year and field leadership; do not infer intimacy with Hina |
| `canon.chinatsu.prefect_team.medic_role` | [Official anime Chinatsu profile](https://sh-anime.shochiku.co.jp/bluearchive-anime/character/hinomiya-chinatsu/) | Official adaptation profile; 1st-year and emergency aid |
| `canon.ako.role_in_gehenna_and_relationship_with_hina` (edited) | [Official anime Ako profile](https://sh-anime.shochiku.co.jp/bluearchive-anime/character/amau-ako/) | Enrich existing record instead of duplicating her role |
| `canon.vol1.hina.stops_ako_abydos_incursion` | [Hina story index](https://bluearchive.fandom.com/wiki/Sorasaki_Hina/Story) and [Foreclosure Task Force story](https://bluearchive.fandom.com/wiki/The_Foreclosure_Task_Force/Story) | Secondary fan-edited episode summaries; original game line-by-line verification still desired |
| `canon.eden.hina.treaty_peace_view` | [Hina story index](https://bluearchive.fandom.com/wiki/Sorasaki_Hina/Story); [Japanese independent summary](https://w.atwiki.jp/aniwotawiki/pages/55620.html) | Character-reported position, not a universal political fact |
| `canon.eden.hina.considered_retirement` | [Treaty of Eden story](https://bluearchive.fandom.com/wiki/Treaty_of_Eden/Story); [Japanese independent summary](https://w.atwiki.jp/aniwotawiki/pages/55620.html) | Distinguishes contemplated retirement from an actual resignation |
| `canon.summer.prefect_team.ako_planned_hina_vacation` | [Nexon KR event announcement](https://forum.nexon.com/bluearchive/board_view?board=1053&thread=2048502) | `audience_only`: the player's view of Ako's plan is not automatically Hina's in-character memory |
| `canon.serenade.hina.field_exploration_piano_preparation` | [Nexon Global event notes](https://forum.nexon.com/bluearchive-en/board_view?board=3217&thread=2886939), [Nexon Games announcement](https://art.natgames.co.kr/bbs/board.php?bo_table=media_event&page=13&wr_id=307) | Event arc context; keep existing detailed piano facts |

## Editorial boundaries
- Do not claim Hina has a private/familial/romantic relationship with members of the Prefect Team on organizational evidence alone.
- Distinguish reported statements and story-time wishes from present-state facts.
- Do not treat fan story summaries as firsthand official game transcripts. Vol.1 and Eden entries are `crosschecked` secondary summaries requiring an optional later line-level game-script audit.
- Existing `fact_type=unknown` lore was deliberately preserved.
- New records omit `entities` and structured `claims` until canonical IDs are added to `EntityResolver`; do not invent unresolved IDs.

## Regression checks
- `uv run pytest tests/core/test_lore.py tests/tooling/test_retrieval_calibration.py`
- `uv run python -m hina_bot.tooling.retrieval_calibration --validate-only`
- Corpus size tests should derive their expected length from the actual packaged lore, not pin an obsolete count.

## Follow-up expansion after review feedback (2026-10-13)

The initial seven new entries left major gaps. This follow-up adds **20 more substantial records**; total corpus is now **157**.

| Focus | Added lore IDs | Source type |
| --- | --- | --- |
| Gehenna / Pandemonium | `canon.gehenna.makoto.character_and_work_style`, `canon.gehenna.iroha.pandemonium_role`, `canon.gehenna.ibuki.pandemonium_role`, `canon.gehenna.makoto.iroha_ibuki_contrast`, `canon.gehenna.makoto_vs_hina.pandemonium_institutional_conflict` | Game-profile mirrors and community substory summaries |
| Prefect Team practice | `canon.gehenna.ako_iori.work_dynamics`, `canon.gehenna.prefect_team.chinatsu_previous_emergency_experience` | Relationship summaries and extracted game equipment descriptions |
| Food service / Gourmet Society | `canon.gehenna.fuuka.school_lunch_profile`, `canon.gehenna.juri.school_lunch_profile`, `canon.gehenna.haruna.gourmet_leader`, `canon.gehenna.gourmet_members_roster` | In-game descriptions / club roster mirrors |
| Hot spring / Emergency medicine | `canon.gehenna.kasumi.hotspring_leader`, `canon.gehenna.megu.hotspring_member`, `canon.gehenna.sena.emergency_medical_leader` | Game-profile mirrors |
| Problem Solver 68 | `canon.gehenna.aru.problem_solver_leader`, `canon.gehenna.problem_solver_68.members`, `canon.gehenna.kayoko.problem_solver_moderator` | Game profiles / club descriptions |
| Eden Treaty | `canon.eden.hina.protects_sensei_after_cathedral_blast`, `canon.eden.hina_sensei.sena_evacuation`, `canon.eden.hina.confides_exhaustion_and_returns_to_battle` | Fan-edited episode recaps; see caveat below |

**Evidence boundaries:** Each new record's `source.url` links to its specific profile or story. Fan-maintained event and story summaries are *not* official line-by-line transcripts. In particular, the new Eden scene-level records should be checked against game dialogue when performing a later high-rigor source pass. Game-profile mirrors paraphrase original in-game descriptions; `crosschecked` is the schema's existing accepted-level value, not evidence of an independently completed transcript audit.

**Knowledge limits:** Most character-profile facts are labeled `public_knowledge`. The Ako–Iori private office dynamic is labeled `audience_only`, preventing it from being promoted into Hina's firsthand memories. The source of truth for server-specific personal relationships remains scoped Discord memory; membership or conflicts between organizations are not evidence of romantic or intimate relations.

**KR timeline caution:** Only content released before 2026-10-13 on the Korean server should be retained as `kr_release=confirmed`; this dataset is based on well-established early Gehenna / Eden content rather than JP-only future arcs.

**Further work:** First-party story-script source pointers and full dialogue evidence would improve provenance of the Eden and group-story entries. Do not use the number of added records as a proxy for sufficient coverage; next evaluate real questions involving Makoto, Ibuki, Iroha, Fuuka, Haruna, Ako and Hina against the new compact/full-context mode.
