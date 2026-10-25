import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project_bridge", ROOT / "bridge.py")
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-bridge-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "项目 with spaces & symbols"
        self.project.mkdir()
        self.hub = self.base / "hub"
        self.hub.mkdir()
        bridge.atomic_json(self.hub / "projects.json", {"projects": {"sample": {
            "windows": str(self.project), "wsl": str(self.project), "label": "测试项目"}}})
        (self.hub / "COMMON.md").write_text("Shared protocol 测试", encoding="utf-8")
        self.instance = bridge.Bridge(self.hub)
        self.fake = self.base / "fake_cli.py"
        self.fake.write_text(
            "import os,sys,time\n"
            "from pathlib import Path\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "text=sys.stdin.buffer.read().decode('utf-8')\n"
            "if 'MAKE_TIMEOUT' in text: time.sleep(10)\n"
            "if 'MAKE_FAILURE' in text: print('fixture error',file=sys.stderr); sys.exit(7)\n"
            "if 'EMPTY_RESULT' in text: sys.exit(0)\n"
            "answer='RESULT 中文\\nCWD='+os.getcwd()+'\\n'+text\n"
            "if '--output-last-message' in sys.argv:\n"
            "    Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(answer,encoding='utf-8')\n"
            "print(answer)\n", encoding="utf-8")

    def engine(self, name):
        return bridge.Engine(name, [sys.executable, str(self.fake)])

    def task(self, tool="pi", prompt="检查项目 & echo 不得作为命令执行", intent="review"):
        return self.instance.create("sample", tool, prompt, intent=intent)

    def run_task(self, task, timeout=10):
        with patch.object(bridge, "detect_engine", self.engine):
            return self.instance.run(task["id"], timeout)

    def test_new_task_is_queued_without_calling_tool(self):
        with patch.object(bridge, "detect_engine", side_effect=AssertionError("no invocation")):
            task = self.task()
        self.assertEqual(task["status"], "queued")
        self.assertFalse((self.instance.folder(task["id"]) / "stdout.log").exists())
        self.assertIn("检查项目", (self.instance.folder(task["id"]) / "TASK.md").read_text(encoding="utf-8"))

    def test_actual_child_uses_canonical_unicode_directory_and_utf8_stdin(self):
        task = self.task()
        result = self.run_task(task)
        self.assertEqual(result["status"], "succeeded")
        text = (self.instance.folder(task["id"]) / "result.md").read_text(encoding="utf-8")
        self.assertIn("CWD=" + str(self.project), text)
        self.assertIn("检查项目 & echo 不得作为命令执行", text)
        self.assertEqual(list((self.hub / "state/locks").glob("*.json")), [])

    def test_codex_final_message_is_read_from_dedicated_output_file(self):
        task = self.task("codex")
        result = self.run_task(task)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue((self.instance.folder(task["id"]) / "result.md").is_file())

    def test_dsh_stdin_headless_adapter(self):
        task = self.task("dsh")
        result = self.run_task(task)
        self.assertEqual(result["status"], "succeeded")

    def test_failed_process_is_not_marked_successful(self):
        task = self.task(prompt="MAKE_FAILURE")
        result = self.run_task(task)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertFalse((self.instance.folder(task["id"]) / "result.md").exists())

    def test_empty_response_is_failure(self):
        task = self.task(prompt="EMPTY_RESULT")
        result = self.run_task(task)
        self.assertEqual(result["status"], "failed")

    def test_timeout_stops_child_releases_lock_and_records_failure(self):
        task = self.task(prompt="MAKE_TIMEOUT")
        result = self.run_task(task, timeout=1)
        self.assertEqual(result["status"], "failed")
        self.assertIn("TimeoutExpired", result["error"])
        self.assertEqual(list((self.hub / "state/locks").glob("*.json")), [])

    def test_project_lock_rejects_other_task_before_running(self):
        first, second = self.task(), self.task("dsh")
        with self.instance.lock(first):
            with patch.object(bridge, "detect_engine", self.engine), self.assertRaises(bridge.BridgeError):
                self.instance.run(second["id"])
        self.assertEqual(self.instance.load(second["id"])["status"], "queued")

    def test_handoff_carries_previous_result_and_does_not_execute(self):
        parent = self.task()
        self.run_task(parent)
        child = self.instance.handoff(parent["id"], "codex", "核对上一轮结论")
        self.assertEqual(child["status"], "queued")
        self.assertEqual(child["origin"], "pi")
        self.assertIn("RESULT 中文", self.instance.prompt(child))
        self.assertFalse((self.instance.folder(child["id"]) / "result.md").exists())

    def test_active_task_cannot_be_handed_off(self):
        task = self.task()
        with self.assertRaises(bridge.BridgeError):
            self.instance.handoff(task["id"], "dsh", "next")

    def test_no_duplicate_run_or_traversal(self):
        task = self.task()
        self.run_task(task)
        with patch.object(bridge, "detect_engine", self.engine), self.assertRaises(bridge.BridgeError):
            self.instance.run(task["id"])
        for task_id in ("../other", "x/../../other", "C:\\other", ""):
            with self.assertRaises(bridge.BridgeError):
                self.instance.load(task_id)

    def test_plan_constructs_commands_without_running(self):
        task = self.task("dsh")
        with patch.object(bridge, "detect_engine", self.engine), patch.object(bridge.subprocess, "Popen", side_effect=AssertionError("must not start")):
            plan = self.instance.plan(task["id"])
        self.assertFalse(plan["will_call_model"])
        self.assertIn("--profile", plan["command"])
        self.assertEqual(plan["command"][plan["command"].index("--profile") + 1], "headless")
        self.assertEqual(Path(plan["command"][plan["command"].index("--patch") + 1]).name, "dsh-account.patch.yml")
        self.assertEqual(plan["command"][-1], "-")

    def test_review_mode_codex_and_pi_flags_preserve_boundaries(self):
        result = self.hub / "result.md"
        codex = bridge.command_for(self.engine("codex"), "review", result)
        self.assertIn("read-only", codex)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex)
        pi = bridge.command_for(self.engine("pi"), "review", result)
        self.assertIn("--no-extensions", pi)
        self.assertIn("--no-mcp", pi)
        self.assertIn("read,grep,find,ls", pi)

    def test_permission_denied_is_distinguished_from_not_installed(self):
        with patch.object(Path, "stat", side_effect=PermissionError("sandbox")):
            with self.assertRaisesRegex(bridge.BridgeError, "Access denied"):
                bridge.installed_file(self.base / "installed.exe")
        self.assertFalse(bridge.installed_file(self.base / "absent.exe"))

    def test_task_captures_default_model_and_later_settings_do_not_change_it(self):
        profiles = bridge.ModelProfiles(self.hub)
        profiles.save("codex", {"mode": "override", "model": "demo-first", "reasoning": "high"})
        task = self.task("codex")
        profiles.save("codex", {"mode": "override", "model": "demo-second"})
        with patch.object(bridge, "detect_engine", self.engine):
            plan = self.instance.plan(task["id"])
        self.assertEqual(plan["model_config"]["model"], "demo-first")
        self.assertIn("demo-first", plan["command"])
        self.assertIn('model_reasoning_effort="high"', plan["command"])
        self.assertIn("read-only", plan["command"])

    def test_explicit_task_model_and_cross_tool_handoff_defaults(self):
        task = self.instance.create("sample", "pi", "test", model_config={"mode": "override", "model": "pi-demo", "provider": "example", "reasoning": "high"})
        self.run_task(task)
        bridge.ModelProfiles(self.hub).save("codex", {"mode": "override", "model": "target-demo"})
        child = self.instance.handoff(task["id"], "codex", "next")
        self.assertEqual(child["model_config"]["model"], "target-demo")
        with patch.object(bridge, "detect_engine", self.engine):
            plan = self.instance.plan(task["id"])
        self.assertIn("--provider", plan["command"])
        self.assertIn("--thinking", plan["command"])
        self.assertIn("--no-extensions", plan["command"])

    def test_dsh_model_patch_is_task_local_and_contains_no_credentials(self):
        task = self.instance.create("sample", "dsh", "test", model_config={"mode": "override", "model": "account-demo"})
        self.run_task(task)
        overlay = json.loads((self.instance.folder(task["id"]) / "model.patch.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay, [{"id": "agent-default-model", "config": {"provider": "deepseek-account", "model": "account-demo"}}])
        with patch.object(bridge, "detect_engine", self.engine):
            command = self.instance.plan(task["id"])["command"]
        self.assertEqual(command.count("--patch"), 2)

    def test_model_validation_precedes_task_directory_creation(self):
        with self.assertRaises(ValueError):
            self.instance.create("sample", "codex", "test", model_config={"mode": "override", "model": "demo", "api_key": "not-allowed"})
        self.assertFalse((self.hub / "tasks").exists())


if __name__ == "__main__":
    unittest.main()
