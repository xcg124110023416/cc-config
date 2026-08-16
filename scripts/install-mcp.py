#!/usr/bin/env python3
"""Register portable MCP servers through the official Claude Code CLI.

This script never writes .claude.json itself. Existence is checked with
`claude mcp get`, equality with a read-only look at the current mcpServers,
and changes are applied through `claude mcp add` / `claude mcp remove`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(claude_bin: str, arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([claude_bin, *arguments], capture_output=True, text=True)


def existing_servers() -> dict[str, Any]:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    candidate = Path(config_dir) / ".claude.json"
    if not candidate.exists():
        candidate = Path.home() / ".claude.json"
    if not candidate.exists():
        return {}
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    servers = data.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def normalized(server: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": server.get("transport", "stdio"),
        "command": server["command"],
        "args": list(server.get("args", []) or []),
    }


def confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        reply = input(prompt + " [y/N] ").strip().lower()
    except EOFError:
        return False
    return reply in {"y", "yes"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--claude-bin", required=True)
    parser.add_argument("--scope", default="user")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    servers = manifest.get("servers", [])
    scope = manifest.get("scope", args.scope)
    problems = 0

    for server in servers:
        name = server["name"]
        transport = server.get("transport", "stdio")
        command = server["command"]
        server_args = list(server.get("args", []) or [])
        requires = list(server.get("requires", []) or [])

        missing_deps = [binary for binary in [command, *requires] if shutil.which(binary) is None]

        probe = run(args.claude_bin, ["mcp", "get", name])
        exists = probe.returncode == 0

        if not exists:
            if missing_deps:
                print(
                    f"[SKIP] {name}: missing dependency: {' '.join(missing_deps)}. "
                    "Run ./doctor.sh and install it first.",
                    file=sys.stderr,
                )
                problems += 1
                continue
            if not args.apply:
                print(f"[DRY] {name}: would register with `claude mcp add`")
                continue
            result = run(
                args.claude_bin,
                ["mcp", "add", "--transport", transport, "--scope", scope, name, "--", command, *server_args],
            )
            if result.returncode == 0:
                print(f"[ADD] {name}")
            else:
                print(f"[ERROR] {name}: {result.stderr.strip()}", file=sys.stderr)
                problems += 1
            continue

        current = existing_servers().get(name)
        desired = normalized(server)
        current_normalized = (
            {
                "type": current.get("type", current.get("transport", "stdio")),
                "command": current.get("command", ""),
                "args": list(current.get("args", []) or []),
            }
            if isinstance(current, dict)
            else None
        )
        if current_normalized == desired:
            print(f"[OK] {name}: already registered identically")
            continue

        print(f"[DIFF] {name}: existing configuration differs")
        print(f"  current: {json.dumps(current_normalized, ensure_ascii=False)}")
        print(f"  desired: {json.dumps(desired, ensure_ascii=False)}")
        if missing_deps:
            print(
                f"  WARNING: missing dependency for desired config: {' '.join(missing_deps)}",
                file=sys.stderr,
            )
            problems += 1
            continue
        if not args.apply:
            print(f"  [DRY] would replace via `claude mcp remove` + `claude mcp add`")
            continue
        if not confirm(f"Replace MCP server '{name}'?"):
            print(f"[SKIP] {name}: declined")
            continue
        remove = run(args.claude_bin, ["mcp", "remove", "--scope", scope, name])
        if remove.returncode != 0:
            print(f"[ERROR] {name} remove: {remove.stderr.strip()}", file=sys.stderr)
            problems += 1
            continue
        add = run(
            args.claude_bin,
            ["mcp", "add", "--transport", transport, "--scope", scope, name, "--", command, *server_args],
        )
        if add.returncode == 0:
            print(f"[REPLACE] {name}")
        else:
            print(f"[ERROR] {name}: {add.stderr.strip()}", file=sys.stderr)
            problems += 1

    if problems:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
