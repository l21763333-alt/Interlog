# Changelog

## 3.0.1 - 2026-07-21

- Canonicalize state containment checks across macOS `/var` aliases and Windows short paths without weakening symlink or junction rejection.
- Emit and consume hook and installer JSON as UTF-8 on Windows, including non-ASCII paths and report content.
- Treat descriptor permission changes as best-effort on Windows versions before Python 3.13, retain junction detection on older Python versions, and preserve the original error during temporary-file cleanup.
- Compare resolved path semantics in installer tests instead of platform-specific path spellings.

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
