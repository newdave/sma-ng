"""Plans for running a command in a throwaway container (``docker run --rm``).

The worker container runs the image's normal entrypoint in ``run`` mode
(docker/docker-entrypoint.sh) as the caller's uid/gid. It has:

* every named path mounted at the same location;
* the host config directory mounted at ``/app/config:ro``, where the tools
  look by default (passing ``-c`` would turn off repair.py's path routing);
* config-derived directories mounted only if they exist (``optmount``);
* GPU devices passed through.
"""

from __future__ import annotations

from dataclasses import dataclass

from resources.cli import mounts as mountlib
from resources.cli.errors import UsageError
from resources.cli.hostfacts import ArgStat
from resources.cli.plan import Plan

WORKDIR = "/app"
LABEL = "sma.throwaway=1"
# The tool argv and mounts are owned by sma; these would conflict with them.
FORBIDDEN_TOOL_ARGS = frozenset({"-c", "--config", "-i", "--input", "--job-id"})


@dataclass(frozen=True)
class Spec:
  script: tuple[str, ...]  # tool argv before the path(s)
  suffix: tuple[str, ...] = ()  # fixed tool args after the path
  multi: bool = False  # several paths allowed
  per_path_steps: bool = False  # one container per path (manual.py takes a single -i)
  all_positional_paths: bool = False  # every non-option arg is a path (repair.py)


SPECS = {
  "convert": Spec(("manual.py", "-a", "-i"), multi=True, per_path_steps=True),
  "preview": Spec(("manual.py", "-a", "-i"), suffix=("-oo",)),
  "audit": Spec(("manual.py", "--audit", "-i")),
  "repair": Spec(("repair.py",), multi=True, all_positional_paths=True),
  "rename": Spec(("rename.py",)),
}
NO_PATH_COMMANDS = {"codecs": ("python", "manual.py", "-cl"), "shell": ("bash",)}
COMMANDS = tuple(SPECS) + tuple(NO_PATH_COMMANDS)


@dataclass(frozen=True)
class Request:
  command: str
  args: tuple[str, ...]  # arguments after the subcommand
  first_index: int  # argv index (as in argstat) of args[0]
  extra_mounts: tuple[str, ...] = ()  # --mount values and client-config extra-mounts


def _split(spec, req, facts):
  """Return (paths as (abspath, label), tool args with paths made absolute)."""
  paths, rest = [], []
  leading = True
  for offset, arg in enumerate(req.args):
    if arg in FORBIDDEN_TOOL_ARGS or any(arg.startswith(f + "=") for f in FORBIDDEN_TOOL_ARGS if f.startswith("--")):
      raise UsageError("sma: %s is set by sma and cannot be passed to %s" % (arg, req.command))
    is_path = not arg.startswith("-") and (spec.all_positional_paths or leading)
    if is_path:
      stat = facts.stat_for(req.first_index + offset)
      paths.append((stat, arg))
      if spec.all_positional_paths:
        rest.append(stat.abspath)  # repair.py takes paths anywhere among its flags
    else:
      leading = False
      rest.append(arg)
  if not paths:
    raise UsageError("sma: %s needs a PATH (see 'sma help')" % req.command)
  if len(paths) > 1 and not spec.multi:
    raise UsageError("sma: %s takes one PATH" % req.command)
  return paths, rest


def _z(facts):
  return ":z" if facts.selinux else ""


def _base_run(facts, mounts, workdir=WORKDIR):
  step_args = ["--rm"]
  if facts.stdin or facts.tty:
    step_args.append("-i")
  if facts.tty:
    step_args.append("-t")
  step_args += ["--label", LABEL, "-w", workdir]
  if not facts.rootless and facts.uid != 0:
    step_args += ["--user", "%d:%d" % (facts.uid, facts.gid)]
  step_args += ["-e", "HOME=/tmp"]
  if facts.config_present:
    step_args += ["-v", "%s/config:/app/config:ro%s" % (facts.sma_home, ",z" if facts.selinux else "")]
  if facts.logs_writable:
    step_args += ["-v", "%s/logs:/app/logs%s" % (facts.sma_home, _z(facts))]
  else:
    step_args += ["--tmpfs", "/app/logs"]
  for path in mounts.required:
    step_args += ["-v", "%s:%s%s" % (path, path, _z(facts))]
  return step_args


def _add_step(plan, facts, mounts, tool_argv, workdir=WORKDIR):
  step = plan.step("run", *_base_run(facts, mounts, workdir))
  for path in mounts.optional:
    step.optmount(path)
  if facts.dri_gids:
    step.arg("--device", "/dev/dri")
    for gid in facts.dri_gids:
      step.arg("--group-add", str(gid))
  if facts.nvidia:
    step.arg("--gpus", "all")
  step.arg(facts.image, "run", *tool_argv)


def build_plan(req, facts, config_dirs=()):
  """Plan for ``req``; ``config_dirs`` are the sma-ng.yml-derived directories."""
  if req.command in NO_PATH_COMMANDS:
    if req.command == "shell":
      cwd = mountlib.required_dir(ArgStat(-1, "d", facts.pwd), facts.pwd)
      mounts = mountlib.build([cwd], list(config_dirs) + list(req.extra_mounts))
      plan = Plan()
      _add_step(plan, facts, mounts, list(NO_PATH_COMMANDS["shell"]) + list(req.args), workdir=cwd)
      return plan
    mounts = mountlib.build([], list(config_dirs) + list(req.extra_mounts))
    plan = Plan()
    _add_step(plan, facts, mounts, list(NO_PATH_COMMANDS[req.command]) + list(req.args))
    return plan

  spec = SPECS.get(req.command)
  if spec is None:
    raise UsageError("sma: unknown command %r (see 'sma help')" % req.command)
  paths, rest = _split(spec, req, facts)
  required = [mountlib.required_dir(stat, label) for stat, label in paths]
  mounts = mountlib.build(required, list(config_dirs) + list(req.extra_mounts))
  plan = Plan()
  if spec.all_positional_paths:
    _add_step(plan, facts, mounts, ["python", *spec.script, *rest])
    return plan
  targets = [stat.abspath for stat, _ in paths] if spec.per_path_steps else [paths[0][0].abspath]
  for target in targets:
    _add_step(plan, facts, mounts, ["python", *spec.script, target, *spec.suffix, *rest])
  return plan
