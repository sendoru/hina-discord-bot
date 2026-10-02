# Changelog

Notable user-facing, operator-facing, compatibility, and migration changes are tracked here.

## Unreleased

### Added

- 현재 Discord 멤버 기반 사용자 식별 기능을 추가했습니다. 로컬 정확 일치, 제한된 시맨틱
  폴백, 기억/기록 조회와 독립된 식별 문맥을 사용합니다. 봇은 Server Members Intent를 항상 요청하며,
  기존의 엄격한 외부 전송 제한은 그대로 유지됩니다.
- Configurable guild channels that treat every human message as a direct bot turn, while keeping bot-authored messages behind explicit triggers.
- Formal release-version and compatibility lifecycle policy.
- CI validation for package version format and release-tag consistency.

### Changed

- 식별된 사용자를 호출·핑하거나 현재 채널의 최근 발언을 조회할 때 더 이상 해당 사용자의
  채널 간 공개 기억을 자동으로 요청하지 않습니다. 과거 별칭은 출처 채널별로 필터링합니다.
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
