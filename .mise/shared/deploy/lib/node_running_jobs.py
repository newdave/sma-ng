"""Read ``running_jobs`` from a stdin JSON node payload.

Used by ``mise run cluster:upgrade`` to poll a remote node's
``running_jobs`` count without embedding inline Python in the shell
task (forbidden per CLAUDE.md). Prints the integer to stdout, or an
empty string when the field is missing or the input is unparseable.
"""

import json
import sys

try:
  data = json.load(sys.stdin)
  print(data.get("running_jobs", ""))
except Exception:
  print("")
