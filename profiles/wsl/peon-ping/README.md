# peon-ping (WSL profile)

Optional profile. Not auto-enabled by `install.sh` on ordinary Ubuntu/VPS.

## Requirements (machine-specific)

- Windows + WSL
- peon-ping installed on the Windows side at `C:\Users\<WINUSER>\.claude\hooks\peon-ping\`
- Windows PowerShell reachable as `powershell.exe` from WSL
- Local audio packs bundled with the peon-ping install

## Enable

1. Replace `<WINUSER>` in `hooks.json` with the Windows username of the target machine.
2. Adjust the `/mnt/c/Users/<WINUSER>/...` paths inside `skills/*/SKILL.md` the same way.
3. Merge `hooks.json` into Claude settings (event → matcher group → hooks), e.g.:

```bash
python3 ../../scripts/merge-settings.py merge-hooks \
  --hooks hooks.json \
  --target "$CLAUDE_CONFIG_DIR/settings.json" \
  --backup-dir "$CLAUDE_CONFIG_DIR/backups/cc-config-profile"
```

4. Symlink or copy `skills/` into `$CLAUDE_DIR/skills/`.

These hooks depend on Windows paths and PowerShell, so they will not work on plain Ubuntu or VPS. `install.sh` never deploys this profile automatically.
