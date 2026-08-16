#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="$CLAUDE_DIR/backups/cc-config-$TIMESTAMP"

printf 'Claude config directory: %s\n' "$CLAUDE_DIR"
printf 'Portable config repository: %s\n' "$REPO_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'ERROR: python3 is required for safe JSON merging. No files were changed.\n' >&2
  exit 1
fi

python3 "$REPO_DIR/scripts/audit-portable.py" "$REPO_DIR" \
  --settings-validator "$REPO_DIR/scripts/merge-settings.py"

confirm() {
  local prompt=$1
  local reply
  if [[ ! -t 0 ]]; then
    return 1
  fi
  read -r -p "$prompt [y/N] " reply
  [[ $reply == y || $reply == Y || $reply == yes || $reply == YES ]]
}

if [[ ! -e $CLAUDE_DIR ]]; then
  printf 'The detected Claude config directory does not exist.\n'
  if ! confirm "Create $CLAUDE_DIR?"; then
    printf 'Cancelled; no files were changed.\n'
    exit 0
  fi
  mkdir -p -- "$CLAUDE_DIR"
elif [[ ! -d $CLAUDE_DIR ]]; then
  printf 'ERROR: detected Claude config path is not a directory: %s\n' "$CLAUDE_DIR" >&2
  exit 1
fi

printf '\nPlanned local changes:\n'
printf '  - validate and sync settings.portable.json to the CC-Switch Claude Common Snippet when cc-switch is available\n'
printf '  - merge approved fields into %s after the Common Snippet refresh\n' "$SETTINGS"
printf '  - link CLAUDE.md and each repository-managed whitelist item individually\n'
printf '  - install an idempotent shell PATH block so %s/bin precedes the real cc-switch\n' "$REPO_DIR"
printf '  - move conflicting existing items into %s before linking\n' "$BACKUP_DIR"
printf '  - check the plugin manifest and offer to install missing plugins\n'
printf '  - register portable MCP servers through the official claude mcp CLI\n'
printf '  - merge portable hooks into %s (serena; never deletes target hooks)\n' "$SETTINGS"
if ! confirm 'Apply this portable configuration?'; then
  printf 'Cancelled; no configuration files were changed.\n'
  exit 0
fi

sync_cc_switch_common() {
  if ! command -v cc-switch >/dev/null 2>&1; then
    printf '\nCC-Switch not found; skipped Common Snippet sync.\n'
    return
  fi

  local common_file
  common_file=$(mktemp "${TMPDIR:-/tmp}/cc-config-common.XXXXXX.json")
  chmod 0600 "$common_file"
  trap 'rm -f -- "$common_file"' RETURN

  python3 "$REPO_DIR/scripts/merge-settings.py" common \
    --portable "$REPO_DIR/settings.portable.json" \
    --output "$common_file"
  cc-switch --app claude config common set --file "$common_file" --apply
  printf 'CC-Switch Claude Common Snippet synced from: %s\n' "$REPO_DIR/settings.portable.json"
  printf 'NOTE: The cc-config wrapper restores portable fields after successful Provider hot-switches.\n'
}

sync_cc_switch_common

# Always merge after syncing. CC-Switch may or may not refresh the live file
# immediately, and this guarantees the current Claude process sees the same source.
python3 "$REPO_DIR/scripts/merge-settings.py" merge \
  --portable "$REPO_DIR/settings.portable.json" \
  --target "$SETTINGS" \
  --backup-dir "$BACKUP_DIR"

backup_conflict() {
  local target=$1
  local relative=${target#"$CLAUDE_DIR"/}
  local destination="$BACKUP_DIR/$relative"
  mkdir -p -- "$(dirname -- "$destination")"
  mv -- "$target" "$destination"
  printf 'Backed up conflict: %s -> %s\n' "$target" "$destination"
}

link_item() {
  local source=$1
  local target=$2
  local resolved_source resolved_target
  mkdir -p -- "$(dirname -- "$target")"
  resolved_source=$(readlink -f -- "$source")
  if [[ -L $target ]]; then
    resolved_target=$(readlink -f -- "$target" 2>/dev/null || true)
    if [[ $resolved_target == "$resolved_source" ]]; then
      printf 'Link already current: %s\n' "$target"
      return
    fi
  fi
  if [[ -e $target || -L $target ]]; then
    backup_conflict "$target"
  fi
  ln -s -- "$source" "$target"
  printf 'Linked: %s -> %s\n' "$target" "$source"
}

link_item "$REPO_DIR/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"

for category in skills agents commands rules hooks output-styles; do
  source_dir="$REPO_DIR/$category"
  [[ -d $source_dir ]] || continue
  mkdir -p -- "$CLAUDE_DIR/$category"
  shopt -s nullglob dotglob
  items=("$source_dir"/*)
  shopt -u nullglob dotglob
  for source in "${items[@]}"; do
    link_item "$source" "$CLAUDE_DIR/$category/$(basename -- "$source")"
  done
done

for filename in keybindings.json; do
  [[ -f $REPO_DIR/$filename ]] || continue
  link_item "$REPO_DIR/$filename" "$CLAUDE_DIR/$filename"
done

chmod 0755 "$REPO_DIR/bin/cc-switch" "$REPO_DIR/scripts/ensure-path.py"
python3 "$REPO_DIR/scripts/ensure-path.py" \
  --backup-dir "$BACKUP_DIR/shell" \
  "$HOME/.bashrc" "$HOME/.profile"

install_plugins() {
  if ! command -v claude >/dev/null 2>&1; then
    printf '\nWARNING: claude is not on PATH; skipped plugin installation.\n' >&2
    printf 'Run ./install.sh again after installing Claude Code.\n' >&2
    return
  fi

  local installed_json marketplaces_json
  if ! installed_json=$(claude plugin list --json 2>/dev/null); then
    printf '\nWARNING: unable to inspect installed Claude plugins; skipped plugin installation.\n' >&2
    return
  fi
  if ! marketplaces_json=$(claude plugin marketplace list --json 2>/dev/null); then
    marketplaces_json='[]'
  fi

  mapfile -t missing_marketplaces < <(
    PLUGINS_FILE="$REPO_DIR/plugins.json" MARKETPLACES_JSON="$marketplaces_json" python3 - <<'PY'
import json, os
manifest = json.load(open(os.environ["PLUGINS_FILE"], encoding="utf-8"))
current = {item["name"] for item in json.loads(os.environ["MARKETPLACES_JSON"])}
for item in manifest.get("marketplaces", []):
    if item["name"] not in current:
        print(f'{item["name"]}\t{item["source"]}')
PY
  )

  if ((${#missing_marketplaces[@]})); then
    printf '\nMissing plugin marketplaces:\n'
    printf '  %s\n' "${missing_marketplaces[@]}"
    if confirm 'Add the missing marketplaces now? (network access required)'; then
      for entry in "${missing_marketplaces[@]}"; do
        source=${entry#*$'\t'}
        claude plugin marketplace add --scope user "$source"
      done
    fi
  fi

  installed_json=$(claude plugin list --json 2>/dev/null || printf '[]')
  mapfile -t missing_plugins < <(
    PLUGINS_FILE="$REPO_DIR/plugins.json" INSTALLED_JSON="$installed_json" python3 - <<'PY'
import json, os
manifest = json.load(open(os.environ["PLUGINS_FILE"], encoding="utf-8"))
current = {item["id"] for item in json.loads(os.environ["INSTALLED_JSON"])}
for plugin in manifest.get("plugins", []):
    if plugin not in current:
        print(plugin)
PY
  )

  if ((${#missing_plugins[@]})); then
    printf '\nMissing Claude plugins:\n'
    printf '  %s\n' "${missing_plugins[@]}"
    if confirm 'Install the missing plugins now? (network access required)'; then
      for plugin in "${missing_plugins[@]}"; do
        claude plugin install --scope user "$plugin"
      done
    fi
  else
    printf '\nAll manifest plugins are installed.\n'
  fi

  installed_json=$(claude plugin list --json 2>/dev/null || printf '[]')
  mapfile -t disabled_plugins < <(
    PLUGINS_FILE="$REPO_DIR/plugins.json" INSTALLED_JSON="$installed_json" python3 - <<'PY'
import json, os
manifest = set(json.load(open(os.environ["PLUGINS_FILE"], encoding="utf-8")).get("plugins", []))
for item in json.loads(os.environ["INSTALLED_JSON"]):
    if item.get("id") in manifest and not item.get("enabled", False):
        print(item["id"])
PY
  )
  for plugin in "${disabled_plugins[@]}"; do
    claude plugin enable --scope user "$plugin"
  done
}

install_plugins

install_mcp() {
  if ! command -v claude >/dev/null 2>&1; then
    printf '\nWARNING: claude is not on PATH; skipped MCP registration.\n' >&2
    return
  fi
  printf '\nRegistering portable MCP servers via the official Claude CLI...\n'
  python3 "$REPO_DIR/scripts/install-mcp.py" \
    --manifest "$REPO_DIR/mcp.portable.json" \
    --claude-bin "$(command -v claude)" \
    --scope user \
    --apply || true
}

install_hooks() {
  printf '\nMerging portable hooks into %s...\n' "$SETTINGS"
  if ! command -v serena-hooks >/dev/null 2>&1; then
    printf 'WARNING: serena-hooks is missing; portable serena hooks were not merged.\n' >&2
    printf '         Run ./doctor.sh after installing serena-hooks.\n' >&2
    return
  fi
  python3 "$REPO_DIR/scripts/merge-settings.py" merge-hooks \
    --hooks "$REPO_DIR/hooks.portable.json" \
    --target "$SETTINGS" \
    --backup-dir "$BACKUP_DIR"
}

install_mcp
install_hooks

printf '\nPortable Claude Code configuration installed.\n'
printf 'Claude config directory: %s\n' "$CLAUDE_DIR"
printf 'CC-Switch provider/API/model fields were not managed by this installer.\n'
printf 'Open a new shell (or source ~/.bashrc) so cc-switch resolves to the portable wrapper.\n'
printf 'Restart Claude Code to load plugin or status-line changes.\n'
