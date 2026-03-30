#!/usr/bin/env python3
"""
scripts/services-json.py <local-ini>

Read all service sections (Sonarr*, Radarr*, Plex*) from the local .ini and
print a JSON object mapping section name -> {key: value, ...}.
Only sections whose names match the service pattern are included.
Only non-empty values are emitted (blank values are skipped).
"""

import json
import re
import sys

SERVICE_PATTERN = re.compile(r"^(Sonarr|Radarr|Plex|Converter)", re.IGNORECASE)

path = sys.argv[1] if len(sys.argv) > 1 else "setup/.local.ini"

services = {}
cur = None

try:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            m = re.match(r"^\[(.+)\]", line)
            if m:
                cur = m.group(1)
                continue
            if cur and SERVICE_PATTERN.match(cur) and "=" in line:
                eq = line.index("=")
                k = line[:eq].strip()
                v = line[eq + 1 :].strip()
                if v:
                    services.setdefault(cur, {})[k] = v
except FileNotFoundError:
    pass

print(json.dumps(services))
