#!/usr/bin/env python3

import importlib.util
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
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
                codex.install(codex.manifest(), codex_home, skills, root / "backup1", state, None, with_upstream=False, with_cc_switch=False, with_peon=False)
                initial = (codex_home / "config.toml").read_text(encoding="utf-8")
                codex.install(codex.manifest(), codex_home, skills, root / "backup2", state, None, with_upstream=False, with_cc_switch=False, with_peon=False)
            self.assertEqual(initial, (codex_home / "config.toml").read_text(encoding="utf-8"))
            self.assertIn('model = "local-model"', initial)
            self.assertIn('model_reasoning_effort = "xhigh"', initial)
            self.assertEqual((True, codex.manifest()["tui"]["status_line"]), codex.status_line_value(initial))
            self.assertIn("Local guidance", (codex_home / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual("local version", (local_skill / "SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue((skills / "mineru" / "SKILL.md").exists())
            self.assertTrue((skills / "peon-ping-config" / "SKILL.md").exists())
            self.assertTrue((skills / "peon-ping-toggle" / "SKILL.md").exists())
            self.assertTrue((root / "backup1" / "config.toml").exists())
            self.assertEqual("xhigh", codex.setting_values(initial)["model_reasoning_effort"])
            (skills / "mineru" / "SKILL.md").write_text("local edit", encoding="utf-8")
            with mock.patch.object(codex.shutil, "which", return_value=None):
                codex.install(codex.manifest(), codex_home, skills, root / "backup3", state, None, with_upstream=False, with_cc_switch=False, with_peon=False)
            self.assertEqual("local edit", (skills / "mineru" / "SKILL.md").read_text(encoding="utf-8"))

    def test_preexisting_preference_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            (root / "config.toml").write_text(
                'model_reasoning_effort = "high"\n[tui]\nstatus_line = ["local"]\n', encoding="utf-8"
            )
            with mock.patch.object(codex.shutil, "which", return_value=None):
                codex.install(codex.manifest(), root, root / "skills", root / "backup", {}, None, with_upstream=False, with_cc_switch=False, with_peon=False)
            self.assertEqual(
                'model_reasoning_effort = "high"\n[tui]\nstatus_line = ["local"]\n',
                (root / "config.toml").read_text(encoding="utf-8"),
            )

    def test_status_line_merge_preserves_other_tui_fields_and_local_choices(self):
        wanted = codex.manifest()["tui"]["status_line"]
        original = '[tui]\ntheme = "local-theme"\n[mcp_servers.local]\ncommand = "local"\n'
        merged = codex.merge_status_line(original, wanted)
        self.assertEqual((True, wanted), codex.status_line_value(merged))
        self.assertIn('theme = "local-theme"', merged)
        self.assertIn('[mcp_servers.local]', merged)
        self.assertEqual(merged, codex.merge_status_line(merged, wanted))
        self.assertEqual((True, wanted), codex.status_line_value(codex.merge_status_line('[tui]', wanted)))
        for existing in ('[tui]\nstatus_line = ["local"]\n', 'tui.status_line = ["local"]\n', 'tui = { theme = "local" }\n'):
            self.assertEqual(existing, codex.merge_status_line(existing, wanted))

    def test_install_preserves_crlf_when_adding_status_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.toml"
            config.write_bytes(b'model_reasoning_effort = "xhigh"\r\n[tui]\r\nshow_tooltips = true\r\n')
            with mock.patch.object(codex.shutil, "which", return_value=None):
                codex.install(codex.manifest(), root, root / "skills", root / "backup", {}, None,
                              with_upstream=False, with_cc_switch=False, with_peon=False)
            result = config.read_bytes()
            self.assertIn(b'status_line = ["model-with-reasoning"', result)
            self.assertEqual(result.count(b"\n"), result.count(b"\r\n"))

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

    def test_hook_trust_state_does_not_hide_hook_definitions(self):
        state = '[hooks.state]\n[hooks.state."/tmp/hooks.json:session_start:0:0"]\n'
        self.assertFalse(codex.has_inline_hooks(state))
        self.assertTrue(codex.has_inline_hooks(state + '[[hooks.Stop]]\n'))

    def test_codex_common_merge_preserves_local_fields_and_removes_stale_key(self):
        existing = (
            'model_reasoning_effort = "xhigh"\n'
            'disable_response_storage = true\n'
            '[tui]\ntheme = "local-theme"\n'
            '[mcp_servers.serena]\ncommand = "local-serena"\n'
        )
        servers = codex.wanted_servers(codex.manifest()["mcp_servers"])
        merged = codex.merge_codex_common(existing, codex.manifest(), servers)
        self.assertNotIn("disable_response_storage", merged)
        self.assertIn('theme = "local-theme"', merged)
        self.assertEqual((True, codex.manifest()["tui"]["status_line"]), codex.status_line_value(merged))
        self.assertIn('command = "local-serena"', merged)
        self.assertEqual(1, merged.count("[mcp_servers.serena]"))
        self.assertIn("[mcp_servers.sciverse]", merged)
        self.assertEqual(merged, codex.merge_codex_common(merged, codex.manifest(), servers))

    def test_codex_common_only_adds_ready_servers(self):
        data = codex.manifest()
        with mock.patch.object(codex, "local_binary", side_effect=lambda name: name if name == "codegraph" else None):
            with mock.patch.object(codex, "sciverse_credential_available", return_value=False):
                servers = codex.ready_common_servers(data)
        self.assertEqual(["codegraph"], [server["name"] for server in servers])
        merged = codex.merge_codex_common("", data, servers)
        self.assertIn("[mcp_servers.codegraph]", merged)
        self.assertNotIn("[mcp_servers.serena]", merged)
        self.assertNotIn("[mcp_servers.sciverse]", merged)

    def test_cc_switch_display_header_is_not_saved_as_toml(self):
        shown = "通用配置片段\n=====\nApp: codex\n\nmodel_reasoning_effort = \"xhigh\"\n"
        self.assertEqual('model_reasoning_effort = "xhigh"\n', codex.cc_switch_common_text(shown))
        self.assertEqual('model_reasoning_effort = "xhigh"\n', codex.cc_switch_common_text(shown.replace("\n", "\r\n")))

    def test_upstream_skill_download_checks_archive_and_rejects_links(self):
        source = {
            "kind": "github", "name": "test", "repository": "example/skills",
            "ref": "a" * 40, "skills": ["skills/example"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            archive_bytes = io.BytesIO()
            with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
                content = b"---\nname: example\n---\n"
                item = tarfile.TarInfo("skills-test/skills/example/SKILL.md")
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
            payload = archive_bytes.getvalue()
            source["archive_sha256"] = hashlib.sha256(payload).hexdigest()
            with mock.patch.object(codex, "fetch_bytes", return_value=payload):
                installed = codex.download_upstream_source(source, target)
            self.assertEqual(content, (installed["example"] / "SKILL.md").read_bytes())
            source["archive_sha256"] = "0" * 64
            with mock.patch.object(codex, "fetch_bytes", return_value=payload):
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    codex.download_upstream_source(source, target / "bad-hash")

            archive_bytes = io.BytesIO()
            with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
                item = tarfile.TarInfo("skills-test/skills/example/unsafe")
                item.type = tarfile.SYMTYPE
                item.linkname = "/tmp/outside"
                archive.addfile(item)
            payload = archive_bytes.getvalue()
            source["archive_sha256"] = hashlib.sha256(payload).hexdigest()
            with mock.patch.object(codex, "fetch_bytes", return_value=payload):
                with self.assertRaisesRegex(ValueError, "unsupported upstream archive entry"):
                    codex.download_upstream_source(source, target / "bad-link")

    def test_transient_upstream_download_retries_with_size_limit(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        with mock.patch.object(codex.urllib.request, "urlopen", side_effect=[urllib.error.URLError("temporary"), response]) as open_url:
            with mock.patch.object(codex.time, "sleep"):
                self.assertEqual(b"ok", codex.fetch_bytes("https://example.com/data", limit=2))
        self.assertEqual(2, open_url.call_count)
        response.__enter__.return_value.read.return_value = b"long"
        with mock.patch.object(codex.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "size limit"):
                codex.fetch_bytes("https://example.com/data", limit=2)

    def test_upstream_skill_keeps_unmanaged_local_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "skills"
            local = home / "example"
            local.mkdir(parents=True)
            (local / "SKILL.md").write_text("local", encoding="utf-8")
            source = {"kind": "files", "name": "example", "files": {"SKILL.md": "a" * 64}}

            def download(_source, directory):
                staged = directory / "example"
                staged.mkdir()
                (staged / "SKILL.md").write_text("upstream", encoding="utf-8")
                return {"example": staged}

            state = {}
            with mock.patch.object(codex, "upstream_sources", return_value=[source]):
                with mock.patch.object(codex, "download_upstream_source", side_effect=download):
                    codex.install_upstream_skills(home, root / "backup", state)
            self.assertEqual("local", (local / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual({}, state["upstream_skills"])

    def test_matt_setup_skill_prefers_agents_for_codex(self):
        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary)
            source = {
                "name": "mattpocock-skills",
                "kind": "github",
            }
            (skill / "SKILL.md").write_text(
                "**Pick the file to edit:**\n\n"
                "- If `CLAUDE.md` exists, edit it.\n"
                "- Else if `AGENTS.md` exists, edit it.\n",
                encoding="utf-8",
            )
            codex.adapt_upstream_skill(source, "setup-matt-pocock-skills", skill)
            adapted = (skill / "SKILL.md").read_text(encoding="utf-8")
            self.assertLess(adapted.index("`AGENTS.md`"), adapted.index("`CLAUDE.md`"))
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                codex.adapt_upstream_skill(source, "setup-matt-pocock-skills", skill)

    def test_peon_hook_merge_preserves_local_sibling_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "cc-config-peon-ping"
            target = root / "hooks.json"
            owned = {"type": "command", "command": str(runtime / "codex-peon-hook.py")}
            local = {"type": "command", "command": "my-local-hook"}
            target.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [owned, local]}]}}), encoding="utf-8")
            additions = codex.peon_hook_groups(runtime)
            merged = codex.merge_peon_hooks(target, additions, runtime)
            target.write_text(merged, encoding="utf-8")
            groups = json.loads(merged)["hooks"]["Stop"]
            self.assertIn(local, groups[0]["hooks"])
            self.assertEqual(2, len(groups))
            self.assertEqual(json.loads(merged), json.loads(codex.merge_peon_hooks(target, additions, runtime)))
            disabled = json.loads(codex.merge_peon_hooks(target, {}, runtime))
            self.assertEqual([{"hooks": [local]}], disabled["hooks"]["Stop"])

    def test_disabled_peon_keeps_unrelated_hook_file_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "hooks.json"
            original = '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"local"}]}]}}\n'
            target.write_text(original, encoding="utf-8")
            with mock.patch.object(codex, "peon_manifest", return_value=({}, {})), mock.patch.object(codex, "peon_profile", return_value="none"):
                codex.install_peon(root, root / "backups", {}, inline_hooks=False)
            self.assertEqual(original, target.read_text(encoding="utf-8"))
            self.assertFalse((root / "backups").exists())

    @unittest.skipUnless(os.name == "nt", "Windows hook shell behavior")
    def test_peon_hook_command_runs_from_windows_shells(self):
        with tempfile.TemporaryDirectory(prefix="peon hook test ") as temporary:
            runtime = Path(temporary)
            (runtime / "codex-peon-hook.py").write_text(
                "import json, sys\nassert json.load(sys.stdin)['hook_event_name'] == 'UserPromptSubmit'\n",
                encoding="utf-8",
            )
            handler = codex.peon_hook_groups(runtime)["UserPromptSubmit"][0]["hooks"][0]
            self.assertEqual(handler["command"], handler["commandWindows"])
            self.assertNotIn('"', handler["command"])
            payload = json.dumps({"hook_event_name": "UserPromptSubmit"})
            for shell in (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"], ["cmd.exe", "/d", "/c"]):
                with self.subTest(shell=shell[0]):
                    result = subprocess.run(
                        [*shell, handler["command"]], input=payload, text=True,
                        capture_output=True, timeout=15, check=False,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell policy behavior")
    def test_peon_hook_runs_adapter_with_restricted_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            adapter = runtime / "adapters" / "codex.ps1"
            adapter.parent.mkdir()
            marker = runtime / "received.json"
            adapter.write_text(
                f"[Console]::In.ReadToEnd() | Set-Content -LiteralPath '{marker}' -Encoding UTF8\n",
                encoding="utf-8",
            )
            wrapper = runtime / "codex-peon-hook.py"
            wrapper.write_bytes((codex.ROOT / "scripts" / "codex-peon-hook.py").read_bytes())
            environment = os.environ.copy()
            environment["PSExecutionPolicyPreference"] = "Restricted"
            payload = '{"hook_event_name":"UserPromptSubmit"}'
            result = subprocess.run(
                [sys.executable, str(wrapper)], input=payload, text=True,
                capture_output=True, timeout=15, check=False, env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(json.loads(payload), json.loads(marker.read_text(encoding="utf-8-sig")))

    def test_peon_sound_manifest_rejects_unsafe_or_unhashed_files(self):
        sound = {"file": "sounds/ready.wav", "sha256": "a" * 64}
        self.assertEqual({"sounds/ready.wav": "a" * 64}, codex.peon_sound_hashes({"categories": {"start": {"sounds": [sound]}}}))
        with self.assertRaisesRegex(ValueError, "unsafe or unhashed"):
            codex.peon_sound_hashes({"categories": {"start": {"sounds": [{"file": "sounds/../escape.wav", "sha256": "a" * 64}]}}})
        with self.assertRaisesRegex(ValueError, "unsafe or unhashed"):
            codex.peon_sound_hashes({"categories": {"start": {"sounds": [{"file": "sounds/ready.wav"}]}}})

    def test_peon_install_is_independent_idempotent_and_preserves_local_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / ".codex"
            state = {}

            def stage_runtime(_upstream, stage):
                (stage / "config.json").write_text(
                    '{"volume": 0.5, "categories": {"task.acknowledge": false}}', encoding="utf-8"
                )
                (stage / "codex-peon-hook.py").write_bytes((codex.ROOT / "scripts" / "codex-peon-hook.py").read_bytes())
                return {"codex-peon-hook.py": codex.file_sha256(stage / "codex-peon-hook.py")}

            def stage_packs(_lock, stage, _runtime, files):
                path = stage / "packs" / "peon" / "openpeon.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"name": "peon"}', encoding="utf-8")
                files["packs/peon/openpeon.json"] = codex.file_sha256(path)

            with mock.patch.object(codex, "peon_profile", return_value="linux"):
                with mock.patch.object(codex, "stage_peon_runtime", side_effect=stage_runtime) as runtime_download:
                    with mock.patch.object(codex, "stage_peon_packs", side_effect=stage_packs) as pack_download:
                        codex.install_peon(codex_home, root / "backup", state, inline_hooks=False)
                        self.assertEqual(1, runtime_download.call_count)
                        self.assertEqual(1, pack_download.call_count)
                        config = codex_home / "cc-config-peon-ping" / "config.json"
                        initial = json.loads(config.read_text(encoding="utf-8"))
                        self.assertTrue(initial["categories"]["task.acknowledge"])
                        initial["volume"] = 0.9
                        initial["categories"]["task.acknowledge"] = False
                        config.write_text(json.dumps(initial), encoding="utf-8")
                        codex.install_peon(codex_home, root / "backup2", state, inline_hooks=False)
                        self.assertEqual(1, runtime_download.call_count)
                        self.assertEqual(1, pack_download.call_count)
            self.assertEqual(0.9, json.loads(config.read_text(encoding="utf-8"))["volume"])
            self.assertFalse(json.loads(config.read_text(encoding="utf-8"))["categories"]["task.acknowledge"])
            hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(set(hooks["hooks"]), {event for event, _ in codex.PEON_EVENTS})

    def test_wsl_peon_audio_player_is_reconciled_without_overwriting_preferences(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            config = runtime / "config.json"
            config.write_text('{"volume": 0.9, "linux_audio_player": ""}', encoding="utf-8")
            with mock.patch.object(codex, "local_binary", side_effect=lambda name: name if name == "paplay" else None):
                codex.reconcile_peon_audio_config(runtime, "wsl-native", Path(temporary) / "backup")
                first = config.read_text(encoding="utf-8")
                codex.reconcile_peon_audio_config(runtime, "wsl-native", Path(temporary) / "backup2")
            self.assertEqual("paplay", json.loads(first)["linux_audio_player"])
            self.assertEqual(0.9, json.loads(first)["volume"])
            self.assertEqual(first, config.read_text(encoding="utf-8"))
            self.assertTrue((Path(temporary) / "backup" / "peon-ping" / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
