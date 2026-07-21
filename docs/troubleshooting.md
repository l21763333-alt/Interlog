# Troubleshooting

## The Marketplace or plugin is not visible

Confirm the repository root contains both Marketplace catalogs, then use the command for the current host:

```text
Codex:       codex plugin marketplace add <repo-or-local-root>
Claude Code: /plugin marketplace add <repo-or-local-root>
```

Install `iterlog@iterlog-marketplace`, then open a new Codex task or run `/reload-plugins` in Claude Code.

For Claude Code validation:

```bash
claude plugin validate . --strict
claude plugin validate ./plugins/iterlog --strict
```

## The Skill appears but the runtime bridge is missing

The `SessionStart` Hook did not run or was disabled. Review Hooks, enable the plugin, and open a new task. Do not guess a plugin-cache path. In Codex, use `/hooks` to inspect and trust the current definitions.

## Python cannot be found

Python 3.10+ is a runtime dependency, although it is not used to install the Marketplace plugin. Check:

```bash
python3 --version
python --version
```

On Windows also try `py -3 --version`. If the interpreter has a different name, set `ITERLOG_PYTHON` to its absolute path before starting Codex or Claude Code. Claude Code also needs `node` on `PATH` for the bundled cross-platform launcher.

## The Claude agent is unavailable

Run `/reload-plugins`, then check `/agents` for `iterlog:iterlog`. Confirm `.claude-plugin/plugin.json` points to `./hooks/claude-hooks.json` and strict validation succeeds.

## Codex uses a general-purpose subagent

This is expected for Marketplace-only Codex because the plugin does not install `~/.codex/agents/*.toml`. The shared Skill sends the same reporting protocol and safe runtime context to exactly one general-purpose read-only worker.

The optional `scripts/manage.py` standalone mode installs the fixed `iterlog` custom agent. Do not enable it alongside Marketplace Hooks.

## The recap says coverage is partial or empty

Captures are created only before automatic compaction. Manual compact, short sessions, and sessions that never reach auto compact may have no snapshot. On-disk transcript content can also lag behind the current UI.

The Subagent may still use the evidence available in the current task, but it must identify missing history instead of assuming no work occurred. To inspect recent captures for the current project, use the exact context argv injected by `SessionStart`; it includes `context --all-sessions --limit 64` and the correct private state root.

## The recap date or timezone is wrong

The default is recalculated from the local runtime environment whenever reporting context is requested. Invoke the Skill with an explicit date and timezone, for example:

```text
$iterlog 复盘 2026-07-20 的开发修改，时区 Asia/Shanghai
```

The renderer rejects missing or invalid `report_date` values.

## Automatic compact is blocked

Capture is fail-closed by default. Inspect the host's `iterlog/events.jsonl`, free disk space, confirm the state root is an absolute path outside the project, and run:

```bash
python3 plugins/iterlog/tools/iterlog.py doctor
```

Set `ITERLOG_FAIL_OPEN=1` only when conversation availability is more important than preserving pre-compact evidence.

## Duplicate captures or Hooks

Do not combine Marketplace installation with `scripts/manage.py install`. Disable or uninstall one path, open a new task, and inspect Hooks again. Duplicate delivery within one Hook set is content-addressed and converges on one capture, but two installations still create unnecessary executions.

The same rule applies when migrating from the earlier `daily-report` plugin. It is a separate plugin identity and must be disabled before Iterlog is enabled. Legacy private state is intentionally left untouched and is not migrated automatically.

## Standalone upgrade reports a conflict

An installed agent, runtime, or Skill differs from the checksum in the ownership manifest. Back up manual changes and use `--force` only when replacing them is intentional. Unrelated Hooks remain preserved.
