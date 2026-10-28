"""Command surface of the in-image `sma` client.

argv mirrors what the user typed after ``sma``; argstat indexes refer to
positions in that argv (0 = first word). Global flags come before the
command. Messages for the user go to stderr; the plan goes to
``<plan dir>/plan``.
"""

from __future__ import annotations

import os
import sys

from resources.cli import clientconfig, hostfacts, mounts, throwaway
from resources.cli.errors import CliError, UsageError
from resources.version import get_commit, get_version

USAGE = """\
usage: sma [--local] [--mount PATH]... [-q] COMMAND [ARGS]

Commands (each runs in a throwaway container that mounts the paths you name):
  convert PATH... [ARGS]   convert, tag and file media (manual.py -a)
  preview PATH [ARGS]      print the conversion plan only (manual.py -oo)
  repair PATH... [-n]      stream-copy repair of damaged media (repair.py)
  audit PATH [ARGS]        library audit report (manual.py --audit)
  rename PATH [ARGS]       rename with SMA naming templates (rename.py)
  codecs                   list supported codecs
  shell [ARGS]             bash in a throwaway container, current directory mounted
  upgrade [TAG]            update the image and this command
  version                  host command and image versions
  help                     this text

Options:
  --local                  always run in a throwaway container
  --mount PATH             also mount PATH (repeatable; only if it exists)
  -q, --quiet              fewer messages from sma itself
"""

LATER = {"jobs", "daemon", "logs", "target", "map"}


def _parse_globals(argv):
  """Return (options, index of the command word)."""
  opts = {"local": False, "quiet": False, "mounts": []}
  i = 0
  while i < len(argv):
    arg = argv[i]
    if arg == "--local":
      opts["local"] = True
    elif arg in ("-q", "--quiet"):
      opts["quiet"] = True
    elif arg == "--mount":
      if i + 1 >= len(argv):
        raise UsageError("sma: --mount needs a PATH")
      opts["mounts"].append(i + 1)
      i += 1
    elif arg in ("-h", "--help"):
      return opts, None
    elif arg.startswith("-"):
      raise UsageError("sma: unknown option %s (see 'sma help')" % arg)
    else:
      return opts, i
    i += 1
  return opts, None


def write_plan(plan, plan_dir):
  path = os.path.join(plan_dir, "plan")
  tmp = path + ".tmp"
  with open(tmp, "w", encoding="utf-8") as f:
    f.write(plan.serialize())
  os.replace(tmp, path)


def run(argv, env=None, out=None):
  env = os.environ if env is None else env
  out = out or sys.stdout
  opts, idx = _parse_globals(argv)
  if idx is None:
    out.write(USAGE)
    return 0 if ("-h" in argv or "--help" in argv) else 2
  command, args = argv[idx], argv[idx + 1 :]
  if command == "help":
    out.write(USAGE)
    return 0
  if command == "version":
    out.write("sma image: %s (commit %s)\n" % (get_version(), get_commit()))
    return 0
  if command == "upgrade":
    raise UsageError("sma: upgrade is handled by the host command; update it with the installer")
  if command in LATER:
    raise UsageError("sma: %s is not available in this version" % command)
  if command not in throwaway.COMMANDS:
    raise UsageError("sma: unknown command %r (see 'sma help')" % command)

  facts = hostfacts.load(env)
  cfg = clientconfig.load(env)
  extra = [facts.stat_for(i).abspath for i in opts["mounts"]] + list(cfg.extra_mounts)
  req = throwaway.Request(command=command, args=tuple(args), first_index=idx + 1, extra_mounts=tuple(extra))
  plan = throwaway.build_plan(req, facts, config_dirs=mounts.config_paths(env.get("SMA_CONFIG_DIR")))
  write_plan(plan, env.get("SMA_PLAN_DIR") or hostfacts.DEFAULT_PLAN_DIR)
  return 0


def main(argv=None, env=None):
  err = sys.stderr
  try:
    return run(sys.argv[1:] if argv is None else argv, env=env)
  except CliError as exc:
    err.write("%s\n" % exc)
    return exc.exit_code
