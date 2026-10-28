"""Client errors and the exit codes they map to (see docs/sma-cli-quickstart.md)."""


class CliError(Exception):
  exit_code = 1


class UsageError(CliError):
  """Bad arguments, a missing path, or a path the target does not serve."""

  exit_code = 2


class Unreachable(CliError):
  """An explicitly chosen daemon target did not answer."""

  exit_code = 3


class AuthError(CliError):
  """The daemon rejected the API key."""

  exit_code = 4


class PlanError(CliError):
  """The plan cannot be built, or bootstrap and image disagree (run sma upgrade)."""

  exit_code = 5
