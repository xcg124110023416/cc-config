#!/usr/bin/env bash
set -u

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_DIR=""
PROJECT_MODE=0
for argument in "$@"; do
  case "$argument" in
    --project)
      PROJECT_MODE=1
      ;;
    --project=*)
      PROJECT_MODE=1
      PROJECT_DIR=${argument#--project=}
      ;;
    -h|--help)
      printf 'Usage: ./doctor.sh [--project /path/to/project]\n'
      printf 'Checks global Claude Code dependencies; add --project to also check project state.\n'
      exit 0
      ;;
    *)
      if ((PROJECT_MODE == 1)); then
        PROJECT_DIR=$argument
        PROJECT_MODE=0
      fi
      ;;
  esac
done
if ((PROJECT_MODE == 1)) && [[ -n $PROJECT_DIR ]]; then
  :
fi

status_ok=0

check() {
  local label=$1
  local binary=$2
  if command -v "$binary" >/dev/null 2>&1; then
    printf '%-20s OK\n' "$label"
  else
    printf '%-20s MISSING\n' "$label"
    status_ok=1
  fi
}

printf 'Global dependencies\n'
printf -- '--------------------\n'
check 'Claude Code' claude
check 'CC-Switch' cc-switch
check 'Python' python3
check 'Node' node
check 'Serena' serena
check 'Serena Hooks' serena-hooks
check 'CodeGraph' codegraph
check 'Sciverse MCP' sciverse-mcp
check 'Sciverse Server' sciverse-mcp-server

sciverse_token="${XDG_CONFIG_HOME:-$HOME/.config}/sciverse/token"
if [[ -r $sciverse_token ]]; then
  printf '%-20s OK\n' 'Sciverse Token'
else
  printf '%-20s MISSING\n' 'Sciverse Token'
  printf '  -> 需要目标机重新配置 SciVerse 凭证，不迁移 token\n'
  status_ok=1
fi

check 'MinerU' mineru
check 'magic-pdf' magic-pdf

printf '\nPortable skills CLI dependencies\n'
printf -- '--------------------------------\n'
for skill in hv-analysis khazix-writer mineru neat-freak paper-translator; do
  root="$REPO_DIR/skills/$skill"
  if [[ ! -d $root ]]; then
    continue
  fi
  if [[ -f $root/scripts/md_to_pdf.py ]]; then
    printf '%-20s %s\n' "hv-analysis" "$(command -v python3 >/dev/null 2>&1 && printf OK || printf 'MISSING python3')"
  fi
  if [[ $skill == mineru ]]; then
    if command -v mineru >/dev/null 2>&1 || command -v magic-pdf >/dev/null 2>&1; then
      printf '%-20s OK\n' 'mineru'
    else
      printf '%-20s MISSING\n' 'mineru'
      status_ok=1
    fi
  fi
done

printf '\nPlugins\n'
printf -- '-------\n'
if command -v claude >/dev/null 2>&1; then
  plugin_json=$(claude plugin list --json 2>/dev/null || printf '[]')
  INSTALLED_JSON="$plugin_json" PLUGINS_FILE="$REPO_DIR/plugins.json" python3 - <<'PY'
import json, os, sys
try:
    installed = {item.get("id") for item in json.loads(os.environ["INSTALLED_JSON"])}
except Exception:
    installed = set()
try:
    manifest = json.load(open(os.environ["PLUGINS_FILE"], encoding="utf-8")).get("plugins", [])
except Exception:
    manifest = []
missing = [plugin for plugin in manifest if plugin not in installed]
for plugin in sorted(installed):
    print(f"{plugin:<20} OK")
for plugin in sorted(missing):
    print(f"{plugin:<20} MISSING")
if missing:
    sys.exit(1)
PY
  [[ $? -ne 0 ]] && status_ok=1
else
  printf '%-20s MISSING\n' 'plugin check'
fi

if [[ -n $PROJECT_DIR ]]; then
  printf '\nProject state: %s\n' "$PROJECT_DIR"
  printf '------------------\n'
  if [[ -d $PROJECT_DIR/.codegraph ]]; then
    printf '%-20s OK\n' '.codegraph/'
  else
    printf '%-20s NOT_INDEXED\n' '.codegraph/'
  fi
  if [[ -f $PROJECT_DIR/CLAUDE.md || -f $PROJECT_DIR/README.md ]]; then
    printf '%-20s OK\n' 'project docs'
  fi
fi

printf '\n'
if ((status_ok == 0)); then
  printf 'Doctor: all global dependencies present.\n'
  exit 0
fi
printf 'Doctor: one or more dependencies missing. Nothing was installed.\n'
exit 1
