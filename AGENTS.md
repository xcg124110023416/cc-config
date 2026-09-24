# cc-config repository

This repository restores portable configuration for Claude Code and Codex.
Read `AGENT_SYNC.md` / `AGENT_SETUP.md` for Claude Code, or
`CODEX_SYNC.md` / `CODEX_SETUP.md` for Codex. Keep the two installers independent.

Never commit credentials, complete local config files, session history, caches,
or machine-specific paths. Inspect diffs and run `scripts/audit-portable.py`
before committing. Do not sync deletions merely because a component is absent
on the current host.
