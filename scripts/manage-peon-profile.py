#!/usr/bin/env python3
"""Install and reconcile the host-native peon-ping profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "profiles" / "peon-ping" / "profile.json"
STATE_NAME = ".cc-config-profile.json"
UNIX_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
    "UserPromptSubmit",
    "Stop",
    "Notification",
    "PermissionRequest",
    "PreToolUse",
    "PostToolUseFailure",
    "PreCompact",
)


class ProfileError(RuntimeError):
    """A profile cannot be selected, installed, or reconciled safely."""


def load_json(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if missing_ok and not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileError(f"JSON root must be an object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def is_wsl() -> bool:
    if os.name != "posix" or platform.system() != "Linux":
        return False
    for path in (Path("/proc/sys/kernel/osrelease"), Path("/proc/version")):
        try:
            if "microsoft" in path.read_text(encoding="utf-8").lower():
                return True
        except OSError:
            continue
    return False


def detected_profile() -> str:
    if os.name == "nt" or platform.system() == "Windows":
        return "windows"
    if platform.system() == "Darwin":
        return "macos"
    if platform.system() == "Linux":
        return "wsl-native" if is_wsl() else "linux"
    return "none"


def select_profile(requested: str | None, manifest: dict[str, Any]) -> str:
    value = requested or os.environ.get("CC_CONFIG_PEON_PROFILE", "auto")
    value = value.strip().lower()
    if value in ("", "auto"):
        value = detected_profile()
    if value == "none":
        return value
    profiles = manifest.get("profiles", {})
    if value not in profiles:
        choices = ", ".join(["auto", "none", *sorted(profiles)])
        raise ProfileError(f"unknown peon profile {value!r}; choose one of: {choices}")
    return value


def claude_dir_from(value: str | None) -> Path:
    raw = value or os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".claude"


def install_dir(claude_dir: Path) -> Path:
    return claude_dir / "hooks" / "peon-ping"


def state_path(claude_dir: Path) -> Path:
    return install_dir(claude_dir) / STATE_NAME


def configured_linux_player(claude_dir: Path) -> str | None:
    config = load_json(install_dir(claude_dir) / "config.json", missing_ok=True)
    value = config.get("linux_audio_player")
    return value if isinstance(value, str) and value else None


def unix_audio_player(profile_id: str | None = None, claude_dir: Path | None = None) -> str | None:
    configured = configured_linux_player(claude_dir) if claude_dir else None
    preferred = configured or os.environ.get("LINUX_AUDIO_PLAYER")
    candidates = [preferred] if preferred else []
    if profile_id == "wsl-native" and Path("/mnt/wslg/PulseServer").exists():
        candidates.extend(["paplay", "ffplay", "mpv", "play", "aplay", "pw-play"])
    else:
        candidates.extend(["pw-play", "paplay", "ffplay", "mpv", "play", "aplay"])
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate
    return None


def profile_problems(profile_id: str, claude_dir: Path) -> list[str]:
    if profile_id == "none":
        return []
    problems: list[str] = []
    if profile_id in ("wsl-native", "linux", "macos"):
        if not shutil.which("bash"):
            problems.append("bash is missing")
        if not shutil.which("python3"):
            problems.append("python3 is missing")
        if profile_id in ("wsl-native", "linux") and not unix_audio_player(profile_id, claude_dir):
            problems.append("no Linux audio player (pw-play, paplay, ffplay, mpv, play, or aplay)")
        if profile_id == "wsl-native":
            pulse = os.environ.get("PULSE_SERVER", "")
            wslg = Path("/mnt/wslg/PulseServer")
            if not pulse and not wslg.exists():
                problems.append("WSLg/PulseAudio endpoint is missing")
        if profile_id == "macos" and not shutil.which("afplay"):
            problems.append("afplay is missing")
    elif profile_id == "windows":
        if not (shutil.which("powershell") or shutil.which("pwsh")):
            problems.append("PowerShell is missing")
    directory = install_dir(claude_dir)
    runtime = directory / ("peon.ps1" if profile_id == "windows" else "peon.sh")
    if not runtime.exists():
        problems.append(f"runtime is missing: {runtime}")
    if profile_id != "windows" and not (directory / "host-native.sh").exists():
        problems.append(f"host wrapper is missing: {directory / 'host-native.sh'}")
    required_skills = ("peon-ping-config", "peon-ping-toggle", "peon-ping-use")
    for skill in required_skills:
        if not (claude_dir / "skills" / skill / "SKILL.md").exists():
            problems.append(f"profile skill is missing: {skill}")
    return problems


def archive_url(manifest: dict[str, Any]) -> str:
    upstream = manifest["upstream"]
    return f"https://codeload.github.com/{upstream['repository']}/tar.gz/{upstream['ref']}"


def download_archive(manifest: dict[str, Any], destination: Path) -> None:
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(archive_url(manifest), timeout=30) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    except OSError as exc:
        raise ProfileError(f"cannot download pinned peon-ping source: {exc}") from exc
    expected = manifest["upstream"]["archive_sha256"]
    if digest.hexdigest() != expected:
        destination.unlink(missing_ok=True)
        raise ProfileError("downloaded peon-ping archive failed SHA-256 verification")


def safe_extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as bundle:
        root = destination.resolve()
        members = bundle.getmembers()
        for member in members:
            resolved = (destination / member.name).resolve()
            if root not in resolved.parents and resolved != root:
                raise ProfileError(f"unsafe path in peon-ping archive: {member.name}")
            if member.issym() or member.islnk():
                raise ProfileError(f"links are not accepted in peon-ping archive: {member.name}")
        bundle.extractall(destination, members=members)
    directories = [item for item in destination.iterdir() if item.is_dir()]
    if len(directories) != 1:
        raise ProfileError("unexpected peon-ping archive layout")
    return directories[0]


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    try:
        subprocess.run(command, check=True, env=env)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProfileError(f"command failed: {' '.join(command)}") from exc


def preserve_local_state(directory: Path) -> dict[str, bytes]:
    preserved: dict[str, bytes] = {}
    for name in ("config.json", ".state.json"):
        path = directory / name
        try:
            preserved[name] = path.read_bytes()
        except OSError:
            pass
    return preserved


def restore_local_state(directory: Path, preserved: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in preserved.items():
        path = directory / name
        path.write_bytes(content)
        os.chmod(path, 0o600)


def backup_settings(settings_path: Path, claude_dir: Path) -> Path | None:
    if not settings_path.exists():
        return None
    backup_dir = claude_dir / "backups" / "cc-config-peon"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"settings.json.{stamp}-{time.time_ns() % 1_000_000_000:09d}.bak"
    shutil.copy2(settings_path, destination)
    os.chmod(destination, 0o600)
    return destination


def write_unix_wrapper(profile_id: str, claude_dir: Path, platform_id: str) -> Path:
    directory = install_dir(claude_dir)
    wrapper = directory / "host-native.sh"
    player = unix_audio_player(profile_id, claude_dir) if profile_id in ("wsl-native", "linux") else None
    player_export = f"export LINUX_AUDIO_PLAYER={shlex.quote(player)}\n" if player else ""
    content = f"""#!/usr/bin/env bash
# Generated by cc-config for profile: {profile_id}
set -euo pipefail
script=${{BASH_SOURCE[0]}}
while [[ -L $script ]]; do
  directory=$(cd -- \"$(dirname -- \"$script\")\" && pwd -P)
  target=$(readlink -- \"$script\")
  [[ $target == /* ]] && script=$target || script=$directory/$target
done
export PEON_PLATFORM={platform_id!r}
{player_export}export CLAUDE_PEON_DIR=\"$(cd -- \"$(dirname -- \"$script\")\" && pwd -P)\"
exec \"$CLAUDE_PEON_DIR/peon.sh\" \"$@\"
"""
    with wrapper.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.chmod(wrapper, 0o755)
    return wrapper


def is_peon_command(command: Any) -> bool:
    if not isinstance(command, str):
        return False
    lowered = command.lower().replace("\\", "/")
    if "peon-ping" not in lowered:
        return False
    names = (
        "peon.sh",
        "peon.ps1",
        "host-native.sh",
        "hook-handle-use.sh",
        "hook-handle-use.ps1",
        "hook-handle-rename.sh",
        "notify.sh",
    )
    return any(name in lowered for name in names)


def clean_peon_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        settings["hooks"] = {}
        return settings
    cleaned_events: dict[str, Any] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            cleaned_events[event] = groups
            continue
        kept_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept_handlers = [
                handler
                for handler in group["hooks"]
                if not (
                    isinstance(handler, dict)
                    and is_peon_command(handler.get("command"))
                )
            ]
            if kept_handlers:
                updated = dict(group)
                updated["hooks"] = kept_handlers
                kept_groups.append(updated)
        if kept_groups:
            cleaned_events[event] = kept_groups
    settings["hooks"] = cleaned_events
    return settings


def add_handler(hooks: dict[str, Any], event: str, matcher: str, handler: dict[str, Any]) -> None:
    groups = hooks.setdefault(event, [])
    target = next(
        (
            group
            for group in groups
            if isinstance(group, dict)
            and group.get("matcher", "") == matcher
            and isinstance(group.get("hooks"), list)
        ),
        None,
    )
    if target is None:
        groups.append({"matcher": matcher, "hooks": [handler]})
    else:
        target["hooks"].append(handler)


def reconcile_unix_hooks(claude_dir: Path) -> None:
    settings_path = claude_dir / "settings.json"
    current = load_json(settings_path, missing_ok=True)
    settings = clean_peon_hooks(json.loads(json.dumps(current)))
    hooks = settings.setdefault("hooks", {})
    base = '"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/peon-ping'
    runtime = f'{base}/host-native.sh"'
    use_handler = f'{base}/scripts/hook-handle-use.sh"'
    rename_handler = f'{base}/scripts/hook-handle-rename.sh"'
    for event in UNIX_EVENTS:
        matcher = "Bash" if event == "PostToolUseFailure" else ""
        handler: dict[str, Any] = {"type": "command", "command": runtime, "timeout": 10}
        if event != "SessionStart":
            handler["async"] = True
        add_handler(hooks, event, matcher, handler)
    add_handler(hooks, "UserPromptSubmit", "", {"type": "command", "command": use_handler, "timeout": 5})
    add_handler(hooks, "UserPromptSubmit", "", {"type": "command", "command": rename_handler, "timeout": 5})
    if settings != current:
        backup_settings(settings_path, claude_dir)
        atomic_json(settings_path, settings)


def reconcile_windows_hooks(claude_dir: Path) -> None:
    settings_path = claude_dir / "settings.json"
    current = load_json(settings_path, missing_ok=True)
    settings = clean_peon_hooks(json.loads(json.dumps(current)))
    hooks = settings.setdefault("hooks", {})
    directory = install_dir(claude_dir)
    runtime_path = directory / "peon.ps1"
    use_path = directory / "scripts" / "hook-handle-use.ps1"
    runtime = f'powershell -NoProfile -NonInteractive -File "{runtime_path}"'
    use_handler = f'powershell -NoProfile -NonInteractive -File "{use_path}"'
    events = (
        "SessionStart",
        "SessionEnd",
        "SubagentStart",
        "Stop",
        "Notification",
        "PermissionRequest",
        "PreToolUse",
        "PostToolUseFailure",
        "PreCompact",
    )
    for event in events:
        add_handler(hooks, event, "", {"type": "command", "command": runtime, "timeout": 10})
    add_handler(hooks, "UserPromptSubmit", "", {"type": "command", "command": use_handler, "timeout": 5})
    if settings != current:
        backup_settings(settings_path, claude_dir)
        atomic_json(settings_path, settings)


def copy_runtime_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        elif item.is_file():
            shutil.copy2(item, target)


def install_unix(profile_id: str, profile: dict[str, Any], manifest: dict[str, Any], claude_dir: Path) -> None:
    directory = install_dir(claude_dir)
    with tempfile.TemporaryDirectory(prefix="cc-config-peon-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "source.tar.gz"
        source_parent = temporary_path / "source"
        source_parent.mkdir()
        download_archive(manifest, archive)
        source = safe_extract(archive, source_parent)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("peon.sh", "relay.sh", "completions.bash", "completions.fish", "completions.zsh", "VERSION", "uninstall.sh"):
            candidate = source / name
            if candidate.exists():
                shutil.copy2(candidate, directory / name)
        for name in ("adapters", "scripts", "docs"):
            candidate = source / name
            if candidate.is_dir():
                copy_runtime_tree(candidate, directory / name)
        config_path = directory / "config.json"
        if not config_path.exists():
            shutil.copy2(source / "config.json", config_path)
        else:
            defaults = load_json(source / "config.json")
            current = load_json(config_path)
            changed = False
            for key, value in defaults.items():
                if key not in current:
                    current[key] = value
                    changed = True
            if changed:
                atomic_json(config_path, current)
        if profile_id == "wsl-native":
            current = load_json(config_path)
            if not current.get("linux_audio_player"):
                player = unix_audio_player(profile_id)
                if player:
                    current["linux_audio_player"] = player
                    atomic_json(config_path, current)
        state_file = directory / ".state.json"
        if not state_file.exists():
            atomic_json(state_file, {})
        for skill in sorted((source / "skills").glob("peon-ping-*")):
            if skill.is_dir():
                copy_runtime_tree(skill, claude_dir / "skills" / skill.name)
        pack_script = directory / "scripts" / "pack-download.sh"
        packs = ",".join(manifest["default_packs"])
        run(["bash", str(pack_script), f"--dir={directory}", f"--packs={packs}"])
    for obsolete in (
        directory / "peon.ps1",
        directory / "scripts" / "win-play.ps1",
        directory / "scripts" / "win-notify.ps1",
    ):
        if obsolete.exists():
            obsolete.unlink()
    for path in (directory / "peon.sh", directory / "relay.sh"):
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for path in (directory / "scripts").glob("*.sh"):
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    wrapper = write_unix_wrapper(profile_id, claude_dir, profile["platform"])
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    shortcut = local_bin / "peon"
    if shortcut.exists() or shortcut.is_symlink():
        shortcut.unlink()
    shortcut.symlink_to(wrapper)
    reconcile_unix_hooks(claude_dir)


def install_windows(profile_id: str, manifest: dict[str, Any], claude_dir: Path) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise ProfileError("PowerShell is required for the Windows profile")
    directory = install_dir(claude_dir)
    preserved = preserve_local_state(directory)
    with tempfile.TemporaryDirectory(prefix="cc-config-peon-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / "source.tar.gz"
        source_parent = temporary_path / "source"
        source_parent.mkdir()
        download_archive(manifest, archive)
        source = safe_extract(archive, source_parent)
        installer = source / "install.ps1"
        text = installer.read_text(encoding="utf-8-sig")
        needle = '$GlobalClaudeDir = Join-Path $env:USERPROFILE ".claude"'
        if text.count(needle) != 1:
            raise ProfileError("pinned Windows installer config path hook changed unexpectedly")
        replacement = '$GlobalClaudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE ".claude" }'
        installer.write_text(text.replace(needle, replacement), encoding="utf-8-sig")
        environment = os.environ.copy()
        environment["CLAUDE_CONFIG_DIR"] = str(claude_dir)
        run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer), "-Global"], env=environment)
    restore_local_state(directory, preserved)


def write_state(profile_id: str, manifest: dict[str, Any], claude_dir: Path) -> None:
    atomic_json(
        state_path(claude_dir),
        {
            "version": 1,
            "profile": profile_id,
            "upstream_ref": manifest["upstream"]["ref"],
            "managed_by": "cc-config",
        },
    )


def current_state(claude_dir: Path) -> dict[str, Any]:
    return load_json(state_path(claude_dir), missing_ok=True)


def profile_is_current(profile_id: str, manifest: dict[str, Any], claude_dir: Path) -> bool:
    state = current_state(claude_dir)
    runtime = install_dir(claude_dir) / ("peon.ps1" if profile_id == "windows" else "peon.sh")
    wrapper = install_dir(claude_dir) / "host-native.sh"
    skills_ok = all(
        (claude_dir / "skills" / skill / "SKILL.md").exists()
        for skill in ("peon-ping-config", "peon-ping-toggle", "peon-ping-use")
    )
    packs_ok = all(
        (install_dir(claude_dir) / "packs" / pack / "sounds").is_dir()
        and any((install_dir(claude_dir) / "packs" / pack / "sounds").iterdir())
        for pack in manifest["default_packs"]
    )
    return (
        state.get("profile") == profile_id
        and state.get("upstream_ref") == manifest["upstream"]["ref"]
        and runtime.exists()
        and (profile_id == "windows" or wrapper.exists())
        and skills_ok
        and packs_ok
    )


def count_peon_handlers(settings_path: Path) -> tuple[int, list[str]]:
    settings = load_json(settings_path, missing_ok=True)
    commands: list[str] = []
    hooks = settings.get("hooks", {})
    if isinstance(hooks, dict):
        for groups in hooks.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for handler in group.get("hooks", []):
                    if isinstance(handler, dict) and is_peon_command(handler.get("command")):
                        commands.append(handler["command"])
    return len(commands), commands


def command_detect(args: argparse.Namespace) -> int:
    manifest = load_json(MANIFEST_PATH)
    print(select_profile(args.profile, manifest))
    return 0


def command_install(args: argparse.Namespace) -> int:
    manifest = load_json(MANIFEST_PATH)
    profile_id = select_profile(args.profile, manifest)
    claude_dir = claude_dir_from(args.claude_dir)
    if profile_id == "none":
        print("Peon profile: NOT_APPLICABLE")
        return 0
    profile = manifest["profiles"][profile_id]
    artifact_prefixes = ("runtime is missing", "host wrapper is missing", "profile skill is missing")
    static_problems = [
        problem for problem in profile_problems(profile_id, claude_dir)
        if not problem.startswith(artifact_prefixes)
    ]
    if static_problems:
        raise ProfileError("; ".join(static_problems))
    if profile_is_current(profile_id, manifest, claude_dir) and not args.force:
        if profile["runtime"] == "unix":
            reconcile_unix_hooks(claude_dir)
        else:
            reconcile_windows_hooks(claude_dir)
        print(f"Peon profile already current: {profile_id}")
        return 0
    if profile["runtime"] == "unix":
        install_unix(profile_id, profile, manifest, claude_dir)
    else:
        install_windows(profile_id, manifest, claude_dir)
    write_state(profile_id, manifest, claude_dir)
    print(f"Peon profile installed: {profile_id}")
    return 0


def command_reconcile(args: argparse.Namespace) -> int:
    manifest = load_json(MANIFEST_PATH)
    profile_id = select_profile(args.profile, manifest)
    claude_dir = claude_dir_from(args.claude_dir)
    if profile_id == "none":
        print("Peon profile: NOT_APPLICABLE")
        return 0
    profile = manifest["profiles"][profile_id]
    if profile["runtime"] == "unix":
        if not (install_dir(claude_dir) / "peon.sh").exists():
            raise ProfileError("peon.sh is not installed; run the install command first")
        write_unix_wrapper(profile_id, claude_dir, profile["platform"])
        reconcile_unix_hooks(claude_dir)
    else:
        if not (install_dir(claude_dir) / "peon.ps1").exists():
            raise ProfileError("peon.ps1 is not installed; run the install command first")
        reconcile_windows_hooks(claude_dir)
    write_state(profile_id, manifest, claude_dir)
    print(f"Peon hooks reconciled: {profile_id}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    manifest = load_json(MANIFEST_PATH)
    profile_id = select_profile(args.profile, manifest)
    claude_dir = claude_dir_from(args.claude_dir)
    print(f"Detected profile: {detected_profile()}")
    print(f"Selected profile: {profile_id}")
    if profile_id == "none":
        print("Status: NOT_APPLICABLE")
        return 0
    problems = profile_problems(profile_id, claude_dir)
    state = current_state(claude_dir)
    if state.get("profile") != profile_id:
        problems.append(f"installed profile is {state.get('profile', 'unmanaged')}")
    if state.get("upstream_ref") != manifest["upstream"]["ref"]:
        problems.append("installed runtime does not match the pinned upstream revision")
    directory = install_dir(claude_dir)
    installed_packs = directory / "packs"
    for pack in manifest["default_packs"]:
        sounds = installed_packs / pack / "sounds"
        if not sounds.is_dir() or not any(item.is_file() for item in sounds.iterdir()):
            problems.append(f"default sound pack is missing or empty: {pack}")
    count, commands = count_peon_handlers(claude_dir / "settings.json")
    expected = 13 if manifest["profiles"][profile_id]["runtime"] == "unix" else 10
    if count != expected:
        problems.append(f"expected {expected} peon handlers, found {count}")
    if profile_id == "wsl-native" and any("powershell" in command.lower() for command in commands):
        problems.append("WSL profile still contains a PowerShell peon hook")
    if problems:
        print("Status: MISSING")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    player = unix_audio_player(profile_id, claude_dir) if profile_id in ("wsl-native", "linux") else None
    if player:
        print(f"Audio backend: {player}")
    print(f"Handlers: {count}")
    print("Status: OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("detect", command_detect),
        ("install", command_install),
        ("reconcile", command_reconcile),
        ("status", command_status),
    ):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--profile", help="auto, none, wsl-native, linux, macos, or windows")
        if name != "detect":
            subparser.add_argument("--claude-dir")
        if name == "install":
            subparser.add_argument("--force", action="store_true")
        subparser.set_defaults(handler=handler)
    return parser


def main() -> int:
    global MANIFEST_PATH
    parser = build_parser()
    args = parser.parse_args()
    MANIFEST_PATH = args.manifest
    try:
        return args.handler(args)
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
