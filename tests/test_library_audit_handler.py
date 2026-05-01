"""Light handler tests for the /library/* routes."""

import json
import unittest.mock as mock

from resources.daemon import handler as handler_mod


def _make_handler(method="GET", path="/library/audit", body=None, query=None):
  """Construct a WebhookHandler-shaped mock with the bits the route methods touch."""
  inst = handler_mod.WebhookHandler.__new__(handler_mod.WebhookHandler)
  inst.command = method
  inst.path = path
  inst.headers = {}
  if body is not None:
    payload = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
    inst.headers["Content-Length"] = str(len(payload))
    inst.rfile = mock.MagicMock()
    inst.rfile.read.return_value = payload
  else:
    inst.rfile = mock.MagicMock()
    inst.rfile.read.return_value = b""
  inst.wfile = mock.MagicMock()
  inst.send_json_response = mock.MagicMock()
  inst.send_response = mock.MagicMock()
  inst.send_header = mock.MagicMock()
  inst.end_headers = mock.MagicMock()
  inst.server = mock.MagicMock()
  inst.server.node_id = "test-node"
  inst.server.path_config_manager = mock.MagicMock()
  inst.server.path_config_manager.audit_paths = []
  inst.server.path_config_manager.audit_settings = mock.MagicMock(skip_dirs=[], dry_run=True)
  inst.server.path_config_manager.is_recycle_bin_path = lambda _p: False
  inst.server.logger = mock.MagicMock()
  inst.server.job_db = mock.MagicMock()
  return inst


def test_get_library_audit_lists_runs():
  inst = _make_handler()
  inst.server.job_db.list_audit_runs.return_value = [{"id": 1, "status": "completed"}]
  inst._get_library_audit({})
  args, _ = inst.send_json_response.call_args
  assert args[0] == 200
  assert args[1]["count"] == 1


def test_get_library_findings_passes_filters():
  inst = _make_handler(path="/library/findings")
  inst.server.job_db.get_findings.return_value = []
  inst._get_library_findings({"status": ["open"], "kind": ["ffprobe_failed"], "limit": ["25"]})
  inst.server.job_db.get_findings.assert_called_with(status="open", kind="ffprobe_failed", path=None, limit=25, offset=0)


def test_post_library_audit_returns_400_when_no_paths():
  inst = _make_handler(method="POST", path="/library/audit", body={"paths": []})
  inst._post_library_audit()
  args, _ = inst.send_json_response.call_args
  assert args[0] == 400


def test_post_library_audit_returns_202_with_audit_id():
  inst = _make_handler(method="POST", path="/library/audit", body={"paths": ["/tmp/x"]})
  inst.server.job_db.create_audit_run.return_value = 7
  with mock.patch("resources.daemon.handler.threading.Thread") as fake_thread:
    inst._post_library_audit()
    fake_thread.assert_called_once()
  args, _ = inst.send_json_response.call_args
  assert args[0] == 202
  assert args[1]["audit_id"] == 7
  assert args[1]["paths"] == ["/tmp/x"]


def test_post_library_audit_falls_back_to_configured_paths():
  inst = _make_handler(method="POST", path="/library/audit", body={})
  inst.server.path_config_manager.audit_paths = [{"path": "/data/movies", "enabled": True}]
  inst.server.job_db.create_audit_run.return_value = 9
  with mock.patch("resources.daemon.handler.threading.Thread"):
    inst._post_library_audit()
  args, _ = inst.send_json_response.call_args
  assert args[0] == 202
  assert args[1]["paths"] == ["/data/movies"]


def test_post_library_finding_action_404_when_not_found():
  inst = _make_handler(method="POST", path="/library/findings/123/ack")
  inst.server.job_db.set_finding_status.return_value = 0
  inst._post_library_finding_action("/library/findings/123/ack", "acked")
  args, _ = inst.send_json_response.call_args
  assert args[0] == 404


def test_post_library_finding_action_200_on_success():
  inst = _make_handler(method="POST", path="/library/findings/123/ack")
  inst.server.job_db.set_finding_status.return_value = 1
  inst._post_library_finding_action("/library/findings/123/ack", "acked")
  args, _ = inst.send_json_response.call_args
  assert args[0] == 200
  assert args[1]["finding_id"] == 123
  assert args[1]["status"] == "acked"


def test_parse_finding_id_handles_ack_path():
  inst = _make_handler()
  assert inst._parse_finding_id("/library/findings/45/ack") == 45
  assert inst._parse_finding_id("/library/findings/9") == 9
