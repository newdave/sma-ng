"""Tests for post-processing integration scripts (bash).

Each script submits jobs to the SMA-NG daemon via curl. Tests run the
scripts in a subprocess with a mock HTTP server standing in for the daemon.
"""

import http.server
import json
import os
import socket
import subprocess
import sys
import threading

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPTS = {
    "radarr": os.path.join(PROJECT_ROOT, "triggers", "media_managers", "radarr.sh"),
    "sonarr": os.path.join(PROJECT_ROOT, "triggers", "media_managers", "sonarr.sh"),
    "sabnzbd": os.path.join(PROJECT_ROOT, "triggers", "usenet", "sabnzbd.sh"),
    "nzbget": os.path.join(PROJECT_ROOT, "triggers", "usenet", "nzbget.sh"),
    "qbittorrent": os.path.join(PROJECT_ROOT, "triggers", "torrents", "qbittorrent.sh"),
    "utorrent": os.path.join(PROJECT_ROOT, "triggers", "torrents", "utorrent.sh"),
}


# ---------------------------------------------------------------------------
# Mock HTTP daemon
# ---------------------------------------------------------------------------


class _MockDaemonHandler(http.server.BaseHTTPRequestHandler):
    """Captures POST /webhook/* requests and responds with a job_id.
    Handles GET /jobs/{id} for polling, always returning completed."""

    def log_message(self, format, *args):
        pass  # silence request logs

    def do_GET(self):
        resp = json.dumps({"status": "completed"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            self.server.received_bodies.append(json.loads(body))
        except Exception:
            self.server.received_bodies.append(body)

        status_code = self.server.response_status
        if status_code == 200:
            resp = json.dumps({"job_id": 1}).encode()
        else:
            resp = json.dumps({"error": self.server.response_error}).encode()

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


class MockDaemon:
    """Minimal HTTP server that records webhook submissions."""

    def __init__(self, response_status=200, response_error=""):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _MockDaemonHandler)
        self.server.received_bodies = []
        self.server.response_status = response_status
        self.server.response_error = response_error
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()

    @property
    def submissions(self):
        return self.server.received_bodies


def _free_port():
    """Return a port number that is not currently in use."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(script_key, args=(), env_override=None, timeout=10, response_status=200, response_error=""):
    """Run a trigger script against the mock daemon.

    Returns (returncode, stdout+stderr, submissions).
    """
    with MockDaemon(response_status=response_status, response_error=response_error) as daemon:
        env = {
            **os.environ,
            "SMA_DAEMON_HOST": "127.0.0.1",
            "SMA_DAEMON_PORT": str(daemon.port),
            # Prevent real polling — scripts exit after submit for most tests
            "SMA_TIMEOUT": "1",
            "SMA_POLL_INTERVAL": "1",
        }
        if env_override:
            env.update(env_override)

        result = subprocess.run(
            [SCRIPTS[script_key]] + list(args),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        submissions = list(daemon.submissions)

    return result, submissions


def _run_no_daemon(script_key, args=(), env_override=None, timeout=10):
    """Run a trigger script pointing at a port where nothing is listening."""
    port = _free_port()
    env = {
        **os.environ,
        "SMA_DAEMON_HOST": "127.0.0.1",
        "SMA_DAEMON_PORT": str(port),
        "SMA_TIMEOUT": "1",
        "SMA_POLL_INTERVAL": "1",
    }
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [SCRIPTS[script_key]] + list(args),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Radarr
# ---------------------------------------------------------------------------


class TestPostRadarr:
    def _env(self, **overrides):
        env = {
            "radarr_eventtype": "Download",
            "radarr_moviefile_path": "/movies/The Matrix (1999)/The Matrix.mkv",
            "radarr_movie_tmdbid": "603",
            "radarr_movie_imdbid": "tt0133093",
            "radarr_movie_id": "1",
            "radarr_moviefile_id": "1",
        }
        env.update(overrides)
        return env

    def test_test_event_exits_zero(self):
        result, submissions = _run("radarr", env_override={"radarr_eventtype": "Test"})
        assert result.returncode == 0
        assert submissions == []

    def test_invalid_event_exits_nonzero(self):
        result, submissions = _run("radarr", env_override={"radarr_eventtype": "Rename"})
        assert result.returncode != 0
        assert submissions == []

    def test_submits_webhook_with_tmdb(self):
        result, submissions = _run("radarr", env_override=self._env())
        assert result.returncode == 0
        assert len(submissions) == 1
        body = submissions[0]
        assert body["movieFile"]["path"] == "/movies/The Matrix (1999)/The Matrix.mkv"
        assert body["movie"]["tmdbId"] == 603

    def test_submits_with_imdb_id(self):
        result, submissions = _run("radarr", env_override=self._env(radarr_movie_tmdbid=""))
        assert result.returncode == 0
        movie = submissions[0]["movie"]
        assert "tmdbId" not in movie
        assert movie["imdbId"] == "tt0133093"

    def test_submits_both_tmdb_and_imdb(self):
        result, submissions = _run("radarr", env_override=self._env())
        movie = submissions[0]["movie"]
        assert "tmdbId" in movie
        assert "imdbId" in movie

    def test_missing_path_exits_nonzero(self):
        result, submissions = _run("radarr", env_override=self._env(radarr_moviefile_path=""))
        assert result.returncode != 0
        assert submissions == []

    def test_sma_config_included_in_payload(self):
        result, submissions = _run(
            "radarr",
            env_override={**self._env(), "SMA_CONFIG": "/config/autoProcess.movies.ini"},
        )
        assert result.returncode == 0
        assert submissions[0].get("config") == "/config/autoProcess.movies.ini"

    def test_no_sma_config_omits_config_key(self):
        env = {**self._env()}
        env.pop("SMA_CONFIG", None)
        result, submissions = _run("radarr", env_override=env)
        assert result.returncode == 0
        assert "config" not in submissions[0]

    def test_daemon_unreachable_exits_nonzero(self):
        result = _run_no_daemon("radarr", env_override=self._env())
        assert result.returncode != 0
        assert "Failed to connect" in result.stderr

    def test_daemon_returns_401_shows_api_key_hint(self):
        result, submissions = _run("radarr", env_override=self._env(), response_status=401)
        assert result.returncode != 0
        assert "SMA_DAEMON_API_KEY" in result.stderr

    def test_daemon_returns_403_shows_api_key_hint(self):
        result, submissions = _run("radarr", env_override=self._env(), response_status=403)
        assert result.returncode != 0
        assert "SMA_DAEMON_API_KEY" in result.stderr

    def test_daemon_returns_500_shows_error_body(self):
        result, submissions = _run(
            "radarr",
            env_override=self._env(),
            response_status=500,
            response_error="internal server error",
        )
        assert result.returncode != 0
        assert "500" in result.stderr

    def test_api_key_sent_in_header(self):
        """When SMA_DAEMON_API_KEY is set the daemon still receives the request
        (the mock doesn't check auth; we just verify the script doesn't crash
        and the job is submitted successfully)."""
        result, submissions = _run(
            "radarr",
            env_override={**self._env(), "SMA_DAEMON_API_KEY": "secret123"},
        )
        assert result.returncode == 0
        assert len(submissions) == 1


# ---------------------------------------------------------------------------
# Sonarr
# ---------------------------------------------------------------------------


class TestPostSonarr:
    def _env(self, **overrides):
        env = {
            "sonarr_eventtype": "Download",
            "sonarr_episodefile_path": "/tv/Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv",
            "sonarr_series_tvdbid": "81189",
            "sonarr_episodefile_seasonnumber": "1",
            "sonarr_episodefile_episodenumbers": "1",
            "sonarr_series_id": "5",
            "sonarr_episodefile_id": "10",
        }
        env.update(overrides)
        return env

    def test_test_event_exits_zero(self):
        result, submissions = _run("sonarr", env_override={"sonarr_eventtype": "Test"})
        assert result.returncode == 0
        assert submissions == []

    def test_invalid_event_exits_nonzero(self):
        result, submissions = _run("sonarr", env_override={"sonarr_eventtype": "Rename"})
        assert result.returncode != 0
        assert submissions == []

    def test_submits_with_tvdb_season_episode(self):
        result, submissions = _run("sonarr", env_override=self._env())
        assert result.returncode == 0
        assert len(submissions) == 1
        body = submissions[0]
        assert body["episodeFile"]["path"] == "/tv/Breaking Bad/Season 01/Breaking.Bad.S01E01.mkv"
        assert body["series"]["tvdbId"] == 81189
        assert body["episodes"][0]["seasonNumber"] == 1
        assert body["episodes"][0]["episodeNumber"] == 1

    def test_multi_episode_all_in_args(self):
        result, submissions = _run(
            "sonarr",
            env_override=self._env(sonarr_episodefile_episodenumbers="1,2,3"),
        )
        assert result.returncode == 0
        ep_numbers = [str(ep["episodeNumber"]) for ep in submissions[0]["episodes"]]
        assert ep_numbers == ["1", "2", "3"]

    def test_missing_path_exits_nonzero(self):
        result, submissions = _run("sonarr", env_override=self._env(sonarr_episodefile_path=""))
        assert result.returncode != 0
        assert submissions == []

    def test_sma_config_included_in_payload(self):
        result, submissions = _run(
            "sonarr",
            env_override={**self._env(), "SMA_CONFIG": "/config/autoProcess.tv.ini"},
        )
        assert result.returncode == 0
        assert submissions[0].get("config") == "/config/autoProcess.tv.ini"

    def test_no_sma_config_omits_config_key(self):
        env = {**self._env()}
        env.pop("SMA_CONFIG", None)
        result, submissions = _run("sonarr", env_override=env)
        assert result.returncode == 0
        assert "config" not in submissions[0]

    def test_daemon_unreachable_exits_nonzero(self):
        result = _run_no_daemon("sonarr", env_override=self._env())
        assert result.returncode != 0
        assert "Failed to connect" in result.stderr

    def test_daemon_returns_401_shows_api_key_hint(self):
        result, submissions = _run("sonarr", env_override=self._env(), response_status=401)
        assert result.returncode != 0
        assert "SMA_DAEMON_API_KEY" in result.stderr

    def test_daemon_returns_403_shows_api_key_hint(self):
        result, submissions = _run("sonarr", env_override=self._env(), response_status=403)
        assert result.returncode != 0
        assert "SMA_DAEMON_API_KEY" in result.stderr

    def test_daemon_returns_500_shows_error_body(self):
        result, submissions = _run(
            "sonarr",
            env_override=self._env(),
            response_status=500,
            response_error="internal server error",
        )
        assert result.returncode != 0
        assert "500" in result.stderr

    def test_api_key_sent_in_header(self):
        result, submissions = _run(
            "sonarr",
            env_override={**self._env(), "SMA_DAEMON_API_KEY": "secret123"},
        )
        assert result.returncode == 0
        assert len(submissions) == 1

    def test_date_based_episode_path_submitted_verbatim(self):
        """The Late Show scenario: date-based filename is passed through as-is."""
        path = (
            "/mnt/unionfs/Media/TV/1080P/The Late Show with Stephen Colbert (2015) {tvdb-289574}"
            "/Season 11/The Late Show with Stephen Colbert (2015) - 2026-04-15 - "
            "Anne Hathaway Josh Johnson [HDTV-1080p][EAC3 2.0][x265]-MeGusta.mkv"
        )
        result, submissions = _run(
            "sonarr",
            env_override=self._env(
                sonarr_episodefile_path=path,
                sonarr_series_tvdbid="289574",
                sonarr_episodefile_seasonnumber="11",
                sonarr_episodefile_episodenumbers="101",
            ),
        )
        assert result.returncode == 0
        body = submissions[0]
        assert body["episodeFile"]["path"] == path
        assert body["series"]["tvdbId"] == 289574
        assert body["episodes"][0]["seasonNumber"] == 11
        assert body["episodes"][0]["episodeNumber"] == 101


# ---------------------------------------------------------------------------
# SABnzbd
# ---------------------------------------------------------------------------


class TestSABPostProcess:
    def test_submits_files_in_directory(self, tmp_path):
        (tmp_path / "movie.mkv").touch()
        (tmp_path / "movie.nfo").touch()
        args = [str(tmp_path), "nzb", "clean", "0", "movies", "group", "0"]
        result, submissions = _run("sabnzbd", args=args)
        assert len(submissions) == 2

    def test_failed_status_skips(self, tmp_path):
        (tmp_path / "movie.mkv").touch()
        args = [str(tmp_path), "nzb", "clean", "0", "movies", "group", "1"]
        result, submissions = _run("sabnzbd", args=args)
        assert submissions == []

    def test_insufficient_args_exits(self):
        result, submissions = _run("sabnzbd", args=["/path", "nzb"])
        assert result.returncode != 0
        assert submissions == []

    def test_bypass_category_skips(self, tmp_path):
        (tmp_path / "movie.mkv").touch()
        args = [str(tmp_path), "nzb", "clean", "0", "bypass", "group", "0"]
        result, submissions = _run("sabnzbd", args=args)
        assert submissions == []


# ---------------------------------------------------------------------------
# NZBGet
# ---------------------------------------------------------------------------


class TestNZBGetPostProcess:
    def _env(self, directory, **overrides):
        env = {
            "NZBOP_VERSION": "21.0",
            "NZBPO_SHOULDCONVERT": "true",
            "NZBPO_BYPASS_CAT": "bypass",
            "NZBPP_TOTALSTATUS": "SUCCESS",
            "NZBPP_DIRECTORY": directory,
            "NZBPP_NZBFILENAME": "test.nzb",
            "NZBPP_CATEGORY": "movies",
        }
        env.update(overrides)
        return env

    def test_submits_files(self, tmp_path):
        (tmp_path / "movie.mkv").touch()
        result, submissions = _run("nzbget", env_override=self._env(str(tmp_path)))
        assert result.returncode == 93  # POSTPROCESS_SUCCESS
        assert len(submissions) == 1

    def test_bypass_category_skips(self, tmp_path):
        (tmp_path / "movie.mkv").touch()
        result, submissions = _run("nzbget", env_override=self._env(str(tmp_path), NZBPP_CATEGORY="bypass"))
        assert result.returncode == 95  # POSTPROCESS_NONE
        assert submissions == []

    def test_convert_disabled_skips(self, tmp_path):
        (tmp_path / "movie.mkv").touch()
        result, submissions = _run("nzbget", env_override=self._env(str(tmp_path), NZBPO_SHOULDCONVERT="false"))
        assert result.returncode == 95
        assert submissions == []

    def test_no_nzbget_env_exits_error(self):
        # Run with a clean env that has no NZBOP_VERSION
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        result, submissions = _run("nzbget", env_override={**env, "NZBOP_VERSION": ""})
        assert result.returncode == 94  # POSTPROCESS_ERROR

    def test_failed_download_skips(self, tmp_path):
        result, submissions = _run("nzbget", env_override=self._env(str(tmp_path), NZBPP_TOTALSTATUS="FAILURE"))
        assert result.returncode == 95
        assert submissions == []

    def test_invalid_directory_exits_error(self):
        result, submissions = _run("nzbget", env_override=self._env("/nonexistent/directory"))
        assert result.returncode == 94

    def test_no_files_submitted_exits_none(self, tmp_path):
        # Empty directory
        result, submissions = _run("nzbget", env_override=self._env(str(tmp_path)))
        assert result.returncode == 95
        assert submissions == []


# ---------------------------------------------------------------------------
# qBittorrent
# ---------------------------------------------------------------------------


class TestQBittorrentPostProcess:
    def test_submits_single_file(self, tmp_path):
        filepath = tmp_path / "movie.mkv"
        filepath.touch()
        # label tracker root_path content_path name hash
        args = ["movies", "tracker", str(tmp_path), str(filepath), "Movie.Name", "abc123"]
        result, submissions = _run("qbittorrent", args=args)
        assert len(submissions) == 1

    def test_submits_directory_contents(self, tmp_path):
        (tmp_path / "ep01.mkv").touch()
        (tmp_path / "ep02.mkv").touch()
        args = ["tv", "tracker", str(tmp_path), str(tmp_path), "Show.S01", "def456"]
        result, submissions = _run("qbittorrent", args=args)
        assert len(submissions) == 2

    def test_bypass_label_skips(self, tmp_path):
        (tmp_path / "file.mkv").touch()
        args = ["bypass", "tracker", str(tmp_path), str(tmp_path), "Name", "hash"]
        result, submissions = _run("qbittorrent", args=args)
        assert submissions == []


# ---------------------------------------------------------------------------
# uTorrent
# ---------------------------------------------------------------------------


class TestUTorrentPostProcess:
    def test_submits_single_file(self, tmp_path):
        filepath = tmp_path / "movie.mkv"
        filepath.touch()
        # label tracker directory kind filename hash name
        args = ["movies", "tracker", str(tmp_path), "single", "movie.mkv", "hash123", "Movie"]
        result, submissions = _run("utorrent", args=args)
        assert len(submissions) == 1

    def test_submits_multi_directory(self, tmp_path):
        (tmp_path / "ep1.mkv").touch()
        (tmp_path / "ep2.mkv").touch()
        args = ["tv", "tracker", str(tmp_path), "multi", "", "hash456", "Show"]
        result, submissions = _run("utorrent", args=args)
        assert len(submissions) == 2

    def test_bypass_skips(self, tmp_path):
        args = ["bypass", "tracker", str(tmp_path), "single", "f.mkv", "hash", "Name"]
        result, submissions = _run("utorrent", args=args)
        assert submissions == []

    def test_insufficient_args_exits(self, tmp_path):
        result, submissions = _run("utorrent", args=["label", "tracker"])
        assert result.returncode != 0
        assert submissions == []
