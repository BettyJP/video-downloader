"""Entry point for `python -m vdl` and PyInstaller executable."""

from __future__ import annotations

import sys

from vdl.cli import main

if __name__ == "__main__":
    sys.exit(main())

