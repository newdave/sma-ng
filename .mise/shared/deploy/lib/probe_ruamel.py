"""Exit 0 if ruamel.yaml is importable, exit 1 otherwise.

Used by ensure_remote_python_deps() in lib.sh to decide whether the host
needs python3-ruamel.yaml installed before the deploy stampers run. We
keep this as its own file so the probe can run via the project venv's
python on hosts where the venv exists, AND via the system python on
fresh hosts that have not yet been through deploy:sync.
"""

import sys

try:
  import ruamel.yaml  # noqa: F401
except ImportError:
  sys.exit(1)
