import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import export_release
import webui


class ExportReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-export-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "working-hub"
        for relative in export_release.FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, path)
        for relative in ("tasks/private/task.json", "state/dsh-account-check.json", "state/maintenance.json", "contexts/private.md", "projects.json", "model-config.json", "screenshot.jpg", ".credentials.yaml"):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("PRIVATE_FIXTURE_NEVER_EXPORT", encoding="utf-8")
        self.output = self.base / "clean-template"

    def test_export_excludes_runtime_data_and_has_verifiable_manifest(self):
        result = export_release.export(self.output, self.source)
        self.assertEqual(json.loads((self.output / "projects.json").read_text(encoding="utf-8"))["projects"], {})
        self.assertFalse(json.loads((self.output / "app-settings.json").read_text(encoding="utf-8"))["auto_sync_sources"])
        manifest = json.loads((self.output / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            data = (self.output / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            # This literal is present in this test's source too, so check generated data locations separately.
            if name.startswith(("state/", "tasks/", "contexts/")) or name in ("projects.json", "model-config.json"):
                self.assertNotIn(b"PRIVATE_FIXTURE_NEVER_EXPORT", data)
        with zipfile.ZipFile(result["archive"]) as bundle:
            self.assertTrue(all(name.startswith("clean-template/") for name in bundle.namelist()))
            self.assertFalse(any("task.json" in name or "credentials.yaml" in name or name.endswith(".jpg") for name in bundle.namelist()))
        self.assertEqual((self.source / "projects.json").read_text(encoding="utf-8"), "PRIVATE_FIXTURE_NEVER_EXPORT")

    def test_clean_copy_bootstraps_empty_without_importing_native_sessions(self):
        export_release.export(self.output, self.source)
        app = webui.App(self.output, probe=False)
        data = app.bootstrap()
        self.assertEqual(data["projects"], [])
        self.assertEqual(data["tasks"], [])
        self.assertEqual(data["sessions"], [])
        self.assertFalse(data["auto_sync_sources"])
        self.assertEqual(list((self.output / "tasks").iterdir()), [self.output / "tasks/README.md"])

    def test_existing_output_and_nested_source_are_not_overwritten(self):
        export_release.export(self.output, self.source)
        with self.assertRaises(ValueError):
            export_release.export(self.output, self.source)
        with self.assertRaises(ValueError):
            export_release.export(self.source / "nested", self.source)


if __name__ == "__main__":
    unittest.main()
