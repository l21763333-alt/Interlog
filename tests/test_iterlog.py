from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "iterlog"
SCRIPT = PLUGIN / "tools" / "iterlog.py"


class IterlogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "sample-project"
        self.project.mkdir()
        self.state = self.root / "private-state"
        self.env = dict(os.environ)
        self.env["CODEX_HOME"] = str(self.root / ".codex")
        self.env["CODEX_ITERLOG_HOME"] = str(self.state)
        self.env["PYTHONIOENCODING"] = "cp1252"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_tool(
        self, *args: str, stdin: dict | None = None, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=json.dumps(stdin, ensure_ascii=False) if stdin is not None else None,
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=self.env,
            cwd=str(cwd) if cwd else None,
            check=False,
        )

    def capture_payload(self, transcript: Path) -> dict:
        return {
            "session_id": "session-123",
            "turn_id": "turn-456",
            "transcript_path": str(transcript),
            "cwd": str(self.project),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
            "model": "test-model",
        }

    def assert_capture_succeeded(
        self, result: subprocess.CompletedProcess[str]
    ) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", result.stdout)

    def test_legacy_windows_reparse_points_are_linklike(self) -> None:
        import runpy

        runtime = runpy.run_path(str(SCRIPT))

        class ReparsePoint:
            def is_symlink(self) -> bool:
                return False

            def lstat(self) -> object:
                return type("Stat", (), {"st_file_attributes": 0x00000400})()

        self.assertTrue(runtime["_is_linklike"](ReparsePoint()))

    def test_capture_is_byte_exact_private_and_idempotent(self) -> None:
        transcript = self.root / "rollout.jsonl"
        original = b'{"type":"message","payload":"hello"}\n\x00opaque\n'
        transcript.write_bytes(original)
        payload = self.capture_payload(transcript)

        first = self.run_tool("capture-hook", stdin=payload)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "")
        raw = list(self.state.glob("captures/*/*/*.jsonl"))
        manifests = list(self.state.glob("captures/*/*/*.manifest.json"))
        self.assertEqual(len(raw), 1)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(raw[0].read_bytes(), original)
        first_mtime = raw[0].stat().st_mtime_ns

        time.sleep(0.01)
        second = self.run_tool("capture-hook", stdin=payload)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(list(self.state.glob("captures/*/*/*.jsonl"))), 1)
        self.assertEqual(raw[0].stat().st_mtime_ns, first_mtime)
        if os.name != "nt":
            self.assertEqual(raw[0].stat().st_mode & 0o777, 0o600)

    def test_duplicate_capture_repairs_a_corrupt_existing_pair(self) -> None:
        transcript = self.root / "rollout.jsonl"
        original = b'{"payload":"authoritative transcript"}\n'
        transcript.write_bytes(original)
        payload = self.capture_payload(transcript)
        self.assert_capture_succeeded(self.run_tool("capture-hook", stdin=payload))
        raw = next(self.state.glob("captures/*/*/*.jsonl"))
        raw.write_bytes(b"CORRUPT")

        repaired = self.run_tool("capture-hook", stdin=payload)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(repaired.stdout, "")
        self.assertEqual(raw.read_bytes(), original)

    def test_sanitized_session_names_do_not_collide(self) -> None:
        transcript = self.root / "rollout.jsonl"
        transcript.write_bytes(b"same evidence\n")
        first_payload = self.capture_payload(transcript)
        first_payload["session_id"] = "session/a"
        second_payload = self.capture_payload(transcript)
        second_payload["session_id"] = "session?a"

        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=first_payload)
        )
        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=second_payload)
        )
        manifests = list(self.state.glob("captures/*/*/*.manifest.json"))
        self.assertEqual(len(manifests), 2)
        self.assertEqual(len({item.parent.name for item in manifests}), 2)
        capture_ids = {
            json.loads(item.read_text(encoding="utf-8"))["capture_id"] for item in manifests
        }
        self.assertEqual(len(capture_ids), 2)

    def test_capture_failure_is_closed_by_default_and_configurable(self) -> None:
        payload = self.capture_payload(self.root / "missing.jsonl")
        failed = self.run_tool("capture-hook", stdin=payload)
        response = json.loads(failed.stdout)
        self.assertFalse(response["continue"])
        self.assertIn("stopReason", response)

        self.env["CODEX_ITERLOG_FAIL_OPEN"] = "1"
        open_result = self.run_tool("capture-hook", stdin=payload)
        self.assertTrue(json.loads(open_result.stdout)["continue"])

    def test_claude_capture_failure_blocks_only_compaction_and_can_fail_open(self) -> None:
        self.env["ITERLOG_HOST"] = "claude"
        payload = self.capture_payload(self.root / "missing.jsonl")

        failed = self.run_tool("capture-hook", stdin=payload)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        response = json.loads(failed.stdout)
        self.assertEqual(response["decision"], "block")
        self.assertIn("compaction", response["reason"].lower())
        self.assertNotIn("continue", response)
        self.assertNotIn("stopReason", response)

        self.env["ITERLOG_FAIL_OPEN"] = "1"
        opened = self.run_tool("capture-hook", stdin=payload)
        opened_response = json.loads(opened.stdout)
        self.assertTrue(opened_response["continue"])
        self.assertNotIn("decision", opened_response)

    def test_explicit_state_root_overrides_environment_and_rejects_relative_path(self) -> None:
        override = self.root / "explicit-private-state"
        self.env["ITERLOG_HOME"] = str(self.root / "ignored-state")
        result = self.run_tool("--state-root", str(override), "doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(Path(payload["state_root"]), override)

        relative = self.run_tool("--state-root", "relative-state", "doctor", cwd=self.project)
        self.assertEqual(relative.returncode, 2)
        self.assertIn("--state-root must be an absolute path", relative.stderr)
        self.assertFalse((self.project / "relative-state").exists())

    def test_context_and_session_hook_export_exact_runtime_bridge(self) -> None:
        transcript = self.root / "bridge-rollout.jsonl"
        transcript.write_text('{"payload":"bridge evidence"}\n', encoding="utf-8")
        captured = self.run_tool("capture-hook", stdin=self.capture_payload(transcript))
        self.assert_capture_succeeded(captured)

        context = self.run_tool(
            "context",
            "--cwd",
            str(self.project),
            "--session-id",
            "session-123",
        )
        self.assertEqual(context.returncode, 0, context.stderr)
        document = json.loads(context.stdout)
        self.assertEqual(document["host"], "codex")
        self.assertEqual(document["state_root"], str(self.state))
        self.assertEqual(len(document["captures"]), 1)
        self.assertRegex(document["default_report_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(document["local_timezone"])
        prefix = document["runtime_argv_prefix"]
        self.assertEqual(prefix[0], sys.executable)
        self.assertEqual(Path(prefix[1]), SCRIPT)
        self.assertEqual(
            prefix[2:],
            ["--host", "codex", "--state-root", str(self.state)],
        )
        self.assertTrue(Path(document["protocol_path"]).is_file())

        session_payload = {
            "session_id": "session-123",
            "cwd": str(self.project),
            "hook_event_name": "SessionStart",
            "source": "startup",
        }
        session = self.run_tool("session-context-hook", stdin=session_payload)
        self.assertEqual(session.returncode, 0, session.stderr)
        hook_output = json.loads(session.stdout)["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "SessionStart")
        additional = hook_output["additionalContext"]
        self.assertIn("iterlog context argv (JSON)", additional)
        self.assertIn("--all-sessions", additional)
        self.assertIn(str(self.state), additional)
        self.assertIn(json.dumps(prefix, ensure_ascii=False), additional)
        self.assertIn(document["protocol_path"], additional)

    @unittest.skipIf(os.name == "nt", "parent symlink aliases require no privileges on POSIX")
    def test_parent_path_alias_preserves_capture_validation(self) -> None:
        real_parent = self.root / "real-state-parent"
        real_parent.mkdir()
        alias_parent = self.root / "state-parent-alias"
        alias_parent.symlink_to(real_parent, target_is_directory=True)
        self.state = alias_parent / "private-state"
        self.env["CODEX_ITERLOG_HOME"] = str(self.state)

        transcript = self.root / "aliased-rollout.jsonl"
        transcript.write_text('{"payload":"aliased evidence"}\n', encoding="utf-8")
        captured = self.run_tool("capture-hook", stdin=self.capture_payload(transcript))
        self.assert_capture_succeeded(captured)

        context = self.run_tool(
            "context",
            "--cwd",
            str(self.project),
            "--session-id",
            "session-123",
        )
        self.assertEqual(context.returncode, 0, context.stderr)
        captures = json.loads(context.stdout)["captures"]
        self.assertEqual(len(captures), 1)

        verified = self.run_tool("verify", "--manifest", captures[0]["manifest_path"])
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertTrue(json.loads(verified.stdout)["capture"]["content_verified"])

    @unittest.skipIf(os.name == "nt", "state-root symlinks require no privileges on POSIX")
    def test_state_root_symlink_remains_rejected(self) -> None:
        real_state = self.root / "real-private-state"
        real_state.mkdir()
        alias_state = self.root / "private-state-alias"
        alias_state.symlink_to(real_state, target_is_directory=True)
        self.env["CODEX_ITERLOG_HOME"] = str(alias_state)

        doctor = self.run_tool("doctor")
        self.assertEqual(doctor.returncode, 2)
        self.assertIn("symlink or junction", doctor.stderr)

    @unittest.skipIf(os.name == "nt", "nested symlinks require no privileges on POSIX")
    def test_nested_state_symlink_remains_rejected(self) -> None:
        outside = self.root / "outside-state"
        outside.mkdir()
        self.state.mkdir()
        (self.state / "captures").symlink_to(outside, target_is_directory=True)

        transcript = self.root / "linked-rollout.jsonl"
        transcript.write_text('{"payload":"must stay private"}\n', encoding="utf-8")
        result = self.run_tool("capture-hook", stdin=self.capture_payload(transcript))
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertFalse(response["continue"])
        self.assertIn("symlink or junction", response["stopReason"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_scoped_claude_agent_gets_context_and_unrelated_agent_does_not(self) -> None:
        self.env["ITERLOG_HOST"] = "claude"
        payload = {
            "session_id": "claude-session",
            "cwd": str(self.project),
            "hook_event_name": "SubagentStart",
            "agent_id": "agent-1",
            "agent_type": "iterlog:iterlog",
        }
        result = self.run_tool("subagent-context-hook", stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("- host: claude", context)
        self.assertIn("renderer argv prefix (JSON)", context)
        self.assertIn("no automatic pre-compact capture", context)

        payload["agent_type"] = "general-purpose"
        unrelated = self.run_tool("subagent-context-hook", stdin=payload)
        self.assertEqual(unrelated.returncode, 0, unrelated.stderr)
        self.assertEqual(unrelated.stdout, "")

    def test_host_specific_default_state_roots_are_isolated(self) -> None:
        for name in (
            "ITERLOG_HOME",
            "CODEX_ITERLOG_HOME",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "PLUGIN_ROOT",
            "CLAUDE_PLUGIN_ROOT",
        ):
            self.env.pop(name, None)

        claude_home = self.root / "claude-config"
        self.env["ITERLOG_HOST"] = "claude"
        self.env["CLAUDE_CONFIG_DIR"] = str(claude_home)
        claude = self.run_tool("doctor")
        self.assertEqual(claude.returncode, 0, claude.stderr)
        claude_payload = json.loads(claude.stdout)
        self.assertEqual(claude_payload["host"], "claude")
        self.assertEqual(
            Path(claude_payload["state_root"]),
            claude_home / "iterlog",
        )

        codex_home = self.root / "codex-config"
        self.env["ITERLOG_HOST"] = "codex"
        self.env["CODEX_HOME"] = str(codex_home)
        codex = self.run_tool("doctor")
        self.assertEqual(codex.returncode, 0, codex.stderr)
        codex_payload = json.loads(codex.stdout)
        self.assertEqual(codex_payload["host"], "codex")
        self.assertEqual(Path(codex_payload["state_root"]), codex_home / "iterlog")

    def test_cli_host_override_wins_over_conflicting_environment(self) -> None:
        self.env.pop("CODEX_HOME", None)
        self.env.pop("PLUGIN_ROOT", None)
        self.env["CLAUDE_CONFIG_DIR"] = str(self.root / "claude-config")
        self.env["ITERLOG_HOST"] = "claude"

        result = self.run_tool("--host", "codex", "doctor")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["host"], "codex")
        self.assertEqual(Path(payload["state_root"]), self.state)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_node_bridge_forwards_stdin_and_marks_host_as_claude(self) -> None:
        node = shutil.which("node")
        bridge = PLUGIN / "tools" / "run_iterlog.js"
        env = self.env.copy()
        env["ITERLOG_PYTHON"] = sys.executable
        env["ITERLOG_HOST"] = "codex"  # the bridge must override this
        payload = {
            "session_id": "桥接会话",
            "cwd": str(self.project),
            "hook_event_name": "SessionStart",
        }
        result = subprocess.run(
            [node, str(bridge), "--host", "claude", "session-context-hook"],
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("- host: claude", context)
        self.assertIn("桥接会话", context)
        self.assertIn(str(self.state), context)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_node_bridge_without_python_fails_closed_for_precompact(self) -> None:
        node = shutil.which("node")
        bridge = PLUGIN / "tools" / "run_iterlog.js"
        env = self.env.copy()
        env["PATH"] = ""
        env["ITERLOG_PYTHON"] = str(self.root / "missing-python")
        payload = {
            "session_id": "bridge-session",
            "cwd": str(self.project),
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        }
        result = subprocess.run(
            [node, str(bridge), "--host", "claude", "capture-hook"],
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["decision"], "block")
        self.assertIn("Python 3.10+", response["reason"])

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("powershell.exe"),
        "Windows PowerShell is not installed",
    )
    def test_powershell_launcher_preserves_chinese_hook_json(self) -> None:
        project = self.root / "中文项目"
        project.mkdir()
        state = self.root / "私有状态"
        launcher = PLUGIN / "tools" / "run_iterlog.ps1"
        env = self.env.copy()
        env["ITERLOG_PYTHON"] = sys.executable
        env["CODEX_ITERLOG_HOME"] = str(state)
        payload = {
            "session_id": "会话-一",
            "cwd": str(project),
            "hook_event_name": "SessionStart",
            "source": "启动",
        }
        result = subprocess.run(
            [
                shutil.which("powershell.exe"),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(launcher),
                "--host",
                "codex",
                "session-context-hook",
            ],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(project.name, context)
        self.assertIn(
            json.dumps(str(project.resolve()), ensure_ascii=False)[1:-1], context
        )
        self.assertIn("会话-一", context)
        self.assertIn(str(state), context)
        self.assertNotIn("\ufffd", result.stdout)

    def test_concurrent_duplicate_capture_converges_on_one_pair(self) -> None:
        transcript = self.root / "rollout.jsonl"
        transcript.write_text('{"payload":"same event"}\n', encoding="utf-8")
        encoded = json.dumps(self.capture_payload(transcript))
        processes = [
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "capture-hook"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=self.env,
            )
            for _ in range(4)
        ]
        for process in processes:
            stdout, stderr = process.communicate(encoded, timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(stdout, "")
        self.assertEqual(len(list(self.state.glob("captures/*/*/*.jsonl"))), 1)
        self.assertEqual(len(list(self.state.glob("captures/*/*/*.manifest.json"))), 1)

    def test_subagent_context_points_to_capture_without_copying_content(self) -> None:
        transcript = self.root / "rollout.jsonl"
        transcript.write_text('{"payload":"sensitive body"}\n', encoding="utf-8")
        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=self.capture_payload(transcript))
        )
        payload = {
            "session_id": "session-123",
            "turn_id": "agent-turn",
            "cwd": str(self.project),
            "hook_event_name": "SubagentStart",
            "agent_id": "agent-1",
            "agent_type": "iterlog",
            "permission_mode": "default",
            "model": "test-model",
        }
        result = self.run_tool("subagent-context-hook", stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("capture_id=", context)
        self.assertIn("manifest=", context)
        self.assertNotIn("snapshot=", context)
        self.assertNotIn("sensitive body", context)
        self.assertNotIn(str(self.project / ".codex"), context)

        manifest = next(self.state.glob("captures/*/*/*.manifest.json"))
        verified = self.run_tool("verify", "--manifest", str(manifest))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        verified_payload = json.loads(verified.stdout)
        self.assertTrue(verified_payload["capture"]["content_verified"])
        self.assertTrue(Path(verified_payload["capture"]["raw_snapshot_path"]).is_file())

    def test_iterlog_context_can_aggregate_captures_across_project_sessions(self) -> None:
        first = self.root / "session-one.jsonl"
        second = self.root / "session-two.jsonl"
        first.write_text('{"work":"one"}\n', encoding="utf-8")
        second.write_text('{"work":"two"}\n', encoding="utf-8")
        first_payload = self.capture_payload(first)
        first_payload.update({"session_id": "session-one", "turn_id": "turn-one"})
        second_payload = self.capture_payload(second)
        second_payload.update({"session_id": "session-two", "turn_id": "turn-two"})
        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=first_payload)
        )
        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=second_payload)
        )

        current_only = self.run_tool(
            "context",
            "--cwd",
            str(self.project),
            "--session-id",
            "session-one",
        )
        self.assertEqual(len(json.loads(current_only.stdout)["captures"]), 1)

        all_sessions = self.run_tool(
            "context",
            "--cwd",
            str(self.project),
            "--all-sessions",
            "--limit",
            "64",
        )
        self.assertEqual(all_sessions.returncode, 0, all_sessions.stderr)
        document = json.loads(all_sessions.stdout)
        self.assertEqual(document["capture_scope"], "all-project-sessions")
        self.assertEqual(len(document["captures"]), 2)

    def test_relative_state_root_is_rejected_before_project_write(self) -> None:
        transcript = self.root / "rollout.jsonl"
        transcript.write_text("opaque\n", encoding="utf-8")
        self.env["CODEX_ITERLOG_HOME"] = "relative-state"
        result = self.run_tool("capture-hook", stdin=self.capture_payload(transcript), cwd=self.project)
        response = json.loads(result.stdout)
        self.assertFalse(response["continue"])
        self.assertFalse((self.project / "relative-state").exists())

    def test_render_writes_only_private_state_and_redacts_secrets(self) -> None:
        transcript = self.root / "render-rollout.jsonl"
        transcript.write_text('{"payload":"evidence"}\n', encoding="utf-8")
        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=self.capture_payload(transcript))
        )
        capture_id = json.loads(
            next(self.state.glob("captures/*/*/*.manifest.json")).read_text(encoding="utf-8")
        )["capture_id"]
        draft = {
            "schema_version": 1,
            "title": "认证模块迭代复盘",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "scope": {"kind": "current-project", "description": "当前认证项目"},
            "coverage": {"status": "partial", "summary": "当前会话与一个快照", "gaps": ["短会话可能缺失"]},
            "summary": "完成认证刷新逻辑并记录验证结果。",
            "completed": [{"item": "修复认证刷新", "outcome": "请求恢复", "verification": "passed", "confidence": "verified", "evidence": ["test: smoke passed"]}],
            "in_progress": [{"item": "增加到期预警", "progress": "已设计", "remaining": "实现告警", "next_step": "补充监控", "confidence": "recorded", "evidence": ["conversation: 明确待办"]}],
            "blockers": [],
            "risks": ["预警尚未实现"],
            "changes": [{"path_or_component": "credential provider", "change": "刷新 token", "reason": "恢复认证", "impact": "认证请求恢复", "confidence": "verified", "evidence": ["change-1"]}],
            "decisions": [{"decision": "采用主动刷新", "reason": "减少失败", "impact": "认证路径", "confidence": "recorded", "evidence": ["conversation: decision"]}],
            "verification": [{"check": "smoke", "status": "passed", "result": "pass", "coverage": "认证路径", "evidence": "test output"}],
            "improvement_suggestions": [{"statement": "为刷新失败补充可观测指标", "confidence": "inferred", "evidence": ["risk: 预警尚未实现"]}],
            "next_day_plan": ["实现到期预警"],
            "reflection": [{"statement": "先补最小 smoke 验证能缩短认证链路排查时间", "confidence": "recorded", "evidence": ["test: smoke passed"]}],
            "evidence_gaps": ["未执行全量回归"],
            "evidence_refs": [
                "Bearer abcdefghijklmnopqrstuvwxyz", "sk-abcdefghijklmnop123456",
                "Authorization: Basic dXNlcjpwYXNz", "github_pat_abcdefghijklmnopqrstuvwxyz123456",
                "xoxb-123456789012-abcdefghijkl", "user@example.com",
            ],
            "source_capture_ids": [capture_id],
        }
        draft_path = self.root / "draft.json"
        draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool(
            "render", "--input", str(draft_path), "--delete-input", "--cwd", str(self.project)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["report_date"], "2026-07-20")
        self.assertEqual(response["coverage_status"], "partial")
        self.assertTrue(response["report_id"].startswith("ITL-20260720-"))
        report = Path(response["report_path"])
        self.assertTrue(report.is_file())
        self.assertTrue(report.is_relative_to(self.state))
        self.assertFalse(any(self.project.iterdir()))
        text = report.read_text(encoding="utf-8")
        for heading in (
            "## 1. 日期、范围与证据覆盖", "## 2. 开发概览", "## 3. 已完成修改与成果",
            "## 4. 进行中的修改与优化", "## 5. 阻塞与风险", "## 6. 关键变更、决策与验证",
            "## 7. 改进建议与下一步", "## 8. 反思、证据缺口与待确认", "## 9. 证据索引",
        ):
            self.assertIn(heading, text)
        self.assertEqual(text.count("\n## "), 9)
        self.assertIn("为刷新失败补充可观测指标", text)
        self.assertIn("先补最小 smoke 验证能缩短认证链路排查时间", text)
        self.assertEqual(report.parent.name, "2026-07-20")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", text)
        self.assertNotIn("sk-abcdefghijklmnop123456", text)
        self.assertNotIn("dXNlcjpwYXNz", text)
        self.assertNotIn("github_pat_", text)
        self.assertNotIn("xoxb-", text)
        self.assertNotIn("user@example.com", text)
        self.assertIn("<redacted:credential>", text)
        self.assertFalse(draft_path.exists())

    def test_empty_iterlog_and_missing_changes_are_explicit(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "空复盘",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "empty", "summary": "没有可用快照", "gaps": ["未发生自动 compact"]},
            "completed": [],
            "changes": [],
            "evidence_gaps": ["当前证据不足"],
        }
        path = self.root / "draft.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project), "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("当前证据范围内未发现可确认的已完成事项", result.stdout)
        self.assertIn("当前证据范围内未发现可归因变更", result.stdout)
        self.assertIn("当前证据不足", result.stdout)

    def test_completed_work_requires_evidence(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "无证据完成项",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
            "completed": [{"item": "声称已完成", "confidence": "verified", "evidence": []}],
        }
        path = self.root / "invalid.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("completed[0] requires evidence", result.stderr)

    def test_completed_verification_uses_status_not_confidence_enum(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "验证状态错误",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
            "completed": [
                {
                    "item": "完成修改",
                    "verification": "verified",
                    "confidence": "verified",
                    "evidence": ["test output"],
                }
            ],
        }
        path = self.root / "invalid-completed-verification.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("completed[0].verification is invalid", result.stderr)

    def test_suggestions_and_reflection_require_structured_evidence(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "无证据建议",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
            "improvement_suggestions": ["凭空建议重构"],
        }
        path = self.root / "invalid-suggestion.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("improvement_suggestions[0] must be an object", result.stderr)

        draft["improvement_suggestions"] = []
        draft["reflection"] = [
            {"statement": "缺少证据的反思", "confidence": "recorded", "evidence": []}
        ]
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("reflection[0] requires evidence", result.stderr)

    def test_invalid_report_date_is_rejected(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "日期错误",
            "report_date": "2026-02-30",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
        }
        path = self.root / "invalid-date.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("valid YYYY-MM-DD", result.stderr)

    def test_key_decision_requires_evidence(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "无证据决策",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
            "decisions": [
                {
                    "decision": "采用新方案",
                    "confidence": "recorded",
                    "evidence": [],
                }
            ],
        }
        path = self.root / "invalid-decision.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("decisions[0] requires evidence", result.stderr)

    def test_complete_coverage_requires_capture_and_no_gaps(self) -> None:
        without_capture = {
            "schema_version": 1,
            "title": "错误完整覆盖",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "complete", "gaps": []},
        }
        path = self.root / "complete-without-capture.json"
        path.write_text(json.dumps(without_capture, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("complete coverage requires", result.stderr)

        with_gap = dict(without_capture)
        with_gap["coverage"] = {"status": "complete", "gaps": ["一个会话未捕获"]}
        with_gap["source_capture_ids"] = ["synthetic-capture-id"]
        path.write_text(json.dumps(with_gap, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot contain evidence gaps", result.stderr)

    def test_unknown_source_capture_id_is_rejected(self) -> None:
        draft = {
            "schema_version": 1,
            "title": "伪造证据引用",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
            "source_capture_ids": ["not-a-real-capture"],
        }
        path = self.root / "bad-capture.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        result = self.run_tool("render", "--input", str(path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("source_capture_ids not found", result.stderr)

    def test_render_reverifies_referenced_capture_digest(self) -> None:
        transcript = self.root / "rollout.jsonl"
        transcript.write_bytes(b"trusted evidence\n")
        self.assert_capture_succeeded(
            self.run_tool("capture-hook", stdin=self.capture_payload(transcript))
        )
        manifest_path = next(self.state.glob("captures/*/*/*.manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        Path(manifest["raw_snapshot_path"]).write_bytes(b"tampered evidence\n")
        draft = {
            "schema_version": 1,
            "title": "tamper check",
            "report_date": "2026-07-20",
            "timezone": "Asia/Shanghai",
            "coverage": {"status": "partial", "gaps": []},
            "source_capture_ids": [manifest["capture_id"]],
        }
        draft_path = self.root / "tampered.json"
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        result = self.run_tool("render", "--input", str(draft_path), "--cwd", str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertIn("source_capture_ids not found", result.stderr)

    def test_runtime_doctor_rejects_invalid_retention(self) -> None:
        self.env["CODEX_ITERLOG_RETENTION_DAYS"] = "-1"
        result = self.run_tool("doctor", cwd=self.project)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be >= 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
