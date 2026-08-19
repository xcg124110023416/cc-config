#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
LOCAL_BACKUPS="$REPO_DIR/.local-backups"
TMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TMP_DIR"' EXIT

printf 'Claude config directory: %s\n' "$CLAUDE_DIR"
printf 'Portable config repository: %s\n' "$REPO_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'ERROR: python3 is required. No repository files were changed.\n' >&2
  exit 1
fi
if [[ ! -d $CLAUDE_DIR ]]; then
  printf 'ERROR: detected Claude config directory does not exist: %s\n' "$CLAUDE_DIR" >&2
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

same_path() {
  [[ $(readlink -f -- "$1" 2>/dev/null || true) == $(readlink -f -- "$2" 2>/dev/null || true) ]]
}

show_diff() {
  local source=$1 target=$2
  if [[ -e $target || -L $target ]]; then
    diff -ruN -- "$target" "$source" || true
  else
    printf '\nNEW portable candidate: %s -> %s\n' "$source" "$target"
    if [[ -f $source ]]; then
      diff -u -- /dev/null "$source" || true
    else
      find "$source" -type f -printf '  + %P\n' | sort
    fi
  fi
}

backup_repo_item() {
  local target=$1
  local relative=${target#"$REPO_DIR"/}
  local destination="$LOCAL_BACKUPS/$(date +%Y%m%d-%H%M%S-%N)/$relative"
  mkdir -p -- "$(dirname -- "$destination")"
  cp -a -- "$target" "$destination"
  printf 'Repository backup: %s\n' "$destination"
}

replace_from_stage() {
  local stage=$1 target=$2
  if [[ -e $target || -L $target ]]; then
    backup_repo_item "$target"
    rm -rf -- "$target"
  fi
  mkdir -p -- "$(dirname -- "$target")"
  cp -a -- "$stage" "$target"
  printf 'Imported: %s\n' "$target"
}

stage_and_offer() {
  local source=$1 target=$2 label=$3
  local stage="$TMP_DIR/candidate-$(printf '%s' "$label" | tr '/ ' '__')"
  if [[ -L $source ]]; then
    if [[ -e $target ]] && same_path "$source" "$target"; then
      return
    fi
    printf '\nSKIP: source candidate is a symlink not owned by this repository: %s\n' "$source"
    return
  fi
  cp -a -- "$source" "$stage"
  if ! python3 "$REPO_DIR/scripts/audit-portable.py" "$stage"; then
    printf 'SKIP: candidate failed the portable safety audit: %s\n' "$source"
    return
  fi
  if [[ -e $target ]] && diff -qr -- "$target" "$stage" >/dev/null 2>&1; then
    return
  fi
  printf '\nChange detected: %s\n' "$label"
  show_diff "$stage" "$target"
  if confirm "Import this change into $target?"; then
    replace_from_stage "$stage" "$target"
  else
    printf 'Skipped: %s\n' "$label"
  fi
}

printf '\nPortable settings are repository-owned and are not imported from live settings or CC-Switch.\n'
printf 'Edit directly: %s\n' "$REPO_DIR/settings.portable.json"
python3 "$REPO_DIR/scripts/merge-settings.py" validate \
  --portable "$REPO_DIR/settings.portable.json"

printf '\nChecking CLAUDE.md and whitelist content...\n'
if [[ -f $CLAUDE_DIR/CLAUDE.md ]]; then
  stage_and_offer "$CLAUDE_DIR/CLAUDE.md" "$REPO_DIR/CLAUDE.md" "CLAUDE.md"
fi

for category in skills agents commands rules hooks output-styles; do
  source_dir="$CLAUDE_DIR/$category"
  [[ -d $source_dir ]] || continue
  shopt -s nullglob dotglob
  items=("$source_dir"/*)
  shopt -u nullglob dotglob
  for source in "${items[@]}"; do
    name=$(basename -- "$source")
    if [[ $category == skills && $name == peon-ping-* ]]; then
      printf 'SKIP: profile-owned skill is refreshed by profiles/peon-ping: %s\n' "$source"
      continue
    fi
    stage_and_offer "$source" "$REPO_DIR/$category/$name" "$category/$name"
  done
done

for filename in keybindings.json; do
  [[ -f $CLAUDE_DIR/$filename ]] || continue
  stage_and_offer "$CLAUDE_DIR/$filename" "$REPO_DIR/$filename" "$filename"
done

if command -v claude >/dev/null 2>&1; then
  installed_json=$(claude plugin list --json 2>/dev/null || printf '[]')
  mapfile -t unmanaged_plugins < <(
    PLUGINS_FILE="$REPO_DIR/plugins.json" INSTALLED_JSON="$installed_json" python3 - <<'PY'
import json, os
managed = set(json.load(open(os.environ["PLUGINS_FILE"], encoding="utf-8")).get("plugins", []))
for item in json.loads(os.environ["INSTALLED_JSON"]):
    if item.get("enabled") and item.get("id") not in managed:
        print(item["id"])
PY
  )
  if ((${#unmanaged_plugins[@]})); then
    printf '\nEnabled plugins not recorded in plugins.json (review manually):\n'
    printf '  - %s\n' "${unmanaged_plugins[@]}"
  fi
fi

printf '\nRunning final repository safety audit...\n'
python3 "$REPO_DIR/scripts/audit-portable.py" "$REPO_DIR" \
  --settings-validator "$REPO_DIR/scripts/merge-settings.py"
printf '\nUpdate check complete. Git add/commit/push were not run.\n'
