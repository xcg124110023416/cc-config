# Claude Code portable config

Portable Claude Code behavior only. CC-Switch remains responsible for Provider, API, Base URL, proxy, credentials, and model routing.

## New machine

```bash
git clone <private-repo> ~/cc-config
cd ~/cc-config
./install.sh
claude
```

The installer uses `CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"`, displays the detected directory, backs up every existing item it changes, validates and syncs `settings.portable.json` to the Claude Common Snippet through the official `cc-switch` CLI when available, merges the same approved behavior fields into the live settings file, links each managed Skill separately, and offers to install missing plugins. It never imports `.claude.json`, credentials, sessions, history, projects, caches, or provider settings.

`settings.portable.json` is the only source of truth for portable behavior. Do not edit or import the generated CC-Switch Common Snippet manually. Each Claude Provider should have **Attach Common Config** enabled.

For CC-Switch proxy takeover mode, `install.sh` also puts `~/cc-config/bin` first in Bash's PATH. The repository wrapper leaves the official `cc-switch` binary in place, forwards ordinary commands unchanged, and runs only the whitelist settings merge after a successful Claude `use` or `provider switch`. This compensates for CC-Switch 5.10.x hot-switches that bypass the normal Common Config live-file merge. Wrapper-created settings backups are limited to the latest 20 files under `$CLAUDE_DIR/backups/cc-config-wrapper/`.

## Recovery

```bash
# Bypass the wrapper and call the official CC-Switch directly.
~/.local/bin/cc-switch

# Re-sync Common Snippet and restore portable configuration.
cd ~/cc-config
./install.sh
```

`claude-hud` is installed from its marketplace when approved. Its status line discovers both the active Claude config directory and `node` at runtime.

MCP is intentionally manual in version 1. Restore these on a new machine if needed, using machine-appropriate commands and paths:

- `codegraph`
- `serena`
- `sciverse`

The peon-ping hooks are intentionally excluded because the current setup depends on WSL, Windows PowerShell, and machine-specific paths.

## Update from the primary machine

```bash
cd ~/cc-config
./update.sh
git status
git add .
git commit
git push
```

`update.sh` only offers new whitelist file items such as Skills, agents, commands, rules, hooks, output styles, and keybindings. It never imports portable settings back from live settings or from the generated Common Snippet, and it does not run Git commands. Edit `settings.portable.json` directly, then run `./install.sh` to validate, sync, and apply it.
