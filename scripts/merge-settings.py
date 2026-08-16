#!/usr/bin/env python3
"""Validate, merge, and extract the portable Claude Code settings subset."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ALLOWED_TOP_LEVEL = {
    "env",
    "attribution",
    "enabledPlugins",
    "statusLine",
    "effortLevel",
    "skipDangerousModePermissionPrompt",
}
ALLOWED_ENV = {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    "DISABLE_AUTOUPDATER",
}
ALLOWED_ATTRIBUTION = {"commit", "pr"}
FORBIDDEN_KEY_PARTS = (
    "token",
    "secret",
    "credential",
    "oauth",
    "apikey",
    "api_key",
    "base_url",
    "model",
    "provider",
    "route",
)


class PortableSettingsError(ValueError):
    pass


def load_object(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if missing_ok and not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PortableSettingsError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PortableSettingsError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PortableSettingsError(f"top-level JSON value must be an object: {path}")
    return value


def validate_portable(value: dict[str, Any]) -> None:
    unexpected = set(value) - ALLOWED_TOP_LEVEL
    if unexpected:
        raise PortableSettingsError(
            "portable settings contain unsupported top-level keys: "
            + ", ".join(sorted(unexpected))
        )

    env = value.get("env", {})
    if not isinstance(env, dict):
        raise PortableSettingsError("env must be an object")
    unexpected_env = set(env) - ALLOWED_ENV
    if unexpected_env:
        raise PortableSettingsError(
            "portable env contains CC-Switch/provider keys or unsupported keys: "
            + ", ".join(sorted(unexpected_env))
        )

    attribution = value.get("attribution", {})
    if not isinstance(attribution, dict):
        raise PortableSettingsError("attribution must be an object")
    unexpected_attribution = set(attribution) - ALLOWED_ATTRIBUTION
    if unexpected_attribution:
        raise PortableSettingsError(
            "portable attribution contains unsupported keys: "
            + ", ".join(sorted(unexpected_attribution))
        )

    plugins = value.get("enabledPlugins", {})
    if not isinstance(plugins, dict) or any(
        not isinstance(key, str) or not isinstance(enabled, bool)
        for key, enabled in plugins.items()
    ):
        raise PortableSettingsError("enabledPlugins must map plugin IDs to booleans")

    status_line = value.get("statusLine")
    if status_line is not None and (
        not isinstance(status_line, dict)
        or status_line.get("type") != "command"
        or not isinstance(status_line.get("command"), str)
    ):
        raise PortableSettingsError("statusLine must be a command object")

    def inspect_keys(node: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                    raise PortableSettingsError(
                        "credential/provider-like key is forbidden: " + ".".join(path + (str(key),))
                    )
                inspect_keys(child, path + (str(key),))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                inspect_keys(child, path + (str(index),))

    # env variable names include CLAUDE_CODE but none of the forbidden provider parts.
    inspect_keys({key: child for key, child in value.items() if key != "enabledPlugins"})


def deep_merge(target: dict[str, Any], portable: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(target)
    for key, value in portable.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def backup_path(source: Path, backup_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = backup_dir / f"{source.name}.{stamp}.bak"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=False)
    return destination


def atomic_write(path: Path, value: dict[str, Any], mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode if mode is not None else 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def command_validate(args: argparse.Namespace) -> int:
    portable = load_object(args.portable)
    validate_portable(portable)
    print(f"Portable settings valid: {args.portable}")
    return 0


def command_common(args: argparse.Namespace) -> int:
    """Write the validated portable object used to generate CC-Switch Common Snippet."""
    portable = load_object(args.portable)
    validate_portable(portable)
    atomic_write(args.output, portable, 0o600)
    return 0


def command_merge(args: argparse.Namespace) -> int:
    portable = load_object(args.portable)
    validate_portable(portable)
    target = load_object(args.target, missing_ok=True)
    merged = deep_merge(target, portable)
    if merged == target and args.target.exists():
        print(f"Settings already current: {args.target}")
        return 0

    existing_mode = stat.S_IMODE(args.target.stat().st_mode) if args.target.exists() else None
    if args.target.exists():
        destination = backup_path(args.target, args.backup_dir)
        print(f"Settings backup: {destination}")
    atomic_write(args.target, merged, existing_mode)
    print(f"Settings merged: {args.target}")
    return 0


def command_merge_hooks(args: argparse.Namespace) -> int:
    source = load_object(args.hooks)
    source_hooks = source.get("hooks")
    if not isinstance(source_hooks, dict):
        raise PortableSettingsError("hooks manifest must contain a hooks object")

    target = load_object(args.target, missing_ok=True)

    def key_of(handler: Any) -> str | None:
        if (
            isinstance(handler, dict)
            and handler.get("type") == "command"
            and isinstance(handler.get("command"), str)
        ):
            return handler["command"]
        return None

    # Structural validation with the official Claude Code schema.
    for event, groups in source_hooks.items():
        if not isinstance(groups, list):
            raise PortableSettingsError(f"event {event} must be a list of matcher groups")
        for group in groups:
            if not isinstance(group, dict):
                raise PortableSettingsError(f"hook group must be an object in event {event}")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise PortableSettingsError(f"hook group must have a hooks list in event {event}")
            if not isinstance(group.get("matcher", ""), str):
                raise PortableSettingsError(f"hook matcher must be a string in event {event}")
            for handler in handlers:
                if key_of(handler) is None:
                    raise PortableSettingsError(
                        "only command hooks are supported in the portable hooks manifest: "
                        f"event {event}"
                    )

    current_hooks = target.get("hooks")
    merged_hooks = dict(current_hooks) if isinstance(current_hooks, dict) else {}
    changed = False

    for event, groups in source_hooks.items():
        existing_groups = merged_hooks.get(event, [])
        existing_set: set[tuple[str, str]] = set()
        for group in existing_groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher", "")
            for handler in group.get("hooks", []):
                command = key_of(handler)
                if command is not None:
                    existing_set.add((matcher, command))

        for group in groups:
            matcher = group.get("matcher", "")
            new_handlers = []
            for handler in group["hooks"]:
                command = key_of(handler)
                assert command is not None
                if (matcher, command) not in existing_set:
                    existing_set.add((matcher, command))
                    new_handlers.append(handler)
            if not new_handlers:
                continue
            target_group = next(
                (
                    candidate
                    for candidate in existing_groups
                    if isinstance(candidate, dict)
                    and candidate.get("matcher", "") == matcher
                    and isinstance(candidate.get("hooks"), list)
                ),
                None,
            )
            if target_group is not None:
                target_group["hooks"].extend(new_handlers)
            else:
                existing_groups.append({"matcher": matcher, "hooks": new_handlers})
            changed = True

        merged_hooks[event] = existing_groups

    if not changed:
        print(f"Hooks already current: {args.target}")
        return 0

    existing_mode = stat.S_IMODE(args.target.stat().st_mode) if args.target.exists() else None
    if args.target.exists():
        destination = backup_path(args.target, args.backup_dir)
        print(f"Hooks backup: {destination}")
    target["hooks"] = merged_hooks
    atomic_write(args.target, target, existing_mode)
    print(f"Hooks merged: {args.target}")
    return 0


def command_extract(args: argparse.Namespace) -> int:
    source = load_object(args.source)
    baseline = load_object(args.baseline)
    validate_portable(baseline)

    extracted: dict[str, Any] = {}
    raw_source_env = source.get("env")
    source_env: dict[str, Any] = raw_source_env if isinstance(raw_source_env, dict) else {}
    env = {key: source_env[key] for key in ALLOWED_ENV if key in source_env}
    if env:
        extracted["env"] = dict(sorted(env.items()))

    for key in ("attribution", "effortLevel", "skipDangerousModePermissionPrompt"):
        if key in source:
            extracted[key] = copy.deepcopy(source[key])
        elif key in baseline:
            extracted[key] = copy.deepcopy(baseline[key])

    baseline_plugins = baseline.get("enabledPlugins", {})
    source_plugins = source.get("enabledPlugins", {})
    if isinstance(baseline_plugins, dict):
        extracted["enabledPlugins"] = {
            plugin_id: source_plugins.get(plugin_id, enabled)
            if isinstance(source_plugins, dict)
            else enabled
            for plugin_id, enabled in baseline_plugins.items()
        }

    # Keep the repository's portable HUD command instead of importing a machine path.
    if "statusLine" in baseline:
        extracted["statusLine"] = copy.deepcopy(baseline["statusLine"])

    # Preserve the stable, human-readable key order from the baseline.
    ordered = {key: extracted[key] for key in baseline if key in extracted}
    for key in extracted:
        if key not in ordered:
            ordered[key] = extracted[key]
    validate_portable(ordered)
    atomic_write(args.output, ordered, 0o600)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--portable", type=Path, required=True)
    validate.set_defaults(func=command_validate)

    common = subparsers.add_parser("common")
    common.add_argument("--portable", type=Path, required=True)
    common.add_argument("--output", type=Path, required=True)
    common.set_defaults(func=command_common)

    merge = subparsers.add_parser("merge")
    merge.add_argument("--portable", type=Path, required=True)
    merge.add_argument("--target", type=Path, required=True)
    merge.add_argument("--backup-dir", type=Path, required=True)
    merge.set_defaults(func=command_merge)

    merge_hooks = subparsers.add_parser("merge-hooks")
    merge_hooks.add_argument("--hooks", type=Path, required=True)
    merge_hooks.add_argument("--target", type=Path, required=True)
    merge_hooks.add_argument("--backup-dir", type=Path, required=True)
    merge_hooks.set_defaults(func=command_merge_hooks)

    extract = subparsers.add_parser("extract")
    extract.add_argument("--source", type=Path, required=True)
    extract.add_argument("--baseline", type=Path, required=True)
    extract.add_argument("--output", type=Path, required=True)
    extract.set_defaults(func=command_extract)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except PortableSettingsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
