"""Tests for triggers/lib/json_tools.py."""

import importlib.util
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(PROJECT_ROOT, "triggers", "lib", "json_tools.py")

spec = importlib.util.spec_from_file_location("trigger_json_tools", MODULE_PATH)
json_tools = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(json_tools)


class TestJsonGet:
  def test_missing_field_returns_default(self, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", open(os.devnull, "r", encoding="utf-8"))
    args = json_tools.build_parser().parse_args(["get", "--field", "job_id", "--default", "missing"])
    rc = args.func(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "missing"

  def test_nested_field_is_returned(self, monkeypatch, capsys, tmp_path):
    payload = tmp_path / "payload.json"
    payload.write_text('{"job": {"status": "running"}}', encoding="utf-8")
    with payload.open("r", encoding="utf-8") as handle:
      monkeypatch.setattr("sys.stdin", handle)
      args = json_tools.build_parser().parse_args(["get", "--field", "job.status", "--default", "missing"])
      rc = args.func(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == "running"


class TestBuildGeneric:
  def test_build_generic_includes_args_and_config(self, capsys):
    args = json_tools.build_parser().parse_args(["build-generic", "--path", "/media/movie.mkv", "--config", "/cfg.ini", "--arg=-tmdb", "--arg=603"])
    rc = args.func(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out == '{"path": "/media/movie.mkv", "args": ["-tmdb", "603"], "config": "/cfg.ini"}'


class TestBuildRadarrEnv:
  def test_build_radarr_env_uses_ids(self, monkeypatch, capsys):
    monkeypatch.setenv("radarr_moviefile_path", "/movies/The Matrix.mkv")
    monkeypatch.setenv("radarr_movie_tmdbid", "603")
    monkeypatch.setenv("radarr_movie_imdbid", "tt0133093")
    monkeypatch.setenv("SMA_CONFIG", "/cfg.ini")
    args = json_tools.build_parser().parse_args(["build-radarr-env"])
    rc = args.func(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert '"tmdbId": 603' in out
    assert '"imdbId": "tt0133093"' in out
    assert '"config": "/cfg.ini"' in out


class TestBuildSonarrEnv:
  def test_build_sonarr_env_uses_episode_list(self, monkeypatch, capsys):
    monkeypatch.setenv("sonarr_episodefile_path", "/tv/show/episode.mkv")
    monkeypatch.setenv("sonarr_series_tvdbid", "81189")
    monkeypatch.setenv("sonarr_episodefile_seasonnumber", "1")
    monkeypatch.setenv("sonarr_episodefile_episodenumbers", "1,2,3")
    args = json_tools.build_parser().parse_args(["build-sonarr-env"])
    rc = args.func(args)
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert '"tvdbId": 81189' in out
    assert out.count('"episodeNumber"') == 3
