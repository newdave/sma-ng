"""Which host paths a throwaway container mounts.

* Required mounts come from the paths named on the command line. The
  bootstrap stat'ed them on the host (argstat): a directory is mounted
  itself, any other existing path through its parent, and a missing path is
  an error. ``docker run -v`` would otherwise create it as a root-owned
  directory.
* Optional mounts come from sma-ng.yml (output directory, move-to, copy-to,
  recycle bin, analyzer model/cache dirs, in the base config and every
  profile), ``extra-mounts`` in the client config, and ``--mount``. They are
  emitted as ``optmount`` plan lines, so the bootstrap only mounts the ones
  that exist on the host.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass

from resources.cli.errors import UsageError
from resources.config_loader import ConfigError, ConfigLoader

DEFAULT_CONFIG_DIR = "/app/config"
CONFIG_FILENAME = "sma-ng.yml"

# ConverterSettings fields holding host directories a run may write to.
CONVERTER_PATH_FIELDS = ("output_directory", "move_to", "copy_to", "recycle_bin")
# Path-looking ConverterSettings fields that are not mount targets.
CONVERTER_IGNORED_FIELDS = ("output_directory_space_ratio", "bypass_if_copying_all", "regex_directory_replace")
ANALYZER_PATH_FIELDS = ("model_dir", "cache_dir")


@dataclass(frozen=True)
class Mounts:
  required: tuple[str, ...]
  optional: tuple[str, ...]


def _norm(path):
  return posixpath.normpath(path)


def _checked(path, label):
  path = _norm(path)
  if not path.startswith("/"):
    raise UsageError("sma: %s: not an absolute path" % label)
  if path == "/":
    raise UsageError("sma: %s: refusing to mount /" % label)
  if ":" in path:
    raise UsageError("sma: %s: paths containing ':' cannot be mounted" % label)
  return path


def required_dir(stat, label):
  """Directory to mount for an argument (its own path for a directory, else its parent)."""
  if stat.kind == "-":
    raise UsageError("sma: %s: no such file or directory" % label)
  path = _checked(stat.abspath, label)
  return path if stat.kind == "d" else _checked(posixpath.dirname(path), label)


def _values(settings, fields):
  for name in fields:
    value = getattr(settings, name, None)
    for item in value if isinstance(value, list) else [value]:
      if isinstance(item, str) and item.startswith("/"):
        yield item


def config_paths(config_dir=None):
  """Absolute directories sma-ng.yml may write to, across the base config and all profiles."""
  config_dir = config_dir or os.environ.get("SMA_CONFIG_DIR") or DEFAULT_CONFIG_DIR
  path = os.path.join(config_dir, CONFIG_FILENAME)
  if not os.path.isfile(path):
    return []
  try:
    cfg = ConfigLoader().load(path)
  except ConfigError as exc:
    raise UsageError("sma: %s: %s" % (CONFIG_FILENAME, exc)) from None
  sections: list[tuple[object | None, object | None]] = [(cfg.base.converter, cfg.base.analyzer)]
  sections.extend((p.converter, p.analyzer) for p in cfg.profiles.values())
  found = []
  for converter, analyzer in sections:
    if converter is not None:
      found.extend(_values(converter, CONVERTER_PATH_FIELDS))
    if analyzer is not None:
      found.extend(_values(analyzer, ANALYZER_PATH_FIELDS))
  return found


def _under(path, parent):
  return path == parent or path.startswith(parent.rstrip("/") + "/")


def build(required, optional):
  """De-duplicate: required paths win; nested required paths collapse into their parent."""
  req = []
  for path in sorted({_norm(p) for p in required}, key=len):
    if not any(_under(path, kept) for kept in req):
      req.append(path)
  opt = []
  for raw in optional:
    path = _checked(raw, raw)
    if path in opt or any(_under(path, r) for r in req):
      continue
    opt.append(path)
  return Mounts(required=tuple(sorted(req)), optional=tuple(opt))
