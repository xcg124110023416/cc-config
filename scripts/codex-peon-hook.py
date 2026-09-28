#!/usr/bin/env python3
"""Run the pinned peon-ping Codex adapter from this host's independent runtime."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    runtime = Path(__file__).resolve().parent
    windows = os.name == "nt"
    adapter = runtime / "adapters" / ("codex.ps1" if windows else "codex.sh")
    if not adapter.is_file():
        return 0
    if windows:
        shell = shutil.which("powershell") or shutil.which("pwsh")
        command = [shell, "-NoProfile", "-NonInteractive", "-File", str(adapter)] if shell else []
        platform_name = "windows"
    else:
        shell = shutil.which("bash")
        command = [shell, str(adapter)] if shell else []
        platform_name = "mac" if platform.system() == "Darwin" else "linux"
    if not command:
        return 0
    environment = os.environ.copy()
    environment["CLAUDE_PEON_DIR"] = str(runtime)
    environment["PEON_PLATFORM"] = platform_name
    try:
        subprocess.run(
            command,
            stdin=sys.stdin.buffer,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # A notification must never block the Codex session.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
