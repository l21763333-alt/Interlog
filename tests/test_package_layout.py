from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "iterlog"
MARKETPLACE_NAME = "iterlog-marketplace"
PLUGIN = ROOT / "plugins" / PLUGIN_NAME


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected a JSON object: {path}")
    return value


def single_handler(document: dict[str, object], event: str) -> tuple[dict[str, object], dict[str, object]]:
    hooks = document["hooks"]
    if not isinstance(hooks, dict):
        raise AssertionError("hooks must be an object")
    groups = hooks[event]
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict):
        raise AssertionError(f"{event} must contain exactly one matcher group")
    handlers = groups[0].get("hooks")
    if not isinstance(handlers, list) or len(handlers) != 1 or not isinstance(handlers[0], dict):
        raise AssertionError(f"{event} must contain exactly one handler")
    return groups[0], handlers[0]


class PackageLayoutTests(unittest.TestCase):
    def test_dual_marketplaces_resolve_to_the_same_plugin(self) -> None:
        codex_path = ROOT / ".agents" / "plugins" / "marketplace.json"
        claude_path = ROOT / ".claude-plugin" / "marketplace.json"
        codex = load_json(codex_path)
        claude = load_json(claude_path)

        self.assertEqual(codex["name"], MARKETPLACE_NAME)
        self.assertEqual(claude["name"], MARKETPLACE_NAME)
        self.assertEqual(len(codex["plugins"]), 1)
        self.assertEqual(len(claude["plugins"]), 1)
        codex_entry = codex["plugins"][0]
        claude_entry = claude["plugins"][0]
        self.assertEqual(codex_entry["name"], PLUGIN_NAME)
        self.assertEqual(claude_entry["name"], PLUGIN_NAME)
        self.assertEqual(
            codex_entry["source"],
            {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
        )
        self.assertEqual(claude_entry["source"], f"./plugins/{PLUGIN_NAME}")
        self.assertEqual(codex_entry["policy"]["installation"], "AVAILABLE")
        self.assertEqual(codex_entry["policy"]["authentication"], "ON_INSTALL")
        self.assertTrue(claude_entry["strict"])
        self.assertEqual((ROOT / codex_entry["source"]["path"]).resolve(), PLUGIN.resolve())
        self.assertEqual((ROOT / claude_entry["source"]).resolve(), PLUGIN.resolve())

    def test_dual_manifests_are_versioned_at_the_standard_paths(self) -> None:
        expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        codex = load_json(PLUGIN / ".codex-plugin" / "plugin.json")
        claude = load_json(PLUGIN / ".claude-plugin" / "plugin.json")

        for manifest in (codex, claude):
            self.assertEqual(manifest["name"], PLUGIN_NAME)
            self.assertEqual(manifest["version"], expected)
        self.assertEqual(codex["skills"], "./skills/")
        self.assertNotIn("hooks", codex, "Codex discovers hooks/hooks.json by convention")
        self.assertEqual(claude["hooks"], "./hooks/claude-hooks.json")
        self.assertRegex(claude["$schema"], r"claude-code-plugin-manifest\.json$")

    def test_codex_hooks_cover_all_four_lifecycle_events(self) -> None:
        document = load_json(PLUGIN / "hooks" / "hooks.json")
        expected = {
            "SessionStart": "session-context-hook",
            "PreCompact": "capture-hook",
            "PostCompact": "postcompact-hook",
            "SubagentStart": "subagent-context-hook",
        }
        self.assertEqual(set(document["hooks"]), set(expected))
        for event, action in expected.items():
            group, handler = single_handler(document, event)
            self.assertEqual(handler["type"], "command")
            self.assertIn(action, handler["command"])
            self.assertIn("$PLUGIN_ROOT/tools/run_iterlog.sh", handler["command"])
            self.assertIn("--host codex", handler["command"])
            self.assertIn(action, handler["commandWindows"])
            self.assertIn("%PLUGIN_ROOT%", handler["commandWindows"])
            self.assertIn("--host codex", handler["commandWindows"])
            self.assertGreater(handler["timeout"], 0)
            if event in {"PreCompact", "PostCompact"}:
                self.assertEqual(group["matcher"], "^auto$")
            if event == "SubagentStart":
                self.assertEqual(group["matcher"], "^iterlog$")

    def test_claude_hooks_use_argv_safe_node_bridge_and_scoped_agent(self) -> None:
        document = load_json(PLUGIN / "hooks" / "claude-hooks.json")
        expected = {
            "SessionStart": "session-context-hook",
            "PreCompact": "capture-hook",
            "PostCompact": "postcompact-hook",
            "SubagentStart": "subagent-context-hook",
        }
        self.assertEqual(set(document["hooks"]), set(expected))
        for event, action in expected.items():
            group, handler = single_handler(document, event)
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["command"], "node")
            self.assertEqual(
                handler["args"],
                [
                    "${CLAUDE_PLUGIN_ROOT}/tools/run_iterlog.js",
                    "--host",
                    "claude",
                    action,
                ],
            )
            self.assertNotIn("commandWindows", handler)
            if event in {"PreCompact", "PostCompact"}:
                self.assertEqual(group["matcher"], "auto")
        subagent_group, _ = single_handler(document, "SubagentStart")
        self.assertEqual(
            subagent_group["matcher"],
            "^iterlog:iterlog$",
        )

    def test_shared_skill_claude_agent_and_protocol_form_one_contract(self) -> None:
        skill_path = PLUGIN / "skills" / "iterlog" / "SKILL.md"
        protocol_path = skill_path.parent / "references" / "subagent-protocol.md"
        agent_path = PLUGIN / "agents" / "iterlog.md"
        metadata_path = skill_path.parent / "agents" / "openai.yaml"
        compat_agent_path = PLUGIN / "compat" / "codex-agent" / "iterlog.toml"
        for path in (skill_path, protocol_path, agent_path, metadata_path, compat_agent_path):
            self.assertTrue(path.is_file(), path)

        skill = skill_path.read_text(encoding="utf-8")
        protocol = protocol_path.read_text(encoding="utf-8")
        agent = agent_path.read_text(encoding="utf-8")
        metadata = metadata_path.read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?m)^name: iterlog$")
        self.assertIn("references/subagent-protocol.md", skill)
        self.assertIn("$iterlog", skill)
        self.assertIn("/iterlog:iterlog", skill)
        self.assertIn("marketplace-only Codex", skill)
        self.assertIn('display_name: "Iterlog"', metadata)
        self.assertIn("$iterlog", metadata)

        self.assertRegex(agent, r"(?m)^name: iterlog$")
        self.assertRegex(agent, r"(?m)^tools: Read, Grep, Glob, Bash$")
        frontmatter = agent.split("---", 2)[1]
        self.assertNotRegex(frontmatter, r"(?m)^tools:.*\b(?:Write|Edit)\b")
        self.assertIn("SubagentStart", agent)
        self.assertIn("exactly one new nine-section development recap", agent)

        for term in (
            "runtime_argv_prefix",
            "source_capture_ids",
            "verify --manifest",
            "render --input",
            "report date",
            "timezone",
            "verified",
            "inferred",
            "unconfirmed",
        ):
            self.assertIn(term, protocol)
        self.assertIn("## Hard boundaries", protocol)
        self.assertIn("## Required report sections", protocol)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_node_bridge_is_valid_javascript(self) -> None:
        bridge = PLUGIN / "tools" / "run_iterlog.js"
        result = subprocess.run(
            [shutil.which("node"), "--check", str(bridge)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_posix_launcher_honors_configured_python(self) -> None:
        launcher = (PLUGIN / "tools" / "run_iterlog.sh").read_text(encoding="utf-8")
        self.assertIn('${ITERLOG_PYTHON:-}', launcher)
        self.assertIn("sys.version_info >= (3, 10)", launcher)
        self.assertIn('exec "$candidate" "$RUNTIME" "$@"', launcher)

    def test_public_package_has_no_legacy_flat_plugin_layout(self) -> None:
        self.assertFalse((ROOT / ".codex-plugin").exists())
        self.assertFalse((ROOT / "hooks").exists())
        self.assertFalse((ROOT / "tools").exists())
        self.assertFalse((ROOT / "skills").exists())
        self.assertFalse((ROOT / "agents").exists())
        self.assertFalse((ROOT / "install").exists())
        self.assertFalse((ROOT / "hooks.json").exists())

    @unittest.skipUnless(shutil.which("git"), "Git is not installed")
    def test_gitignore_keeps_the_nested_skill_in_release_sources(self) -> None:
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/iterlog/", ignore_text.splitlines())
        self.assertIn("/daily-report/", ignore_text.splitlines())
        self.assertNotIn("iterlog/", ignore_text.splitlines())

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / ".gitignore").write_text(ignore_text, encoding="utf-8")
            skill = (
                repository
                / "plugins"
                / PLUGIN_NAME
                / "skills"
                / "iterlog"
                / "SKILL.md"
            )
            skill.parent.mkdir(parents=True)
            skill.write_text("release-critical skill\n", encoding="utf-8")
            initialized = subprocess.run(
                [shutil.which("git"), "init", "--quiet"],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            checked = subprocess.run(
                [shutil.which("git"), "check-ignore", "--quiet", str(skill)],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 1, checked.stderr)

    def test_release_versions_are_consistent(self) -> None:
        expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        codex = load_json(PLUGIN / ".codex-plugin" / "plugin.json")
        claude = load_json(PLUGIN / ".claude-plugin" / "plugin.json")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        manager = (ROOT / "scripts" / "manage.py").read_text(encoding="utf-8")
        runtime = (PLUGIN / "tools" / "iterlog.py").read_text(encoding="utf-8")
        self.assertEqual(codex["version"], expected)
        self.assertEqual(claude["version"], expected)
        self.assertRegex(pyproject, rf'(?m)^version = "{re.escape(expected)}"$')
        self.assertIn(f'PACKAGE_VERSION = "{expected}"', manager)
        self.assertIn(f'RUNTIME_VERSION = "{expected}"', runtime)


if __name__ == "__main__":
    unittest.main()
