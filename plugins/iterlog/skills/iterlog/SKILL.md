---
name: iterlog
description: Generate and persist an evidence-backed Markdown development recap for the current project. It covers completed modifications, ongoing optimization, problems and blockers, causes, concrete changes, decisions, verification, improvement suggestions, reflection, evidence gaps, and next steps for today or a specified date. Use when the user invokes $iterlog or /iterlog:iterlog, or asks for 日报、开发日志、开发复盘、修改总结、优化总结、变更记录、验证总结、今日进展、下班总结、明日计划、daily dev log, engineering recap, or change summary. Do not trigger merely because the day is ending, and do not implement project changes.
---

# Iterlog

Create one concise development recap without making the user repeat the format, evidence rules, or storage path.

Before delegating, read [references/subagent-protocol.md](references/subagent-protocol.md) completely. Treat it as the authoritative reporting protocol.

## Invocation and scope

- Codex: `$iterlog [optional date or scope]`
- Claude Code: `/iterlog:iterlog [optional date or scope]`
- A clear natural-language request for a development recap, daily work summary, change summary, or optimization review may also trigger the skill.

Default to the current project's current local date. Honor an explicit `YYYY-MM-DD`, timezone, or narrower scope. Do not claim coverage of other projects. Automatic `PreCompact(auto)` capture preserves evidence but does not generate a report; short sessions without automatic compaction may be absent and must be recorded as an evidence gap.

## Delegate exactly once

Choose the first available route:

1. In Claude Code, spawn the plugin agent `iterlog:iterlog`.
2. In Codex, if the optional compatibility agent exists, spawn `iterlog`.
3. In marketplace-only Codex, spawn exactly one general-purpose read-only subagent. First run the `iterlog context argv` injected by the Iterlog `SessionStart` hook. Give the subagent:
   - the complete protocol from the reference file;
   - the requested date, timezone, and current-project scope;
   - relevant current-task evidence;
   - the complete JSON emitted by the context command, unchanged;
   - an instruction not to modify project files and not to spawn another Iterlog agent.

Never spawn multiple recap agents, recurse, or create a competing report in the parent task. The reporter may inspect existing Git state, diffs, logs, files, and recorded test results read-only; it must not run costly or state-changing verification unless the user separately authorizes it.

If the marketplace-only route has no injected runtime bridge, explain that plugin hooks are not active, ask the user to review hooks and open a new task, and do not guess an interpreter or cache path. Normal marketplace installation never requires the Python installer.

## Return

Wait for the subagent. Return the persisted recap's absolute path, report date, evidence-coverage status, blocker summary, and any persistence warning. Do not silently upgrade unverified work to completed work.
