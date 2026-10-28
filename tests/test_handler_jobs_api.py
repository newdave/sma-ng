"""POST /jobs, GET /jobs/{id}/output, GET /paths and kind-aware job responses."""

import json

import pytest

from tests.test_handler import _get_response_body, _get_response_bytes, _make_handler, _make_server


def _post(server, payload, headers=None):
  body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
  hdrs = {"Content-Type": "application/json", "Content-Length": str(len(body)), "X-API-Key": "k"}
  hdrs.update(headers or {})
  h = _make_handler("POST", "/jobs", body=body, headers=hdrs, server=server)
  h.do_POST()
  return h


def _get(server, path, headers=None):
  hdrs = {"X-API-Key": "k"}
  hdrs.update(headers or {})
  h = _make_handler("GET", path, headers=hdrs, server=server)
  h.do_GET()
  return h


@pytest.fixture
def server():
  return _make_server(api_key="k")


class TestPostJobs:
  def test_repair_queued(self, server, tmp_path):
    server.job_db.add_job.return_value = 12
    h = _post(server, {"kind": "repair", "path": str(tmp_path), "args": ["-n"]})
    assert h._response_code == 202
    body = _get_response_body(h)
    assert body["status"] == "queued" and body["job_id"] == 12 and body["kind"] == "repair"
    args, kwargs = server.job_db.add_job.call_args
    assert args == (str(tmp_path), "/config/sma-ng.yml", ["-n"])
    assert kwargs["kind"] == "repair" and kwargs["priority"] == 0 and kwargs["request_source"] == "cli"
    server.notify_workers.assert_called_once()

  def test_default_args_and_same_extension_skip_not_applied(self, server, tmp_path):
    server.path_config_manager.get_args_for_path.return_value = ["--tv"]
    server.path_config_manager.should_skip_same_extension.return_value = True
    media = tmp_path / "a.mp4"
    media.write_text("x")
    h = _post(server, {"kind": "rename", "path": str(media)})
    assert h._response_code == 202
    assert server.job_db.add_job.call_args.args[2] == []

  def test_preview_priority_and_profile(self, server, tmp_path):
    media = tmp_path / "a.mkv"
    media.write_text("x")
    server.path_config_manager.get_profile_for_path.return_value = "rq"
    h = _post(server, {"kind": "preview", "path": str(media)})
    assert h._response_code == 202
    args, kwargs = server.job_db.add_job.call_args
    assert args[2] == ["--profile", "rq"] and kwargs["priority"] == 100 and kwargs["request_profile"] == "rq"

  def test_explicit_priority_and_profile(self, server, tmp_path):
    h = _post(server, {"kind": "audit", "path": str(tmp_path), "profile": "hq", "priority": 5})
    assert h._response_code == 202
    args, kwargs = server.job_db.add_job.call_args
    assert args[2] == [] and kwargs["priority"] == 5 and kwargs["request_profile"] == "hq"

  def test_duplicate(self, server, tmp_path):
    server.job_db.add_job.return_value = None
    server.job_db.find_active_job.return_value = {"id": 4}
    h = _post(server, {"kind": "repair", "path": str(tmp_path)})
    assert h._response_code == 200
    assert _get_response_body(h) == {"status": "duplicate", "job_id": 4, "kind": "repair", "path": str(tmp_path)}
    assert server.job_db.find_active_job.call_args.kwargs == {"kind": "repair"}

  def test_requires_auth(self, server, tmp_path):
    h = _post(server, {"kind": "repair", "path": str(tmp_path)}, headers={"X-API-Key": "wrong"})
    assert h._response_code == 401
    server.job_db.add_job.assert_not_called()

  @pytest.mark.parametrize(
    "payload,match",
    [
      ({"kind": "transcode", "path": "/"}, "Unknown job kind"),
      ({"kind": "repair", "path": "/", "args": ["-c", "/etc/x"]}, "-c is set by the daemon"),
      ({"kind": "repair", "path": "/definitely/missing"}, "Path does not exist"),
      ({"kind": "repair"}, "No path provided"),
      ({"kind": 3, "path": "/"}, "kind must be a string"),
      ({"kind": "repair", "path": "/", "args": [1]}, "args a list of strings"),
      ({"kind": "repair", "path": "/", "profile": 3}, "profile must be a string"),
      ({"kind": "repair", "path": "/", "priority": "high"}, "priority must be an integer"),
      ({"kind": "repair", "path": "/", "priority": True}, "priority must be an integer"),
    ],
  )
  def test_bad_requests(self, server, payload, match):
    h = _post(server, payload)
    assert h._response_code == 400
    assert match in _get_response_body(h)["error"]
    server.job_db.add_job.assert_not_called()

  def test_not_json(self, server):
    h = _post(server, b"/just/a/path")
    assert h._response_code == 400
    assert _get_response_body(h)["error"] == "Body must be a JSON object"

  def test_args_string_is_split(self, server, tmp_path):
    h = _post(server, {"kind": "repair", "path": str(tmp_path), "args": "-n --no-plex"})
    assert h._response_code == 202
    assert server.job_db.add_job.call_args.args[2] == ["-n", "--no-plex"]

  def test_recycle_bin_refused(self, server, tmp_path):
    server.path_config_manager.is_recycle_bin_path.return_value = True
    h = _post(server, {"kind": "repair", "path": str(tmp_path)})
    assert h._response_code == 400
    assert "recycle-bin" in _get_response_body(h)["error"]

  def test_preview_of_directory_refused(self, server, tmp_path):
    h = _post(server, {"kind": "preview", "path": str(tmp_path)})
    assert h._response_code == 400
    assert _get_response_body(h)["error"] == "preview needs a file, not a directory"

  def test_convert_uses_existing_dispatch(self, server, tmp_path):
    media = tmp_path / "a.mkv"
    media.write_text("x")
    server.path_config_manager.get_args_for_path.return_value = ["--tv"]
    h = _post(server, {"path": str(media), "profile": "lq"})
    assert h._response_code == 202
    args, kwargs = server.job_db.add_job.call_args
    assert "--tv" in args[2] and ["--profile", "lq"] == args[2][-2:]
    assert "kind" not in kwargs  # convert goes through the original queue path

  def test_request_source_header(self, server, tmp_path):
    _post(server, {"kind": "repair", "path": str(tmp_path)}, headers={"X-SMA-Request-Source": "scan"})
    assert server.job_db.add_job.call_args.kwargs["request_source"] == "scan"

  def test_cluster_gate_409(self, tmp_path):
    server = _make_server(api_key="k", is_distributed=True)
    server.job_db.nodes_missing_capability.return_value = ["sma-slave0"]
    h = _post(server, {"kind": "repair", "path": str(tmp_path)})
    assert h._response_code == 409
    body = _get_response_body(h)
    assert body["node"] == "sma-slave0" and "upgrade all nodes" in body["error"]
    server.job_db.add_job.assert_not_called()

  def test_cluster_gate_passes(self, tmp_path):
    server = _make_server(api_key="k", is_distributed=True)
    server.job_db.nodes_missing_capability.return_value = []
    assert _post(server, {"kind": "repair", "path": str(tmp_path)})._response_code == 202


class TestJobOutput:
  def test_full_and_offsets(self, server):
    server.job_db.get_job_output.return_value = ("é line\nnext\n", 10)
    h = _get(server, "/jobs/3/output")
    assert h._response_code == 200
    assert h._response_headers["Content-Type"] == "text/plain; charset=utf-8"
    data = "é line\nnext\n".encode()
    assert _get_response_bytes(h) == data
    assert h._response_headers["X-Next-Offset"] == str(10 + len(data))
    assert h._response_headers["X-Output-Dropped"] == "10"
    h = _get(server, "/jobs/3/output?offset=%d" % (10 + 3))
    assert _get_response_bytes(h) == data[3:]
    h = _get(server, "/jobs/3/output?offset=%d" % (10 + len(data)))
    assert _get_response_bytes(h) == b""

  def test_offset_below_dropped_starts_at_oldest(self, server):
    server.job_db.get_job_output.return_value = ("abc", 100)
    assert _get_response_bytes(_get(server, "/jobs/3/output?offset=5")) == b"abc"

  @pytest.mark.parametrize("path,code", [("/jobs/x/output", 400), ("/jobs/3/output?offset=-1", 400), ("/jobs/3/output?offset=z", 400), ("/jobs/3/x/output", 404)])
  def test_bad_requests(self, server, path, code):
    server.job_db.get_job_output.return_value = ("", 0)
    assert _get(server, path)._response_code == code

  def test_unknown_job(self, server):
    server.job_db.get_job_output.return_value = None
    assert _get(server, "/jobs/99/output")._response_code == 404

  def test_requires_auth(self, server):
    assert _get(server, "/jobs/3/output", headers={"X-API-Key": "no"})._response_code == 401


class TestJobResponses:
  def test_get_job_has_kind_and_no_output(self, server):
    server.job_db.get_job.return_value = {"id": 1, "status": "completed", "kind": "repair", "output": "x", "output_dropped": 0}
    body = _get_response_body(_get(server, "/jobs/1"))
    assert body["kind"] == "repair" and "output" not in body and "output_dropped" not in body

  def test_get_job_null_kind_reported_as_convert(self, server):
    server.job_db.get_job.return_value = {"id": 1, "status": "pending", "kind": None}
    assert _get_response_body(_get(server, "/jobs/1"))["kind"] == "convert"

  def test_get_jobs_list(self, server):
    server.job_db.get_jobs.return_value = [{"id": 1, "config": "/c/sma-ng.yml", "output": "big", "output_dropped": 3}]
    jobs = _get_response_body(_get(server, "/jobs"))["jobs"]
    assert jobs[0]["kind"] == "convert" and "output" not in jobs[0]


class TestPaths:
  def test_roots(self, server):
    pcm = server.path_config_manager
    pcm.routing_match_paths.return_value = ["/mnt/media/TV/", "relative"]
    pcm.scan_paths = [{"path": "/mnt/media/Movies"}, {"nope": 1}]
    pcm.audit_paths = [{"path": "/mnt/media/TV"}]
    pcm.path_rewrites = [{"from": "/downloads", "to": "/mnt/dl"}]
    assert _get_response_body(_get(server, "/paths")) == {"roots": ["/downloads", "/mnt/media/Movies", "/mnt/media/TV"]}

  def test_requires_auth(self, server):
    assert _get(server, "/paths", headers={"X-API-Key": "no"})._response_code == 401
