#!/usr/bin/env python3
"""Restore and inspect portable Codex configuration on the current host."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- cc-config:codex begin -->"
END = "<!-- cc-config:codex end -->"
ALLOWED_SETTINGS = {"model_reasoning_effort": {"minimal", "low", "medium", "high", "xhigh"}}
SETTING_LINE = re.compile(r"^\s*model_reasoning_effort\s*=\s*(['\"])([^'\"]+)\1\s*(?:#.*)?$")
UPSTREAM_LOCK = ROOT / "codex" / "upstream-skills.json"
PEON_LOCK = ROOT / "codex" / "peon.json"
PEON_EVENTS = (
    ("SessionStart", "startup|resume|clear"),
    ("UserPromptSubmit", ""),
    ("PermissionRequest", ""),
    ("PreCompact", "manual|auto"),
    ("SubagentStart", ""),
    ("SubagentStop", ""),
    ("Stop", ""),
)
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
HEX_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


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


def sciverse_credential_available() -> bool:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    credential = config_home / "sciverse" / "token"
    return credential.is_file() and credential.stat().st_size > 0


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
    tui = data.get("tui")
    status_line = tui.get("status_line") if isinstance(tui, dict) else None
    if (
        not isinstance(tui, dict) or set(tui) != {"status_line"}
        or not isinstance(status_line, list) or not status_line
        or any(not isinstance(item, str) or not SAFE_NAME.fullmatch(item) for item in status_line)
        or len(status_line) != len(set(status_line))
    ):
        raise ValueError("unsupported portable Codex status line")
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


def existing_skill_digest(directory: Path) -> str | None:
    if not directory.is_dir() or directory.is_symlink():
        return None
    try:
        return skill_digest(directory)
    except ValueError:
        return None


def local_skill_sources() -> list[Path]:
    sources = sorted(
        (
            skill
            for directory in (ROOT / "skills", ROOT / "codex" / "skills")
            if directory.is_dir()
            for skill in directory.iterdir()
            if (skill / "SKILL.md").is_file()
        ),
        key=lambda skill: skill.name,
    )
    names = [skill.name for skill in sources]
    if len(names) != len(set(names)):
        raise ValueError("duplicate repository skill name")
    return sources


def upstream_sources() -> list[dict]:
    data = json.loads(UPSTREAM_LOCK.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("sources"), list):
        raise ValueError("invalid codex/upstream-skills.json")
    names: set[str] = set()
    source_names: set[str] = set()
    for source in data["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("name"), str) or not SAFE_NAME.fullmatch(source["name"]):
            raise ValueError("invalid upstream skill source name")
        if source["name"] in source_names:
            raise ValueError("duplicate upstream skill source")
        source_names.add(source["name"])
        if source.get("kind") == "github":
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", "")):
                raise ValueError("invalid upstream GitHub repository")
            if not HEX_COMMIT.fullmatch(source.get("ref", "")) or not HEX_SHA256.fullmatch(source.get("archive_sha256", "")):
                raise ValueError("upstream GitHub source must pin a commit and archive checksum")
            paths = source.get("skills")
            if not isinstance(paths, list) or not paths:
                raise ValueError("upstream GitHub source has no skills")
            for path in paths:
                parts = PurePosixPath(path).parts if isinstance(path, str) else ()
                if len(parts) < 2 or parts[0] != "skills" or any(not SAFE_NAME.fullmatch(part) for part in parts[1:]):
                    raise ValueError(f"unsafe upstream skill path: {path}")
                name = parts[-1]
                if name in names:
                    raise ValueError(f"duplicate upstream skill: {name}")
                names.add(name)
        elif source.get("kind") == "files":
            name = source["name"]
            if name in names:
                raise ValueError(f"duplicate upstream skill: {name}")
            names.add(name)
            base = source.get("base_url", "")
            parsed = urllib.parse.urlparse(base)
            if parsed.scheme != "https" or not parsed.netloc or not base.endswith("/") or parsed.query or parsed.fragment:
                raise ValueError("upstream file source requires an HTTPS directory URL")
            files = source.get("files")
            if not isinstance(files, dict) or "SKILL.md" not in files:
                raise ValueError("upstream file source requires SKILL.md")
            for path, checksum in files.items():
                parts = PurePosixPath(path).parts if isinstance(path, str) else ()
                if not parts or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts) or path.startswith("/"):
                    raise ValueError(f"unsafe upstream file path: {path}")
                if not HEX_SHA256.fullmatch(checksum):
                    raise ValueError(f"invalid upstream file checksum: {path}")
        else:
            raise ValueError(f"unknown upstream skill source: {source['name']}")
    return data["sources"]


def upstream_skill_names(sources: list[dict]) -> list[str]:
    return [
        path.rsplit("/", 1)[-1] if source["kind"] == "github" else source["name"]
        for source in sources
        for path in (source["skills"] if source["kind"] == "github" else [source["name"]])
    ]


def fetch_bytes(url: str, *, limit: int = 10 * 1024 * 1024) -> bytes:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError(f"upstream download exceeds size limit: {url}")
            return content
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)
    raise AssertionError("unreachable upstream download retry state")


def adapt_upstream_skill(source: dict, name: str, skill: Path) -> None:
    """Apply the one approved Codex-specific change to a pinned upstream skill."""
    if source["name"] != "mattpocock-skills" or name != "setup-matt-pocock-skills":
        return
    path = skill / "SKILL.md"
    original = path.read_text(encoding="utf-8")
    before = (
        "**Pick the file to edit:**\n\n"
        "- If `CLAUDE.md` exists, edit it.\n"
        "- Else if `AGENTS.md` exists, edit it.\n"
    )
    after = (
        "**Pick the file to edit (Codex adaptation):**\n\n"
        "- If `AGENTS.md` exists, edit it.\n"
        "- Else if `CLAUDE.md` exists, edit it.\n"
    )
    if original.count(before) != 1:
        raise ValueError("Matt setup skill no longer matches the reviewed Codex adaptation")
    path.write_text(original.replace(before, after), encoding="utf-8")


def download_upstream_source(source: dict, directory: Path) -> dict[str, Path]:
    if source["kind"] == "files":
        skill = directory / source["name"]
        for path, checksum in source["files"].items():
            content = fetch_bytes(source["base_url"] + urllib.parse.quote(path, safe="/"), limit=5 * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != checksum:
                raise ValueError(f"upstream file checksum mismatch: {source['name']}/{path}")
            target = skill / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return {source["name"]: skill}

    repository = source["repository"]
    revision = source["ref"]
    url = f"https://codeload.github.com/{repository}/tar.gz/{revision}"
    content = fetch_bytes(url)
    if hashlib.sha256(content).hexdigest() != source["archive_sha256"]:
        raise ValueError(f"upstream archive checksum mismatch: {source['name']}")
    selected = {path + "/": directory / path.rsplit("/", 1)[-1] for path in source["skills"]}
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members = archive.getmembers()
        roots = {member.name.split("/", 1)[0] for member in members}
        if len(roots) != 1:
            raise ValueError(f"unexpected upstream archive layout: {source['name']}")
        root = roots.pop() + "/"
        for member in members:
            for prefix, skill in selected.items():
                full_prefix = root + prefix
                if not member.name.startswith(full_prefix):
                    continue
                relative = member.name[len(full_prefix):]
                if not relative:
                    continue
                parts = PurePosixPath(relative).parts
                if any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts):
                    raise ValueError(f"unsafe path in upstream archive: {member.name}")
                target = skill.joinpath(*parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile() and member.size <= 5 * 1024 * 1024:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    item = archive.extractfile(member)
                    if item is None:
                        raise ValueError(f"unreadable upstream file: {member.name}")
                    target.write_bytes(item.read())
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
                else:
                    raise ValueError(f"unsupported upstream archive entry: {member.name}")
    for skill in selected.values():
        if not (skill / "SKILL.md").is_file():
            raise ValueError(f"upstream skill is missing SKILL.md: {skill.name}")
        adapt_upstream_skill(source, skill.name, skill)
    return {skill.name: skill for skill in selected.values()}


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


def status_line_value(content: str) -> tuple[bool, list[str] | None]:
    """Read the TUI footer without parsing or copying unrelated local settings."""
    in_tui = False
    top = True
    found: list[list[str] | None] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("#"):
            in_tui = bool(re.match(r"^\[tui\](?:\s*#.*)?$", stripped))
            top = False
        if top and re.match(r"^\s*tui\s*=", line):
            return True, None  # Preserve an inline table rather than duplicate it.
        match = re.match(r"^\s*status_line\s*=\s*(.*)$", line) if in_tui else None
        if top and not match:
            match = re.match(r"^\s*tui\.status_line\s*=\s*(.*)$", line)
        if match:
            try:
                value = ast.literal_eval(match.group(1))
            except (SyntaxError, ValueError):
                value = None
            found.append(value if isinstance(value, list) and all(isinstance(item, str) for item in value) else None)
    return bool(found), found[0] if len(found) == 1 else None


def merge_status_line(content: str, desired: list[str]) -> str:
    if status_line_value(content)[0]:
        return content
    newline = "\r\n" if "\r\n" in content else "\n"
    line = f"status_line = {json.dumps(desired)}{newline}"
    lines = content.splitlines(keepends=True)
    for index, existing in enumerate(lines):
        if re.match(r"^\s*\[tui\]\s*(?:#.*)?$", existing):
            if not existing.endswith("\n"):
                lines[index] += newline
            lines.insert(index + 1, line)
            return "".join(lines)
    separator = newline * 2 if content and not content.endswith("\n") else newline if content else ""
    return content + separator + f"[tui]{newline}" + line


def has_inline_hooks(content: str) -> bool:
    """Ignore Codex's hook trust records, which are not hook definitions."""
    return any(
        re.match(r'^\s*\[\[?hooks(?:\]|\.(?!state(?:\.|\]|")))', line)
        for line in content.splitlines()
    )


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


def ready_common_servers(data: dict) -> list[dict]:
    """Do not preinstall commands that this host cannot currently run."""
    return [
        server for server in wanted_servers(data["mcp_servers"])
        if all(local_binary(dependency) for dependency in server["requires"])
        and (server["name"] != "sciverse" or sciverse_credential_available())
    ]


def merge_codex_common(existing: str, data: dict, servers: list[dict]) -> str:
    """Keep local CC-Switch fields while adding only repository-managed Codex fields."""
    lines: list[str] = []
    top_level = True
    for line in existing.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            top_level = False
        if top_level and re.match(r"^\s*disable_response_storage\s*=", line):
            continue
        lines.append(line)
    content = "".join(lines)
    found = setting_values(content)
    for key, value in data["settings"].items():
        if key not in found:
            content = f'{key} = {json.dumps(value)}\n' + content
    content = merge_status_line(content, data["tui"]["status_line"])
    existing_servers = {
        quoted or bare
        for quoted, bare in re.findall(
            r'(?m)^\s*\[mcp_servers\.(?:"([^"]+)"|([A-Za-z0-9_-]+))\]\s*$', content
        )
    }
    for server in servers:
        if server["name"] in existing_servers:
            continue
        content = content.rstrip() + (
            f'\n\n[mcp_servers.{server["name"]}]\n'
            f'command = {json.dumps(server["command"])}\n'
            f'args = {json.dumps(server["args"])}\n'
        )
    return content


def cc_switch_common_text(output: str) -> str:
    output = output.replace("\r\n", "\n")
    header, separator, snippet = output.partition("\n\n")
    if separator and re.search(r"(?m)^App:\s*codex\s*$", header):
        return snippet
    return output


def sync_cc_switch_common(data: dict, backup_dir: Path) -> None:
    binary = local_binary("cc-switch")
    if not binary:
        print("[SKIP] CC-Switch Codex Common Config: cc-switch is not installed")
        return
    current = run(binary, "--app", "codex", "config", "common", "show")
    if current.returncode:
        print("[WARN] CC-Switch Codex Common Config could not be read")
        return
    existing = cc_switch_common_text(current.stdout)
    desired = merge_codex_common(existing, data, ready_common_servers(data))
    if desired == existing:
        print("[OK] CC-Switch Codex Common Config")
        return
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file = backup_dir / "cc-switch-codex-common.toml"
    save(backup_file, existing)
    with tempfile.TemporaryDirectory(prefix="cc-config-codex-common-") as temporary:
        snippet = Path(temporary) / "common.toml"
        save(snippet, desired)
        formatted = run(binary, "--app", "codex", "config", "common", "format", "--file", str(snippet))
        if formatted.returncode:
            print("[WARN] CC-Switch rejected the generated Codex Common Config")
            return
        result = run(binary, "--app", "codex", "config", "common", "set", "--file", str(snippet))
    print(f"[{'ADD' if result.returncode == 0 else 'WARN'}] CC-Switch Codex Common Config")


def inspect_cc_switch_common(data: dict, *, sync: bool) -> int:
    binary = local_binary("cc-switch")
    if not binary:
        print("[SKIP] optional CC-Switch Codex Common Config")
        return 0
    current = run(binary, "--app", "codex", "config", "common", "show")
    if current.returncode:
        print("[DIFF] CC-Switch Codex Common Config could not be read")
        return 0 if sync else 1
    existing = cc_switch_common_text(current.stdout)
    matches = merge_codex_common(existing, data, ready_common_servers(data)) == existing
    print(f"[{'OK' if matches else 'DIFF'}] CC-Switch Codex Common Config")
    return 0 if matches or sync else 1


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


def upstream_revision(source: dict) -> str:
    if source["kind"] == "github":
        return source["archive_sha256"]
    content = json.dumps(source["files"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def install_upstream_skills(skills_home: Path, backup_dir: Path, state: dict) -> None:
    installed = state.setdefault("upstream_skills", {})
    for source in upstream_sources():
        names = upstream_skill_names([source])
        revision = upstream_revision(source)
        if all(
            isinstance(installed.get(name), dict)
            and installed[name].get("revision") == revision
            and existing_skill_digest(skills_home / name) == installed[name].get("digest")
            for name in names
        ):
            for name in names:
                print(f"[OK] upstream skill {name}")
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="cc-config-codex-skills-") as temporary:
                available = download_upstream_source(source, Path(temporary))
                for name, original in available.items():
                    destination = skills_home / name
                    desired = skill_digest(original)
                    current_digest = existing_skill_digest(destination)
                    previous = installed.get(name)
                    if current_digest == desired:
                        print(f"[OK] upstream skill {name}")
                    elif destination.exists() or destination.is_symlink():
                        if not isinstance(previous, dict) or current_digest != previous.get("digest"):
                            print(f"[KEEP] local upstream skill differs: {destination}")
                            continue
                        backup(destination, backup_dir / "skills")
                        if destination.is_dir() and not destination.is_symlink():
                            shutil.rmtree(destination)
                        else:
                            destination.unlink()
                        shutil.copytree(original, destination)
                        print(f"[ADD] upstream skill {name}: {destination}")
                    else:
                        shutil.copytree(original, destination)
                        print(f"[ADD] upstream skill {name}: {destination}")
                    installed[name] = {"revision": revision, "digest": desired, "source": source["name"]}
        except (OSError, ValueError, tarfile.TarError) as error:
            print(f"[WARN] upstream skill source {source['name']}: {error}")


def inspect_upstream_skills(skills_home: Path, state: dict, *, sync: bool) -> int:
    problems = 0
    installed = state.get("upstream_skills", {})
    for source in upstream_sources():
        revision = upstream_revision(source)
        for name in upstream_skill_names([source]):
            record = installed.get(name) if isinstance(installed, dict) else None
            destination = skills_home / name
            current = (
                isinstance(record, dict)
                and record.get("revision") == revision
                and existing_skill_digest(destination) == record.get("digest")
            )
            print(f"[{'OK' if current else 'DIFF'}] upstream skill {name}")
            problems += not current and not sync
    return problems


def peon_manifest() -> tuple[dict, dict]:
    lock = json.loads(PEON_LOCK.read_text(encoding="utf-8"))
    if lock.get("version") != 1 or lock.get("upstream_profile") != "profiles/peon-ping/profile.json":
        raise ValueError("invalid codex/peon.json")
    if not isinstance(lock.get("prompt_sound_on_submit"), bool):
        raise ValueError("Codex peon-ping prompt sound setting must be boolean")
    profile = json.loads((ROOT / lock["upstream_profile"]).read_text(encoding="utf-8"))
    upstream = profile.get("upstream", {})
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", upstream.get("repository", "")):
        raise ValueError("invalid peon-ping repository")
    if not HEX_COMMIT.fullmatch(upstream.get("ref", "")) or not HEX_SHA256.fullmatch(upstream.get("archive_sha256", "")):
        raise ValueError("peon-ping runtime must pin a commit and checksum")
    packs = lock.get("packs")
    if not isinstance(packs, list) or {pack.get("name") for pack in packs if isinstance(pack, dict)} != set(profile["default_packs"]):
        raise ValueError("Codex peon-ping packs must match the portable profile")
    for pack in packs:
        if (
            not isinstance(pack, dict)
            or not SAFE_NAME.fullmatch(pack.get("name", ""))
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", pack.get("repository", ""))
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", pack.get("ref", ""))
            or not SAFE_NAME.fullmatch(pack.get("path", ""))
            or not HEX_SHA256.fullmatch(pack.get("manifest_sha256", ""))
        ):
            raise ValueError("invalid Codex peon-ping pack lock")
    return lock, profile


def peon_profile(profile: dict) -> str:
    selected = os.environ.get("CC_CONFIG_PEON_PROFILE", "auto").strip().lower()
    if os.name == "nt" or platform.system() == "Windows":
        host = "windows"
    elif platform.system() == "Darwin":
        host = "macos"
    elif platform.system() == "Linux":
        version = Path("/proc/version").read_text(encoding="utf-8", errors="replace") if Path("/proc/version").exists() else ""
        host = "wsl-native" if "microsoft" in version.lower() else "linux"
    else:
        host = "none"
    if selected in ("", "auto"):
        selected = host
    if selected != "none" and selected not in profile.get("profiles", {}):
        raise ValueError(f"unsupported Codex peon-ping profile: {selected}")
    allowed = {"windows": {"windows"}, "macos": {"macos"}, "linux": {"linux"}, "wsl-native": {"wsl-native", "linux"}}
    if selected != "none" and selected not in allowed.get(host, set()):
        raise ValueError(f"Codex peon-ping profile {selected} does not match this {host} host")
    return selected


def peon_runtime(codex_home: Path) -> Path:
    return codex_home / "cc-config-peon-ping"


def file_sha256(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def peon_target(runtime: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if relative.startswith("/") or not parts or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts):
        raise ValueError(f"unsafe peon-ping path: {relative}")
    target = runtime.joinpath(*parts)
    if runtime.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent == runtime or runtime in parent.parents):
        raise ValueError(f"peon-ping path crosses a symlink: {target}")
    return target


def peon_pack_revision(lock: dict) -> str:
    encoded = json.dumps(lock["packs"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def peon_record_current(record: dict, runtime: Path, profile_id: str, upstream: dict, pack_revision: str) -> bool:
    hook_source = ROOT / "scripts" / "codex-peon-hook.py"
    files = record.get("files")
    return (
        record.get("profile") == profile_id
        and record.get("archive_sha256") == upstream["archive_sha256"]
        and record.get("pack_lock_sha256") == pack_revision
        and record.get("hook_sha256") == file_sha256(hook_source)
        and isinstance(files, dict)
        and bool(files)
        and all(file_sha256(peon_target(runtime, path)) == checksum for path, checksum in files.items())
        and (runtime / "config.json").is_file()
        and not (runtime / "config.json").is_symlink()
        and (runtime / ".state.json").is_file()
        and not (runtime / ".state.json").is_symlink()
    )


def stage_peon_runtime(upstream: dict, stage: Path) -> dict[str, str]:
    url = f'https://codeload.github.com/{upstream["repository"]}/tar.gz/{upstream["ref"]}'
    content = fetch_bytes(url, limit=40 * 1024 * 1024)
    if hashlib.sha256(content).hexdigest() != upstream["archive_sha256"]:
        raise ValueError("peon-ping runtime checksum mismatch")
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        members = archive.getmembers()
        roots = {member.name.split("/", 1)[0] for member in members}
        if len(roots) != 1:
            raise ValueError("unexpected peon-ping archive layout")
        prefix = roots.pop() + "/"
        for member in members:
            if not member.name.startswith(prefix):
                continue
            relative = member.name[len(prefix):]
            selected = (
                relative in {"peon.sh", "relay.sh", "VERSION", "config.json", "install.ps1", "adapters/codex.sh", "adapters/codex.ps1"}
                or relative.startswith("scripts/")
            )
            if not selected or member.isdir():
                continue
            parts = PurePosixPath(relative).parts
            if (
                not parts
                or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts)
                or not member.isfile()
                or member.size > 5 * 1024 * 1024
            ):
                raise ValueError(f"unsafe peon-ping archive entry: {member.name}")
            item = archive.extractfile(member)
            if item is None:
                raise ValueError(f"unreadable peon-ping archive entry: {member.name}")
            payload = item.read()
            target = stage.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            if relative not in ("config.json", "install.ps1"):
                files[relative] = hashlib.sha256(payload).hexdigest()
    installer = (stage / "install.ps1").read_text(encoding="utf-8-sig")
    start = "$hookScript = @'\n"
    end = "\n'@\n\n$hookScriptPath"
    if installer.count(start) != 1 or installer.count(end) != 1:
        raise ValueError("pinned Windows peon-ping runtime layout changed")
    script = installer.split(start, 1)[1].split(end, 1)[0] + "\n"
    (stage / "peon.ps1").write_text(script, encoding="utf-8")
    files["peon.ps1"] = file_sha256(stage / "peon.ps1")
    (stage / "install.ps1").unlink()
    for required in ("peon.sh", "peon.ps1", "adapters/codex.sh", "adapters/codex.ps1", "config.json"):
        if not (stage / required).is_file():
            raise ValueError(f"peon-ping runtime lacks {required}")
    hook_source = ROOT / "scripts" / "codex-peon-hook.py"
    shutil.copy2(hook_source, stage / "codex-peon-hook.py")
    files["codex-peon-hook.py"] = file_sha256(hook_source)
    return files


def peon_sound_hashes(manifest: dict) -> dict[str, str]:
    sounds: dict[str, str] = {}
    categories = manifest.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("invalid peon-ping sound manifest")
    for category in categories.values():
        if not isinstance(category, dict) or not isinstance(category.get("sounds"), list):
            raise ValueError("invalid peon-ping sound category")
        for sound in category["sounds"]:
            name = sound.get("file") if isinstance(sound, dict) else None
            digest = sound.get("sha256") if isinstance(sound, dict) else None
            parts = PurePosixPath(name).parts if isinstance(name, str) else ()
            if (
                len(parts) < 2
                or parts[0] != "sounds"
                or any(part in ("", ".", "..") or "\\" in part or ":" in part for part in parts)
                or not isinstance(digest, str)
                or not HEX_SHA256.fullmatch(digest)
            ):
                raise ValueError("unsafe or unhashed peon-ping sound")
            if name in sounds and sounds[name] != digest:
                raise ValueError(f"conflicting peon-ping sound checksum: {name}")
            sounds[name] = digest
    if not sounds:
        raise ValueError("peon-ping sound manifest is empty")
    return sounds


def stage_peon_packs(lock: dict, stage: Path, runtime: Path, files: dict[str, str]) -> None:
    downloads: list[tuple[str, str, str]] = []
    for pack in lock["packs"]:
        base = f'https://raw.githubusercontent.com/{pack["repository"]}/{pack["ref"]}/{pack["path"]}/'
        content = fetch_bytes(base + "openpeon.json", limit=1024 * 1024)
        if hashlib.sha256(content).hexdigest() != pack["manifest_sha256"]:
            raise ValueError(f'peon-ping pack manifest checksum mismatch: {pack["name"]}')
        manifest = json.loads(content)
        if manifest.get("name") != pack["name"]:
            raise ValueError(f'peon-ping pack name mismatch: {pack["name"]}')
        relative = f'packs/{pack["name"]}/openpeon.json'
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        files[relative] = pack["manifest_sha256"]
        for sound, digest in peon_sound_hashes(manifest).items():
            relative = f'packs/{pack["name"]}/{sound}'
            files[relative] = digest
            if file_sha256(runtime / relative) != digest:
                downloads.append((relative, base + urllib.parse.quote(sound, safe="/"), digest))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as workers:
        pending = {workers.submit(fetch_bytes, url, limit=5 * 1024 * 1024): (relative, digest) for relative, url, digest in downloads}
        for task in concurrent.futures.as_completed(pending):
            relative, digest = pending[task]
            content = task.result()
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError(f"peon-ping sound checksum mismatch: {relative}")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def install_peon_file(stage: Path, runtime: Path, relative: str, digest: str, previous: dict, backup_dir: Path) -> bool:
    target = peon_target(runtime, relative)
    current = file_sha256(target)
    if current == digest:
        return True
    if target.exists() or target.is_symlink():
        if current is None or current != previous.get(relative):
            print(f"[KEEP] local peon-ping file differs: {target}")
            return False
        backup_target = backup_dir / "peon-ping" / relative
        backup_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_target)
    source = stage / relative
    if not source.is_file():
        raise ValueError(f"peon-ping staging file is missing: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def peon_hook_groups(runtime: Path) -> dict:
    arguments = [sys.executable, str(runtime / "codex-peon-hook.py")]
    command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    groups = {}
    for event, matcher in PEON_EVENTS:
        handler = {"type": "command", "command": command, "timeout": 30}
        if os.name == "nt":
            handler["command_windows"] = command
        group = {"hooks": [handler]}
        if matcher:
            group["matcher"] = matcher
        groups[event] = [group]
    return groups


def merge_peon_hooks(target: Path, additions: dict, runtime: Path) -> str:
    current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(current, dict) or not isinstance(current.get("hooks", {}), dict):
        raise ValueError("Codex hooks.json has an unexpected shape")
    groups = current.setdefault("hooks", {})
    marker = str(runtime / "codex-peon-hook.py")
    for event in set(groups) | set(additions):
        existing = groups.get(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"invalid Codex hook event: {event}")
        kept = []
        for group in existing:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept.append(group)
                continue
            handlers = [
                handler for handler in group["hooks"]
                if not (isinstance(handler, dict) and marker in str(handler.get("command", "")))
            ]
            if len(handlers) == len(group["hooks"]):
                kept.append(group)
            elif handlers:
                kept.append({**group, "hooks": handlers})
        for group in additions.get(event, []):
            if group not in kept:
                kept.append(group)
        if kept:
            groups[event] = kept
        else:
            groups.pop(event, None)
    return json.dumps(current, indent=2, ensure_ascii=False) + "\n"


def peon_audio_problems(profile_id: str) -> list[str]:
    issues = []
    if profile_id == "windows":
        if not (local_binary("powershell") or local_binary("pwsh")):
            issues.append("PowerShell is missing")
        return issues
    if not local_binary("bash"):
        issues.append("bash is missing")
    if not local_binary("python3"):
        issues.append("python3 is missing")
    if profile_id == "macos":
        if not local_binary("afplay"):
            issues.append("afplay is missing")
    else:
        if not any(local_binary(name) for name in ("pw-play", "paplay", "ffplay", "mpv", "play", "aplay")):
            issues.append("Linux audio player is missing")
        if profile_id == "wsl-native" and not os.environ.get("PULSE_SERVER") and not Path("/mnt/wslg/PulseServer").exists():
            issues.append("WSLg/PulseAudio endpoint is missing")
    return issues


def reconcile_peon_audio_config(runtime: Path, profile_id: str, backup_dir: Path) -> None:
    if profile_id != "wsl-native":
        return
    player = next((name for name in ("paplay", "ffplay", "mpv", "play", "aplay", "pw-play") if local_binary(name)), None)
    if not player:
        return
    config = runtime / "config.json"
    if not config.is_file() or config.is_symlink():
        return
    current = json.loads(config.read_text(encoding="utf-8"))
    if not isinstance(current, dict):
        raise ValueError("invalid local peon-ping config.json")
    if current.get("linux_audio_player"):
        return
    backup(config, backup_dir / "peon-ping")
    current["linux_audio_player"] = player
    save(config, json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    print(f"[ADD] Codex peon-ping WSL audio player: {player}")


def install_peon(codex_home: Path, backup_dir: Path, state: dict, *, inline_hooks: bool) -> None:
    lock, profile = peon_manifest()
    profile_id = peon_profile(profile)
    runtime = peon_runtime(codex_home)
    hooks_target = codex_home / "hooks.json"
    if profile_id == "none":
        print("[SKIP] Codex peon-ping: profile disabled or unsupported")
        if hooks_target.exists() and not inline_hooks and str(runtime / "codex-peon-hook.py") in hooks_target.read_text(encoding="utf-8"):
            desired = merge_peon_hooks(hooks_target, {}, runtime)
            if desired != hooks_target.read_text(encoding="utf-8"):
                backup(hooks_target, backup_dir)
                save(hooks_target, desired)
                print("[ADD] disabled managed Codex peon-ping hooks")
        return
    previous = state.get("peon") if isinstance(state.get("peon"), dict) else {}
    if runtime.exists() and not runtime.is_dir() or runtime.is_symlink():
        print(f"[KEEP] Codex peon-ping target is not a plain directory: {runtime}")
        return
    if runtime.exists() and not previous:
        print(f"[KEEP] unowned Codex peon-ping directory: {runtime}")
        return
    upstream = profile["upstream"]
    if peon_record_current(previous, runtime, profile_id, upstream, peon_pack_revision(lock)):
        print(f"[OK] Codex peon-ping runtime and packs: {profile_id}")
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="cc-config-codex-peon-") as temporary:
                stage = Path(temporary)
                files = stage_peon_runtime(upstream, stage)
                stage_peon_packs(lock, stage, runtime, files)
                runtime.mkdir(parents=True, exist_ok=True)
                prior_files = previous.get("files", {}) if isinstance(previous.get("files"), dict) else {}
                conflicts = 0
                for relative, digest in sorted(files.items()):
                    conflicts += not install_peon_file(stage, runtime, relative, digest, prior_files, backup_dir)
                config = runtime / "config.json"
                defaults = json.loads((stage / "config.json").read_text(encoding="utf-8"))
                if not isinstance(defaults, dict):
                    raise ValueError("invalid pinned peon-ping config.json")
                categories = defaults.get("categories")
                if not isinstance(categories, dict):
                    raise ValueError("invalid pinned peon-ping categories")
                categories["task.acknowledge"] = lock["prompt_sound_on_submit"]
                if not config.exists():
                    save(config, json.dumps(defaults, indent=2, ensure_ascii=False) + "\n")
                elif config.is_file() and not config.is_symlink():
                    current = json.loads(config.read_text(encoding="utf-8"))
                    if not isinstance(current, dict):
                        raise ValueError("invalid local peon-ping config.json")
                    changed = False
                    for key, value in defaults.items():
                        if key not in current:
                            current[key] = value
                            changed = True
                    if changed:
                        backup(config, backup_dir / "peon-ping")
                        save(config, json.dumps(current, indent=2, ensure_ascii=False) + "\n")
                else:
                    print(f"[KEEP] local peon-ping config differs: {config}")
                    conflicts += 1
                if not (runtime / ".state.json").exists():
                    save(runtime / ".state.json", "{}\n")
                state["peon"] = {
                    "profile": profile_id,
                    "archive_sha256": upstream["archive_sha256"],
                    "pack_lock_sha256": peon_pack_revision(lock),
                    "hook_sha256": file_sha256(ROOT / "scripts" / "codex-peon-hook.py"),
                    "files": files,
                }
                print(f"[{'WARN' if conflicts else 'ADD'}] Codex peon-ping runtime and packs: {profile_id}; local conflicts: {conflicts}")
        except (OSError, ValueError, tarfile.TarError, urllib.error.URLError) as error:
            print(f"[WARN] Codex peon-ping installation: {error}")
            return
    try:
        reconcile_peon_audio_config(runtime, profile_id, backup_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[WARN] Codex peon-ping WSL audio configuration: {error}")
    if inline_hooks:
        print("[KEEP] inline Codex hooks present; peon-ping hooks need manual review")
        return
    desired = merge_peon_hooks(hooks_target, peon_hook_groups(runtime), runtime)
    if not hooks_target.exists() or hooks_target.read_text(encoding="utf-8") != desired:
        backup(hooks_target, backup_dir)
        save(hooks_target, desired)
        print(f"[ADD] Codex peon-ping hooks: {hooks_target}")


def inspect_peon(codex_home: Path, state: dict, *, sync: bool) -> int:
    lock, profile = peon_manifest()
    profile_id = peon_profile(profile)
    if profile_id == "none":
        print("[SKIP] Codex peon-ping: profile disabled or unsupported")
        return 0
    runtime = peon_runtime(codex_home)
    record = state.get("peon") if isinstance(state.get("peon"), dict) else {}
    current = peon_record_current(record, runtime, profile_id, profile["upstream"], peon_pack_revision(lock))
    print(f"[{'OK' if current else 'DIFF'}] Codex peon-ping runtime and {len(lock['packs'])} packs: {profile_id}")
    problems = not current
    issues = peon_audio_problems(profile_id)
    if issues:
        print(f"[DIFF] Codex peon-ping local audio: {'; '.join(issues)}")
        problems = True
    else:
        print("[OK] Codex peon-ping local audio")
    config = codex_home / "config.toml"
    if has_inline_hooks(config.read_text(encoding="utf-8") if config.exists() else ""):
        print("[DIFF] inline Codex hooks conflict with managed hooks.json")
        problems = True
    else:
        hooks = codex_home / "hooks.json"
        existing = hooks.read_text(encoding="utf-8") if hooks.exists() else ""
        wanted = merge_peon_hooks(hooks, peon_hook_groups(runtime), runtime)
        matched = bool(existing) and json.loads(existing) == json.loads(wanted)
        print(f"[{'OK' if matched else 'DIFF'}] Codex peon-ping hooks")
        problems |= not matched
    return int(bool(problems) and not sync)


def install(data: dict, codex_home: Path, skills_home: Path, backup_dir: Path, state: dict, binary: str | None, *, with_upstream: bool = True, with_cc_switch: bool = True, with_peon: bool = True) -> int:
    guidance = codex_home / "AGENTS.md"
    existing = guidance.read_text(encoding="utf-8") if guidance.exists() else ""
    updated = managed_guidance(existing, (ROOT / "codex" / "AGENTS.md").read_text(encoding="utf-8"))
    config = codex_home / "config.toml"
    if config.exists():
        with config.open("r", encoding="utf-8", newline="") as source:
            current = source.read()
    else:
        current = ""
    found = setting_values(current)
    hooks_target = codex_home / "hooks.json"
    inline_hooks = has_inline_hooks(current)
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
    present, status_line = status_line_value(current)
    if present and status_line != data["tui"]["status_line"]:
        print("[KEEP] existing tui.status_line differs from portable preference")
    elif not present:
        backup(config, backup_dir)
        current = merge_status_line(current, data["tui"]["status_line"])
        save(config, current)
        print(f"[ADD] tui.status_line in {config}")

    skills_home.mkdir(parents=True, exist_ok=True)
    installed = state.setdefault("skills", {})
    for source in local_skill_sources():
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

    if with_upstream:
        install_upstream_skills(skills_home, backup_dir, state)

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

    if with_peon:
        install_peon(codex_home, backup_dir, state, inline_hooks=inline_hooks)

    if binary:
        for server in wanted_servers(data["mcp_servers"]):
            name = server["name"]
            status = mcp_status(binary, server)
            missing = [dep for dep in server["requires"] if local_binary(dep) is None]
            missing_credential = name == "sciverse" and not sciverse_credential_available()
            if status == "current":
                if missing or missing_credential:
                    issues = []
                    if missing:
                        issues.append(f"local dependency is missing: {', '.join(missing)}")
                    if missing_credential:
                        issues.append("local credential is missing")
                    print(f"[WARN] MCP {name} is configured but {'; '.join(issues)}")
                else:
                    print(f"[OK] MCP {name}")
            elif status == "conflict":
                print(f"[KEEP] existing MCP {name} differs; inspect with codex mcp get {name}")
            elif missing:
                print(f"[SKIP] MCP {name}: local dependency is missing: {', '.join(missing)}")
            elif missing_credential:
                print(f"[SKIP] MCP {name}: local credential is missing")
            else:
                response = run(binary, "mcp", "add", name, "--", server["command"], *server["args"])
                print(f"[{'ADD' if response.returncode == 0 else 'WARN'}] MCP {name}")
    else:
        print("[SKIP] MCP registration: codex CLI is not installed")

    if with_cc_switch:
        sync_cc_switch_common(data, backup_dir)
    save(codex_home / ".cc-config-state.json", json.dumps(state, indent=2) + "\n")
    return 0


def inspect(data: dict, codex_home: Path, skills_home: Path, binary: str | None, *, sync: bool, state: dict) -> int:
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
    _, status_line = status_line_value(config.read_text(encoding="utf-8") if config.exists() else "")
    status_line_matches = status_line == data["tui"]["status_line"]
    print(f"[{'OK' if status_line_matches else 'DIFF'}] tui.status_line")
    problems += not status_line_matches
    for source in local_skill_sources():
        destination = skills_home / source.name
        current = destination.is_symlink() and destination.resolve() == source.resolve()
        if not current and destination.is_dir() and not destination.is_symlink():
            current = skill_digest(source) == skill_digest(destination)
        print(f"[{'OK' if current else 'DIFF'}] skill {source.name}")
        problems += not current
    problems += inspect_upstream_skills(skills_home, state, sync=sync)
    if binary:
        for server in wanted_servers(data["mcp_servers"]):
            status = mcp_status(binary, server)
            missing = [dep for dep in server["requires"] if local_binary(dep) is None]
            missing_credential = server["name"] == "sciverse" and not sciverse_credential_available()
            detail = f"; local dependency missing: {', '.join(missing)}" if missing else ""
            if missing_credential:
                detail += "; local credential missing"
            print(f"[{'OK' if status == 'current' and not missing and not missing_credential else 'DIFF'}] MCP {server['name']}: {status}{detail}")
            problems += (status != "current" or bool(missing) or missing_credential) and not sync
    else:
        print("[DIFF] codex CLI missing; cannot inspect MCP")
        problems += not sync
    if has_inline_hooks(config.read_text(encoding="utf-8") if config.exists() else ""):
        print("[SKIP] inline Codex hooks present; review manually")
    elif (codex_home / "hooks.json").exists() and local_binary("serena-hooks"):
        target = codex_home / "hooks.json"
        existing = json.loads(target.read_text(encoding="utf-8"))
        merged = json.loads(merge_hooks(target, ROOT / "codex" / "hooks.json"))
        print(f"[{'OK' if merged == existing else 'DIFF'}] Serena hooks")
    else:
        print("[SKIP] optional Serena hooks")
    problems += inspect_peon(codex_home, state, sync=sync)
    problems += inspect_cc_switch_common(data, sync=sync)
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
    state_file = codex_home / ".cc-config-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    if args.action != "install":
        return inspect(data, codex_home, skills_home, binary, sync=args.action == "sync", state=state)
    print(f"Codex home: {codex_home}\nSkills: {skills_home}")
    print("Install portable guidance, pinned upstream skills, host-native peon-ping, optional hooks, and missing MCP servers.")
    print("Pinned upstream skills and peon-ping may require network downloads; existing local customizations are preserved.")
    if not args.yes:
        if not sys.stdin.isatty() or input("Apply to this host? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled; nothing changed.")
            return 0
    backup_dir = codex_home / "backups" / ("cc-config-codex-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    return install(data, codex_home, skills_home, backup_dir, state, binary)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
