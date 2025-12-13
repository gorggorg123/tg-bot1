#!/usr/bin/env python3
"""Lightweight syntax smoke test runner.

Runs compileall for the repository and tabnanny for the reviews module.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _run([sys.executable, "-m", "compileall", "-q", str(repo_root)], cwd=repo_root)
    _run([sys.executable, "-m", "tabnanny", "-v", str(repo_root / "botapp" / "reviews.py")], cwd=repo_root)


if __name__ == "__main__":
    main()
