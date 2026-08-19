#!/usr/bin/env python3

import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "merge-settings.py"
SPEC = importlib.util.spec_from_file_location("merge_settings", MODULE_PATH)
assert SPEC and SPEC.loader
merge_settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_settings)


class QuickConfigOwnershipTests(unittest.TestCase):
    def test_quick_config_fields_are_not_portable(self) -> None:
        quick_config_values = (
            {"attribution": {"commit": "", "pr": ""}},
            {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}},
            {"env": {"ENABLE_TOOL_SEARCH": "true"}},
            {"env": {"DISABLE_AUTOUPDATER": "1"}},
        )
        for value in quick_config_values:
            with self.subTest(value=value):
                with self.assertRaises(merge_settings.PortableSettingsError):
                    merge_settings.validate_portable(value)

    def test_extract_does_not_import_quick_config_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.json"
            baseline = directory / "baseline.json"
            output = directory / "output.json"
            source.write_text(
                json.dumps(
                    {
                        "attribution": {"commit": "", "pr": ""},
                        "env": {
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                            "ENABLE_TOOL_SEARCH": "true",
                            "DISABLE_AUTOUPDATER": "1",
                        },
                        "effortLevel": "high",
                    }
                ),
                encoding="utf-8",
            )
            baseline.write_text(json.dumps({"effortLevel": "medium"}), encoding="utf-8")

            merge_settings.command_extract(
                argparse.Namespace(source=source, baseline=baseline, output=output)
            )

            extracted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(extracted["effortLevel"], "high")
            self.assertEqual(extracted["enabledPlugins"], {})
            self.assertNotIn("attribution", extracted)
            self.assertNotIn("env", extracted)


if __name__ == "__main__":
    unittest.main()
