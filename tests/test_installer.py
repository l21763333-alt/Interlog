from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "iterlog"
MANAGER = ROOT / "scripts" / "manage.py"
TOOL_SOURCE = PLUGIN / "tools" / "iterlog.py"
OWNED_ACTIONS = {
    "session-context-hook",
    "capture-hook",
    "postcompact-hook",
    "subagent-context-hook",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def owned_action(handler: object) -> str | None:
    if not isinstance(handler, dict) or handler.get("type") != "command":
        return None
    for key in ("command", "commandWindows"):
        value = handler.get(key)
        if not isinstance(value, str) or "iterlog.py" not in value.replace("\\", "/"):
            continue
        for action in OWNED_ACTIONS:
            if action in value:
                return action
    return None


def owned_handlers(document: dict[str, object]) -> list[tuple[str, dict[str, object], str]]:
    result: list[tuple[str, dict[str, object], str]] = []
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            for handler in group["hooks"]:
                action = owned_action(handler)
                if action is not None and isinstance(handler, dict):
                    result.append((str(event), handler, action))
    return result


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "user-home"
        self.codex_home = self.root / "codex-home"
        self.skill_root = self.home / ".agents" / "skills"

    def run_manager(
        self,
        command: str,
        *extra: str,
        codex_home: Path | None = None,
        skill_root: Path | None = None,
        include_skill_dir: bool = True,
        expected: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        codex_home = codex_home or self.codex_home
        skill_root = skill_root or self.skill_root
        arguments = [
            sys.executable,
            str(MANAGER),
            command,
            "--codex-home",
            str(codex_home),
        ]
        if include_skill_dir:
            arguments.extend(["--skill-dir", str(skill_root)])
        arguments.extend(["--json", *extra])
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        environment["USERPROFILE"] = str(self.home)
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"manager did not emit JSON: {error}\n{completed.stdout}\n{completed.stderr}")
        self.assertIsInstance(payload, dict)
        return completed, payload

    def read_hooks(self, codex_home: Path | None = None) -> dict[str, object]:
        path = (codex_home or self.codex_home) / "hooks.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_install_empty_home_uses_default_skill_root_and_doctor(self) -> None:
        _, result = self.run_manager("install", include_skill_dir=False)

        self.assertTrue(result["changed"])
        self.assertTrue((self.codex_home / "agents" / "iterlog.toml").is_file())
        self.assertEqual(
            (self.codex_home / "tools" / "iterlog" / "iterlog.py").read_bytes(),
            TOOL_SOURCE.read_bytes(),
        )
        self.assertTrue((self.skill_root / "iterlog" / "SKILL.md").is_file())
        self.assertTrue(
            (
                self.skill_root
                / "iterlog"
                / "references"
                / "subagent-protocol.md"
            ).is_file()
        )
        manifest_path = self.codex_home / "iterlog-install.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["package"], "iterlog")
        self.assertEqual(manifest["version"], "3.0.0")
        self.assertGreaterEqual(len(manifest["files"]), 5)
        self.assertTrue(
            all(
                item["source"].replace("\\", "/").startswith(
                    "plugins/iterlog/"
                )
                for item in manifest["files"]
            )
        )
        handlers = owned_handlers(self.read_hooks())
        self.assertEqual({item[2] for item in handlers}, OWNED_ACTIONS)
        self.assertEqual(len(handlers), 4)
        for _, handler, _ in handlers:
            self.assertIn("command", handler)
            self.assertIn("commandWindows", handler)
            self.assertIn("--host", handler["command"])
            self.assertIn("codex", handler["command"])
            self.assertIn("--host", handler["commandWindows"])
            self.assertIn("codex", handler["commandWindows"])

        _, doctor = self.run_manager("doctor", include_skill_dir=False)
        self.assertEqual(doctor["status"], "healthy")
        self.assertTrue(all(item["ok"] for item in doctor["checks"]))

    def test_doctor_rejects_a_missing_installed_hook_interpreter(self) -> None:
        self.run_manager("install")
        hooks = self.read_hooks()
        manifest_path = self.codex_home / "iterlog-install.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tool = self.codex_home / "tools" / "iterlog" / "iterlog.py"
        missing_python = self.root / "deleted-python" / "python.exe"

        fingerprints: dict[str, str] = {}
        for _, handler, action in owned_handlers(hooks):
            arguments = [
                str(missing_python),
                str(tool),
                "--host",
                "codex",
                action,
            ]
            command = subprocess.list2cmdline(arguments)
            handler["command"] = command
            handler["commandWindows"] = command
            fingerprints[action] = fingerprint(handler)
        for item in manifest["hooks"]:
            item["fingerprint"] = fingerprints[item["action"]]

        (self.codex_home / "hooks.json").write_text(
            json.dumps(hooks, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _, doctor = self.run_manager("doctor", expected=1)
        self.assertEqual(doctor["status"], "unhealthy")
        executable_checks = [
            item for item in doctor["checks"] if item["name"] == "hook-executable"
        ]
        self.assertEqual(len(executable_checks), 1)
        self.assertFalse(executable_checks[0]["ok"])
        self.assertIn(str(missing_python), executable_checks[0]["detail"])

    def test_complex_hooks_are_merged_and_legacy_handlers_are_deduplicated(self) -> None:
        self.codex_home.mkdir(parents=True)
        original = {
            "version": 17,
            "vendorExtension": {"keep": [1, {"two": True}]},
            "hooks": {
                "PreCompact": [
                    {
                        "matcher": "^auto$",
                        "description": "shared group",
                        "hooks": [
                            {"type": "command", "command": "echo preserve-me", "timeout": 4},
                            {
                                "type": "command",
                                "command": "/old/python /old/iterlog.py capture-hook",
                            },
                        ],
                    }
                ],
                "ForeignEvent": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {"type": "prompt", "prompt": "leave untouched"},
                            {
                                "type": "command",
                                "commandWindows": (
                                    "C:\\old\\python.exe C:\\old\\iterlog.py "
                                    "postcompact-hook"
                                ),
                            },
                        ],
                    }
                ],
            },
        }
        (self.codex_home / "hooks.json").write_text(
            json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self.run_manager("install")
        merged = self.read_hooks()

        self.assertEqual(merged["version"], 17)
        self.assertEqual(merged["vendorExtension"], original["vendorExtension"])
        serialized = json.dumps(merged, ensure_ascii=False)
        self.assertIn("echo preserve-me", serialized)
        self.assertIn("leave untouched", serialized)
        handlers = owned_handlers(merged)
        self.assertEqual(len(handlers), 4)
        self.assertEqual([item[2] for item in handlers].count("session-context-hook"), 1)
        self.assertEqual([item[2] for item in handlers].count("capture-hook"), 1)
        self.assertEqual([item[2] for item in handlers].count("postcompact-hook"), 1)
        self.assertEqual([item[2] for item in handlers].count("subagent-context-hook"), 1)
        self.assertNotIn("/old/iterlog.py", serialized)
        self.assertNotIn("C:\\old\\iterlog.py", serialized)

    def test_install_is_byte_for_byte_idempotent(self) -> None:
        self.run_manager("install")
        observed = [
            self.codex_home / "hooks.json",
            self.codex_home / "iterlog-install.json",
            self.codex_home / "agents" / "iterlog.toml",
            self.codex_home / "tools" / "iterlog" / "iterlog.py",
            self.skill_root / "iterlog" / "SKILL.md",
        ]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in observed}

        _, second = self.run_manager("install")

        self.assertFalse(second["changed"])
        self.assertEqual(second["actions"], [])
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in observed}
        self.assertEqual(after, before)

    def test_paths_with_spaces_are_quoted_for_both_shell_families(self) -> None:
        codex_home = self.root / "Codex Home With Spaces 测试"
        skill_root = self.root / "Skill Root With Spaces 技能"
        self.run_manager("install", codex_home=codex_home, skill_root=skill_root)

        tool = codex_home / "tools" / "iterlog" / "iterlog.py"
        handlers = owned_handlers(self.read_hooks(codex_home))
        self.assertEqual(len(handlers), 4)
        for _, handler, _ in handlers:
            command = handler["command"]
            command_windows = handler["commandWindows"]
            self.assertIn(str(tool), command)
            self.assertIn(str(tool), command_windows)
            self.assertIn("'", command)
            self.assertIn(f'"{tool}"', command_windows)
        self.assertTrue((skill_root / "iterlog" / "SKILL.md").is_file())

    def test_two_codex_homes_are_isolated(self) -> None:
        first_home = self.root / "account-one" / ".codex"
        second_home = self.root / "account-two" / ".codex"
        first_skills = self.root / "account-one" / ".agents" / "skills"
        second_skills = self.root / "account-two" / ".agents" / "skills"

        self.run_manager("install", codex_home=first_home, skill_root=first_skills)
        self.run_manager("install", codex_home=second_home, skill_root=second_skills)

        first_hooks = "\n".join(
            str(handler.get("command", "")) + str(handler.get("commandWindows", ""))
            for _, handler, _ in owned_handlers(self.read_hooks(first_home))
        )
        second_hooks = "\n".join(
            str(handler.get("command", "")) + str(handler.get("commandWindows", ""))
            for _, handler, _ in owned_handlers(self.read_hooks(second_home))
        )
        self.assertIn(str(first_home), first_hooks)
        self.assertNotIn(str(second_home), first_hooks)
        self.assertIn(str(second_home), second_hooks)
        self.assertNotIn(str(first_home), second_hooks)
        self.assertTrue((first_skills / "iterlog" / "SKILL.md").is_file())
        self.assertTrue((second_skills / "iterlog" / "SKILL.md").is_file())

    def test_upgrade_rejects_modified_file_and_force_replaces_it_with_backup(self) -> None:
        self.run_manager("install")
        target = self.codex_home / "tools" / "iterlog" / "iterlog.py"
        target.write_bytes(target.read_bytes() + b"\n# local modification\n")
        modified = target.read_bytes()

        _, refused = self.run_manager("upgrade", expected=2)
        self.assertEqual(refused["status"], "error")
        self.assertTrue(refused["conflicts"])
        self.assertEqual(target.read_bytes(), modified)

        _, forced = self.run_manager("upgrade", "--force")
        self.assertEqual(target.read_bytes(), TOOL_SOURCE.read_bytes())
        self.assertIsNotNone(forced["backup"])
        backup = Path(forced["backup"])
        self.assertTrue((backup / "tools" / "iterlog" / "iterlog.py").is_file())

    def test_upgrade_migrates_v1_flat_sources_and_three_hooks_to_current(self) -> None:
        self.codex_home.mkdir(parents=True)
        legacy_files = [
            (
                self.codex_home / "agents" / "iterlog.toml",
                b'name = "iterlog"\n# v1 agent\n',
                "agent",
                "agents/iterlog.toml",
            ),
            (
                self.codex_home / "tools" / "iterlog" / "iterlog.py",
                b"#!/usr/bin/env python3\n# v1 runtime\n",
                "tool",
                "tools/iterlog.py",
            ),
            (
                self.skill_root / "iterlog" / "SKILL.md",
                b"---\nname: iterlog\ndescription: v1\n---\n",
                "skill",
                "skills/iterlog/SKILL.md",
            ),
            (
                self.skill_root / "iterlog" / "agents" / "openai.yaml",
                b'interface:\n  display_name: "Iterlog"\n',
                "skill",
                "skills/iterlog/agents/openai.yaml",
            ),
        ]
        manifest_files: list[dict[str, object]] = []
        for target, data, kind, source in legacy_files:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest_files.append(
                {
                    "path": str(target.resolve()),
                    "sha256": sha256_bytes(data),
                    "kind": kind,
                    "source": source,
                }
            )

        tool = legacy_files[1][0]
        legacy_specs = (
            ("PreCompact", "^auto$", "capture-hook"),
            ("PostCompact", "^auto$", "postcompact-hook"),
            ("SubagentStart", "^iterlog$", "subagent-context-hook"),
        )
        hooks: dict[str, list[dict[str, object]]] = {}
        manifest_hooks: list[dict[str, str]] = []
        for event, matcher, action in legacy_specs:
            handler = {
                "type": "command",
                "command": f'python3 "{tool}" {action}',
                "commandWindows": f'python "{tool}" {action}',
                "timeout": 10,
            }
            hooks[event] = [{"matcher": matcher, "hooks": [handler]}]
            manifest_hooks.append(
                {
                    "event": event,
                    "matcher": matcher,
                    "action": action,
                    "fingerprint": fingerprint(handler),
                }
            )
        (self.codex_home / "hooks.json").write_text(
            json.dumps({"hooks": hooks}, indent=2),
            encoding="utf-8",
        )
        legacy_manifest = {
            "schema_version": 1,
            "package": "iterlog",
            "version": "1.0.0",
            "installed_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "source_root": "/legacy/iterlog",
            "codex_home": str(self.codex_home.resolve()),
            "skill_dir": str(self.skill_root.resolve()),
            "skill_target": str((self.skill_root / "iterlog").resolve()),
            "files": manifest_files,
            "hooks": manifest_hooks,
        }
        (self.codex_home / "iterlog-install.json").write_text(
            json.dumps(legacy_manifest, indent=2),
            encoding="utf-8",
        )

        _, result = self.run_manager("upgrade")

        self.assertTrue(result["changed"])
        self.assertIsNotNone(result["backup"])
        migrated = json.loads(
            (self.codex_home / "iterlog-install.json").read_text(encoding="utf-8")
        )
        self.assertEqual(migrated["version"], "3.0.0")
        self.assertEqual(migrated["installed_at"], legacy_manifest["installed_at"])
        self.assertEqual(len(migrated["hooks"]), 4)
        self.assertEqual({item["action"] for item in migrated["hooks"]}, OWNED_ACTIONS)
        self.assertTrue(
            all(
                item["source"].replace("\\", "/").startswith(
                    "plugins/iterlog/"
                )
                for item in migrated["files"]
            )
        )
        self.assertEqual(
            (self.codex_home / "tools" / "iterlog" / "iterlog.py").read_bytes(),
            TOOL_SOURCE.read_bytes(),
        )
        self.assertTrue(
            (
                self.skill_root
                / "iterlog"
                / "references"
                / "subagent-protocol.md"
            ).is_file()
        )
        handlers = owned_handlers(self.read_hooks())
        self.assertEqual(len(handlers), 4)
        self.assertEqual({item[2] for item in handlers}, OWNED_ACTIONS)

    def test_uninstall_preserves_unrelated_hooks_data_and_modified_managed_file(self) -> None:
        self.codex_home.mkdir(parents=True)
        unrelated = {
            "settings": {"unknown": "keep"},
            "hooks": {
                "PreCompact": [
                    {
                        "matcher": "^manual$",
                        "hooks": [{"type": "command", "command": "echo unrelated"}],
                    }
                ]
            },
        }
        (self.codex_home / "hooks.json").write_text(json.dumps(unrelated), encoding="utf-8")
        self.run_manager("install")
        data_file = self.codex_home / "iterlog" / "reports" / "keep.md"
        data_file.parent.mkdir(parents=True)
        data_file.write_text("persistent report", encoding="utf-8")
        tool = self.codex_home / "tools" / "iterlog" / "iterlog.py"
        tool.write_bytes(tool.read_bytes() + b"\n# preserve this local edit\n")

        _, result = self.run_manager("uninstall")

        self.assertTrue(tool.is_file(), "modified managed files must never be deleted")
        self.assertTrue(any("modified managed file" in item for item in result["warnings"]))
        self.assertFalse((self.codex_home / "agents" / "iterlog.toml").exists())
        self.assertFalse((self.skill_root / "iterlog" / "SKILL.md").exists())
        self.assertFalse((self.codex_home / "iterlog-install.json").exists())
        self.assertEqual(data_file.read_text(encoding="utf-8"), "persistent report")
        remaining = self.read_hooks()
        self.assertEqual(remaining["settings"], unrelated["settings"])
        self.assertIn("echo unrelated", json.dumps(remaining))
        self.assertEqual(owned_handlers(remaining), [])

    def test_uninstall_purge_data_and_dry_run(self) -> None:
        _, dry = self.run_manager("install", "--dry-run")
        self.assertTrue(dry["dry_run"])
        self.assertTrue(dry["actions"])
        self.assertFalse(self.codex_home.exists())

        self.run_manager("install")
        state = self.codex_home / "iterlog"
        (state / "captures" / "session").mkdir(parents=True)
        (state / "captures" / "session" / "capture.json").write_text("{}", encoding="utf-8")

        _, result = self.run_manager("uninstall", "--purge-data")

        self.assertFalse(state.exists())
        self.assertFalse(result["data_preserved"])


if __name__ == "__main__":
    unittest.main()
