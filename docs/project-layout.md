# Project layout

Python sources are grouped by responsibility instead of being kept directly under `src/hina_bot`.

```text
src/hina_bot/
├── ai/          # LLM orchestration, provider adapters, RP policy, web/vision request wrapping
├── core/        # configuration, persistence, routing, memory/knowledge primitives
├── discord/     # Discord client, slash commands, recent context, visual input collection
├── tooling/     # lore/evaluation CLI and offline pipelines
├── data/        # packaged lore data
└── prompts/     # packaged character/relationship prompts

tests/
├── ai/          # LLM/provider/search/vision/output-policy tests
├── core/        # state, lore, storage, migration tests
├── discord/     # Discord command/runtime/visual-input integration tests
└── tooling/     # CLI/evaluation pipeline tests
```

Vision-related responsibilities are intentionally split across two layers.

- `src/hina_bot/discord/vision.py`: collect bounded visual inputs from the **current Discord message**
  (attachments, custom emoji, raster stickers).
- `src/hina_bot/ai/vision.py`: represent provider-neutral `VisualInput`, attach current-turn images to the
  answer request, and add the vision-specific trust boundary.
- `src/hina_bot/ai/providers.py`: translate the common image blocks into provider-specific request formats.

Vision is not a new information-routing source. `information_routing` still decides whether a question needs
memory, clock, local lore, web, or general reasoning; visual inputs are an orthogonal modality that can be used
at the same time. See [`vision-input.md`](vision-input.md) for the current scope and limits.

The console entry points use the new package paths. A small compatibility layer in `hina_bot.__init__`
keeps the previous flat imports (for example `hina_bot.config`) working for existing local scripts/tests;
new code should prefer the feature package paths such as `hina_bot.core.config`.
