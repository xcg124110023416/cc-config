#!/usr/bin/env python3
"""Install an idempotent managed PATH block into shell startup files."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import tempfile
from pathlib import Path

BEGIN = "# >>> cc-config wrapper >>>"
END = "# <<< cc-config wrapper <<<"
BLOCK = f'''{BEGIN}
case ":$PATH:" in
  *":$HOME/cc-config/bin:"*) ;;
  *) export PATH="$HOME/cc-config/bin:$PATH" ;;
esac
{END}'''


def updated_text(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    inside = False
    found_begin = False
    for line in lines:
        if line == BEGIN:
            if inside or found_begin:
                raise ValueError("duplicate or nested cc-config PATH block")
            inside = True
            found_begin = True
            continue
        if line == END:
            if not inside:
                raise ValueError("cc-config PATH block ends without a start marker")
            inside = False
            continue
        if not inside:
            output.append(line)
    if inside:
        raise ValueError("cc-config PATH block has no end marker")
    while output and not output[-1].strip():
        output.pop()
    output.extend(["", BLOCK])
    return "\n".join(output) + "\n"


def atomic_write(path: Path, content: str, mode: int) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(path: Path, backup_dir: Path) -> None:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = updated_text(original)
    if original == updated:
        print(f"PATH block already current: {path}")
        return

    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = backup_dir / path.name
        shutil.copy2(path, destination, follow_symlinks=False)
        print(f"Shell startup backup: {destination}")
        mode = stat.S_IMODE(path.stat().st_mode)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = 0o644
    atomic_write(path, updated, mode)
    print(f"PATH block installed: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.files:
        install(path, args.backup_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
