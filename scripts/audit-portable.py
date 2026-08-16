#!/usr/bin/env python3
"""Conservative secret and machine-state audit for the portable repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

EXCLUDED_DIRS = {".git", ".serena", "__pycache__", ".local-backups"}
FORBIDDEN_NAMES = {
    ".claude.json",
    "settings.json",
    "config.json",
    "history.jsonl",
    ".env",
}
FORBIDDEN_DIR_NAMES = {
    "projects",
    "sessions",
    "cache",
    "paste-cache",
    "image-cache",
    "file-history",
    "shell-snapshots",
    "session-env",
    "backups",
    "tasks",
    "plans",
    "teams",
    "telemetry",
}
SENSITIVE_NAME = re.compile(r"(?:token|secret|credential|oauth|api[-_ ]?key)", re.IGNORECASE)
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{12,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "credential assignment",
        re.compile(
            r"(?i)(?:api[_ -]?key|auth[_ -]?token|access[_ -]?token|client[_ -]?secret|password|credential)"
            r"\s*[\"']?\s*[:=]\s*[\"'](?!\s*(?:PROXY_MANAGED)?\s*[\"'])[A-Za-z0-9_./+=:-]{8,}"
        ),
    ),
)
MACHINE_PATHS = (
    ("hard-coded Linux home", re.compile(r"/home/(?!user(?:/|\b)|\$USER(?:/|\b)|\$\{?USER\}?)[A-Za-z0-9._-]+/")),
    ("hard-coded WSL Windows home", re.compile(r"/mnt/[a-z]/Users/[A-Za-z0-9._-]+/", re.IGNORECASE)),
    ("hard-coded Windows home", re.compile(r"[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+[\\/]", re.IGNORECASE)),
)
TEXT_SUFFIXES = {
    "",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".ps1",
}


def iter_paths(root: Path):
    if root.is_file() or root.is_symlink():
        yield root
        return
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        base = Path(directory)
        for name in sorted(dirnames):
            yield base / name
        for name in sorted(filenames):
            yield base / name


def display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


FORBIDDEN_MANIFEST_KEY = re.compile(
    r"(token|secret|credential|oauth|api[-_ ]?key|header)", re.IGNORECASE
)


def validate_mcp_manifest(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid JSON in {path.name}: {exc}"]
    servers = data.get("servers")
    if not isinstance(servers, list):
        return [f"{path.name}: servers must be a list"]
    for server in servers:
        if not isinstance(server, dict):
            findings.append(f"{path.name}: server must be an object")
            continue
        name = server.get("name", "<unnamed>")
        command = server.get("command")
        if not isinstance(command, str) or not command:
            findings.append(f"{path.name}: server {name} missing command")
        for key in server:
            if FORBIDDEN_MANIFEST_KEY.search(key):
                findings.append(f"{path.name}: server {name} has forbidden key {key}")
        for text in [command, *server.get("args", [])]:
            if not isinstance(text, str):
                continue
            for label, pattern in MACHINE_PATHS:
                if pattern.search(text):
                    findings.append(f"{path.name}: {label} in server {name}")
    return findings


def validate_hooks_manifest(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid JSON in {path.name}: {exc}"]
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return [f"{path.name}: hooks must be an object"]
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            findings.append(f"{path.name}: event {event} must be a list")
            continue
        for group in groups:
            if not isinstance(group, dict):
                findings.append(f"{path.name}: event {event} group must be an object")
                continue
            if not isinstance(group.get("matcher", ""), str):
                findings.append(f"{path.name}: event {event} matcher must be a string")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                findings.append(f"{path.name}: event {event} group missing hooks list")
                continue
            for handler in handlers:
                if (
                    not isinstance(handler, dict)
                    or handler.get("type") != "command"
                    or not isinstance(handler.get("command"), str)
                ):
                    findings.append(f"{path.name}: event {event} has non-command hook")
                else:
                    command = handler["command"]
                    for label, pattern in SECRET_PATTERNS:
                        if pattern.search(command):
                            findings.append(f"{path.name}: possible {label} in event {event}")
                    for label, pattern in MACHINE_PATHS:
                        if pattern.search(command):
                            findings.append(f"{path.name}: {label} in event {event}")
    return findings


def audit(root: Path, settings_validator: Path | None) -> list[str]:
    findings: list[str] = []
    root = root.resolve()
    if not root.exists():
        return [f"audit target does not exist: {root}"]

    for path in iter_paths(root):
        shown = display(path, root)
        name = path.name.lower()
        if path.is_symlink():
            findings.append(f"symlink is not allowed inside repository content: {shown}")
            continue
        if path.is_dir():
            if name in FORBIDDEN_DIR_NAMES:
                findings.append(f"runtime-state directory is forbidden: {shown}")
            continue
        if name in FORBIDDEN_NAMES or name.startswith(".env."):
            findings.append(f"sensitive/runtime filename is forbidden: {shown}")
        elif SENSITIVE_NAME.search(name):
            findings.append(f"credential-like filename requires manual exclusion: {shown}")

        if path.stat().st_size > 5 * 1024 * 1024:
            findings.append(f"file exceeds the 5 MiB portable limit: {shown}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore"}:
            findings.append(f"binary or unsupported file type requires review: {shown}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"non-UTF-8 file requires review: {shown}")
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"possible {label}: {shown}")
        for label, pattern in MACHINE_PATHS:
            if pattern.search(text):
                findings.append(f"{label}: {shown}")

    for manifest_name, validator in (
        ("mcp.portable.json", validate_mcp_manifest),
        ("hooks.portable.json", validate_hooks_manifest),
    ):
        manifest_path = root / manifest_name
        if manifest_path.exists():
            findings.extend(validator(manifest_path))

    portable = root / "settings.portable.json"
    if settings_validator and portable.exists():
        result = subprocess.run(
            [sys.executable, str(settings_validator), "validate", "--portable", str(portable)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            findings.append(f"portable settings validation failed: {detail}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--settings-validator", type=Path)
    args = parser.parse_args()
    findings = audit(args.root, args.settings_validator)
    if findings:
        print("Portable content audit FAILED:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print(f"Portable content audit passed: {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
