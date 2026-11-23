"""Decision logic of the web UI API-key shim (SMANG-43): when it asks for the key and when it does not."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from resources.daemon.handler import _AUTH_SHIM, _with_auth_shim

HARNESS = Path(__file__).parent / "js_helpers" / "auth_shim_harness.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(tmp_path, **scenario):
  shim = tmp_path / "shim.html"
  shim.write_text(_AUTH_SHIM, encoding="utf-8")
  out = subprocess.run(["node", str(HARNESS), str(shim), json.dumps(scenario)], capture_output=True, text=True, timeout=30, check=True)
  return json.loads(out.stdout)


def test_shim_injected_before_head_close():
  html = _with_auth_shim("<html><head></head><body></body></html>")
  assert html.index("sma_api_key") < html.index("</head>")


@needs_node
def test_valid_saved_key_never_prompts(tmp_path):
  r = _run(tmp_path, stored="good", serverKey="good")
  assert r == {"status": 200, "dialogs": 0, "calls": ["good"], "stored": "good"}


@needs_node
def test_missing_key_prompts_once(tmp_path):
  r = _run(tmp_path, serverKey="good")
  assert r["dialogs"] == 1 and r["status"] == "pending"


@needs_node
def test_wrong_key_is_forgotten_and_prompts_once(tmp_path):
  r = _run(tmp_path, stored="bad", serverKey="good")
  assert r["dialogs"] == 1 and r["stored"] == ""


@needs_node
def test_key_saved_by_other_page_is_used_without_prompt(tmp_path):
  r = _run(tmp_path, serverKey="good", laterStored="good")
  assert r == {"status": 200, "dialogs": 0, "calls": ["", "good"], "stored": "good"}
