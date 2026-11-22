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


def test_every_manual_option_is_allowed_unless_protected():
  import ast
  import pathlib

  tree = ast.parse(pathlib.Path(job_kinds.__file__).resolve().parents[2].joinpath("manual.py").read_text())
  opts = [a.value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument" for a in n.args if isinstance(a, ast.Constant) and str(a.value).startswith("-")]
  assert "-cl" in opts and "--config" in opts
  for opt in opts:
    expected = opt in job_kinds.FORBIDDEN_ARGS
    assert (job_kinds.forbidden_arg([opt]) is not None) == expected, opt


def test_script_path_override():
  argv = job_kinds.get("preview").argv("/ignored", "/p", "/c", 1, [], python="py", script_path="/x/fake.py")
  assert argv[1] == "/x/fake.py"
