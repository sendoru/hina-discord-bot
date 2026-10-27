# AGENTS.md

## Working conventions

- Do not commit or push directly to `main`.
- Do not merge a PR unless the user explicitly asks for the merge.
- For implementation work, create a feature branch unless the user explicitly says otherwise.
- When the user asks only for investigation or design, do not modify files, create branches, commits, pushes, issues, or PRs.
- Before proposing a fix, inspect the relevant existing implementation and tests first.
- Prefer small, focused changes over broad refactors unless the broader change is explicitly requested.

## Validation

- Run the most relevant focused tests after making changes.
- Run the broader test suite when the change has cross-cutting impact.
- Report which tests were run and whether they passed.
- Do not claim a CI result unless it was actually checked.

## Dashboard

- Treat the dashboard as an operational/debugging UI, not just a presentation layer.
- Preserve useful raw/internal identifiers when simplifying their display.
- Prefer reusable presentation helpers over duplicating parsing/formatting logic across Jinja templates.
- Avoid changing runtime semantics while doing dashboard-only UI work unless explicitly requested.

## Git / PR workflow

- Keep commits scoped and descriptive.
- Before opening a PR, summarize the changed files and validation performed.
- Do not close issues unless the implemented change actually resolves them.
- Do not merge dependent PRs out of order.

## Project docs

- Read relevant files under `docs/` before changing behavior documented there.
- Update documentation when user-visible or operational behavior changes.