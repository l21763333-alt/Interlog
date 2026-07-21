# Privacy and data handling

## Data collected

On `PreCompact(trigger=auto)`, the runtime copies the host transcript currently present on disk as opaque bytes. It can contain prompts, source code, commands, tool output, paths, credentials, and personal information. A manifest stores operational metadata, the original path, byte count, and SHA-256.

The Iterlog runtime makes no network requests. Fetching or updating a Marketplace repository is performed by Codex or Claude Code.

## Storage

Defaults are outside project repositories:

- Codex: `$CODEX_HOME/iterlog`
- Claude Code: `${CLAUDE_CONFIG_DIR:-~/.claude}/iterlog`

Each store contains:

- `captures/`: raw private evidence and manifests
- `reports/`: rendered development recaps grouped by report date
- `events.jsonl`: capture and compact audit events

Set `ITERLOG_HOME` only to an absolute path outside the current project. Separate operating-system users have separate default stores. Accounts sharing one OS profile and host configuration directory also share that store.

POSIX permissions are tightened to `0700` for directories and `0600` for files when supported. Windows `chmod` does not replace ACL review; protect the user profile and avoid shared directories.

## Retention and deletion

Raw captures default to 30 days and are cleaned after a later successful capture. `ITERLOG_RETENTION_DAYS=0` disables cleanup. Reports are retained indefinitely.

Marketplace uninstall does not remove the external Iterlog directory. Standalone `scripts/manage.py uninstall` also preserves it unless `--purge-data` is explicit.

## Coverage limitations

Automatic capture only runs before automatic compaction. A session without automatic compaction might have no stored snapshot, and a disk transcript can lag behind the visible conversation. The recap therefore distinguishes complete, partial, and empty evidence coverage and lists any known gaps.

## Sharing recaps

The renderer removes common credential patterns, private keys, email addresses, remote images, and active HTML tags. Redaction is best effort, not proof that a recap is safe to publish. Review every recap before sharing, and never commit the raw state directory.

## Legacy state

Iterlog does not automatically move or delete private state created by the earlier `daily-report` plugin. Disable the old plugin before enabling Iterlog to avoid duplicate capture hooks, then archive or remove the old state only through an explicit user-controlled operation.
