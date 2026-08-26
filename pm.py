#!/usr/bin/env python
"""Entry point: `python pm.py <command>`. Same thing as `python -m printmoney`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from printmoney.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
