---
name: peon-ping-config
description: Change volume, sound categories, packs, or rotation in the Codex peon-ping runtime. Use when the user asks to adjust peon-ping settings, not to install or create packs.
---

# Configure Codex peon-ping

Use the independent runtime at `${CODEX_HOME:-$HOME/.codex}/cc-config-peon-ping/` on Unix, or `cc-config-peon-ping` under `$env:CODEX_HOME` (defaulting to `$HOME/.codex`) on Windows. Work with its `config.json`; do not edit the Claude Code installation.

Read the current config, back it up, change only the requested fields, and validate the resulting JSON. Keep any unrelated local settings and credentials private. Check `packs/<name>/openpeon.json` exists before choosing a pack.

- `volume` is a number from `0.0` to `1.0`.
- Inside `categories`, the literal key `"task.acknowledge"` controls the sound when a prompt is sent; `"task.complete"` controls the completion sound.
- `default_pack` chooses the pack when no rotation or session override applies. `pack_rotation` and `pack_rotation_mode` control rotation.

Report the changed fields and whether a new Codex session is needed. A subsequent hook event uses the updated config; do not reinstall peon-ping just to change a setting.
