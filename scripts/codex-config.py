#!/usr/bin/env python3
"""Restore and inspect portable Codex configuration on the current host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- cc-config:codex begin -->"
END = "<!-- cc-config:codex end -->"
ALLOWED_SETTINGS = {"model_reasoning_effort": {"minimal", "low", "medium", "high", "xhigh"}}
SETTING_LINE = re.compile(r"^\s*model_reasoning_effort\s*=\s*(['\"])([^'\"]+)\1\s*(?:#.*)?$")


def paths() -> tuple[Path, Path, Path]:
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    if not codex_home.is_absolute():
        raise ValueError("CODEX_HOME must be an absolute path")
    if os.name != "nt" and any(
        re.match(r"^/mnt/[a-z]/", str(path.resolve()), re.I)
        for path in (home, codex_home)
    ):
        raise ValueError("Codex home is on a Windows drive; use this system's own home directory")
    return home, codex_home, home / ".agents" / "skills"


def local_binary(name: str) -> str | None:
    binary = shutil.which(name)
    # WSL may inherit Windows PATH entries. Do not use another system's tools.
    if binary and os.name != "nt" and re.match(r"^/mnt/[a-z]/", str(Path(binary).resolve()), re.I):
        return None
    return binary


def codex_binary() -> str | None:
    binary = local_binary("codex")
    if not binary and os.name == "nt":
        app_data = os.environ.get("LOCALAPPDATA")
        if app_data:
            bundled = Path(app_data) / "OpenAI" / "Codex" / "bin" / "codex.exe"
            if bundled.is_file():
                binary = str(bundled)
    if not binary and shutil.which("codex"):
        print("[SKIP] codex on a Windows mount; install the Linux CLI in this system")
    return binary


def manifest() -> dict:
    data = json.loads((ROOT / "codex" / "portable.json").read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("mcp_servers"), list):
        raise ValueError("invalid codex/portable.json")
    settings = data.get("settings")
    if not isinstance(settings, dict) or any(
        key not in ALLOWED_SETTINGS or value not in ALLOWED_SETTINGS[key]
        for key, value in settings.items()
    ):
        raise ValueError("unsupported portable Codex setting")
    servers = json.loads((ROOT / "mcp.portable.json").read_text(encoding="utf-8"))["servers"]
    known = {server["name"] for server in servers}
    if any(not isinstance(name, str) or name not in known for name in data["mcp_servers"]):
        raise ValueError("Codex MCP list contains an unknown server")
    return data


def skill_digest(directory: Path) -> str:
    result = hashlib.sha256()
    for item in sorted(directory.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"skill contains a symlink: {item}")
        if item.is_file():
            result.update(item.relative_to(directory).as_posix().encode("utf-8"))
            result.update(item.read_bytes())
    return result.hexdigest()


def save(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(dir=path.parent, prefix=".cc-config-", suffix=".tmp")
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup(path: Path, backup_dir: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / path.name
    if path.is_dir() and not path.is_symlink():
        shutil.copytree(path, target)
    else:
        shutil.copy2(path, target, follow_symlinks=False)


def managed_guidance(existing: str, portable: str) -> str:
    block = BEGIN + "\n" + portable.rstrip() + "\n" + END
    if existing.count(BEGIN) != existing.count(END) or existing.count(BEGIN) > 1:
        raise ValueError("invalid Codex AGENTS.md managed block")
    if BEGIN not in existing:
        return existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
    start = existing.index(BEGIN)
    end = existing.index(END) + len(END)
    if end < start:
        raise ValueError("reversed Codex AGENTS.md managed block")
    return existing[:start] + block + existing[end:]


def setting_values(content: str) -> dict[str, str]:
    """Find the one managed top-level TOML key without parsing private fields."""
    values: dict[str, str] = {}
    top = True
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("#"):
            top = False
        if top and re.match(r"^\s*model_reasoning_effort\s*=", line):
            match = SETTING_LINE.match(line)
            if not match or "model_reasoning_effort" in values:
                raise ValueError("ambiguous model_reasoning_effort in config.toml")
            values["model_reasoning_effort"] = match.group(2)
    return values


def wanted_servers(names: list[str]) -> list[dict]:
    source = json.loads((ROOT / "mcp.portable.json").read_text(encoding="utf-8"))
    indexed = {item["name"]: item for item in source["servers"]}
    results = []
    for name in names:
        server = indexed[name]
        command, args = server["command"], list(server.get("args", []))
        requires = [command, *server.get("requires", [])]
        if name == "serena":
            args = [arg.replace("--context=claude-code", "--context=codex") for arg in args]
        if name == "sciverse":
            command = sys.executable
            args = [str(ROOT / "scripts" / "sciverse-mcp-codex.py")]
            requires = ["sciverse-mcp-server"]
        results.append({"name": name, "command": command, "args": args, "requires": requires})
    return results


def run(binary: str, *args: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(paths()[1])
    return subprocess.run(
        [binary, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, env=environment,
    )


def mcp_status(binary: str, server: dict) -> str:
    response = run(binary, "mcp", "get", server["name"], "--json")
    if response.returncode:
        return "missing"
    try:
        current = json.loads(response.stdout)
    except json.JSONDecodeError:
        return "conflict"
    transport = current.get("transport", {})
    if (
        isinstance(transport, dict)
        and transport.get("type") == "stdio"
        and transport.get("command") == server["command"]
        and transport.get("args", []) == server["args"]
        and not transport.get("env")
        and not transport.get("env_vars")
        and current.get("enabled", True)
    ):
        return "current"
    return "conflict"


def merge_hooks(target: Path, source: Path) -> str:
    portable = json.loads(source.read_text(encoding="utf-8"))["hooks"]
    current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(current, dict) or not isinstance(current.get("hooks", {}), dict):
        raise ValueError("Codex hooks.json has an unexpected shape")
    groups = current.setdefault("hooks", {})
    for event, additions in portable.items():
        existing = groups.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"invalid Codex hook event: {event}")
        for group in additions:
            if group not in existing:
                existing.append(group)
    return json.dumps(current, indent=2, ensure_ascii=False) + "\n"


def install(data: dict, codex_home: Path, skills_home: Path, backup_dir: Path, state: dict, binary: str | None) -> int:
    guidance = codex_home / "AGENTS.md"
    existing = guidance.read_text(encoding="utf-8") if guidance.exists() else ""
    updated = managed_guidance(existing, (ROOT / "codex" / "AGENTS.md").read_text(encoding="utf-8"))
    config = codex_home / "config.toml"
    current = config.read_text(encoding="utf-8") if config.exists() else ""
    found = setting_values(current)
    hooks_target = codex_home / "hooks.json"
    inline_hooks = bool(re.search(r"(?m)^\s*\[hooks(?:[.\]]|$)", current))
    portable_hooks = None
    if local_binary("serena-hooks") and not inline_hooks:
        portable_hooks = merge_hooks(hooks_target, ROOT / "codex" / "hooks.json")

    if updated != existing:
        backup(guidance, backup_dir)
        save(guidance, updated)
        print(f"[ADD] global guidance: {guidance}")

    for key, desired in data["settings"].items():
        if key in found and found[key] != desired:
            print(f"[KEEP] existing {key}; portable preference is {desired}")
        elif key not in found:
            backup(config, backup_dir)
            current = f'{key} = "{desired}"\n' + current
            save(config, current)
            print(f"[ADD] {key} in {config}")

    skills_home.mkdir(parents=True, exist_ok=True)
    installed = state.setdefault("skills", {})
    for source in sorted((ROOT / "skills").iterdir()):
        if not (source / "SKILL.md").is_file():
            continue
        destination = skills_home / source.name
        desired = skill_digest(source)
        if destination.is_symlink() and destination.resolve() == source.resolve():
            print(f"[OK] skill {source.name}")
            continue
        current_digest = skill_digest(destination) if destination.is_dir() and not destination.is_symlink() else None
        if current_digest == desired:
            installed[source.name] = desired
            print(f"[OK] skill {source.name}")
            continue
        if destination.is_symlink() and not destination.exists():
            backup(destination, backup_dir / "skills")
            destination.unlink()
        elif destination.exists() or destination.is_symlink():
            if current_digest != installed.get(source.name) or current_digest is None:
                print(f"[KEEP] local skill differs: {destination}")
                continue
            backup(destination, backup_dir / "skills")
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        installed[source.name] = desired
        print(f"[ADD] skill {source.name}: {destination}")

    if inline_hooks:
        print("[KEEP] inline Codex hooks present; review before adding portable hooks")
    elif portable_hooks is not None:
        if not hooks_target.exists() or hooks_target.read_text(encoding="utf-8") != portable_hooks:
            backup(hooks_target, backup_dir)
            save(hooks_target, portable_hooks)
            print(f"[ADD] Serena hooks: {hooks_target}")
    else:
        if not inline_hooks:
            print("[SKIP] Serena hooks: native serena-hooks is not installed")

    if binary:
        for server in wanted_servers(data["mcp_servers"]):
            name = server["name"]
            status = mcp_status(binary, server)
            missing = [dep for dep in server["requires"] if local_binary(dep) is None]
            if status == "current":
                if missing:
                    print(f"[WARN] MCP {name} is configured but local dependency is missing: {', '.join(missing)}")
                else:
                    print(f"[OK] MCP {name}")
            elif status == "conflict":
                print(f"[KEEP] existing MCP {name} differs; inspect with codex mcp get {name}")
            elif missing:
                print(f"[SKIP] MCP {name}: local dependency is missing: {', '.join(missing)}")
            else:
                response = run(binary, "mcp", "add", name, "--", server["command"], *server["args"])
                print(f"[{'ADD' if response.returncode == 0 else 'WARN'}] MCP {name}")
    else:
        print("[SKIP] MCP registration: codex CLI is not installed")

    save(codex_home / ".cc-config-state.json", json.dumps(state, indent=2) + "\n")
    return 0


def inspect(data: dict, codex_home: Path, skills_home: Path, binary: str | None, *, sync: bool) -> int:
    problems = 0
    guidance = codex_home / "AGENTS.md"
    content = guidance.read_text(encoding="utf-8") if guidance.exists() else ""
    portable = (ROOT / "codex" / "AGENTS.md").read_text(encoding="utf-8")
    if BEGIN in content and managed_guidance(content, portable) == content:
        print("[OK] global AGENTS.md")
    else:
        print("[DIFF] global AGENTS.md portable block")
        problems += 1
    if (codex_home / "AGENTS.override.md").exists():
        print("[NOTE] AGENTS.override.md takes precedence over global AGENTS.md")
    config = codex_home / "config.toml"
    values = setting_values(config.read_text(encoding="utf-8") if config.exists() else "")
    for key, wanted in data["settings"].items():
        value = values.get(key)
        print(f"[{'OK' if value == wanted else 'DIFF'}] {key}: {value or 'missing'} (portable: {wanted})")
        problems += value != wanted
    for source in sorted((ROOT / "skills").iterdir()):
        if not (source / "SKILL.md").is_file():
            continue
        destination = skills_home / source.name
        current = destination.is_symlink() and destination.resolve() == source.resolve()
        if not current and destination.is_dir() and not destination.is_symlink():
            current = skill_digest(source) == skill_digest(destination)
        print(f"[{'OK' if current else 'DIFF'}] skill {source.name}")
        problems += not current
    if binary:
        for server in wanted_servers(data["mcp_servers"]):
            status = mcp_status(binary, server)
            missing = [dep for dep in server["requires"] if local_binary(dep) is None]
            detail = f"; local dependency missing: {', '.join(missing)}" if missing else ""
            print(f"[{'OK' if status == 'current' and not missing else 'DIFF'}] MCP {server['name']}: {status}{detail}")
            problems += (status != "current" or bool(missing)) and not sync
    else:
        print("[DIFF] codex CLI missing; cannot inspect MCP")
        problems += not sync
    if re.search(r"(?m)^\s*\[hooks(?:[.\]]|$)", config.read_text(encoding="utf-8") if config.exists() else ""):
        print("[SKIP] inline Codex hooks present; review manually")
    elif (codex_home / "hooks.json").exists() and local_binary("serena-hooks"):
        target = codex_home / "hooks.json"
        existing = json.loads(target.read_text(encoding="utf-8"))
        merged = json.loads(merge_hooks(target, ROOT / "codex" / "hooks.json"))
        print(f"[{'OK' if merged == existing else 'DIFF'}] Serena hooks")
    else:
        print("[SKIP] optional Serena hooks")
    if sync:
        print("Review DIFF entries before changing repository files; no local state was imported.")
        return 0
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "doctor", "sync"))
    parser.add_argument("--yes", action="store_true", help="Apply install without interactive confirmation")
    args = parser.parse_args()
    data = manifest()
    _, codex_home, skills_home = paths()
    binary = codex_binary()
    if args.action != "install":
        return inspect(data, codex_home, skills_home, binary, sync=args.action == "sync")
    print(f"Codex home: {codex_home}\nSkills: {skills_home}")
    print("Install portable guidance, skills, one missing preference, optional hooks and missing MCP servers.")
    if not args.yes:
        if not sys.stdin.isatty() or input("Apply to this host? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled; nothing changed.")
            return 0
    state_file = codex_home / ".cc-config-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    backup_dir = codex_home / "backups" / ("cc-config-codex-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    return install(data, codex_home, skills_home, backup_dir, state, binary)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
