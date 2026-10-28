"""Smoke-test imports expected to work inside the runtime Docker image.

Run as `python3 scripts/docker_smoke_imports.py`, which puts scripts/ (not the
app root) on sys.path; daemon.py is a top-level module of the app root, not an
installed package, so the root is added explicitly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import converter  # noqa: E402
import daemon  # noqa: E402
import resources.cli  # noqa: E402
import resources.readsettings  # noqa: E402

print("imports OK")
