"""Per-user client config: ~/.config/sma/config.yml (mounted at /home-config).

::

  default-target: nas
  targets:
    nas:
      url: http://nas.lan:8585
      api-key: "..."            # written by `sma target add --api-key-stdin`
      mappings:
        - {local: /Volumes/Media, remote: /mnt/unionfs/Media}
  extra-mounts: [/srv/scratch]

A file holding API keys must not be readable by group or others.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field

from ruamel.yaml import YAML, YAMLError

from resources.cli.errors import UsageError

DEFAULT_DIR = "/home-config"
FILENAME = "config.yml"
LOCAL_TARGET = "local"
LOCAL_URL = "http://127.0.0.1:8585"


@dataclass
class Target:
  name: str
  url: str
  api_key: str | None = None
  mappings: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ClientConfig:
  default_target: str | None = None
  targets: dict[str, Target] = field(default_factory=dict)
  extra_mounts: list[str] = field(default_factory=list)
  raw: dict = field(default_factory=dict)


def config_dir(env=None):
  env = os.environ if env is None else env
  return env.get("SMA_CLIENT_HOME") or DEFAULT_DIR


def config_path(env=None):
  return os.path.join(config_dir(env), FILENAME)


def _fail(message):
  raise UsageError("sma: ~/.config/sma/%s: %s" % (FILENAME, message))


def _parse_targets(raw):
  targets = {}
  for name, data in (raw.get("targets") or {}).items():
    if not isinstance(data, dict) or not isinstance(data.get("url"), str) or not data["url"]:
      _fail("target %r needs a url" % name)
    key = data.get("api-key")
    if key is not None and not isinstance(key, str):
      _fail("api-key of target %r must be a string" % name)
    mappings = []
    for entry in data.get("mappings") or []:
      if not isinstance(entry, dict) or not isinstance(entry.get("local"), str) or not isinstance(entry.get("remote"), str):
        _fail("mappings of target %r must be {local: PATH, remote: PATH} entries" % name)
      mappings.append((entry["local"], entry["remote"]))
    targets[str(name)] = Target(str(name), data["url"].rstrip("/"), key or None, mappings)
  return targets


def load(env=None):
  path = config_path(env)
  if not os.path.isfile(path):
    return ClientConfig()
  try:
    with open(path, encoding="utf-8") as f:
      raw = YAML(typ="safe").load(f) or {}
  except (OSError, YAMLError) as exc:
    raise UsageError("sma: cannot read ~/.config/sma/%s: %s" % (FILENAME, exc)) from None
  if not isinstance(raw, dict):
    _fail("must be a YAML mapping")
  if not isinstance(raw.get("targets") or {}, dict):
    _fail("targets must be a mapping of name to settings")
  mounts = raw.get("extra-mounts") or []
  if not isinstance(mounts, list) or not all(isinstance(m, str) for m in mounts):
    _fail("extra-mounts must be a list of paths")
  targets = _parse_targets(raw)
  if any(t.api_key for t in targets.values()) and os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
    _fail("holds API keys but other users can read it; run: chmod 600 ~/.config/sma/%s" % FILENAME)
  default = raw.get("default-target")
  return ClientConfig(default_target=default if isinstance(default, str) else None, targets=targets, extra_mounts=list(mounts), raw=raw)


def save(cfg, env=None):
  """Write the config back (0600, atomically), keeping keys this client does not know."""
  raw = dict(cfg.raw)
  raw["targets"] = {}
  for t in cfg.targets.values():
    entry = {"url": t.url}
    if t.api_key:
      entry["api-key"] = t.api_key
    if t.mappings:
      entry["mappings"] = [{"local": local, "remote": remote} for local, remote in t.mappings]
    raw["targets"][t.name] = entry
  if not raw["targets"]:
    del raw["targets"]
  if cfg.default_target:
    raw["default-target"] = cfg.default_target
  else:
    raw.pop("default-target", None)
  path = config_path(env)
  os.makedirs(os.path.dirname(path), exist_ok=True)
  tmp = path + ".tmp"
  fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
  with os.fdopen(fd, "w", encoding="utf-8") as f:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.dump(raw, f)
  os.chmod(tmp, 0o600)
  os.replace(tmp, path)
