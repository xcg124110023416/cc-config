#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "codex-config.py"
SPEC = importlib.util.spec_from_file_location("codex_config", MODULE_PATH)
assert SPEC and SPEC.loader
codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex)


class CodexMigrationTests(unittest.TestCase):
    def test_guidance_block_preserves_local_content_and_is_repeatable(self):
        original = "# My local rule\nKeep this.\n"
        changed = codex.managed_guidance(original, "Use portable skills.\n")
        self.assertIn(original.strip(), changed)
        self.assertEqual(changed, codex.managed_guidance(changed, "Use portable skills.\n"))
        with self.assertRaises(ValueError):
            codex.managed_guidance(codex.BEGIN + "\nmissing end", "new")

    def test_install_keeps_existing_config_and_unmanaged_skill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home, skills = root / ".codex", root / ".agents" / "skills"
            codex_home.mkdir()
            skills.mkdir(parents=True)
            (codex_home / "AGENTS.md").write_text("Local guidance\n", encoding="utf-8")
            (codex_home / "config.toml").write_text(
                'model = "local-model"\n[mcp_servers.local]\ncommand = "local-server"\n', encoding="utf-8"
            )
            local_skill = skills / "neat-freak"
            local_skill.mkdir()
            (local_skill / "SKILL.md").write_text("local version", encoding="utf-8")
            state = {}
            with mock.patch.object(codex.shutil, "which", return_value=None):
                codex.install(codex.manifest(), codex_home, skills, root / "backup1", state, None)
                initial = (codex_home / "config.toml").read_text(encoding="utf-8")
                codex.install(codex.manifest(), codex_home, skills, root / "backup2", state, None)
            self.assertEqual(initial, (codex_home / "config.toml").read_text(encoding="utf-8"))
            self.assertIn('model = "local-model"', initial)
            self.assertIn('model_reasoning_effort = "xhigh"', initial)
            self.assertIn("Local guidance", (codex_home / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual("local version", (local_skill / "SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue((skills / "mineru" / "SKILL.md").exists())
            self.assertTrue((root / "backup1" / "config.toml").exists())
            self.assertEqual("xhigh", codex.setting_values(initial)["model_reasoning_effort"])
            (skills / "mineru" / "SKILL.md").write_text("local edit", encoding="utf-8")
            with mock.patch.object(codex.shutil, "which", return_value=None):
                codex.install(codex.manifest(), codex_home, skills, root / "backup3", state, None)
            self.assertEqual("local edit", (skills / "mineru" / "SKILL.md").read_text(encoding="utf-8"))

    def test_preexisting_preference_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            (root / "config.toml").write_text('model_reasoning_effort = "high"\n', encoding="utf-8")
            with mock.patch.object(codex.shutil, "which", return_value=None):
                codex.install(codex.manifest(), root, root / "skills", root / "backup", {}, None)
            self.assertEqual('model_reasoning_effort = "high"\n', (root / "config.toml").read_text(encoding="utf-8"))

    def test_mcp_cli_shape_and_local_customizations(self):
        server = {"name": "codegraph", "command": "codegraph", "args": ["serve", "--mcp"]}
        current = {"enabled": True, "transport": {"type": "stdio", "command": "codegraph", "args": server["args"], "env": None}}
        with mock.patch.object(codex, "run", return_value=mock.Mock(returncode=0, stdout=json.dumps(current))):
            self.assertEqual("current", codex.mcp_status("codex", server))
        current["transport"]["env"] = {"LOCAL": "value"}
        with mock.patch.object(codex, "run", return_value=mock.Mock(returncode=0, stdout=json.dumps(current))):
            self.assertEqual("conflict", codex.mcp_status("codex", server))

    def test_codex_cli_uses_resolved_home_even_when_environment_is_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(codex.os.environ, {"CODEX_HOME": ""}):
                with mock.patch.object(codex.Path, "home", return_value=Path(temporary)):
                    with mock.patch.object(codex.subprocess, "run") as invoke:
                        codex.run("codex", "mcp", "list")
            self.assertEqual(str(Path(temporary) / ".codex"), invoke.call_args.kwargs["env"]["CODEX_HOME"])
            self.assertEqual("utf-8", invoke.call_args.kwargs["encoding"])

    def test_hooks_merge_keeps_local_handlers_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "hooks.json"
            target.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "local"}]}]}}), encoding="utf-8")
            source = codex.ROOT / "codex" / "hooks.json"
            merged = json.loads(codex.merge_hooks(target, source))
            self.assertEqual(2, len(merged["hooks"]["SessionStart"]))
            target.write_text(json.dumps(merged), encoding="utf-8")
            self.assertEqual(merged, json.loads(codex.merge_hooks(target, source)))


if __name__ == "__main__":
    unittest.main()
