from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "manage-peon-profile.py"
SPEC = importlib.util.spec_from_file_location("manage_peon_profile", MODULE_PATH)
assert SPEC and SPEC.loader
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


class ProfileSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = profile.load_json(ROOT / "profiles" / "peon-ping" / "profile.json")

    def test_explicit_profile_wins(self) -> None:
        with mock.patch.dict(os.environ, {"CC_CONFIG_PEON_PROFILE": "linux"}):
            self.assertEqual(profile.select_profile(None, self.manifest), "linux")

    def test_none_is_supported(self) -> None:
        self.assertEqual(profile.select_profile("none", self.manifest), "none")

    def test_auto_uses_detection(self) -> None:
        with mock.patch.object(profile, "detected_profile", return_value="wsl-native"):
            self.assertEqual(profile.select_profile("auto", self.manifest), "wsl-native")

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(profile.ProfileError):
            profile.select_profile("wsl-windows-bridge", self.manifest)


class HookReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.claude_dir = Path(self.temporary.name)
        runtime = self.claude_dir / "hooks" / "peon-ping"
        (runtime / "scripts").mkdir(parents=True)
        (runtime / "peon.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        (runtime / "peon.ps1").write_text("# powershell\n", encoding="utf-8")
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "serena-hooks activate --client=claude-code"},
                            {
                                "type": "command",
                                "command": "powershell.exe -File 'peon-ping/peon.ps1'",
                            },
                        ],
                    }
                ],
                "SessionEnd": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "serena-hooks cleanup --client=claude-code"}
                        ],
                    }
                ],
            }
        }
        profile.atomic_json(self.claude_dir / "settings.json", settings)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commands(self) -> list[str]:
        settings = json.loads((self.claude_dir / "settings.json").read_text(encoding="utf-8"))
        return [
            handler["command"]
            for groups in settings["hooks"].values()
            for group in groups
            for handler in group.get("hooks", [])
            if isinstance(handler, dict) and isinstance(handler.get("command"), str)
        ]

    def test_unix_wrapper_uses_lf_line_endings(self) -> None:
        wrapper = profile.write_unix_wrapper("wsl-native", self.claude_dir, "linux")
        self.assertNotIn(b"\r\n", wrapper.read_bytes())
        self.assertTrue(wrapper.read_bytes().endswith(b"\n"))

    def test_unix_reconcile_migrates_legacy_and_preserves_serena(self) -> None:
        profile.write_unix_wrapper("wsl-native", self.claude_dir, "linux")
        profile.reconcile_unix_hooks(self.claude_dir)
        commands = self.commands()
        self.assertIn("serena-hooks activate --client=claude-code", commands)
        self.assertIn("serena-hooks cleanup --client=claude-code", commands)
        self.assertFalse(any("powershell" in command.lower() and "peon" in command.lower() for command in commands))
        self.assertEqual(sum("peon-ping" in command for command in commands), 13)

    def test_unix_reconcile_is_idempotent(self) -> None:
        profile.write_unix_wrapper("linux", self.claude_dir, "linux")
        profile.reconcile_unix_hooks(self.claude_dir)
        first = (self.claude_dir / "settings.json").read_bytes()
        profile.reconcile_unix_hooks(self.claude_dir)
        self.assertEqual(first, (self.claude_dir / "settings.json").read_bytes())

    def test_windows_reconcile_replaces_unix_profile(self) -> None:
        profile.write_unix_wrapper("linux", self.claude_dir, "linux")
        profile.reconcile_unix_hooks(self.claude_dir)
        profile.reconcile_windows_hooks(self.claude_dir)
        commands = self.commands()
        self.assertEqual(sum("peon-ping" in command for command in commands), 10)
        self.assertTrue(all("host-native.sh" not in command for command in commands))
        self.assertTrue(any("peon.ps1" in command for command in commands))

    def test_local_config_and_state_round_trip(self) -> None:
        runtime = self.claude_dir / "hooks" / "peon-ping"
        config = runtime / "config.json"
        state = runtime / ".state.json"
        config.write_bytes(b'{"enabled": true}\n')
        state.write_bytes(b'{"last": "sound"}\n')
        preserved = profile.preserve_local_state(runtime)
        config.unlink()
        state.unlink()
        profile.restore_local_state(runtime, preserved)
        self.assertEqual(config.read_bytes(), b'{"enabled": true}\n')
        self.assertEqual(state.read_bytes(), b'{"last": "sound"}\n')

    def test_cleaner_does_not_remove_unrelated_notify_script(self) -> None:
        settings = profile.load_json(self.claude_dir / "settings.json")
        settings["hooks"]["Stop"] = [
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": "/opt/another-tool/notify.sh"}],
            }
        ]
        cleaned = profile.clean_peon_hooks(settings)
        self.assertEqual(cleaned["hooks"]["Stop"][0]["hooks"][0]["command"], "/opt/another-tool/notify.sh")


if __name__ == "__main__":
    unittest.main()
