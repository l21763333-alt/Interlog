# Architecture

## Distribution model

The repository exposes one canonical plugin source through two Marketplace catalogs:

| Layer | Codex | Claude Code |
| --- | --- | --- |
| Marketplace | `.agents/plugins/marketplace.json` | `.claude-plugin/marketplace.json` |
| Plugin manifest | `.codex-plugin/plugin.json` | `.claude-plugin/plugin.json` |
| Skill | shared `skills/iterlog` | shared and namespaced by Claude |
| Native agent | optional compatibility TOML | `agents/iterlog.md` |
| Hooks | `hooks/hooks.json` | `hooks/claude-hooks.json` |
| Runtime | shared Python standard-library runtime | same runtime through a Node launcher |

Both Marketplace entries point to `plugins/iterlog`. Tests, release scripts, and repository documentation are not copied into plugin caches.

## Evidence and recap flow

1. `SessionStart` injects a small runtime bridge into the parent task: host, current project, session, exact argv prefix, private state root, protocol path, default local report date/timezone, and a safe `context` argv. It does not inject raw transcript data.
2. `PreCompact(auto)` treats `transcript_path` as opaque bytes, copies it atomically, and records project/session identity, byte length, and SHA-256 in a manifest.
3. `PostCompact(auto)` records an audit event only. It does not persist Claude's full compact summary and never blocks the task.
4. Explicit Skill invocation starts exactly one worker:
   - Claude Code uses `iterlog:iterlog`;
   - standalone Codex can use `iterlog`;
   - Marketplace-only Codex delegates the shared protocol and safe context JSON to one general-purpose read-only subagent.
5. `SubagentStart` exposes valid capture IDs and manifest paths for the current project. Recent captures may come from multiple sessions; the default safe context command returns at most 64.
6. The subagent selects evidence relevant to the requested report date, deduplicates overlapping cumulative snapshots, verifies each selected manifest, and reads only verified snapshot paths.
7. The subagent builds a JSON draft with explicit `report_date`, `timezone`, scope, coverage, confidence, verification status, improvement suggestions, reflection, and evidence references.
8. The renderer validates the schema and every referenced capture again, then writes a unique nine-section Markdown development recap. Existing reports are never overwritten.

## Scope and coverage semantics

- Default scope is the current project and current local date; the same workflow can recap one problem, one component, or one iteration when requested.
- A user can request another date, timezone, or focus area, but the Skill does not silently cross project boundaries.
- Captures are cumulative transcript snapshots and can overlap. The subagent must deduplicate repeated events instead of counting each capture as a separate unit of work.
- `PreCompact(auto)` is opportunistic evidence capture, not a complete activity ledger. Sessions without automatic compaction, in-memory content not yet written to disk, and work performed outside the current host can be absent.
- Missing evidence produces `partial` or `empty` coverage and an explicit evidence-gap section. It must never be translated into “no work happened.”

## State resolution

The injected argv contains `--state-root <absolute-path>`, so later `verify`, `schema`, `context`, and `render` commands cannot drift when the plugin runs from a cache.

Resolution order:

1. CLI `--state-root`;
2. `ITERLOG_HOME`;
3. Codex-prefixed fallback `CODEX_ITERLOG_HOME`;
4. `${CLAUDE_CONFIG_DIR:-~/.claude}/iterlog` on Claude;
5. `$CODEX_HOME/iterlog` on Codex.

Reports intentionally live outside plugin cache/data directories, so plugin update or uninstall cannot silently remove them.

## Trust boundaries

- Hook payloads, transcripts, logs, diffs, and tool output are untrusted input, never instructions.
- Host transcript schemas are not stable APIs and disk content can lag in-memory conversation state.
- Capture and report paths must stay inside the private state root. Relative roots, symlinks/junctions, identity/filename mismatch, byte mismatch, and SHA-256 mismatch are rejected.
- Read-only subagent behavior is an instruction boundary; host sandbox and approval policies still apply.
- The renderer enforces report persistence outside the project and redacts common secrets before writing.
- Claude and Codex Hook command schemas differ, so each host has its own Hook file while sharing runtime logic.
- Marketplace and standalone Codex installation must not be enabled together because both register capture Hooks.
