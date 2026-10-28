"""Job kinds the daemon runs: which tool, its argv, and what counts as success.

``convert`` is the original job type and keeps its exact argv. The other
kinds run SMA-NG's other tools on a path submitted through ``POST /jobs``.
Every kind gets ``-c <config>``: inside the daemon the tools' default config
path (``/app/config/sma-ng.yml``) does not exist, and the daemon has already
routed the path to a config when the job was queued.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Workers advertise this in their heartbeat; PostgreSQL clusters refuse
# non-convert jobs while any online node lacks it (it would run them as
# conversions).
CAPABILITY = "job-kinds-v1"

# Set by the daemon on the tool's command line; never accepted from a request.
FORBIDDEN_ARGS = frozenset({"-c", "--config", "-i", "--input", "--job-id"})


@dataclass(frozen=True)
class JobKind:
  name: str
  script: str
  directory_ok: bool
  success_codes: frozenset[int]
  uses_profile: bool  # the tool takes --profile (manual.py-based kinds)

  def argv(self, script_dir, path, config, job_id, args, python=None, script_path=None):
    """Tool command line; *script_path* overrides ``script_dir/script`` (the worker's manual.py)."""
    python = python or sys.executable
    script = script_path or os.path.join(script_dir, self.script)
    args = list(args)
    if self.name == "convert":
      return [python, script, "-a", "-i", path, "-c", config, "--job-id", str(job_id)] + args
    if self.name == "preview":
      return [python, script, "-a", "-i", path, "-c", config, "--job-id", str(job_id), "-oo"] + args
    if self.name == "audit":
      return [python, script, "--audit", "-i", path, "-c", config, "--job-id", str(job_id)] + args
    return [python, script, path, "-c", config] + args

  def succeeded(self, returncode):
    return returncode in self.success_codes


JOB_KINDS = {
  kind.name: kind
  for kind in (
    JobKind("convert", "manual.py", directory_ok=True, success_codes=frozenset({0}), uses_profile=True),
    JobKind("preview", "manual.py", directory_ok=False, success_codes=frozenset({0}), uses_profile=True),
    # manual.py --audit exits 1 when it finds problems: the audit itself worked.
    JobKind("audit", "manual.py", directory_ok=True, success_codes=frozenset({0, 1}), uses_profile=False),
    JobKind("repair", "repair.py", directory_ok=True, success_codes=frozenset({0}), uses_profile=False),
    JobKind("rename", "rename.py", directory_ok=True, success_codes=frozenset({0}), uses_profile=False),
  )
}
SUPPORTED_KINDS = tuple(JOB_KINDS)
DEFAULT_KIND = "convert"


def get(name):
  """Return the JobKind for *name* (None/empty means convert); KeyError if unknown."""
  return JOB_KINDS[name or DEFAULT_KIND]


def forbidden_arg(args):
  """First argument the daemon sets itself (``-c``, ``--config=x``, ...), or None."""
  for arg in args:
    if arg in FORBIDDEN_ARGS:
      return arg
    if arg.startswith("--") and arg.split("=", 1)[0] in FORBIDDEN_ARGS:
      return arg
  return None
