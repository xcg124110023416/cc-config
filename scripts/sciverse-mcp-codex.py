#!/usr/bin/env python3
"""Start the local SciVerse MCP server with this host's own credential."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    credential = config_home / "sciverse" / "token"
    if not credential.is_file():
        print("SciVerse credential missing; authenticate on this host.", file=sys.stderr)
        return 2
    server = shutil.which("sciverse-mcp-server")
    if server and os.name != "nt" and re.match(r"^/mnt/[a-z]/", str(Path(server).resolve()), re.I):
        server = None
    if not server:
        print("sciverse-mcp-server is not installed on this host.", file=sys.stderr)
        return 127
    environment = os.environ.copy()
    environment["SCIVERSE_API_TOKEN"] = credential.read_text(encoding="utf-8").strip()
    return subprocess.call([server, *sys.argv[1:]], env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
