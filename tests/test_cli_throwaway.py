"""Tests for resources/cli/throwaway.py (worker container plans)."""

import dataclasses

import pytest

from resources.cli import throwaway
from resources.cli.errors import UsageError
from resources.cli.hostfacts import ArgStat, HostFacts

IMAGE = "ghcr.io/newdave/sma-ng:3.2.23"


def _facts(argstat=(), **kw):
  base = HostFacts(
    pwd="/home/dave",
    uid=1000,
    gid=1000,
    os="Linux",
    home="/home/dave",
    sma_home="/opt/sma",
    image=IMAGE,
    bootstrap_version="dev",
    tty=False,
    stdin=False,
    dri_gids=(),
    nvidia=False,
    rootless=False,
    selinux=False,
    logs_writable=True,
    config_present=True,
    argstat=tuple(argstat),
    daemon_container=None,
  )
  return dataclasses.replace(base, **kw)


def _req(command, *args, extra=()):
  return throwaway.Request(command=command, args=tuple(args), first_index=1, extra_mounts=tuple(extra))


def _steps(plan):
  return [step.items for step in plan.steps]


def _args(items):
  return [v for k, v in items if k == "arg"]


def test_repair_full_argv():
  facts = _facts([ArgStat(1, "d", "/a")])
  plan = throwaway.build_plan(_req("repair", "/a"), facts)
  (items,) = _steps(plan)
  assert _args(items) == [
    "run",
    "--rm",
    "--label",
    "sma.throwaway=1",
    "-w",
    "/app",
    "--user",
    "1000:1000",
    "-e",
    "HOME=/tmp",
    "-v",
    "/opt/sma/config:/app/config",
    "-v",
    "/opt/sma/logs:/app/logs",
    "-v",
    "/a:/a",
    IMAGE,
    "run",
    "python",
    "repair.py",
    "/a",
  ]
  assert "-c" not in _args(items)


def test_repair_relative_paths_and_flags_anywhere():
  facts = _facts([ArgStat(1, "-", "/x"), ArgStat(2, "d", "/home/dave/tv"), ArgStat(3, "f", "/mnt/m/a.mkv")])
  plan = throwaway.build_plan(throwaway.Request("repair", ("-n", "tv", "/mnt/m/a.mkv"), 1), facts)
  args = _args(plan.steps[0].items)
  assert args[-5:] == ["python", "repair.py", "-n", "/home/dave/tv", "/mnt/m/a.mkv"]
  assert "/home/dave/tv:/home/dave/tv" in args and "/mnt/m:/mnt/m" in args


def test_rootless_and_root_skip_user_selinux_adds_z():
  facts = _facts([ArgStat(1, "d", "/a")], rootless=True, selinux=True)
  args = _args(throwaway.build_plan(_req("repair", "/a"), facts).steps[0].items)
  assert "--user" not in args
  assert "/a:/a:z" in args and "/opt/sma/config:/app/config:z" in args and "/opt/sma/logs:/app/logs:z" in args
  args = _args(throwaway.build_plan(_req("repair", "/a"), _facts([ArgStat(1, "d", "/a")], uid=0)).steps[0].items)
  assert "--user" not in args


def test_gpu_flags():
  facts = _facts([ArgStat(1, "d", "/a")], dri_gids=(44, 993), nvidia=True)
  args = _args(throwaway.build_plan(_req("repair", "/a"), facts).steps[0].items)
  i = args.index("--device")
  assert args[i : i + 7] == ["--device", "/dev/dri", "--group-add", "44", "--group-add", "993", "--gpus"]
  assert args[i + 7] == "all"
  assert args.index(IMAGE) > i


def test_logs_tmpfs_and_no_config():
  facts = _facts([ArgStat(1, "d", "/a")], logs_writable=False, config_present=False)
  args = _args(throwaway.build_plan(_req("repair", "/a"), facts).steps[0].items)
  assert args[args.index("--tmpfs") + 1] == "/app/logs"
  assert not any(a.endswith(":/app/config") for a in args)


def test_optional_mounts_are_optmount_lines_before_image():
  facts = _facts([ArgStat(1, "d", "/a")])
  plan = throwaway.build_plan(_req("repair", "/a", extra=["/srv/x"]), facts, config_dirs=["/transcode", "/a/sub"])
  items = plan.steps[0].items
  opt = [v for k, v in items if k == "optmount"]
  assert opt == ["/transcode", "/srv/x"]
  image_pos = items.index(("arg", IMAGE))
  assert all(items.index(("optmount", p)) < image_pos for p in opt)


def test_convert_one_step_per_path_tty_flags():
  facts = _facts([ArgStat(1, "f", "/m/a.mkv"), ArgStat(2, "f", "/m/b.mkv"), ArgStat(3, "-", "/x")], tty=True)
  plan = throwaway.build_plan(throwaway.Request("convert", ("/m/a.mkv", "/m/b.mkv", "-tmdb", "603"), 1), facts)
  assert len(plan.steps) == 2
  for step, path in zip(plan.steps, ["/m/a.mkv", "/m/b.mkv"]):
    args = _args(step.items)
    assert args[1:4] == ["--rm", "-i", "-t"]
    assert args[-7:] == ["python", "manual.py", "-a", "-i", path, "-tmdb", "603"]


def test_stdin_without_tty_is_i_only():
  facts = _facts([ArgStat(1, "f", "/m/a.mkv")], stdin=True)
  args = _args(throwaway.build_plan(_req("preview", "/m/a.mkv"), facts).steps[0].items)
  assert args[1:3] == ["--rm", "-i"] and "-t" not in args


def test_preview_audit_rename_argv():
  facts = _facts([ArgStat(1, "f", "/m/a.mkv"), ArgStat(2, "-", "/x")])
  preview = _args(throwaway.build_plan(_req("preview", "/m/a.mkv", "-p"), facts).steps[0].items)
  assert preview[-7:] == ["python", "manual.py", "-a", "-i", "/m/a.mkv", "-oo", "-p"]
  facts = _facts([ArgStat(1, "d", "/m")])
  audit = _args(throwaway.build_plan(_req("audit", "/m"), facts).steps[0].items)
  assert audit[-5:] == ["python", "manual.py", "--audit", "-i", "/m"]
  rename = _args(throwaway.build_plan(_req("rename", "/m", "--dry-run"), facts).steps[0].items)
  assert rename[-4:] == ["python", "rename.py", "/m", "--dry-run"]


def test_codecs_and_shell():
  facts = _facts()
  codecs = _args(throwaway.build_plan(_req("codecs"), facts).steps[0].items)
  assert codecs[-5:-3] == [IMAGE, "run"]
  assert codecs[-3:] == ["python", "manual.py", "-cl"]
  shell = _args(throwaway.build_plan(_req("shell", "-c", "id"), facts).steps[0].items)
  assert shell[shell.index("-w") + 1] == "/home/dave"
  assert "/home/dave:/home/dave" in shell
  assert shell[-3:] == ["bash", "-c", "id"]


@pytest.mark.parametrize(
  "command,args,match",
  [
    ("repair", ("-n",), "needs a PATH"),
    ("preview", ("/m/a.mkv", "/m/b.mkv"), "takes one PATH"),
    ("repair", ("/m/a.mkv", "-c", "/etc/x.yml"), "-c is set by sma"),
    ("convert", ("/m/a.mkv", "--config=/x"), "--config=/x is set by sma"),
    ("convert", ("/m/a.mkv", "--job-id", "3"), "--job-id is set by sma"),
    ("frobnicate", ("/m/a.mkv",), "unknown command"),
  ],
)
def test_usage_errors(command, args, match):
  facts = _facts([ArgStat(1, "f", "/m/a.mkv"), ArgStat(2, "f", "/m/b.mkv"), ArgStat(3, "-", "/x"), ArgStat(4, "-", "/y")])
  with pytest.raises(UsageError, match=match):
    throwaway.build_plan(throwaway.Request(command, args, 1), facts)


def test_missing_path_exit_2():
  facts = _facts([ArgStat(1, "-", "/m/gone.mkv")])
  with pytest.raises(UsageError, match="gone.mkv: no such file") as exc:
    throwaway.build_plan(_req("convert", "gone.mkv"), facts)
  assert exc.value.exit_code == 2
