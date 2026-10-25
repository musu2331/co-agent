import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_workspace import initialize_hub


class SetupWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="co-agent-setup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "fresh-clone"

    def test_fresh_workspace_is_empty_and_does_not_auto_import_sessions(self):
        result = initialize_hub(self.root)
        self.assertEqual(len(result["created"]), 4)
        self.assertEqual(json.loads((self.root / "projects.json").read_text(encoding="utf-8"))["projects"], {})
        self.assertFalse(json.loads((self.root / "app-settings.json").read_text(encoding="utf-8"))["auto_sync_sources"])
        self.assertEqual(list((self.root / "tasks").iterdir()), [])

    def test_existing_local_configuration_is_preserved_byte_for_byte(self):
        initialize_hub(self.root)
        project_file = self.root / "projects.json"
        custom = '{"projects":{"mine":{"label":"My project"}}}\n'
        project_file.write_text(custom, encoding="utf-8")
        self.assertEqual(initialize_hub(self.root)["created"], [])
        self.assertEqual(project_file.read_text(encoding="utf-8"), custom)

    def test_only_missing_files_are_initialized(self):
        initialize_hub(self.root)
        (self.root / "model-config.json").unlink()
        self.assertEqual(initialize_hub(self.root)["created"], ["model-config.json"])


if __name__ == "__main__":
    unittest.main()
