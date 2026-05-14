#!/usr/bin/env python3
"""Remove all contents of results.xcassets without deleting the folder itself."""

import shutil
from pathlib import Path

root = Path(__file__).parent / "results.xcassets"

if not root.exists():
    print("results.xcassets not found — nothing to clean.")
else:
    count = 0
    for item in root.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        count += 1
    print(f"Cleaned {count} item(s) from results.xcassets/")
