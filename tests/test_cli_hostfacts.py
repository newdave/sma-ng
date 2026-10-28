"""Tests for resources/cli/hostfacts.py."""

import json

import pytest

from resources.cli import hostfacts
from resources.cli.errors import PlanError

BASE_ENV = {
  "SMA_HOST_PWD": "/home/dave",
  "SMA_HOST_UID": "1000",
  "SMA_HOST_GID": "1001",
  "SMA_HOME": "/opt/sma",
  "SMA_IMAGE": "ghcr.io/newdave/sma-ng:latest",
}


def _load(tmp_path, **env):
  return hostfacts.load(dict(BASE_ENV, **env), plan_dir=str(tmp_path))


def test_complete_env_and_argstat(tmp_path):
  (tmp_path / "argstat").write_text("0\t-\t/home/dave/repair\n1\td\t/mnt/tv\n2\tf\t/home/dave/x y.mkv\n")
  facts = _load(
    tmp_path,
    SMA_HOST_OS="Darwin",
    SMA_HOST_TTY="1",
    SMA_HOST_STDIN="1",
    SMA_HOST_DRI_GIDS="993,44,44",
    SMA_HOST_NVIDIA="1",
    SMA_HOST_ROOTLESS="1",
    SMA_HOST_SELINUX="1",
    SMA_HOST_LOGS_WRITABLE="1",
    SMA_HOST_CONFIG="1",
    SMA_HOST_VERSION="abc123",
  )
  assert (facts.uid, facts.gid, facts.os, facts.bootstrap_version) == (1000, 1001, "Darwin", "abc123")
  assert facts.dri_gids == (44, 993)
  assert facts.tty and facts.stdin and facts.nvidia and facts.rootless and facts.selinux
  assert facts.logs_writable and facts.config_present
  assert facts.stat_for(1) == hostfacts.ArgStat(1, "d", "/mnt/tv")
  assert facts.stat_for(2).abspath == "/home/dave/x y.mkv"
  assert facts.daemon_container is None


def test_defaults_when_optional_facts_absent(tmp_path):
  facts = _load(tmp_path)
  assert facts.argstat == ()
  assert facts.dri_gids == ()
  assert not (facts.tty or facts.nvidia or facts.rootless or facts.selinux or facts.config_present)
  assert facts.os == "Linux" and facts.bootstrap_version == "dev"


@pytest.mark.parametrize("name", ["SMA_HOST_PWD", "SMA_HOST_UID", "SMA_HOST_GID", "SMA_HOME", "SMA_IMAGE"])
def test_missing_required_fact_named(tmp_path, name):
  env = dict(BASE_ENV)
  del env[name]
  with pytest.raises(PlanError, match=name):
    hostfacts.load(env, plan_dir=str(tmp_path))


@pytest.mark.parametrize(
  "env,match",
  [
    ({"SMA_HOST_UID": "x"}, "SMA_HOST_UID"),
    ({"SMA_HOST_PWD": "relative"}, "SMA_HOST_PWD"),
    ({"SMA_HOST_DRI_GIDS": "44,video"}, "SMA_HOST_DRI_GIDS"),
  ],
)
def test_malformed_fact_named(tmp_path, env, match):
  with pytest.raises(PlanError, match=match):
    _load(tmp_path, **env)


@pytest.mark.parametrize("line", ["x\td\t/a", "1\tq\t/a", "1\td\trelative", "1\td"])
def test_malformed_argstat(tmp_path, line):
  (tmp_path / "argstat").write_text(line + "\n")
  with pytest.raises(PlanError, match="argstat line 1"):
    _load(tmp_path)


def test_stat_for_unknown_index(tmp_path):
  with pytest.raises(PlanError, match="argument 4"):
    _load(tmp_path).stat_for(4)


def test_daemon_json_first_object(tmp_path):
  (tmp_path / "daemon.json").write_text(json.dumps([{"Name": "/sma-ng"}, {"Name": "/other"}]))
  assert _load(tmp_path).daemon_container == {"Name": "/sma-ng"}


def test_daemon_json_object_and_garbage(tmp_path):
  (tmp_path / "daemon.json").write_text(json.dumps({"Name": "/sma-ng"}))
  assert _load(tmp_path).daemon_container == {"Name": "/sma-ng"}
  (tmp_path / "daemon.json").write_text("[]")
  assert _load(tmp_path).daemon_container is None
  (tmp_path / "daemon.json").write_text("{not json")
  with pytest.raises(PlanError, match="daemon.json"):
    _load(tmp_path)


def test_plan_dir_from_env(tmp_path):
  (tmp_path / "argstat").write_text("0\td\t/x\n")
  facts = hostfacts.load(dict(BASE_ENV, SMA_PLAN_DIR=str(tmp_path)))
  assert facts.stat_for(0).abspath == "/x"
