# Changelog

Notable user-facing, operator-facing, compatibility, and migration changes are tracked here.

## Unreleased

### Added

- 대화 모델이 필요할 때만 현재 Discord 채널의 사용자 명단을 로컬 function tool로 조회할 수
  있습니다. 도구는 현재 채널을 볼 수 있는 사람의 server nickname, global name, username, user ID만
  반환하며, 봇은 이를 위해 Server Members Intent를 항상 요청합니다.
- Configurable guild channels that treat every human message as a direct bot turn, while keeping bot-authored messages behind explicit triggers.
- Formal release-version and compatibility lifecycle policy.
- CI validation for package version format and release-tag consistency.

### Changed

- 사용자 이름 해석은 별도 preflight resolver 대신 일반 답변의 `get_current_channel_members`
  도구 호출로 처리합니다. 사용자 명단은 매 요청에 선제적으로 포함하지 않고 모델이 실제로 요청한
  턴에만 provider로 전달됩니다.
- Clarified the distinct roles of `app_version`, `build_revision`, and `runtime_id`.

### Removed

- 실효성이 낮았던 exact/fuzzy/semantic identity resolver, historical alias fallback, `resolved_identities`
  context와 전용 Identity dashboard를 제거했습니다.

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
