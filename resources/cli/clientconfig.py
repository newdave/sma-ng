"""Per-user client config: ~/.config/sma/config.yml (mounted at /home-config).

Phase 1 reads ``extra-mounts`` only::

  extra-mounts: [/srv/scratch]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ruamel.yaml import YAML, YAMLError

from resources.cli.errors import UsageError

DEFAULT_DIR = "/home-config"
FILENAME = "config.yml"


@dataclass
class ClientConfig:
  extra_mounts: list[str] = field(default_factory=list)


def config_dir(env=None):
  env = os.environ if env is None else env
  return env.get("SMA_CLIENT_HOME") or DEFAULT_DIR


def load(env=None):
  path = os.path.join(config_dir(env), FILENAME)
  if not os.path.isfile(path):
    return ClientConfig()
  try:
    with open(path, encoding="utf-8") as f:
      raw = YAML(typ="safe").load(f) or {}
  except (OSError, YAMLError) as exc:
    raise UsageError("sma: cannot read ~/.config/sma/%s: %s" % (FILENAME, exc)) from None
  if not isinstance(raw, dict):
    raise UsageError("sma: ~/.config/sma/%s must be a YAML mapping" % FILENAME)
  mounts = raw.get("extra-mounts") or []
  if not isinstance(mounts, list) or not all(isinstance(m, str) for m in mounts):
    raise UsageError("sma: extra-mounts in ~/.config/sma/%s must be a list of paths" % FILENAME)
  return ClientConfig(extra_mounts=list(mounts))
