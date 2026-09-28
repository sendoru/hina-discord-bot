# Versioning and compatibility policy

This project uses release versions to describe supported compatibility boundaries and build
revisions to identify the exact code that produced a runtime event.

## Sources of identity

`pyproject.toml` under `[project].version` is the single source of truth for the release
version. Release versions use plain `MAJOR.MINOR.PATCH` values such as `0.2.1`.

Structured telemetry has three separate identity fields:

- `app_version`: installed package release version from `pyproject.toml`
- `build_revision`: exact Git commit when available, otherwise a source fingerprint
- `runtime_id`: one process lifetime

Do not use `app_version` alone to compare two unreleased builds. Builds from the same release
line can share an app version while having different `build_revision` values.

## Release tags

Release tags use `vX.Y.Z` and must exactly match `[project].version`.

The CI version check runs for normal branches and pull requests to validate the version format.
On a tag-triggered run it additionally rejects a tag that does not match the package version.

Example:

```text
project.version = "0.2.0"
valid tag        = v0.2.0
invalid tag      = v0.1.9
```

## Version bump policy before 1.0

While the project remains in the `0.x` series, use the following operational convention:

- **PATCH**: bug fixes, internal refactors, prompt/quality tuning, observability changes, and
  other changes that preserve documented operational compatibility.
- **MINOR**: new user/admin-visible features or an intentional compatibility boundary involving
  documented configuration, commands, integrations, or migration requirements.
- **MAJOR**: reserved for a future stable compatibility contract.

SemVer permits `0.x` releases to change quickly, but this project still documents intentional
breaking changes rather than treating pre-1.0 as permission for silent breakage.

## Deprecation and legacy removal

Classify a compatibility path before removing it.

### Unreleased or internal-only code

Code that was never released, is unreachable, or is strictly an internal implementation detail
may be removed without a compatibility grace period. Normal tests and review still apply.

### Released operational interfaces

Documented environment variables, config names, CLI/admin commands, and equivalent operational
interfaces should normally follow this lifecycle:

1. mark the old surface deprecated and document the replacement;
2. keep the compatibility path through at least the next minor release boundary;
3. remove it in a later minor release;
4. record the removal and any required operator action in the changelog.

A compatibility shim can be retained longer when real deployments still need it. Security fixes
or clearly unsafe behavior may require a shorter lifecycle and should be documented explicitly.

### Persistent data and database migrations

Persistent data is stricter than a config alias. A released database migration or data upgrade
path must not be deleted merely because one minor release has passed.

Before removing old migration compatibility, establish that supported deployments can still
upgrade from the oldest supported persisted state, or provide an explicit intermediate migration
path. Treat this separately from source-level dead-code cleanup.

## Release procedure

For a manual release:

1. choose the intended PATCH or MINOR bump;
2. update `[project].version` in `pyproject.toml`;
3. move relevant `CHANGELOG.md` entries from **Unreleased** into the release section;
4. merge the release state to `main`;
5. tag that exact commit as `vX.Y.Z`;
6. let CI verify the tag/package-version match.

Automatic publishing and automatic version bumping are intentionally out of scope for now.

## Relationship to legacy cleanup

Use this policy as the boundary for legacy cleanup:

- remove unreleased/dead/internal compatibility code as soon as tests show it is safe;
- label released shims with the version or release boundary that allows removal;
- keep persistent-data migrations until the supported upgrade path is explicitly narrowed.

This makes versioning a prerequisite for systematic cleanup rather than a separate cosmetic
release process.
