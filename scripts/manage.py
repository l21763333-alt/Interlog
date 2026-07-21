#!/usr/bin/env python3
"""Manage the optional standalone Codex installation without third-party dependencies.

Marketplace installation is the default distribution path. This lifecycle is
kept only for users who require the fixed-name `iterlog` Codex agent;
do not enable it alongside marketplace hooks.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PACKAGE_NAME = "iterlog"
PACKAGE_VERSION = "3.0.0"
SCHEMA_VERSION = 1
MANIFEST_NAME = "iterlog-install.json"
SKILL_NAME = "iterlog"
TOOL_NAME = "iterlog.py"
AGENT_NAME = "iterlog.toml"
STATE_DIR_NAME = "iterlog"
HOOK_SPECS = (
    (
        "SessionStart",
        "",
        "session-context-hook",
        10,
        "Loading the portable Iterlog runtime bridge",
    ),
    (
        "PreCompact",
        "^auto$",
        "capture-hook",
        60,
        "Capturing context before automatic compaction",
    ),
    (
        "PostCompact",
        "^auto$",
        "postcompact-hook",
        10,
        "Recording automatic compaction completion",
    ),
    (
        "SubagentStart",
        "^iterlog$",
        "subagent-context-hook",
        10,
        "Loading Iterlog evidence",
    ),
)
HOOK_ACTIONS = frozenset(spec[2] for spec in HOOK_SPECS)


class ManageError(RuntimeError):
    """A safe, user-actionable management failure."""

    def __init__(self, message: str, *, conflicts: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.conflicts = list(conflicts)


@dataclass(frozen=True)
class DesiredFile:
    source: Path
    target: Path
    data: bytes
    digest: str
    kind: str
    backup_label: Path
    mode: int


class OperationLog:
    def __init__(self, command: str, dry_run: bool) -> None:
        self.command = command
        self.dry_run = dry_run
        self.actions: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    @property
    def changed(self) -> bool:
        return bool(self.actions)

    def add(self, operation: str, path: Path, detail: str = "") -> None:
        item: dict[str, Any] = {"operation": operation, "path": str(path)}
        if detail:
            item["detail"] = detail
        self.actions.append(item)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _resolve(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _plugin_root() -> Path:
    return _source_root() / "plugins" / PACKAGE_NAME


def _default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    return _resolve(configured) if configured else _resolve(Path.home() / ".codex")


def _default_skill_root() -> Path:
    return _resolve(Path.home() / ".agents" / "skills")


def _skill_target(skill_dir: Path) -> Path:
    # Accept both the documented skill root and an explicit final skill directory.
    return skill_dir if skill_dir.name == SKILL_NAME else skill_dir / SKILL_NAME


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _fingerprint(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _backup_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, mode)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _remove_empty_parents(start: Path, stop: Path) -> None:
    current = start
    stop = stop.resolve(strict=False)
    while current != stop and _is_relative_to(current.resolve(strict=False), stop):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _read_json_object(path: Path, *, force: bool = False) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        if force:
            return {}
        raise ManageError(f"expected a regular JSON file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if force:
            return {}
        raise ManageError(f"cannot parse {path}: {error}") from error
    if not isinstance(value, dict):
        if force:
            return {}
        raise ManageError(f"expected a JSON object in {path}")
    return value


def _load_manifest(path: Path, *, force: bool = False) -> dict[str, Any] | None:
    if not path.exists():
        return None
    manifest = _read_json_object(path, force=force)
    valid = (
        manifest.get("schema_version") == SCHEMA_VERSION
        and manifest.get("package") == PACKAGE_NAME
        and isinstance(manifest.get("files"), list)
        and isinstance(manifest.get("hooks"), list)
    )
    if not valid:
        if force:
            return None
        raise ManageError(
            f"invalid or foreign install manifest: {path}; use --force only after inspecting it"
        )
    return manifest


def _manifest_file_map(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not manifest:
        return result
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        digest = item.get("sha256")
        kind = item.get("kind")
        if isinstance(path, str) and isinstance(digest, str) and isinstance(kind, str):
            result[_path_key(path)] = item
    return result


def _desired_files(codex_home: Path, skill_dir: Path) -> list[DesiredFile]:
    root = _plugin_root()
    sources: list[tuple[Path, Path, str, Path]] = [
        (
            root / "compat" / "codex-agent" / AGENT_NAME,
            codex_home / "agents" / AGENT_NAME,
            "agent",
            Path("agents") / AGENT_NAME,
        ),
        (
            root / "tools" / TOOL_NAME,
            codex_home / "tools" / STATE_DIR_NAME / TOOL_NAME,
            "tool",
            Path("tools") / STATE_DIR_NAME / TOOL_NAME,
        ),
    ]
    skill_source = root / "skills" / SKILL_NAME
    skill_target = _skill_target(skill_dir)
    if not skill_source.is_dir():
        raise ManageError(f"missing packaged skill directory: {skill_source}")
    for source in sorted(skill_source.rglob("*")):
        if not source.is_file() or source.is_symlink():
            continue
        if "__pycache__" in source.parts or source.suffix in {".pyc", ".pyo"}:
            continue
        relative = source.relative_to(skill_source)
        sources.append(
            (
                source,
                skill_target / relative,
                "skill",
                Path("skills") / SKILL_NAME / relative,
            )
        )

    desired: list[DesiredFile] = []
    for source, target, kind, label in sources:
        if not source.is_file() or source.is_symlink():
            raise ManageError(f"missing packaged source file: {source}")
        data = source.read_bytes()
        # Installed configuration is intentionally user-readable; only the runtime
        # script needs an executable bit.  Fixed modes also make zip/Git installs
        # behave the same on Windows and POSIX hosts.
        mode = 0o755 if kind == "tool" else 0o644
        desired.append(
            DesiredFile(
                source=source,
                target=target.resolve(strict=False),
                data=data,
                digest=_sha256_bytes(data),
                kind=kind,
                backup_label=label,
                mode=mode,
            )
        )
    return desired


def _command_strings(tool_path: Path, action: str) -> tuple[str, str]:
    arguments = [
        str(Path(sys.executable).resolve(strict=False)),
        str(tool_path),
        "--host",
        "codex",
        action,
    ]
    return " ".join(shlex.quote(value) for value in arguments), subprocess.list2cmdline(arguments)


def _command_executable(command: Any, *, windows: bool) -> str | None:
    """Return argv[0] from one of this manager's shell-free hook commands."""
    if not isinstance(command, str) or not command.strip():
        return None
    value = command.lstrip()
    if not windows:
        try:
            arguments = shlex.split(value, posix=True)
        except ValueError:
            return None
        return arguments[0] if arguments else None
    if value.startswith('"'):
        closing = value.find('"', 1)
        return value[1:closing] if closing > 1 else None
    return value.split(None, 1)[0]


def _new_handlers(tool_path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event, matcher, action, timeout, status_message in HOOK_SPECS:
        command, command_windows = _command_strings(tool_path, action)
        handler = {
            "type": "command",
            "command": command,
            "commandWindows": command_windows,
            "timeout": timeout,
            "statusMessage": status_message,
        }
        result.append(
            {
                "event": event,
                "matcher": matcher,
                "action": action,
                "handler": handler,
                "fingerprint": _fingerprint(handler),
            }
        )
    return result


def _owned_action(handler: Any) -> str | None:
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return None
    commands = [handler.get("command"), handler.get("commandWindows")]
    for value in commands:
        if not isinstance(value, str):
            continue
        normalized = value.replace("\\", "/").lower()
        if TOOL_NAME.lower() not in normalized:
            continue
        for action in HOOK_ACTIONS:
            if action in value:
                return action
    return None


def _iter_owned_handlers(hooks_document: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any], str]]:
    hooks = hooks_document.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                action = _owned_action(handler)
                if action and isinstance(handler, dict):
                    yield str(event), handler, action


def _remove_owned_handlers(
    hooks_document: dict[str, Any],
    *,
    allowed_fingerprints: dict[str, set[str]] | None = None,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    document = copy.deepcopy(hooks_document)
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return document, []
    removed: list[tuple[str, str]] = []
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        next_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                next_groups.append(group)
                continue
            next_handlers: list[Any] = []
            removed_from_group = False
            for handler in group["hooks"]:
                action = _owned_action(handler)
                permitted = action is not None
                if permitted and allowed_fingerprints is not None:
                    permitted = _fingerprint(handler) in allowed_fingerprints.get(action, set())
                if permitted and action is not None:
                    removed.append((str(event), action))
                    removed_from_group = True
                else:
                    next_handlers.append(handler)
            updated_group = copy.deepcopy(group)
            updated_group["hooks"] = next_handlers
            if (
                removed_from_group
                and not next_handlers
                and set(updated_group).issubset({"matcher", "hooks"})
            ):
                continue
            next_groups.append(updated_group)
        hooks[event] = next_groups
        if not next_groups:
            del hooks[event]
    return document, removed


def _merge_hooks(
    original: dict[str, Any], new_handlers: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    document, removed = _remove_owned_handlers(original)
    hooks = document.get("hooks")
    if hooks is None:
        hooks = {}
        document["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ManageError("hooks.json field 'hooks' must be an object")

    for spec in new_handlers:
        event = spec["event"]
        matcher = spec["matcher"]
        groups = hooks.get(event)
        if groups is None:
            groups = []
            hooks[event] = groups
        if not isinstance(groups, list):
            raise ManageError(f"hooks.json field hooks.{event} must be a list")
        destination: dict[str, Any] | None = None
        for group in groups:
            if (
                isinstance(group, dict)
                and group.get("matcher") == matcher
                and isinstance(group.get("hooks"), list)
            ):
                destination = group
                break
        if destination is None:
            destination = {"matcher": matcher, "hooks": []}
            groups.append(destination)
        destination["hooks"].append(copy.deepcopy(spec["handler"]))
    return document, removed


def _previous_hook_fingerprints(manifest: dict[str, Any] | None) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if not manifest:
        return result
    for item in manifest.get("hooks", []):
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        fingerprint = item.get("fingerprint")
        if isinstance(action, str) and isinstance(fingerprint, str):
            result.setdefault(action, set()).add(fingerprint)
    return result


def _hook_modification_conflicts(
    hooks_document: dict[str, Any], manifest: dict[str, Any] | None
) -> list[str]:
    expected = _previous_hook_fingerprints(manifest)
    if not expected:
        return []
    conflicts: list[str] = []
    for event, handler, action in _iter_owned_handlers(hooks_document):
        if _fingerprint(handler) not in expected.get(action, set()):
            conflicts.append(f"modified managed hook: {event}/{action}")
    return conflicts


def _ensure_backup(
    codex_home: Path,
    backup_holder: list[Path | None],
    log: OperationLog,
) -> Path:
    if backup_holder[0] is None:
        backup_holder[0] = codex_home / "backups" / STATE_DIR_NAME / _backup_stamp()
        if not log.dry_run:
            backup_holder[0].mkdir(parents=True, exist_ok=False)
    return backup_holder[0]


def _backup_existing(
    source: Path,
    label: Path,
    codex_home: Path,
    backup_holder: list[Path | None],
    log: OperationLog,
) -> None:
    if not source.exists() and not source.is_symlink():
        return
    backup_root = _ensure_backup(codex_home, backup_holder, log)
    destination = backup_root / label
    log.add("backup", source, f"to {destination}")
    if log.dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        link_record = destination.with_suffix(destination.suffix + ".symlink.txt")
        _atomic_write(
            link_record,
            (os.readlink(source) + "\n").encode("utf-8"),
            0o600,
        )
    elif source.is_file():
        _atomic_write(destination, source.read_bytes(), stat.S_IMODE(source.stat().st_mode))


def _target_state(target: Path) -> tuple[str, str | None]:
    if target.is_symlink():
        return "symlink", None
    if target.is_file():
        return "file", _sha256_file(target)
    if target.exists():
        return "other", None
    return "missing", None


def _manifest_payload(
    codex_home: Path,
    skill_dir: Path,
    desired: list[DesiredFile],
    handlers: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    installed_at = previous.get("installed_at") if previous else None
    if not isinstance(installed_at, str):
        installed_at = _utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "package": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "installed_at": installed_at,
        "updated_at": _utc_now(),
        "source_root": str(_source_root()),
        "codex_home": str(codex_home),
        "skill_dir": str(skill_dir),
        "skill_target": str(_skill_target(skill_dir)),
        "files": [
            {
                "path": str(item.target),
                "sha256": item.digest,
                "kind": item.kind,
                "source": str(item.source.relative_to(_source_root())),
            }
            for item in desired
        ],
        "hooks": [
            {
                "event": item["event"],
                "matcher": item["matcher"],
                "action": item["action"],
                "fingerprint": item["fingerprint"],
            }
            for item in handlers
        ],
    }


def _manifest_equivalent(left: dict[str, Any] | None, right: dict[str, Any]) -> bool:
    if not left:
        return False
    ignored = {"installed_at", "updated_at"}
    return {k: v for k, v in left.items() if k not in ignored} == {
        k: v for k, v in right.items() if k not in ignored
    }


def _install_or_upgrade(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    command = args.command
    codex_home = _resolve(args.codex_home)
    skill_dir = _resolve(args.skill_dir)
    manifest_path = codex_home / MANIFEST_NAME
    hooks_path = codex_home / "hooks.json"
    log = OperationLog(command, args.dry_run)

    manifest = _load_manifest(manifest_path, force=args.force)
    if manifest and _path_key(manifest.get("codex_home", "")) != _path_key(codex_home):
        if not args.force:
            raise ManageError(f"manifest belongs to another CODEX_HOME: {manifest_path}")
        manifest = None
    desired = _desired_files(codex_home, skill_dir)
    desired_by_key = {_path_key(item.target): item for item in desired}
    previous_files = _manifest_file_map(manifest)
    handlers = _new_handlers(codex_home / "tools" / STATE_DIR_NAME / TOOL_NAME)

    hooks_document = _read_json_object(hooks_path, force=args.force)
    hook_conflicts = _hook_modification_conflicts(hooks_document, manifest)
    try:
        merged_hooks, _ = _merge_hooks(hooks_document, handlers)
    except ManageError:
        if not args.force:
            raise
        merged_hooks, _ = _merge_hooks({}, handlers)
    merged_hooks_data = _pretty_json(merged_hooks)
    hooks_changed = not hooks_path.is_file() or hooks_path.read_bytes() != merged_hooks_data

    conflicts: list[str] = list(hook_conflicts)
    writes: list[DesiredFile] = []
    removals: list[tuple[Path, dict[str, Any]]] = []
    for item in desired:
        state, current_digest = _target_state(item.target)
        previous = previous_files.get(_path_key(item.target))
        if state == "file" and current_digest == item.digest:
            continue
        if state == "missing":
            writes.append(item)
            continue
        previous_digest = previous.get("sha256") if previous else None
        is_unchanged_owned = state == "file" and current_digest == previous_digest
        if is_unchanged_owned and command == "install" and not args.force:
            conflicts.append(f"installed package has updates; run upgrade: {item.target}")
        elif previous and is_unchanged_owned:
            writes.append(item)
        elif args.force:
            writes.append(item)
        else:
            conflicts.append(f"target exists or was modified: {item.target}")

    for key, previous in previous_files.items():
        if key in desired_by_key:
            continue
        path_value = previous.get("path")
        if not isinstance(path_value, str):
            continue
        target = _resolve(path_value)
        state, current_digest = _target_state(target)
        if state == "missing":
            continue
        unchanged = state == "file" and current_digest == previous.get("sha256")
        if command == "install" and not args.force:
            conflicts.append(f"installed package layout changed; run upgrade: {target}")
        elif unchanged or args.force:
            removals.append((target, previous))
        else:
            conflicts.append(f"obsolete managed file was modified: {target}")

    if conflicts and not args.force:
        raise ManageError("refusing to overwrite conflicting changes", conflicts=conflicts)

    backup_holder: list[Path | None] = [None]
    for target, previous in removals:
        source_label = previous.get("source")
        if isinstance(source_label, str) and not Path(source_label).is_absolute():
            label = Path("obsolete") / source_label
        else:
            label = Path("obsolete") / str(previous.get("kind", "file")) / target.name
        _backup_existing(target, label, codex_home, backup_holder, log)
        log.add("remove", target, "obsolete managed file")
        if not log.dry_run:
            _remove_path(target)

    for item in writes:
        _backup_existing(item.target, item.backup_label, codex_home, backup_holder, log)
        log.add("write", item.target, f"sha256={item.digest}")
        if not log.dry_run:
            if item.target.exists() and not item.target.is_file() and not item.target.is_symlink():
                _remove_path(item.target)
            _atomic_write(item.target, item.data, item.mode)

    if hooks_changed:
        _backup_existing(hooks_path, Path("hooks.json"), codex_home, backup_holder, log)
        log.add("merge-hooks", hooks_path, "installed exactly one handler for each managed event")
        if not log.dry_run:
            _atomic_write(hooks_path, merged_hooks_data, 0o600)

    new_manifest = _manifest_payload(codex_home, skill_dir, desired, handlers, manifest)
    manifest_changed = not _manifest_equivalent(manifest, new_manifest)
    if writes or removals or hooks_changed:
        manifest_changed = True
    if manifest_changed:
        _backup_existing(
            manifest_path,
            Path("install-manifest.json"),
            codex_home,
            backup_holder,
            log,
        )
        log.add("write-manifest", manifest_path)
        if not log.dry_run:
            _atomic_write(manifest_path, _pretty_json(new_manifest), 0o600)

    result = {
        "command": command,
        "status": "ok",
        "changed": log.changed,
        "dry_run": log.dry_run,
        "codex_home": str(codex_home),
        "skill_target": str(_skill_target(skill_dir)),
        "manifest": str(manifest_path),
        "backup": str(backup_holder[0]) if backup_holder[0] else None,
        "actions": log.actions,
        "warnings": log.warnings,
    }
    return 0, result


def _doctor(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    codex_home = _resolve(args.codex_home)
    skill_dir = _resolve(args.skill_dir)
    manifest_path = codex_home / MANIFEST_NAME
    hooks_path = codex_home / "hooks.json"
    checks: list[dict[str, Any]] = []
    hook_executables: list[str] = []
    runtime_python: str | None = None

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    check("python", sys.version_info >= (3, 10), sys.version.split()[0])
    try:
        desired = _desired_files(codex_home, skill_dir)
        check("package-sources", True, f"{len(desired)} files")
    except ManageError as error:
        desired = []
        check("package-sources", False, str(error))

    try:
        manifest = _load_manifest(manifest_path)
        check("manifest", manifest is not None, str(manifest_path))
    except ManageError as error:
        manifest = None
        check("manifest", False, str(error))

    if manifest:
        check(
            "package-version",
            manifest.get("version") == PACKAGE_VERSION,
            f"installed={manifest.get('version')}, packaged={PACKAGE_VERSION}",
        )
        for item in manifest.get("files", []):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                check("managed-file", False, "invalid manifest entry")
                continue
            path = _resolve(item["path"])
            state, digest = _target_state(path)
            ok = state == "file" and digest == item.get("sha256")
            check("managed-file", ok, str(path))

    try:
        hooks_document = _read_json_object(hooks_path)
        expected = _previous_hook_fingerprints(manifest)
        found: dict[str, list[str]] = {action: [] for action in HOOK_ACTIONS}
        for _, handler, action in _iter_owned_handlers(hooks_document):
            found[action].append(_fingerprint(handler))
            command_key = "commandWindows" if os.name == "nt" else "command"
            executable = _command_executable(
                handler.get(command_key), windows=os.name == "nt"
            )
            if executable:
                hook_executables.append(executable)
        for action in sorted(HOOK_ACTIONS):
            fingerprints = found[action]
            ok = len(fingerprints) == 1
            if expected:
                ok = ok and fingerprints[0] in expected.get(action, set()) if fingerprints else False
            check("hook", ok, f"{action}: count={len(fingerprints)}")
    except ManageError as error:
        check("hooks", False, str(error))

    unique_executables = sorted(set(hook_executables), key=os.path.normcase)
    if len(unique_executables) != 1:
        check(
            "hook-executable",
            False,
            f"expected one interpreter across owned hooks, found {len(unique_executables)}",
        )
    else:
        configured_executable = unique_executables[0]
        configured_path = Path(configured_executable).expanduser()
        if configured_path.is_absolute():
            resolved_executable = str(configured_path.resolve(strict=False))
            executable_exists = Path(resolved_executable).is_file()
        else:
            resolved_executable = shutil.which(configured_executable) or configured_executable
            executable_exists = shutil.which(configured_executable) is not None
        check("hook-executable", executable_exists, resolved_executable)
        if executable_exists:
            try:
                observed = subprocess.run(
                    [resolved_executable, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                detail = (observed.stdout or observed.stderr).strip()
                match = re.search(r"Python\s+(\d+)\.(\d+)", detail)
                supported = bool(
                    observed.returncode == 0
                    and match
                    and (int(match.group(1)), int(match.group(2))) >= (3, 10)
                )
                check("hook-python", supported, detail or resolved_executable)
                if supported:
                    runtime_python = resolved_executable
            except (OSError, subprocess.SubprocessError) as error:
                check("hook-python", False, str(error))

    codex_cli = shutil.which("codex")
    warnings: list[str] = [
        "Hook trust cannot be verified non-interactively; review the current definitions with /hooks."
    ]
    if codex_cli:
        try:
            observed = subprocess.run(
                [codex_cli, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            detail = (observed.stdout or observed.stderr).strip() or codex_cli
            check("codex-cli", observed.returncode == 0, detail)
            features = subprocess.run(
                [codex_cli, "features", "list"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            feature_text = (features.stdout or features.stderr).strip()
            hooks_enabled = bool(
                re.search(r"(?im)^\s*hooks\s+\S+\s+true\s*$", feature_text)
            )
            agents_enabled = bool(
                re.search(r"(?im)^\s*multi_agent\s+\S+\s+true\s*$", feature_text)
            )
            if features.returncode == 0 and hooks_enabled and agents_enabled:
                check("codex-features", True, "hooks=true, multi_agent=true")
            else:
                warnings.append(
                    "Could not confirm hooks=true and multi_agent=true from `codex features list`; "
                    "verify the target Codex client before use."
                )
        except (OSError, subprocess.SubprocessError) as error:
            check("codex-cli", False, str(error))
    else:
        warnings.append("Codex CLI was not found on PATH; client version/features were not inspected.")

    runtime_target = codex_home / "tools" / STATE_DIR_NAME / TOOL_NAME
    if runtime_target.is_file():
        if runtime_python is None:
            check("runtime-doctor", False, "installed hook interpreter is unavailable")
        else:
            try:
                runtime_env = os.environ.copy()
                runtime_env["CODEX_HOME"] = str(codex_home)
                observed = subprocess.run(
                    [runtime_python, str(runtime_target), "--host", "codex", "doctor"],
                    cwd=_source_root(),
                    env=runtime_env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                check(
                    "runtime-doctor",
                    observed.returncode == 0,
                    (observed.stdout or observed.stderr).strip(),
                )
            except (OSError, subprocess.SubprocessError) as error:
                check("runtime-doctor", False, str(error))

    healthy = bool(manifest) and all(item["ok"] for item in checks)
    return (0 if healthy else 1), {
        "command": "doctor",
        "status": "healthy" if healthy else "unhealthy",
        "changed": False,
        "dry_run": True,
        "codex_home": str(codex_home),
        "manifest": str(manifest_path),
        "checks": checks,
        "warnings": warnings,
    }


def _uninstall(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    codex_home = _resolve(args.codex_home)
    skill_dir = _resolve(args.skill_dir)
    manifest_path = codex_home / MANIFEST_NAME
    hooks_path = codex_home / "hooks.json"
    state_path = codex_home / STATE_DIR_NAME
    log = OperationLog("uninstall", args.dry_run)
    manifest = _load_manifest(manifest_path, force=args.force)
    if not manifest and not args.force and not args.purge_data:
        raise ManageError(f"Iterlog is not installed in {codex_home}")

    backup_holder: list[Path | None] = [None]
    if manifest:
        allowed_hooks = _previous_hook_fingerprints(manifest)
        hooks_document = _read_json_object(hooks_path, force=args.force)
        cleaned_hooks, removed_hooks = _remove_owned_handlers(
            hooks_document, allowed_fingerprints=allowed_hooks
        )
        candidate_count = sum(1 for _ in _iter_owned_handlers(hooks_document))
        if candidate_count > len(removed_hooks):
            log.warn("one or more modified Iterlog hook handlers were preserved")
        if removed_hooks:
            _backup_existing(hooks_path, Path("hooks.json"), codex_home, backup_holder, log)
            log.add("merge-hooks", hooks_path, f"removed {len(removed_hooks)} managed handlers")
            if not log.dry_run:
                _atomic_write(hooks_path, _pretty_json(cleaned_hooks), 0o600)

        for item in manifest.get("files", []):
            if not isinstance(item, dict):
                continue
            path_value = item.get("path")
            expected_digest = item.get("sha256")
            if not isinstance(path_value, str) or not isinstance(expected_digest, str):
                log.warn("invalid file entry in manifest was ignored")
                continue
            target = _resolve(path_value)
            state, digest = _target_state(target)
            if state == "missing":
                continue
            if state != "file" or digest != expected_digest:
                log.warn(f"modified managed file was preserved: {target}")
                continue
            source_label = item.get("source")
            if isinstance(source_label, str) and not Path(source_label).is_absolute():
                label = Path("uninstall") / source_label
            else:
                label = Path("uninstall") / str(item.get("kind", "file")) / target.name
            _backup_existing(target, label, codex_home, backup_holder, log)
            log.add("remove", target, "unmodified managed file")
            if not log.dry_run:
                target.unlink()

        _backup_existing(
            manifest_path,
            Path("install-manifest.json"),
            codex_home,
            backup_holder,
            log,
        )
        log.add("remove-manifest", manifest_path)
        if not log.dry_run:
            manifest_path.unlink(missing_ok=True)

        if not log.dry_run:
            _remove_empty_parents(codex_home / "tools" / STATE_DIR_NAME, codex_home)
            _remove_empty_parents(codex_home / "agents", codex_home)
            recorded_skill = manifest.get("skill_target")
            if isinstance(recorded_skill, str):
                target = _resolve(recorded_skill)
                stop = target.parent
                _remove_empty_parents(target, stop)
    elif args.force and hooks_path.exists():
        hooks_document = _read_json_object(hooks_path, force=True)
        cleaned_hooks, removed_hooks = _remove_owned_handlers(hooks_document)
        if removed_hooks:
            _backup_existing(hooks_path, Path("hooks.json"), codex_home, backup_holder, log)
            log.add("merge-hooks", hooks_path, f"removed {len(removed_hooks)} legacy handlers")
            if not log.dry_run:
                _atomic_write(hooks_path, _pretty_json(cleaned_hooks), 0o600)

    if args.purge_data and (state_path.exists() or state_path.is_symlink()):
        if state_path.resolve(strict=False) == codex_home.resolve(strict=False):
            raise ManageError("refusing to purge CODEX_HOME itself")
        log.add("purge-data", state_path)
        if not log.dry_run:
            _remove_path(state_path)

    return 0, {
        "command": "uninstall",
        "status": "ok",
        "changed": log.changed,
        "dry_run": log.dry_run,
        "codex_home": str(codex_home),
        "manifest": str(manifest_path),
        "data_preserved": not args.purge_data,
        "backup": str(backup_holder[0]) if backup_holder[0] else None,
        "actions": log.actions,
        "warnings": log.warnings,
    }


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--codex-home",
        default=str(_default_codex_home()),
        help="Codex home to manage (default: CODEX_HOME or ~/.codex)",
    )
    parser.add_argument(
        "--skill-dir",
        default=str(_default_skill_root()),
        help="user skill root, or the final iterlog skill directory",
    )
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    parser.add_argument("--force", action="store_true", help="override install/upgrade conflicts")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("install", "install the standalone Codex agent, tool, skill, and hooks"),
        ("upgrade", "safely update an existing standalone installation"),
        ("doctor", "verify the installation and hook ownership"),
    ):
        command_parser = subparsers.add_parser(name, help=help_text)
        _add_common_arguments(command_parser)
    uninstall = subparsers.add_parser("uninstall", help="remove owned, unmodified files")
    _add_common_arguments(uninstall)
    uninstall.add_argument(
        "--purge-data",
        action="store_true",
        help="also delete $CODEX_HOME/iterlog reports and captures",
    )
    return parser


def _print_result(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"{result['command']}: {result['status']}")
    for item in result.get("actions", []):
        suffix = f" ({item['detail']})" if item.get("detail") else ""
        prefix = "would " if result.get("dry_run") else ""
        print(f"  {prefix}{item['operation']}: {item['path']}{suffix}")
    for warning in result.get("warnings", []):
        print(f"  warning: {warning}")
    for check in result.get("checks", []):
        marker = "ok" if check.get("ok") else "failed"
        print(f"  [{marker}] {check.get('name')}: {check.get('detail')}")
    if not result.get("actions") and result.get("status") == "ok":
        print("  already up to date")


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"install", "upgrade"}:
            code, result = _install_or_upgrade(args)
        elif args.command == "doctor":
            code, result = _doctor(args)
        else:
            code, result = _uninstall(args)
    except ManageError as error:
        result = {
            "command": args.command,
            "status": "error",
            "error": str(error),
            "conflicts": error.conflicts,
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"{args.command}: error: {error}", file=sys.stderr)
            for conflict in error.conflicts:
                print(f"  conflict: {conflict}", file=sys.stderr)
        return 2
    _print_result(result, as_json=args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
