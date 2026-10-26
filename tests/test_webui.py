import http.client
import json
import shutil
import subprocess
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge
import webui
import test_bridge as fixture_helpers


class WebUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for client recovery verification")
    def test_client_reconnects_once_without_duplicate_dispatch_or_retrying_other_errors(self):
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('web/app.js', 'utf8');
const helper = source.slice(source.indexOf('async function api('), source.indexOf('\nfunction toast('));
(async () => {
  for (const scenario of ['recover', 'origin', 'repeat', 'network']) {
    let calls = 0, dispatched = 0, pages = 0;
    const context = {
      DOMParser: class { parseFromString() { return {querySelector: () => ({content: 'B'.repeat(43)})}; } },
      fetch: async (path, options) => {
        if (path === '/') { pages++; return {ok: true, text: async () => 'fresh page'}; }
        calls++;
        if (scenario === 'network') throw new Error('connection lost');
        const fresh = options.headers['X-Bridge-Token'] === 'B'.repeat(43);
        if (fresh && scenario === 'recover') {
          dispatched++;
          assert.deepEqual(JSON.parse(options.body), {run: true});
          return {ok: true, status: 200, json: async () => ({accepted: true})};
        }
        return {ok: false, status: 403, json: async () => ({error: scenario === 'origin' ? '请求来源不匹配。' : '页面会话已更新，请刷新页面后重试。'})};
      }
    };
    vm.createContext(context);
    vm.runInContext('let token="'+'A'.repeat(43)+'"; let tokenRefresh=null;\n'+helper, context);
    const run = vm.runInContext('api("/api/tasks", {run: true})', context);
    if (scenario === 'recover') { await run; assert.equal(dispatched, 1); assert.equal(calls, 2); assert.equal(pages, 1); }
    else { await assert.rejects(run); assert.equal(dispatched, 0); assert.equal(calls, scenario === 'repeat' ? 2 : 1); assert.equal(pages, scenario === 'repeat' ? 1 : 0); }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))

    def setUp(self):
        self.fixture = fixture_helpers.BridgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.hub
        self.app = webui.App(self.root, probe=False)
        self.server = webui.Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.patches = [patch.object(bridge, "detect_engine", self.fixture.engine), patch.object(webui, "detect_engine", self.fixture.engine)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def shutdown(self):
        for job in list(self.app.jobs.values()):
            job.cancel.set()
        for job in list(self.app.jobs.values()):
            job.thread.join(timeout=5)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, body=None, *, token=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        outgoing = {"Content-Type": "application/json"}
        if token:
            outgoing["X-Bridge-Token"] = self.server.token
        outgoing.update(headers or {})
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        connection.request("POST" if body is not None else "GET", path, body=raw, headers=outgoing)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        content_type = response.getheader("Content-Type", "")
        connection.close()
        return status, json.loads(data) if "application/json" in content_type else data

    def create(self, **extra):
        status, data = self.request("/api/tasks", {"project": "sample", "tool": "pi", "prompt": "检查中文路径与结果", "title": "网页任务", **extra})
        self.assertEqual(status, 200, data)
        return data["task"]

    def wait_task(self, task_id, statuses=("succeeded", "failed", "interrupted")):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            _, result = self.request("/api/tasks/" + task_id)
            if result["task"]["status"] in statuses:
                return result
            time.sleep(0.03)
        self.fail("Task did not reach expected status")

    def test_page_and_health_work_without_token_but_api_requires_it(self):
        status, page = self.request("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), page)
        self.assertNotIn(b"__BRIDGE_TOKEN__", page)
        self.assertEqual(self.request("/api/health", token=False)[0], 200)
        self.assertEqual(self.request("/api/bootstrap", token=False)[0], 403)
        self.assertEqual(self.request("/api/bootstrap")[0], 200)

    def test_launcher_does_not_reuse_another_hubs_server(self):
        port = self.server.server_port
        self.assertTrue(webui.health(port))
        self.assertFalse(webui.health(port, self.root))
        bridge.atomic_json(self.root / "state/webui.json", {"port": port, "pid": webui.os.getpid()})
        self.assertTrue(webui.health(port, self.root))
        self.assertFalse(webui.health(port, self.fixture.base / "new-clean-copy"))
        bridge.atomic_json(self.root / "state/webui.json", {"port": port, "pid": -1})
        self.assertFalse(webui.health(port, self.root))

    def test_wrong_host_and_cross_origin_are_rejected(self):
        self.assertEqual(self.request("/api/bootstrap", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/tasks", {}, headers={"Origin": "https://attacker.example"})[0], 403)

    def test_paths_cannot_read_arbitrary_files(self):
        for path in ("/../projects.json", "/%2e%2e/projects.json", "/api/tasks/bad%2Fpath", "/api/tasks/bad/download/extra"):
            self.assertIn(self.request(path)[0], (400, 404))

    def test_new_task_does_not_call_model(self):
        task = self.create()
        self.assertEqual(task["status"], "queued")
        self.assertFalse((self.root / "tasks" / task["id"] / "stdout.log").exists())

    def test_workflow_api_pins_new_stage_and_review_retry_preserves_executor(self):
        status, result = self.request("/api/workflows/sample", {"primary_tool": "pi", "reviewer_tool": "codex", "primary_model": {"mode": "override", "model": "pinned-demo"}, "scope": "只改测试", "acceptance": "离线回归通过"})
        self.assertEqual(status, 200, result)
        status, result = self.request("/api/tasks", {"project": "sample", "prompt": "新增边界测试", "intent": "work", "title": "接续 · 重试 · 边界测试"})
        self.assertEqual(status, 200, result)
        parent = result["task"]
        self.assertEqual(parent["tool"], "pi")
        self.assertEqual(parent["model_config"]["model"], "pinned-demo")
        parent["status"] = "succeeded"
        self.app.bridge().save(parent)
        status, review = self.request(f"/api/tasks/{parent['id']}/handoff", {"handoff_mode": "review", "intent": "review", "instruction": "只审查边界测试", "handoff_card": {"completed": "已新增测试", "evidence": "待核对日志"}})
        self.assertEqual(status, 200, review)
        task = review["task"]
        self.assertEqual(task["tool"], "codex")
        self.assertEqual(task["intent"], "review")
        self.assertEqual(task["title"], "审查 · 边界测试")
        task["status"] = "failed"
        self.app.bridge().save(task)
        status, retried = self.request(f"/api/tasks/{task['id']}/retry", {})
        self.assertEqual(status, 200, retried)
        self.assertEqual(retried["task"]["tool"], "codex")
        self.assertEqual(retried["task"]["contract"]["executor_tool"], "pi")
        status, continued = self.request(f"/api/tasks/{task['id']}/handoff", {"handoff_mode": "continue", "instruction": "完成剩余测试"})
        self.assertEqual(status, 200, continued)
        self.assertEqual(continued["task"]["tool"], "pi")
        self.assertEqual(continued["task"]["model_config"]["model"], "pinned-demo")
        self.assertEqual(continued["task"]["intent"], "work")
        self.assertEqual(continued["task"]["contract"]["acceptance"], "离线回归通过")
        self.assertTrue(self.request("/api/bootstrap")[1]["workflows"]["sample"])
        self.assertTrue(self.request(f"/api/tasks/{task['id']}")[1]["handoff_card"])

    def test_workflow_and_card_api_reject_invalid_authorization_fields_without_new_tasks(self):
        self.assertEqual(self.request("/api/workflows/sample", {"primary_tool": "pi", "api_key": "forbidden"})[0], 400)
        parent = self.create()
        parent["status"] = "succeeded"
        self.app.bridge().save(parent)
        before = len(list((self.root / "tasks").glob("*/task.json")))
        status, result = self.request(f"/api/tasks/{parent['id']}/handoff", {"instruction": "继续", "handoff_mode": "continue", "handoff_card": {"scope": "扩大权限"}})
        self.assertEqual(status, 400, result)
        self.assertEqual(len(list((self.root / "tasks").glob("*/task.json"))), before)

    def test_saved_tasks_and_cached_sessions_remain_visible_during_slow_sync(self):
        task = self.create()
        record = {"id": "cold-session", "key": "pi-cold", "source": "pi", "project": "sample",
                  "title": "saved session", "state": "unknown", "cached": True, "stale": True}
        self.app.catalog.sessions[record["key"]] = record
        entered, release = threading.Event(), threading.Event()
        def slow_refresh(config):
            entered.set()
            release.wait(4)
        with patch.object(self.app.catalog, "refresh", slow_refresh):
            try:
                self.app.refresh_sources()
                self.assertTrue(entered.wait(1))
                status, data = self.request("/api/bootstrap")
                self.assertEqual(status, 200)
                self.assertTrue(data["syncing"])
                self.assertTrue(data["sources_cached"])
                self.assertIn(task["id"], {t["id"] for t in data["tasks"]})
                self.assertIn(record["key"], {s["key"] for s in data["sessions"]})
                status, result = self.request("/api/sessions/pi-cold/handoff", {"instruction": "continue", "tool": "pi", "run": False})
                self.assertEqual(status, 400)
                self.assertIn("正文", result["error"])
            finally:
                release.set()
        self.assertFalse((self.root / "tasks" / task["id"] / "stdout.log").exists())

    def test_fast_codex_probe_is_published_while_other_tools_are_still_checking(self):
        release, slow_started = threading.Event(), threading.Event()
        def engine(name):
            if name != "codex":
                slow_started.set()
                release.wait(4)
            return self.fixture.engine(name)
        try:
            with patch.object(webui, "detect_engine", engine):
                self.app.refresh_tools()
                self.assertTrue(slow_started.wait(1))
                deadline = time.monotonic() + 2
                while self.app.tools["codex"].get("checking") and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(self.app.tools["codex"]["available"])
                self.assertFalse(self.app.tools["codex"]["checking"])
                self.assertTrue(self.app.checking)
                self.assertTrue(self.app.tools["pi"]["checking"])
        finally:
            release.set()
            deadline = time.monotonic() + 3
            while self.app.checking and time.monotonic() < deadline:
                time.sleep(.02)

    def test_create_and_execute_returns_result_and_download(self):
        task = self.create(run=True)
        detail = self.wait_task(task["id"])
        self.assertEqual(detail["task"]["status"], "succeeded")
        self.assertIn("RESULT 中文", detail["result"])
        self.assertIn("CWD=" + str(self.fixture.project), detail["result"])
        status, raw = self.request(f"/api/tasks/{task['id']}/download")
        self.assertEqual(status, 200)
        self.assertIn("RESULT 中文".encode("utf-8"), raw)

    def test_running_task_can_be_stopped_and_lock_is_released(self):
        task = self.create(prompt="MAKE_TIMEOUT", run=True)
        self.wait_task(task["id"], ("running",))
        self.assertEqual(self.request(f"/api/tasks/{task['id']}/stop", {})[0], 200)
        detail = self.wait_task(task["id"])
        self.assertEqual(detail["task"]["status"], "interrupted")
        self.assertEqual(list((self.root / "state/locks").glob("*.json")), [])

    def test_duplicate_project_execution_is_rejected(self):
        first = self.create(prompt="MAKE_TIMEOUT", run=True)
        second = self.create()
        self.assertEqual(self.request(f"/api/tasks/{second['id']}/run", {})[0], 400)
        self.assertEqual(self.request(f"/api/tasks/{first['id']}/run", {})[0], 400)
        self.request(f"/api/tasks/{first['id']}/stop", {})
        self.wait_task(first["id"])

    def test_handoff_is_queued_with_parent_result(self):
        parent = self.create(run=True)
        self.wait_task(parent["id"])
        status, data = self.request(f"/api/tasks/{parent['id']}/handoff", {"tool": "codex", "instruction": "复核上轮结果", "intent": "review"})
        self.assertEqual(status, 200)
        task = data["task"]
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["parent"], parent["id"])
        self.assertIn("RESULT 中文", self.app.bridge().prompt(task))

    def test_failed_job_can_be_retried_without_overwriting_history(self):
        parent = self.create(prompt="MAKE_FAILURE", run=True)
        self.assertEqual(self.wait_task(parent["id"])["task"]["status"], "failed")
        status, data = self.request(f"/api/tasks/{parent['id']}/retry", {})
        self.assertEqual(status, 200)
        self.assertNotEqual(data["task"]["id"], parent["id"])
        self.assertEqual(data["task"]["status"], "queued")
        self.assertEqual(self.app.bridge().load(parent["id"])["status"], "failed")

    def test_mapping_can_be_added_and_renamed(self):
        other = self.fixture.base / "另一个 项目"
        other.mkdir()
        status, result = self.request("/api/projects", {"id": "other", "label": "新项目", "path": str(other)})
        if sys.platform != "win32":
            self.assertEqual(status, 400)
            return
        self.assertEqual(status, 200, result)
        self.assertTrue(result["project"]["wsl"].startswith("/mnt/"))
        self.assertEqual(self.request("/api/projects", {"id": "other", "label": "重命名", "path": str(other)})[0], 200)

    def test_mapping_change_is_blocked_for_pending_tasks(self):
        self.create()
        other = self.fixture.base / "other"
        other.mkdir()
        status, _ = self.request("/api/projects", {"id": "sample", "label": "变更项目", "path": str(other)})
        self.assertEqual(status, 400)

    def test_user_text_is_returned_as_data_and_invalid_run_type_is_rejected(self):
        prompt = '<img src=x onerror="alert(1)">\n中文任务'
        task = self.create(prompt=prompt)
        self.assertEqual(self.request("/api/tasks/" + task["id"])[1]["request"], prompt)
        status, _ = self.request("/api/tasks", {"project": "sample", "tool": "pi", "prompt": "test", "run": "false"})
        self.assertEqual(status, 400)

    def test_cancelling_queued_task_does_not_start_tool(self):
        task = self.create()
        status, _ = self.request(f"/api/tasks/{task['id']}/stop", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.bridge().load(task["id"])["status"], "interrupted")
        self.assertFalse((self.root / "tasks" / task["id"] / "stdout.log").exists())

    def test_imported_markdown_headings_are_preserved(self):
        prompt = "# 导入说明\n\n## Reference files\n\n这里是文档正文，不是元数据。\n\n继续保留这一段。"
        task = self.create(prompt=prompt)
        self.assertEqual(self.request("/api/tasks/" + task["id"])[1]["request"], prompt)

    def external(self, state="idle"):
        record = {"key": "pi-existing", "id": "pi-native-id", "source": "pi", "title": "已在 Pi 完成", "cwd": str(self.fixture.project),
                  "canonical_path": str(self.fixture.project), "project": "pi-project", "mapped_project": "sample", "state": state,
                  "request": "原有要求", "result": "实际 Pi 答复 中文", "record_path": "fixture-session.jsonl"}
        self.app.catalog.sessions[record["key"]] = record
        return record

    def test_external_completion_keeps_failed_invocation_and_blocks_retry(self):
        parent = self.create(prompt="MAKE_FAILURE", run=True)
        self.wait_task(parent["id"])
        _, retry = self.request(f"/api/tasks/{parent['id']}/retry", {})
        self.external()
        status, data = self.request(f"/api/tasks/{retry['task']['id']}/resolve", {"session_key": "pi-existing", "note": "用户已完成并验收", "related": True})
        self.assertEqual(status, 200, data)
        self.assertEqual(set(data["resolved"]), {parent["id"], retry["task"]["id"]})
        detail = self.request(f"/api/tasks/{parent['id']}")[1]
        self.assertEqual(detail["task"]["status"], "failed")
        self.assertEqual(detail["task"]["display_status"], "completed_external")
        self.assertIn("实际 Pi 答复 中文", detail["external_result"])
        self.assertEqual(self.request(f"/api/tasks/{parent['id']}/retry", {})[0], 400)
        self.assertEqual(self.request(f"/api/tasks/{retry['task']['id']}/run", {})[0], 400)
        _, next_step = self.request(f"/api/tasks/{parent['id']}/handoff", {"tool": "codex", "instruction": "下一步复核"})
        self.assertIn("实际 Pi 答复 中文", self.app.bridge().prompt(next_step["task"]))

    def test_resolving_retry_family_does_not_complete_a_different_next_step(self):
        parent = self.create(prompt="MAKE_FAILURE", run=True)
        self.wait_task(parent["id"])
        _, next_step = self.request(f"/api/tasks/{parent['id']}/handoff", {"tool": "codex", "instruction": "不同的下一步"})
        self.external()
        self.request(f"/api/tasks/{parent['id']}/resolve", {"session_key": "pi-existing", "note": "完成原任务", "related": True})
        self.assertNotIn("resolution", self.app.bridge().load(next_step["task"]["id"]))

    def test_existing_session_handoff_queues_context_without_model_call(self):
        self.external()
        status, result = self.request("/api/sessions/pi-existing/handoff", {"tool": "codex", "instruction": "继续复核"})
        self.assertEqual(status, 200, result)
        task = result["task"]
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["project"], "sample")
        self.assertIn("实际 Pi 答复 中文", self.app.bridge().prompt(task))
        self.assertFalse((self.root / "tasks" / task["id"] / "stdout.log").exists())

    def test_missing_credentials_are_not_reported_as_ready_and_fail_before_launch(self):
        failed = self.create(tool="dsh", prompt="MAKE_FAILURE", run=True)
        self.wait_task(failed["id"])
        (self.root / "tasks" / failed["id"] / "stderr.log").write_text("dsh: MISSING_CREDENTIAL: no API key", encoding="utf-8")
        snapshot = self.request("/api/bootstrap")[1]
        self.assertFalse(snapshot["tools"]["dsh"]["execution_ready"])
        queued = self.create(tool="dsh")
        self.assertEqual(self.request(f"/api/tasks/{queued['id']}/run", {})[0], 400)
        self.assertFalse((self.root / "tasks" / queued["id"] / "stdout.log").exists())
        self.assertEqual(self.request("/api/tools/dsh/acknowledge", {})[0], 200)
        self.assertNotIn("execution_ready", self.request("/api/bootstrap")[1]["tools"]["dsh"])

    def test_open_source_turn_can_queue_handoff_but_not_run_it(self):
        self.external(state="turn_open")
        body = {"tool": "codex", "instruction": "继续复核", "run": True}
        self.assertEqual(self.request("/api/sessions/pi-existing/handoff", body)[0], 400)
        body["run"] = False
        self.assertEqual(self.request("/api/sessions/pi-existing/handoff", body)[0], 200)

    def test_unknown_session_id_cannot_select_an_arbitrary_path(self):
        self.assertEqual(self.request("/api/sessions/unknown")[0], 400)
        self.assertEqual(self.request("/api/sessions/..%2Fauth.json")[0], 400)

    def test_native_session_from_our_own_invocation_is_merged(self):
        task = self.create()
        self.external()["bridge_task_id"] = task["id"]
        snapshot = self.request("/api/bootstrap")[1]
        self.assertEqual(len(snapshot["tasks"]), 1)
        self.assertEqual(snapshot["sessions"], [])

    def test_weekly_maintenance_is_separate_from_task_workbench(self):
        self.assertEqual(self.request("/api/maintenance")[0], 200)
        status, report = self.request("/api/maintenance/run", {})
        self.assertEqual(status, 200, report)
        self.assertEqual(report["moved_files"], 0)
        self.assertEqual(self.request("/api/bootstrap")[1]["tasks"], [])
        self.assertEqual(len(self.request("/api/maintenance")[1]["history"]), 1)
        self.assertEqual(self.request("/api/maintenance/settings", {"enabled": False})[0], 200)
        self.assertFalse(self.app.maintenance.due())

    def test_discarded_project_and_tasks_are_hidden_and_can_be_restored(self):
        task = self.create()
        self.request(f"/api/tasks/{task['id']}/stop", {})
        self.assertEqual(self.request("/api/maintenance/projects/sample", {"discarded": True})[0], 200)
        snapshot = self.request("/api/bootstrap")[1]
        self.assertEqual(snapshot["projects"], [])
        self.assertEqual(snapshot["tasks"], [])
        self.assertEqual(self.request("/api/tasks", {"project": "sample", "tool": "pi", "prompt": "should not run"})[0], 400)
        self.assertEqual(self.request("/api/maintenance/projects/sample", {"discarded": False})[0], 200)
        self.assertEqual(len(self.request("/api/bootstrap")[1]["tasks"]), 1)
        self.assertTrue((self.fixture.project).is_dir())

    def test_project_with_pending_task_cannot_be_discarded(self):
        self.create()
        self.assertEqual(self.request("/api/maintenance/projects/sample", {"discarded": True})[0], 400)

    def test_official_account_proof_unblocks_legacy_api_key_failure(self):
        task = self.create(tool="dsh", prompt="MAKE_FAILURE", run=True)
        self.wait_task(task["id"])
        (self.root / "tasks" / task["id"] / "stderr.log").write_text("dsh: MISSING_CREDENTIAL: no API key", encoding="utf-8")
        bridge.atomic_json(self.root / "state/dsh-account-check.json", {"available": True, "checked_at": "2099-01-01T00:00:00+00:00", "configuration_sha256": bridge.dsh_account_config_digest()})
        health = self.request("/api/bootstrap")[1]["tools"]["dsh"]
        self.assertTrue(health["execution_ready"])
        self.assertTrue(health["account_verified"])
        self.assertEqual(health["provider"], "deepseek-account")
        self.assertEqual(self.app.bridge().load(task["id"])["status"], "failed")

    def test_account_failure_after_verification_is_reported_again(self):
        task = self.create(tool="dsh", prompt="MAKE_FAILURE", run=True)
        self.wait_task(task["id"])
        (self.root / "tasks" / task["id"] / "stderr.log").write_text("dsh: ACCOUNT_SIGN_IN_REQUIRED: sign in", encoding="utf-8")
        bridge.atomic_json(self.root / "state/dsh-account-check.json", {"available": True, "checked_at": "2000-01-01T00:00:00+00:00", "configuration_sha256": bridge.dsh_account_config_digest()})
        health = self.request("/api/bootstrap")[1]["tools"]["dsh"]
        self.assertFalse(health["execution_ready"])
        self.assertFalse(health["account_verified"])
        self.assertIn("官方账号需要重新登录", health["execution_error"])

    def test_model_settings_require_token_and_do_not_create_tasks(self):
        self.assertEqual(self.request("/api/models", token=False)[0], 403)
        value = {"mode": "override", "model": "example-model", "reasoning": "high"}
        self.assertEqual(self.request("/api/models/codex", value)[0], 200)
        snapshot = self.request("/api/bootstrap")[1]
        self.assertEqual(snapshot["tasks"], [])
        self.assertEqual(snapshot["model_profiles"]["codex"], value)
        self.assertEqual(snapshot["model_profiles"]["pi"], {"mode": "inherit"})
        self.assertEqual(self.request("/api/models/codex", {**value, "api_key": "forbidden"})[0], 400)

    def test_new_tasks_can_override_the_workspace_model(self):
        self.request("/api/models/pi", {"mode": "override", "model": "default-demo"})
        first = self.create()
        second = self.create(model_config={"mode": "override", "model": "single-task-demo"})
        self.assertEqual(first["model_config"]["model"], "default-demo")
        self.assertEqual(second["model_config"]["model"], "single-task-demo")
        self.assertIn("single-task-demo", self.request("/api/tasks/" + second["id"])[1]["task"]["model_label"])

    def test_retry_keeps_the_original_model_snapshot(self):
        task = self.create(prompt="MAKE_FAILURE", run=True, model_config={"mode": "override", "model": "original-demo"})
        self.wait_task(task["id"])
        self.request("/api/models/pi", {"mode": "override", "model": "changed-default"})
        status, retry = self.request(f"/api/tasks/{task['id']}/retry", {})
        self.assertEqual(status, 200)
        self.assertEqual(retry["task"]["model_config"], task["model_config"])

    def test_dsh_model_change_does_not_claim_it_was_already_verified(self):
        bridge.atomic_json(self.root / "state/dsh-account-check.json", {"available": True, "model": "deepseek-flash", "checked_at": "2099-01-01T00:00:00+00:00", "configuration_sha256": bridge.dsh_account_config_digest()})
        self.request("/api/models/dsh", {"mode": "override", "model": "different-account-model"})
        health = self.request("/api/bootstrap")[1]["tools"]["dsh"]
        self.assertTrue(health["account_verified"])
        self.assertFalse(health["model_verified"])
        self.assertEqual(health["checked_model"], "deepseek-flash")

    def test_clean_install_can_start_with_source_sync_disabled(self):
        bridge.atomic_json(self.root / "app-settings.json", {"auto_sync_sources": False})
        app = webui.App(self.root, probe=False)
        with patch.object(app, "refresh_sources", side_effect=AssertionError("must not scan native histories")):
            self.assertFalse(app.bootstrap()["auto_sync_sources"])
        with patch.object(self.app, "refresh_sources"):
            self.assertEqual(self.request("/api/sources/settings", {"enabled": False})[0], 200)
        self.assertFalse(webui.App(self.root, probe=False).auto_sync_sources)


if __name__ == "__main__":
    unittest.main()
