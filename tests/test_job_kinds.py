"""Tests for resources/daemon/job_kinds.py."""

import pytest

from resources.daemon import job_kinds


def test_convert_argv_matches_original_worker_argv():
  argv = job_kinds.get("convert").argv("/app", "/m/a.mkv", "/config/sma-ng.yml", 7, ["--tv"], python="py")
  assert argv == ["py", "/app/manual.py", "-a", "-i", "/m/a.mkv", "-c", "/config/sma-ng.yml", "--job-id", "7", "--tv"]


@pytest.mark.parametrize(
  "kind,expected",
  [
    ("preview", ["py", "/app/manual.py", "-a", "-i", "/m/a.mkv", "-c", "/cfg", "--job-id", "3", "-oo", "-x"]),
    ("audit", ["py", "/app/manual.py", "--audit", "-i", "/m/a.mkv", "-c", "/cfg", "--job-id", "3", "-x"]),
    ("repair", ["py", "/app/repair.py", "/m/a.mkv", "-c", "/cfg", "-x"]),
    ("rename", ["py", "/app/rename.py", "/m/a.mkv", "-c", "/cfg", "-x"]),
  ],
)
def test_other_kinds_argv(kind, expected):
  assert job_kinds.get(kind).argv("/app", "/m/a.mkv", "/cfg", 3, ["-x"], python="py") == expected


def test_default_python_is_current_interpreter():
  import sys

  assert job_kinds.get("repair").argv("/app", "/p", "/c", 1, [])[0] == sys.executable


def test_success_codes():
  assert job_kinds.get("audit").succeeded(0) and job_kinds.get("audit").succeeded(1)
  assert not job_kinds.get("audit").succeeded(2)
  for name in ("convert", "preview", "repair", "rename"):
    assert job_kinds.get(name).succeeded(0)
    assert not job_kinds.get(name).succeeded(1)


def test_get_defaults_and_unknown():
  assert job_kinds.get(None).name == "convert"
  assert job_kinds.get("").name == "convert"
  with pytest.raises(KeyError):
    job_kinds.get("transcode")


def test_directory_and_profile_rules():
  assert not job_kinds.get("preview").directory_ok
  assert all(job_kinds.get(k).directory_ok for k in ("convert", "audit", "repair", "rename"))
  assert {k for k in job_kinds.SUPPORTED_KINDS if job_kinds.get(k).uses_profile} == {"convert", "preview"}


@pytest.mark.parametrize("args,bad", [(["-c", "x"], "-c"), (["-n", "--config=/x"], "--config=/x"), (["-i", "/x"], "-i"), (["--input"], "--input"), (["--job-id", "3"], "--job-id")])
def test_forbidden_args(args, bad):
  assert job_kinds.forbidden_arg(args) == bad


def test_allowed_args():
  assert job_kinds.forbidden_arg(["-n", "--no-plex", "-tmdb", "603", "-tmdb603"]) is None


BYPASS_ARGS = ["-c/evil", "-i/evil", "-ci", "-ac", "-aai/x", "--conf", "--conf=/evil", "--inp=/evil", "--job", "--job=9", "--c", "--in", "--config", "--input=/x"]


@pytest.mark.parametrize("arg", BYPASS_ARGS)
def test_forbidden_arg_abbreviations_and_attached_values(arg):
  assert job_kinds.forbidden_arg(["-tmdb", "603", arg]) == arg


@pytest.mark.parametrize(
  "args",
  [
    ["-cl"],
    ["-imdb", "tt1"],
    ["-imdb=tt1"],
    ["-nc", "-nd", "-np", "-pr", "-pse", "-oo"],
    ["-pcinema"],
    ["-sc"],
    ["-a", "-fc", "-fr", "-tmdb603"],
    ["--codeclist", "--imdbid", "tt1", "--tmdbid=1", "--profile", "x", "--forceconvert"],
    ["--audit", "--no-plex", "--notag", "--nocopy"],
  ],
)
def test_legitimate_args_still_allowed(args):
  assert job_kinds.forbidden_arg(args) is None


def _tool_options(script):
  """(option strings, single-letter short flags that take no value) from the tool's add_argument calls."""
  import ast
  import pathlib

  tree = ast.parse(pathlib.Path(job_kinds.__file__).resolve().parents[2].joinpath(script).read_text())
  opts, bool_short = [], {"h"}  # argparse adds -h/--help
  for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"):
      continue
    names = [a.value for a in node.args if isinstance(a, ast.Constant) and str(a.value).startswith("-")]
    opts += names
    action = next((k.value.value for k in node.keywords if k.arg == "action" and isinstance(k.value, ast.Constant)), None)
    if action in ("store_true", "store_false", "store_const", "append_const", "count", "help", "version"):
      bool_short |= {n[1] for n in names if len(n) == 2}
  return opts, bool_short


def test_every_manual_option_is_allowed_unless_protected():
  opts, _ = _tool_options("manual.py")
  assert "-cl" in opts and "--config" in opts
  for opt in opts:
    expected = opt in job_kinds.FORBIDDEN_ARGS
    assert (job_kinds.forbidden_arg([opt]) is not None) == expected, opt


@pytest.mark.parametrize("kind", job_kinds.SUPPORTED_KINDS)
def test_every_tool_option_is_allowed_unless_protected(kind):
  """A new flag in any tool fails here until job_kinds knows whether it bundles or is protected."""
  opts, bool_short = _tool_options(job_kinds.get(kind).script)
  assert "--config" in opts
  for opt in opts:
    expected = opt in FORBIDDEN_ARGS_ALL
    assert (job_kinds.forbidden_arg([opt], kind) is not None) == expected, (kind, opt)
  # Every value-less short flag must be known, or "-<flag>c/evil" smuggles in -c.
  assert set(job_kinds.bool_short_flags(kind)) == bool_short, kind
  for flag in bool_short:
    assert job_kinds.forbidden_arg(["-%sc/evil" % flag], kind) is not None, (kind, flag)


FORBIDDEN_ARGS_ALL = {"-c", "--config", "-i", "--input", "--job-id"}

# repair.py has store_true -n/-v and rename.py has -v: "-nc/evil" there is "-n -c /evil".
TOOL_BUNDLE_BYPASSES = {
  "repair": [["-nc/evil"], ["-vc/evil"], ["-nvc", "/evil"], ["-nc", "/evil"], ["-vnc=/evil"], ["-hc/evil"], ["--conf=/evil"]],
  "rename": [["-vc/evil"], ["-vc", "/evil"], ["-vvc/evil"], ["-hc/evil"], ["--co=/evil"]],
}


@pytest.mark.parametrize("kind,args", [(k, a) for k, cases in TOOL_BUNDLE_BYPASSES.items() for a in cases])
def test_bool_flag_bundles_rejected_per_kind(kind, args):
  assert job_kinds.forbidden_arg(args, kind) is not None


@pytest.mark.parametrize("kind", ["convert", "preview", "audit"])
def test_manual_nc_still_allowed_for_manual_kinds(kind):
  assert job_kinds.forbidden_arg(["-nc", "-cl", "-imdb", "tt1"], kind) is None
  assert job_kinds.forbidden_arg(["-ac/evil"], kind) == "-ac/evil"


def test_forbidden_arg_accepts_job_kind_object():
  assert job_kinds.forbidden_arg(["-nc/evil"], job_kinds.get("repair")) == "-nc/evil"
  assert job_kinds.forbidden_arg(["-nc"], job_kinds.get("convert")) is None


def test_script_path_override():
  argv = job_kinds.get("preview").argv("/ignored", "/p", "/c", 1, [], python="py", script_path="/x/fake.py")
  assert argv[1] == "/x/fake.py"
