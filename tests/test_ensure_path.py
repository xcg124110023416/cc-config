#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ensure-path.py"
SPEC = importlib.util.spec_from_file_location("ensure_path", MODULE_PATH)
assert SPEC and SPEC.loader
ensure_path = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ensure_path)


class PathBlockTests(unittest.TestCase):
    def test_existing_later_entry_is_moved_to_front(self) -> None:
        self.assertIn(
            '"$HOME/cc-config/bin"|"$HOME/cc-config/bin:"*',
            ensure_path.BLOCK,
        )
        self.assertNotIn('*":$HOME/cc-config/bin:"*)', ensure_path.BLOCK)

    def test_updated_text_is_idempotent(self) -> None:
        first = ensure_path.updated_text("export PATH=\"$HOME/.local/bin:$PATH\"\n")
        self.assertEqual(ensure_path.updated_text(first), first)


if __name__ == "__main__":
    unittest.main()
