"""Facts about the Docker host, collected by the bootstrap (setup/sma-host.sh).

The client runs in a container and cannot see host paths or the host's
Docker, so the bootstrap reports what the client needs:

* ``SMA_HOST_*`` / ``SMA_HOME`` / ``SMA_IMAGE`` environment variables;
* ``<plan dir>/argstat``: one ``<index>\\t<d|f|->\\t<abspath>`` line per
  argument given to ``sma`` (directory, other existing path, missing);
* ``<plan dir>/daemon.json``: ``docker inspect sma-ng`` output, when that
  container exists.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from resources.cli.errors import PlanError

DEFAULT_PLAN_DIR = "/plan"


@dataclass(frozen=True)
class ArgStat:
  index: int
  kind: str  # "d" directory, "f" other existing path, "-" missing
  abspath: str


@dataclass(frozen=True)
class HostFacts:
  pwd: str
  uid: int
  gid: int
  os: str
  home: str
  sma_home: str
  image: str
  bootstrap_version: str
  tty: bool
  stdin: bool
  dri_gids: tuple[int, ...]
  nvidia: bool
  rootless: bool
  selinux: bool
  logs_writable: bool
  config_present: bool
  argstat: tuple[ArgStat, ...]
  daemon_container: dict | None

  def stat_for(self, index: int) -> ArgStat:
    for stat in self.argstat:
      if stat.index == index:
        return stat
    raise PlanError("no argstat entry for argument %d" % index)


def _required(env, name):
  value = env.get(name, "")
  if not value:
    raise PlanError("missing host fact %s; run sma through the installed bootstrap" % name)
  return value


def _int(env, name):
  value = _required(env, name)
  try:
    return int(value)
  except ValueError:
    raise PlanError("host fact %s is not an integer: %r" % (name, value)) from None


def _flag(env, name):
  return env.get(name, "") == "1"


def _gids(env):
  raw = env.get("SMA_HOST_DRI_GIDS", "").strip()
  if not raw:
    return ()
  try:
    return tuple(sorted({int(part) for part in raw.split(",") if part.strip()}))
  except ValueError:
    raise PlanError("host fact SMA_HOST_DRI_GIDS is malformed: %r" % raw) from None


def parse_argstat(text):
  stats = []
  for lineno, line in enumerate(text.splitlines(), 1):
    if not line:
      continue
    parts = line.split("\t", 2)
    if len(parts) != 3 or parts[1] not in ("d", "f", "-") or not parts[0].isdigit() or not parts[2].startswith("/"):
      raise PlanError("argstat line %d is malformed: %r" % (lineno, line))
    stats.append(ArgStat(int(parts[0]), parts[1], parts[2]))
  return tuple(stats)


def _read_daemon(plan_dir):
  path = os.path.join(plan_dir, "daemon.json")
  if not os.path.isfile(path):
    return None
  try:
    with open(path, encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, ValueError) as exc:
    raise PlanError("daemon.json is unreadable: %s" % exc) from None
  if isinstance(data, list):
    return data[0] if data and isinstance(data[0], dict) else None
  return data if isinstance(data, dict) else None


def load(env=None, plan_dir=None):
  """Build HostFacts from the environment and the plan directory."""
  env = os.environ if env is None else env
  plan_dir = plan_dir or env.get("SMA_PLAN_DIR") or DEFAULT_PLAN_DIR
  argstat_path = os.path.join(plan_dir, "argstat")
  try:
    with open(argstat_path, encoding="utf-8") as f:
      argstat = parse_argstat(f.read())
  except FileNotFoundError:
    argstat = ()
  pwd = _required(env, "SMA_HOST_PWD")
  if not pwd.startswith("/"):
    raise PlanError("host fact SMA_HOST_PWD is not absolute: %r" % pwd)
  return HostFacts(
    pwd=pwd,
    uid=_int(env, "SMA_HOST_UID"),
    gid=_int(env, "SMA_HOST_GID"),
    os=env.get("SMA_HOST_OS", "Linux") or "Linux",
    home=env.get("SMA_HOST_HOME", ""),
    sma_home=_required(env, "SMA_HOME"),
    image=_required(env, "SMA_IMAGE"),
    bootstrap_version=env.get("SMA_HOST_VERSION", "dev") or "dev",
    tty=_flag(env, "SMA_HOST_TTY"),
    stdin=_flag(env, "SMA_HOST_STDIN"),
    dri_gids=_gids(env),
    nvidia=_flag(env, "SMA_HOST_NVIDIA"),
    rootless=_flag(env, "SMA_HOST_ROOTLESS"),
    selinux=_flag(env, "SMA_HOST_SELINUX"),
    logs_writable=_flag(env, "SMA_HOST_LOGS_WRITABLE"),
    config_present=_flag(env, "SMA_HOST_CONFIG"),
    argstat=argstat,
    daemon_container=_read_daemon(plan_dir),
  )
