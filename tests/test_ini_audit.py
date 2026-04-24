"""Tests for scripts/ini_audit.py."""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
import ini_audit  # noqa: E402
from ini_audit import Finding, audit_cross_file, audit_ini  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _write(path, content):
  with open(path, "w") as f:
    f.write(content)


SAMPLE_INI = """\
[Converter]
ffmpeg = ffmpeg
ffprobe = ffprobe
output-extension = mp4

[Video]
codec = h264
"""

LIVE_CLEAN = """\
[Converter]
ffmpeg = ffmpeg
ffprobe = ffprobe
output-extension = mp4

[Video]
codec = h264
"""

LIVE_MISSING_KEY = """\
[Converter]
ffmpeg = ffmpeg
output-extension = mp4

[Video]
codec = h264
"""

LIVE_DEPRECATED_KEY = """\
[Converter]
ffmpeg = ffmpeg
ffprobe = ffprobe
output-extension = mp4
old-option = yes

[Video]
codec = h264
"""

LIVE_MISSING_SECTION = """\
[Converter]
ffmpeg = ffmpeg
ffprobe = ffprobe
output-extension = mp4
"""


# ── audit_ini ─────────────────────────────────────────────────────────────────


class TestAuditIni:
  def test_clean_live_produces_no_findings(self, tmp_path):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_CLEAN)
    findings = audit_ini(str(s), str(l))
    assert findings == []

  def test_reports_missing_key_as_warning(self, tmp_path):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_MISSING_KEY)
    findings = audit_ini(str(s), str(l))
    missing = [f for f in findings if f.key == "ffprobe"]
    assert len(missing) == 1
    assert missing[0].level == "warning"
    assert missing[0].section == "Converter"

  def test_yaml_missing_key_as_warning(self, tmp_path):
    s = tmp_path / "sample.yaml"
    l = tmp_path / "live.yaml"
    _write(s, "Converter:\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n")
    _write(l, "Converter:\n  ffmpeg: ffmpeg\n")
    findings = audit_ini(str(s), str(l))
    missing = [f for f in findings if f.key == "ffprobe"]
    assert len(missing) == 1
    assert missing[0].level == "warning"

  def test_reports_deprecated_key_as_info(self, tmp_path):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_DEPRECATED_KEY)
    findings = audit_ini(str(s), str(l))
    deprecated = [f for f in findings if f.key == "old-option"]
    assert len(deprecated) == 1
    assert deprecated[0].level == "info"

  def test_sonarr_and_radarr_sections_are_not_deprecated(self, tmp_path):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(
      l,
      LIVE_CLEAN + "\n[Sonarr-Kids]\nhost = sonarr-kids.example.com\n\n[Radarr-4K]\nhost = radarr-4k.example.com\n",
    )
    findings = audit_ini(str(s), str(l))
    deprecated_secs = [f for f in findings if f.section in {"Sonarr-Kids", "Radarr-4K"} and f.key == ""]
    assert deprecated_secs == [], "Sonarr/Radarr wildcard sections must not be flagged as deprecated"

  def test_reports_missing_section_as_warning(self, tmp_path):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_MISSING_SECTION)
    findings = audit_ini(str(s), str(l))
    missing_secs = [f for f in findings if f.section == "Video" and f.key == ""]
    assert len(missing_secs) == 1
    assert missing_secs[0].level == "warning"

  def test_finding_str_includes_source_and_message(self, tmp_path):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_MISSING_KEY)
    findings = audit_ini(str(s), str(l))
    rendered = [str(f) for f in findings]
    assert any(str(l) in r for r in rendered)
    assert any("ffprobe" in r for r in rendered)


# ── Exit codes ────────────────────────────────────────────────────────────────


class TestExitCodes:
  def test_exit_zero_when_no_findings(self, tmp_path, monkeypatch, capsys):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_CLEAN)
    monkeypatch.setattr(
      sys,
      "argv",
      ["ini_audit.py", "--sample", str(s), "--ini", str(l)],
    )
    with pytest.raises(SystemExit) as exc_info:
      ini_audit.main()
    assert exc_info.value.code == 0

  def test_exit_one_when_findings_exist(self, tmp_path, monkeypatch):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_MISSING_KEY)
    monkeypatch.setattr(
      sys,
      "argv",
      ["ini_audit.py", "--sample", str(s), "--ini", str(l)],
    )
    with pytest.raises(SystemExit) as exc_info:
      ini_audit.main()
    assert exc_info.value.code == 1


# ── JSON output ───────────────────────────────────────────────────────────────


class TestJsonOutput:
  def test_json_flag_emits_valid_json_array(self, tmp_path, monkeypatch, capsys):
    s = tmp_path / "sample.ini"
    l = tmp_path / "live.ini"
    _write(s, SAMPLE_INI)
    _write(l, LIVE_MISSING_KEY)
    monkeypatch.setattr(
      sys,
      "argv",
      ["ini_audit.py", "--sample", str(s), "--ini", str(l), "--json"],
    )
    with pytest.raises(SystemExit):
      ini_audit.main()
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert isinstance(parsed, list)
    assert all("level" in item and "message" in item for item in parsed)


# ── audit_cross_file ──────────────────────────────────────────────────────────


class TestAuditCrossFile:
  def _daemon_json(self, tmp_path, ffmpeg_dir):
    d = tmp_path / "daemon.json"
    d.write_text(json.dumps({"ffmpeg_dir": ffmpeg_dir}))
    return str(d)

  def test_bare_name_is_consistent(self, tmp_path):
    daemon = self._daemon_json(tmp_path, "/opt/ffmpeg")
    ini = tmp_path / "live.ini"
    _write(ini, "[Converter]\nffmpeg = ffmpeg\nffprobe = ffprobe\n")
    findings = audit_cross_file(str(daemon), [str(ini)])
    assert findings == []

  def test_absolute_path_inside_ffmpeg_dir_is_consistent(self, tmp_path):
    daemon = self._daemon_json(tmp_path, "/opt/ffmpeg")
    ini = tmp_path / "live.ini"
    _write(ini, "[Converter]\nffmpeg = /opt/ffmpeg/ffmpeg\nffprobe = /opt/ffmpeg/ffprobe\n")
    findings = audit_cross_file(str(daemon), [str(ini)])
    assert findings == []

  def test_absolute_path_outside_ffmpeg_dir_is_conflict(self, tmp_path):
    daemon = self._daemon_json(tmp_path, "/opt/ffmpeg")
    ini = tmp_path / "live.ini"
    _write(ini, "[Converter]\nffmpeg = /usr/bin/ffmpeg\nffprobe = ffprobe\n")
    findings = audit_cross_file(str(daemon), [str(ini)])
    conflicts = [f for f in findings if f.key == "ffmpeg"]
    assert len(conflicts) == 1
    assert conflicts[0].level == "warning"

  def test_no_findings_when_ffmpeg_dir_not_set(self, tmp_path):
    daemon = tmp_path / "daemon.json"
    daemon.write_text(json.dumps({"ffmpeg_dir": None}))
    ini = tmp_path / "live.ini"
    _write(ini, "[Converter]\nffmpeg = /anywhere/ffmpeg\n")
    findings = audit_cross_file(str(daemon), [str(ini)])
    assert findings == []

  def test_yaml_daemon_cross_file(self, tmp_path):
    daemon = tmp_path / "sma-ng.yml"
    daemon.write_text("Daemon:\n  ffmpeg_dir: /opt/ffmpeg\n")
    config = tmp_path / "live.yaml"
    config.write_text("Converter:\n  ffmpeg: /usr/bin/ffmpeg\n  ffprobe: ffprobe\n")
    findings = audit_cross_file(str(daemon), [str(config)])
    conflicts = [f for f in findings if f.key == "ffmpeg"]
    assert len(conflicts) == 1

  def test_malformed_daemon_json_returns_error_finding(self, tmp_path):
    daemon = tmp_path / "daemon.json"
    daemon.write_text("not valid json{{{")
    ini = tmp_path / "live.ini"
    _write(ini, "[Converter]\nffmpeg = ffmpeg\n")
    findings = audit_cross_file(str(daemon), [str(ini)])
    assert len(findings) == 1
    assert findings[0].level == "error"
