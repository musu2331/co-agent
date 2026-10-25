import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_config import ModelProfiles, normalize


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-models-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = ModelProfiles(self.root / "hub", self.home)
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.home / ".codex"), "PI_CODING_AGENT_DIR": str(self.home / ".pi/agent")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, relative, value):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_defaults_do_not_create_native_or_bridge_configuration(self):
        self.assertEqual(self.config.read(), {name: {"mode": "inherit"} for name in ("codex", "pi", "dsh")})
        self.assertFalse(self.config.path.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_profiles_are_independent_and_persist(self):
        value = {"mode": "override", "model": "demo-model", "reasoning": "high"}
        self.config.save("codex", value)
        self.config.save("pi", {"mode": "override", "model": "other-model", "provider": "example"})
        self.assertEqual(ModelProfiles(self.root / "hub", self.home).read()["codex"], value)
        self.assertEqual(self.config.read()["dsh"], {"mode": "inherit"})

    def test_secrets_commands_and_invalid_values_are_rejected(self):
        for value in ({"mode": "override", "model": "demo", "api_key": "sensitive"},
                      {"mode": "override", "model": "--unsafe"}, {"mode": "override", "model": "a\nb"},
                      {"mode": "inherit", "model": "unexpected"}, {"mode": []}, {"mode": "override", "model": 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config.save("codex", value)
        with self.assertRaises(ValueError):
            normalize("dsh", {"mode": "override", "model": "demo", "provider": "untrusted"})
        with self.assertRaises(ValueError):
            normalize("dsh", {"mode": "override", "model": "demo", "reasoning": "high"})

    def test_catalog_reads_allowlisted_fields_without_copying_credentials(self):
        secret = "FAKE_SECRET_MUST_NOT_APPEAR"
        self.write(".pi/agent/settings.json", {"defaultModel": "demo-model", "defaultProvider": "example", "apiKey": secret})
        self.write(".pi/agent/models.json", {"providers": {"example": {"apiKey": secret, "baseUrl": "https://private.example/", "models": [{"id": "custom-model", "name": "Custom"}]}}})
        self.write(".codex/models_cache.json", {"models": [{"slug": "visible", "visibility": "list", "supported_reasoning_levels": [{"effort": "low"}]}, {"slug": "internal", "visibility": "hide"}]})
        auth = self.write(".codex/auth.json", {"token": secret})
        before = auth.read_bytes()
        catalog = self.config.catalog()
        text = json.dumps(catalog)
        self.assertNotIn(secret, text)
        self.assertNotIn("private.example", text)
        self.assertNotIn("internal", text)
        self.assertIn("custom-model", text)
        self.assertEqual(auth.read_bytes(), before)

    def test_known_codex_model_cannot_select_unsupported_effort(self):
        self.write(".codex/models_cache.json", {"models": [{"slug": "demo", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]}]})
        with self.assertRaises(ValueError):
            self.config.save("codex", {"mode": "override", "model": "demo", "reasoning": "ultra"})
        self.config.save("codex", {"mode": "override", "model": "demo", "reasoning": "high"})

    def test_corrupt_native_metadata_does_not_prevent_manual_configuration(self):
        self.write(".pi/agent/models.json", {"providers": None})
        self.write(".codex/models_cache.json", ["invalid shape"])
        self.assertEqual(self.config.catalog()["choices"]["codex"], [])
        self.config.save("dsh", {"mode": "override", "model": "account-model"})


if __name__ == "__main__":
    unittest.main()
