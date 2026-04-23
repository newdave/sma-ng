"""Compatibility wrapper for the shared INI merge helper.

This keeps ``import ini_merge`` working from the repository root while the
implementation continues to live under ``.mise/shared/deploy/lib`` for task
reuse.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_IMPL_PATH = Path(__file__).resolve().parent / ".mise" / "shared" / "deploy" / "lib" / "ini_merge.py"

_SPEC = spec_from_file_location("_sma_ini_merge_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
  raise ImportError("Unable to load ini_merge implementation from %s" % _IMPL_PATH)

_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

__all__ = [name for name in dir(_MODULE) if not name.startswith("_")]

for _name in __all__:
  globals()[_name] = getattr(_MODULE, _name)
