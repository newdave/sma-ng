"""Plan protocol v1: the Docker commands the host bootstrap runs for the client.

The in-image client never talks to Docker. It writes a plan file that
``setup/sma-host.sh`` reads line by line and turns into ``docker`` argv with
``set --`` (never ``eval``)::

  sma-plan 1
  step docker
  arg run
  arg --rm
  optmount /transcode
  arg ghcr.io/newdave/sma-ng:latest
  end

``optmount PATH`` becomes ``-v PATH:PATH`` (``:z`` under SELinux) only when
PATH is a directory on the host. Steps run in order; a non-zero step aborts;
the final step is ``exec``'d so the terminal, signals and exit code go to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from resources.cli.errors import PlanError

PLAN_VERSION = 1
PLAN_HEADER = "sma-plan %d" % PLAN_VERSION

# Keep in sync with the allow-list in setup/sma-host.sh (run_plan).
ALLOWED_SUBCOMMANDS = frozenset({"run", "pull", "rm", "stop", "restart", "logs", "compose", "exec", "create", "cp", "inspect"})


__all__ = ["ALLOWED_SUBCOMMANDS", "PLAN_HEADER", "PLAN_VERSION", "Plan", "PlanError", "Step"]


def _check_value(value: str) -> str:
  if not isinstance(value, str):
    raise PlanError("plan values must be strings, got %r" % (value,))
  if "\n" in value or "\r" in value or "\0" in value:
    raise PlanError("plan values cannot contain newlines or NUL: %r" % value)
  return value


@dataclass
class Step:
  """One ``docker`` invocation: ordered ``("arg"|"optmount", value)`` items."""

  items: list[tuple[str, str]] = field(default_factory=list)

  def arg(self, *values: str) -> Step:
    for value in values:
      self.items.append(("arg", _check_value(value)))
    return self

  def optmount(self, path: str) -> Step:
    self.items.append(("optmount", _check_value(path)))
    return self

  def args(self) -> list[str]:
    return [value for kind, value in self.items if kind == "arg"]

  def validate(self) -> None:
    if not self.items or self.items[0][0] != "arg":
      raise PlanError("a step must start with a docker subcommand")
    sub = self.items[0][1]
    if sub not in ALLOWED_SUBCOMMANDS:
      raise PlanError("docker subcommand %r is not allowed in a plan" % sub)


@dataclass
class Plan:
  steps: list[Step] = field(default_factory=list)

  def step(self, subcommand: str, *args: str) -> Step:
    step = Step().arg(subcommand, *args)
    step.validate()
    self.steps.append(step)
    return step

  def serialize(self) -> str:
    lines = [PLAN_HEADER]
    for step in self.steps:
      step.validate()
      lines.append("step docker")
      lines.extend("%s %s" % item for item in step.items)
      lines.append("end")
    return "\n".join(lines) + "\n"
