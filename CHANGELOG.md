# Changelog

Notable user-facing, operator-facing, compatibility, and migration changes are tracked here.

## Unreleased

### Added

- Opt-in current Discord member identity resolution with local exact matching, bounded semantic
  fallback and identity context independent of memory/history. Enable Server Members Intent and
  `DISCORD_MEMBERS_INTENT=true` for a maintained directory; existing strict egress limits remain.
- Configurable guild channels that treat every human message as a direct bot turn, while keeping bot-authored messages behind explicit triggers.
- Formal release-version and compatibility lifecycle policy.
- CI validation for package version format and release-tag consistency.

### Changed

- Calling/pinging resolved users and querying their recent channel speech no longer automatically
  requests their cross-channel public memory. Historical aliases are filtered per source channel.
- Clarified the distinct roles of `app_version`, `build_revision`, and `runtime_id`.

### Removed

- Obsolete Discord-client implicit LLM construction and standalone module entrypoints; `hina-bot`
  now has a single composition root in `discord.runtime_entry`.

- Unreleased internal compatibility wrappers for web-search routing and an ignored structured-memory
  policy argument. Runtime deployment compatibility and persistent-data migration paths are unchanged.
- The internal `DashboardService` compatibility facade; dashboard routes now receive domain-specific
  read services directly.
- Shadowed legacy `answer`, `summarize`, and `summarize_shared` implementations from the base LLM;
  current request assembly and memory-summary layers remain the active implementations.
- The base LLM's OpenAI-only implicit client fallback; provider-aware composition now owns client creation.

## 0.1.0

- Existing package-version baseline from before formal release tracking.
- Earlier project history is intentionally not reconstructed release-by-release.
