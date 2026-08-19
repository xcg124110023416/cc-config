# Host-native peon-ping profiles

This profile family restores peon-ping according to the target host. The
repository owns selection and hook reconciliation; the pinned upstream archive
provides the runtime, skills, and sound-pack downloader.

| Profile | Runtime | Audio path |
|---|---|---|
| `wsl-native` | Unix `peon.sh` | WSLg/Pulse/PipeWire via a Linux player; never PowerShell |
| `linux` | Unix `peon.sh` | Native Linux player |
| `macos` | Unix `peon.sh` | Native `afplay` |
| `windows` | PowerShell `peon.ps1` | Native Windows media APIs |
| `none` | none | Not applicable / explicitly disabled |

The default is `auto`. Override it for a supported host class with
`CC_CONFIG_PEON_PROFILE`, for example:

```bash
CC_CONFIG_PEON_PROFILE=linux ./install.sh
CC_CONFIG_PEON_PROFILE=none ./install.sh
```

`profile.json` pins the upstream revision and archive SHA-256. Do not replace
that pin without reviewing the upstream diff, downloading the matching archive,
and updating the hash. Runtime files, packs, user config, state, and logs remain
outside Git under `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/peon-ping/`.

The manager preserves `config.json` and `.state.json`, replaces only recognized
peon hook commands, and leaves Serena and unrelated local hooks intact. WSL is
intentionally forced through the upstream Linux backend because upstream's
default WSL backend invokes Windows PowerShell.

```bash
python3 scripts/manage-peon-profile.py detect
python3 scripts/manage-peon-profile.py status
python3 scripts/manage-peon-profile.py install
python3 scripts/manage-peon-profile.py reconcile
```

`status` and `doctor.sh` do not play sound or access the network. Installation
downloads the pinned archive plus the declared default packs. Restart Claude
Code after installing or changing profiles.
