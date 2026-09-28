# Changelog

Notable user-facing, operator-facing, compatibility, and migration changes are tracked here.

## Unreleased

### Added

- Formal release-version and compatibility lifecycle policy.
- CI validation for package version format and release-tag consistency.

### Changed

- Clarified the distinct roles of `app_version`, `build_revision`, and `runtime_id`.

### Removed

- Unreleased internal compatibility wrappers for web-search routing and an ignored structured-memory
  policy argument. Runtime deployment compatibility and persistent-data migration paths are unchanged.
- The internal `DashboardService` compatibility facade; dashboard routes now receive domain-specific
  read services directly.
- Shadowed legacy `answer`, `summarize`, and `summarize_shared` implementations from the base LLM;
  current request assembly and memory-summary layers remain the active implementations.

## 0.1.0

- Existing package-version baseline from before formal release tracking.
- Earlier project history is intentionally not reconstructed release-by-release.
