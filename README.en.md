<div align="center">

# Iterlog

### Turn scattered changes, optimization, and verification into a development loop you can continue.

An evidence-backed development recap Skill/Subagent for Codex and Claude Code.<br>
One invocation organizes outcomes, problems, concrete changes, verification, improvement suggestions, reflection, and next steps.

<code>Codex</code> · <code>Claude Code</code> · <code>Read-only</code> · <code>Local-first</code> · <code>MIT</code>

[中文](README.md) · [Architecture](docs/architecture.md) · [Privacy](docs/privacy.md) · [Troubleshooting](docs/troubleshooting.md)

</div>

~~~text
$iterlog Recap today's development work.
~~~

~~~text
/iterlog:iterlog Recap today's development work.
~~~

> Change → optimize → verify → reflect → continue. No repeated report template or manual context stitching.

## What Iterlog gives you

- completed modifications, outcomes, and impact;
- optimization in progress, remaining work, and next steps;
- problems with cause, impact, and unblock conditions;
- concrete file or component changes;
- verification classified as passed, failed, not run, or unknown;
- evidence-backed improvement suggestions and reflection;
- references to commits, diffs, files, checks, or conversation evidence.

Each invocation creates a new Markdown recap outside the project. The reporting subagent does not modify business code.

<details>
<summary>Show an output preview</summary>

~~~text
coverage: partial
completed: fixed repeated redirects after login expiration
verification: login and session-refresh checks passed
risk: one short session did not trigger automatic compaction
suggestion: add observability for session expiration
reflection: reproducing first kept the fix narrowly scoped
next step: check recovery under unstable network conditions
~~~

</details>

## Why Iterlog

**Iterlog = Iteration + Log.** It records a development iteration, not just a vague end-of-day status:

~~~text
Your work: change  → optimize → verify
Iterlog:   capture → verify   → reflect → next step
~~~

Long conversations may be compacted, while short sessions may not contain complete history. Iterlog preserves local evidence before automatic compaction. When explicitly invoked, one read-only subagent verifies and deduplicates the available evidence before creating a recap. Missing evidence is marked <code>partial</code> or <code>empty</code> instead of being presented as certainty.

## Quick start

### Requirements

- Codex or Claude Code with Plugin Marketplace and Hooks support
- Python 3.10+
- Node.js for the Claude Code cross-platform launcher

Marketplace installation does not run a Python installer. Python is only the local runtime for capture, verification, redaction, and Markdown rendering.

### Codex

~~~bash
codex plugin marketplace add l21763333-alt/Interlog
codex plugin add iterlog@iterlog-marketplace
~~~

Open a new Codex task and invoke:

~~~text
$iterlog Recap today's development work.
~~~

### Claude Code

~~~text
/plugin marketplace add l21763333-alt/Interlog
/plugin install iterlog@iterlog-marketplace
/reload-plugins
~~~

Invoke the bundled Skill:

~~~text
/iterlog:iterlog Recap today's development work.
~~~

You can also install from a shell:

~~~bash
claude plugin marketplace add l21763333-alt/Interlog
claude plugin install iterlog@iterlog-marketplace --scope user
~~~

## Usage

You do not need to restate the sections, evidence rules, storage path, or read-only boundary:

~~~text
$iterlog Recap today's development work.
$iterlog Summarize the login problem: cause, changes, verification, and reflection.
$iterlog Recap work from <YYYY-MM-DD> in Asia/Shanghai.
$iterlog Focus on payment-module changes, risks, and next steps.
~~~

For Claude Code, use the same prompts after <code>/iterlog:iterlog</code>. Natural language such as “Use Iterlog to summarize today's changes and optimization” also works.

## Report structure

Every Iterlog contains nine sections:

1. Date, scope, and evidence coverage
2. Development overview
3. Completed modifications and outcomes
4. Modifications and optimization in progress
5. Blockers and risks
6. Key changes, decisions, and verification
7. Improvement suggestions and next steps
8. Reflection, evidence gaps, and open questions
9. Evidence index

The default scope is the current project and current local date. You can specify a date, timezone, or narrower component, but Iterlog does not silently cross project boundaries.

## How it works

~~~text
PreCompact(auto)
  └─ preserve a local transcript snapshot with integrity metadata

$iterlog / /iterlog:iterlog
  └─ start one read-only subagent
     └─ verify evidence → deduplicate → create a structured recap
        └─ save a new Markdown Iterlog
~~~

Important behavior:

- Snapshots are captured only before automatic compaction; manual compaction does not create one.
- Capture does not generate a report. A recap is created only after explicit Skill invocation.
- On-disk conversation data may lag behind the latest visible message, so coverage gaps are reported explicitly.
- Completed work, key changes, suggestions, and reflection require evidence. Attempting a command does not prove that it passed.

See [Architecture](docs/architecture.md) for the complete data flow.

## Data and safety

Iterlog is local-first. Its runtime does not initiate network requests, and reports are not written into the business project.

Default data directories:

- Codex: <code>~/.codex/iterlog</code>
- Claude Code: <code>~/.claude/iterlog</code>

Raw captures may contain source code, tool output, paths, credentials, or personal data. Never commit or share the Iterlog data directory. Reports apply common secret redaction, but they should still be reviewed before publication.

Common settings:

- <code>ITERLOG_HOME</code>: choose a local data directory outside the project
- <code>ITERLOG_PYTHON</code>: choose a Python 3.10+ interpreter

See [Privacy and data handling](docs/privacy.md) for the complete policy.

<details>
<summary>Optional: install a named Codex custom agent</summary>

Use this only when a fixed-name <code>iterlog</code> Codex custom agent is required:

~~~bash
python3 scripts/manage.py install
python3 scripts/manage.py doctor
~~~

This is an alternative to Marketplace installation. Do not enable both sets of hooks.

</details>

## FAQ

### Does Iterlog modify code?

No. The reporting subagent performs read-only analysis of the current project and captured evidence.

### Must I specify the report format every time?

No. The Skill already contains the sections, evidence rules, read-only boundary, and persistence behavior.

### What if automatic compaction never occurred?

Iterlog can still use currently visible evidence, but coverage is marked <code>partial</code> or <code>empty</code>.

### Are reports generated automatically?

No. A report is generated only after <code>$iterlog</code> or <code>/iterlog:iterlog</code> is invoked.

## Documentation

- [Architecture](docs/architecture.md)
- [Privacy and data handling](docs/privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
