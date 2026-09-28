---
name: peon-ping-toggle
description: Pause, resume, or toggle sounds in the Codex peon-ping runtime. Use when the user asks to mute or unmute peon-ping; use peon-ping-config for volume or category changes.
---

# Toggle Codex peon-ping

Resolve the independent runtime at `${CODEX_HOME:-$HOME/.codex}/cc-config-peon-ping/` on Unix, or `cc-config-peon-ping` under `$env:CODEX_HOME` (defaulting to `$HOME/.codex`) on Windows. Do not use the Claude Code runtime.

Operate only on this Codex runtime, so muting it does not change a separate Claude Code or OpenCode installation:

- Unix (including WSL): a `.paused` marker in the runtime means muted. Create that marker to pause; remove that exact marker to resume.
- Windows: the runtime's `config.json` field `enabled` controls muting. Back up the file and change only that boolean (`false` to pause, `true` to resume); validate the JSON afterward.

For a literal toggle, inspect the current state and invert it. For mute or unmute, set the requested state idempotently. Report the resulting state.
