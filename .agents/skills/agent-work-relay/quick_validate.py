#!/usr/bin/env python3
"""Canonical skill-root validator. Loads scripts/ without a global pytest path."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from validate_skill_bundle import main

if __name__ == "__main__":
    raise SystemExit(main())
