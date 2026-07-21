# Changelog

## 3.0.0 - 2026-07-20

- Rename the project to Iterlog and define the development loop: change, optimize, verify, reflect, continue.
- Add native Marketplace catalogs backed by one canonical plugin source.
- Add a shared `$iterlog` Skill, Claude native read-only agent, and optional Codex `iterlog` custom agent.
- Capture opaque transcript evidence before automatic compaction with path, identity, size, and SHA-256 verification.
- Aggregate recent valid captures across sessions in the current project while deduplicating cumulative snapshots.
- Add explicit report date, local timezone, evidence coverage, confidence, verification, improvement, and reflection semantics.
- Add deterministic immutable nine-section Markdown development recaps stored outside business projects.
- Add cross-platform launchers, privacy controls, retention, credential redaction, tests, and CI.
- Keep the Python lifecycle manager only as an optional standalone Codex compatibility path; Marketplace installation requires no Python installer.
- Treat this release as a new plugin identity; legacy Daily Report state is preserved and never migrated automatically.
