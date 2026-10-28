"""Command surface of the in-image `sma` client.

argv mirrors what the user typed after ``sma``; argstat indexes refer to
positions in that argv (0 = first word). Global flags come before the
command. Messages for the user go to stderr; tool output and --json go to
stdout; a throwaway run's plan goes to ``<plan dir>/plan``.
"""

from __future__ import annotations

import os
import sys

from resources.cli import clientconfig, hostfacts, manage, mounts, remote, routing, throwaway
from resources.cli.errors import CliError, UsageError
from resources.version import get_commit, get_version

USAGE = """\
usage: sma [--target NAME] [--local] [--no-wait] [--json] [--mount PATH]... [-q] COMMAND [ARGS]

Commands (sent to the daemon when one answers, otherwise run in a throwaway
container that mounts the paths you name):
  convert PATH... [ARGS]   convert, tag and file media (manual.py -a)
  preview PATH [ARGS]      print the conversion plan only (manual.py -oo)
  repair PATH... [-n]      stream-copy repair of damaged media (repair.py)
  audit PATH [ARGS]        library audit report (manual.py --audit)
  rename PATH [ARGS]       rename with SMA naming templates (rename.py)
  codecs                   list supported codecs (throwaway container)
  shell [ARGS]             bash in a throwaway container, current directory mounted

Daemon jobs and settings:
  jobs [list|show ID|logs ID [-f]|cancel ID]
  target add NAME URL [--api-key-stdin] | list | use NAME | rm NAME
  map add TARGET LOCAL REMOTE | list [TARGET] | rm TARGET LOCAL

  upgrade [TAG]            update the image and this command
  version                  host command and image versions
  help                     this text

Options:
  --target NAME            send to this daemon (default: default-target, else a local daemon if one answers)
  --local                  run in a throwaway container even when a daemon answers
  --no-wait                print job ids instead of following the jobs
  --json                   machine-readable output on stdout
  --mount PATH             also mount PATH in throwaway runs (repeatable; only if it exists)
  -q, --quiet              fewer messages from sma itself
"""

LATER = {"daemon", "logs"}
DAEMON_COMMANDS = set(remote.KINDS)


def _parse_globals(argv):
  """Return (options, index of the command word)."""
  opts = {"local": False, "quiet": False, "mounts": [], "target": None, "wait": True, "json": False}
  i = 0
  while i < len(argv):
    arg = argv[i]
    if arg == "--local":
      opts["local"] = True
    elif arg in ("-q", "--quiet"):
      opts["quiet"] = True
    elif arg == "--no-wait":
      opts["wait"] = False
    elif arg == "--json":
      opts["json"] = True
    elif arg in ("--mount", "--target"):
      if i + 1 >= len(argv):
        raise UsageError("sma: %s needs a value" % arg)
      if arg == "--mount":
        opts["mounts"].append(i + 1)
      else:
        opts["target"] = argv[i + 1]
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


def run(argv, env=None, out=None, err=None, stdin=None, client=None):
  env = os.environ if env is None else env
  out = out or sys.stdout
  err = err or sys.stderr
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
  if command not in throwaway.COMMANDS and command not in ("jobs", "target", "map"):
    raise UsageError("sma: unknown command %r (see 'sma help')" % command)

  cfg = clientconfig.load(env)
  if command == "target":
    return manage.target(args, cfg, env, out, err, stdin=stdin or sys.stdin)
  if command == "map":
    return manage.mapping(args, cfg, env, out, err)

  facts = hostfacts.load(env)
  opts["env_target"] = env.get("SMA_TARGET")
  if command == "jobs":
    return manage.jobs(args, cfg, facts, opts, out, err)

  if command in DAEMON_COMMANDS:
    resolved = routing.resolve_target(cfg, opts["target"], opts["env_target"])
    daemon = routing.decide(resolved, facts, local=opts["local"], warn=lambda m: err.write(m + "\n"), client=client)
    if daemon is not None:
      return remote.run(command, args, idx + 1, facts, resolved, daemon, out, err, wait=opts["wait"], as_json=opts["json"], quiet=opts["quiet"])

  extra = [facts.stat_for(i).abspath for i in opts["mounts"]] + list(cfg.extra_mounts)
  req = throwaway.Request(command=command, args=tuple(args), first_index=idx + 1, extra_mounts=tuple(extra))
  plan = throwaway.build_plan(req, facts, config_dirs=mounts.config_paths(env.get("SMA_CONFIG_DIR")))
  write_plan(plan, env.get("SMA_PLAN_DIR") or hostfacts.DEFAULT_PLAN_DIR)
  return 0


def main(argv=None, env=None):
  try:
    return run(sys.argv[1:] if argv is None else argv, env=env)
  except CliError as exc:
    sys.stderr.write("%s\n" % exc)
    return exc.exit_code
  except KeyboardInterrupt:
    sys.stderr.write("\n")
    return 130
