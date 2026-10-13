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
