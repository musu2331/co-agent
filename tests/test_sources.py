import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import atomic_json
from sources import Catalog, key_for


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-sources-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.project = self.home / "DeskBox/workspace/demo"
        self.project.mkdir(parents=True)
        self.config = {"demo": {"windows": str(self.project), "wsl": str(self.project), "label": "demo"}}
        self.pi = self.home / ".pi/agent/sessions/root/session.jsonl"
        self.pi.parent.mkdir(parents=True)
        self.write_pi()
        database = self.home / ".codex/state_5.sqlite"
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript("""
              CREATE TABLE projects(id TEXT,name TEXT,position INTEGER);
              CREATE TABLE project_roots(project_id TEXT,path TEXT,position INTEGER);
              CREATE TABLE threads(id TEXT,title TEXT,cwd TEXT,project_id TEXT,created_at INTEGER,
                updated_at INTEGER,rollout_path TEXT,archived INTEGER,thread_source TEXT,source TEXT);
            """)
            connection.execute("INSERT INTO projects VALUES ('project','My project',0)")
            connection.execute("INSERT INTO project_roots VALUES ('project',?,0)", (str(self.project),))
            for identity, archived, source, thread_source in [("kept", 0, "vscode", "user"), ("archived", 1, "vscode", "user"), ("internal", 0, '{"subagent":{}}', "guardian_review")]:
                connection.execute("INSERT INTO threads VALUES (?,?,?,'project',100,200,?,?,?,?)", (identity, "Original title", str(self.project), "missing.jsonl", archived, thread_source, source))
        atomic_json(database.parent / ".codex-global-state.json", {"local-projects": {"desktop-project": {"name": "界面中的项目", "rootPaths": [str(self.project)]}}, "thread-project-assignments": {"kept": {"projectId": "desktop-project"}}})
        (database.parent / "session_index.jsonl").write_text(json.dumps({"id": "kept", "thread_name": "用户看到的标题"}) + "\n", encoding="utf-8")
        self.dsh = self.home / ".dsh/storages"
        atomic_json(self.dsh / "workspace.json", {"global": {"workspaceIds": ["w"], "archivedSessionIds": ["session-old"]}, "tables": {"workspaces": {"w": {"title": "DSH workspace", "path": str(self.project), "sessionIds": ["session-one", "session-old"]}}}})
        for name in ["one", "old", "seed"]:
            atomic_json(self.dsh / "session_projcache/sessions" / ("session-" + name + ".json"), {"record": {"identity": {"cwd": str(self.project), "createdAt": 200000, "isSeeded": name == "seed"}, "rows": {"title": {"val": "DSH title"}, "titleInput": {"val": None}, "turnBoundary": {"val": {"openTurnStartSeq": None}}, "turnOutline": {"val": None}}}})
        # These files must never be read or copied by the adapters.
        for file in [self.home / ".pi/agent/auth.json", self.home / ".dsh/.credentials.yaml", database.parent / "auth.json"]:
            file.write_text("this-is-not-valid-json: secret fixture", encoding="utf-8")
        self.catalog = Catalog(self.home / "hub", home=self.home)

    def write_pi(self, final=True):
        events = [
            {"type": "session", "id": "pi-session", "cwd": str(self.home), "timestamp": "2026-10-24T10:00:00Z"},
            {"type": "message", "id": "u", "parentId": None, "timestamp": "2026-10-24T10:01:00Z", "message": {"role": "user", "content": "先读 " + str(self.project / "TRANSFER.md")}},
        ]
        if final:
            events.append({"type": "message", "id": "a", "parentId": "u", "timestamp": "2026-10-24T10:02:00Z", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "Pi result 中文"}]}})
        self.pi.write_text("\n".join(json.dumps(x) for x in events) + "\n", encoding="utf-8")

    def refresh(self):
        self.catalog.refresh(self.config)
        return self.catalog.snapshot()

    def test_sources_exclude_archived_internal_and_seed_sessions(self):
        before = hashlib.sha256((self.home / ".codex/state_5.sqlite").read_bytes()).hexdigest()
        snapshot = self.refresh()
        self.assertTrue(all(source["ok"] for source in snapshot["sources"].values()), snapshot)
        self.assertEqual({s["id"] for s in snapshot["sessions"]}, {"pi-session", "kept", "session-one"})
        self.assertEqual(before, hashlib.sha256((self.home / ".codex/state_5.sqlite").read_bytes()).hexdigest())

    def test_pi_returned_turn_is_not_claimed_as_completed_project(self):
        snapshot = self.refresh()
        session = next(s for s in snapshot["sessions"] if s["source"] == "pi")
        self.assertEqual(session["state"], "idle")
        self.assertEqual(session["mapped_project"], "demo")
        self.assertEqual(session["association"], "文档路径关联")
        self.assertEqual(self.catalog.detail(session["key"])["result"], "Pi result 中文")
        self.assertNotIn("result", session)  # Full content is fetched only when selected.

    def test_refresh_picks_up_open_turn_without_claiming_live_process(self):
        self.refresh()
        self.write_pi(final=False)
        snapshot = self.refresh()
        session = next(s for s in snapshot["sessions"] if s["source"] == "pi")
        self.assertEqual(session["state"], "turn_open")
        self.assertIn("不等同于进程存活", session["state_note"])

    def test_codex_uses_app_grouping_and_visible_title(self):
        snapshot = self.refresh()
        session = next(s for s in snapshot["sessions"] if s["source"] == "codex")
        self.assertEqual(session["title"], "用户看到的标题")
        self.assertEqual(session["project"], key_for("codex", "project:desktop-project"))
        self.assertTrue(any(p["label"] == "界面中的项目" for p in snapshot["projects"]))

    def test_dsh_null_projection_fields_are_valid(self):
        snapshot = self.refresh()
        session = next(s for s in snapshot["sessions"] if s["source"] == "dsh")
        self.assertEqual(session["title"], "DSH title")
        self.assertEqual(session["state"], "idle")

    def test_source_error_preserves_last_good_records_as_stale(self):
        self.refresh()
        (self.dsh / "workspace.json").write_text("partial", encoding="utf-8")
        snapshot = self.refresh()
        self.assertFalse(snapshot["sources"]["dsh"]["ok"])
        self.assertTrue(next(s for s in snapshot["sessions"] if s["source"] == "dsh")["stale"])

    def test_moved_desktop_directory_resolves_to_existing_workspace(self):
        old = self.home / "Desktop/demo"
        self.assertEqual(self.catalog.canonical(str(old)), str(self.project.resolve()))
        self.assertEqual(self.catalog.mapped_project(str(self.home), "读取 " + str(old / "README.md"), self.config)[0], "demo")

    def test_archived_records_disappear_on_next_refresh(self):
        self.refresh()
        with closing(sqlite3.connect(self.home / ".codex/state_5.sqlite")) as connection, connection:
            connection.execute("UPDATE threads SET archived=1 WHERE id='kept'")
        self.assertNotIn("kept", {s["id"] for s in self.refresh()["sessions"]})

    def test_pi_branch_uses_current_leaf_not_abandoned_answer(self):
        self.write_pi()
        with self.pi.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "message", "id": "fork", "parentId": "u", "timestamp": "2026-10-24T11:00:00Z", "message": {"role": "assistant", "stopReason": "stop", "content": "Current branch"}}) + "\n")
        self.refresh()
        self.assertEqual(self.catalog.detail(key_for("pi", "pi-session"))["result"], "Current branch")

    def test_completion_evidence_can_locate_an_earlier_referenced_turn(self):
        with self.pi.open("a", encoding="utf-8") as stream:
            for i in range(5):
                stream.write(json.dumps({"type": "message", "id": "later" + str(i), "parentId": "a" if i == 0 else "later" + str(i - 1), "message": {"role": "user", "content": "之后的无关事项"}}) + "\n")
        self.refresh()
        record = self.catalog.evidence(key_for("pi", "pi-session"), [str(self.project / "TRANSFER.md")])
        self.assertIn("evidence_note", record)
        self.assertEqual(record["result"], "Pi result 中文")

    def test_codex_open_turn_survives_large_preview_and_updates_on_completion(self):
        path = self.home / "rollout.jsonl"
        path.write_text(json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n" + json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "x" * 450000}]}}) + "\n", encoding="utf-8")
        self.assertEqual(self.catalog._cached(path, self.catalog._codex_record)["state"], "turn_open")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "done"}}) + "\n")
        record = self.catalog._cached(path, self.catalog._codex_record)
        self.assertEqual(record["state"], "idle")
        self.assertEqual(record["result"], "done")
        self.assertTrue(record["request"].startswith("xxxx"))

    def test_matching_request_without_answer_does_not_borrow_an_older_answer(self):
        with self.pi.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "message", "id": "u2", "parentId": "a", "message": {"role": "user", "content": "再次处理 " + str(self.project / "TRANSFER.md")}}) + "\n")
        self.refresh()
        record = self.catalog.evidence(key_for("pi", "pi-session"), [str(self.project / "TRANSFER.md")])
        self.assertEqual(record["result"], "")

    def test_bridge_connection_diagnostics_are_not_project_sessions(self):
        atomic_json(self.dsh / "session_projcache/sessions/session-check.json", {
            "record": {"identity": {"cwd": str(self.home / "hub/state/diagnostics/dsh-account"), "isSeeded": False}, "rows": {}}})
        snapshot = self.refresh()
        self.assertNotIn("session-check", {session["id"] for session in snapshot["sessions"]})


if __name__ == "__main__":
    unittest.main()
