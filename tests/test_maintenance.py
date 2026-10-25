from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import BridgeError
from maintenance import Maintenance


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="bridge-maintenance-")
        self.addCleanup(self.tempdir.cleanup)
        self.base = Path(self.tempdir.name)
        self.root = self.base / "hub"
        self.original = self.base / "original-project"
        self.original.mkdir()
        (self.original / "source.py").write_text("preserve original project", encoding="utf-8")
        self.tick = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        self.maintenance = Maintenance(self.root, clock=lambda: self.tick)
        self.project = {"id": "discarded", "label": "废弃项目", "native_path": str(self.original), "busy": False}
        self.projects = [self.project]
        self.maintenance.update_index(self.projects, sources_ok=True)

    def old_file(self, name="old.log", days=8):
        path = self.maintenance.temp / "discarded" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("bridge temporary data", encoding="utf-8")
        age = (self.tick - timedelta(days=days)).timestamp()
        os.utime(path, (age, age))
        return path

    def test_weekly_schedule_and_disabled_switch(self):
        self.assertFalse(self.maintenance.due())
        self.tick += timedelta(days=7)
        self.assertTrue(self.maintenance.due())
        self.maintenance.configure(False)
        self.assertFalse(self.maintenance.due())
        self.maintenance.configure(True)
        self.maintenance.run(self.projects)
        self.assertFalse(self.maintenance.due())

    def test_cleanup_only_marked_index_and_old_temp_preserves_original_and_results(self):
        old = self.old_file()
        recent = self.old_file("recent.log", days=1)
        result = self.root / "tasks/job/result.md"
        result.parent.mkdir(parents=True)
        result.write_text("accepted work result", encoding="utf-8")
        self.maintenance.mark("discarded", True, self.projects)
        report = self.maintenance.run(self.projects)
        self.assertEqual(report["moved_files"], 2)
        self.assertFalse(old.exists())
        self.assertTrue(recent.is_file())
        self.assertEqual((self.original / "source.py").read_text(encoding="utf-8"), "preserve original project")
        self.assertEqual(result.read_text(encoding="utf-8"), "accepted work result")
        self.assertTrue((Path(report["quarantine"]) / "state/tmp/discarded/old.log").is_file())

    def test_unmarked_old_projects_are_not_treated_as_discarded(self):
        old = self.old_file(days=365)
        self.tick += timedelta(days=365)
        report = self.maintenance.run(self.projects)
        self.assertEqual(report["moved_files"], 0)
        self.assertTrue(old.is_file())

    def test_restore_recreates_index_and_visibility(self):
        self.maintenance.mark("discarded", True, self.projects)
        self.maintenance.run(self.projects)
        self.maintenance.mark("discarded", False, self.projects)
        self.maintenance.update_index(self.projects, sources_ok=True)
        self.assertEqual(self.maintenance.hidden_ids(), set())
        self.assertTrue((self.maintenance.index / "discarded.json").is_file())

    def test_busy_project_cannot_be_marked_and_is_skipped_if_becomes_busy(self):
        self.project["busy"] = True
        with self.assertRaises(BridgeError):
            self.maintenance.mark("discarded", True, self.projects)
        self.project["busy"] = False
        self.maintenance.mark("discarded", True, self.projects)
        self.project["busy"] = True
        report = self.maintenance.run(self.projects)
        self.assertEqual(report["skipped_busy"], ["discarded"])
        self.assertTrue((self.maintenance.index / "discarded.json").is_file())

    def test_orphan_index_has_a_grace_period_and_source_errors_do_not_purge_it(self):
        self.maintenance.update_index([], sources_ok=True)
        self.assertEqual(self.maintenance.run([])["moved_files"], 0)
        self.tick += timedelta(days=8)
        self.assertEqual(self.maintenance.run([], sources_ok=False)["moved_files"], 0)
        self.assertEqual(self.maintenance.run([], sources_ok=True)["moved_files"], 1)

    def test_quarantine_uses_move_time_not_original_file_age(self):
        self.old_file(days=365)
        self.maintenance.mark("discarded", True, self.projects)
        first = self.maintenance.run(self.projects)
        self.assertEqual(first["purged_files"], 0)
        self.tick += timedelta(days=31)
        later = self.maintenance.run(self.projects)
        self.assertEqual(later["purged_files"], 2)
        self.assertTrue((self.original / "source.py").is_file())

    def test_path_escape_identifiers_are_rejected(self):
        with self.assertRaises(BridgeError):
            self.maintenance.mark("../original-project", True, self.projects)
        self.assertFalse(self.maintenance.safe_file(self.original / "source.py", self.maintenance.index))

    def test_junction_or_symlink_is_not_followed(self):
        folder = self.maintenance.temp / "discarded"
        folder.mkdir(parents=True)
        link = folder / "linked-original"
        if os.name == "nt":
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(self.original)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0)
        else:
            link.symlink_to(self.original, target_is_directory=True)
        self.maintenance.mark("discarded", True, self.projects)
        report = self.maintenance.run(self.projects)
        self.assertEqual(report["moved_files"], 1)  # Index only, never linked original files.
        self.assertTrue((self.original / "source.py").is_file())

    def test_restart_retains_schedule_and_marked_projects(self):
        self.maintenance.mark("discarded", True, self.projects)
        expected = self.maintenance.snapshot()
        restarted = Maintenance(self.root, clock=lambda: self.tick)
        self.assertEqual(restarted.snapshot(), expected)


if __name__ == "__main__":
    unittest.main()
