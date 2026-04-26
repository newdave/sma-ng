"""Schema-level tests for resources.config_schema and the daemon's secret
redaction. The bulk of pydantic / loader behaviour (extra-keys warn, hard
type-fail, routing→service cross-ref) is covered by ``test_config_loader``;
this module focuses on the secrets contract that lives in
``resources.daemon.constants`` + ``resources.daemon.config._strip_secrets``.
"""

from __future__ import annotations

import pytest

from resources.config_schema import SmaConfig
from resources.daemon.config import _strip_secrets
from resources.daemon.constants import SECRET_KEYS, SERVICE_SECRET_FIELDS


class TestSecretRedaction:
  def test_daemon_secrets_redacted(self):
    data = {
      "daemon": {
        "api_key": "topsecret",
        "db_url": "postgres://u:p@h/db",
        "username": "admin",
        "password": "hunter2",
        "node_id": "uuid-xyz",
        "host": "0.0.0.0",  # non-secret stays
      },
    }
    redacted = _strip_secrets(data)
    for key in SECRET_KEYS:
      assert key not in redacted["daemon"], f"{key} should be stripped"
    assert redacted["daemon"]["host"] == "0.0.0.0"

  def test_service_instance_secrets_redacted(self):
    data = {
      "services": {
        "sonarr": {
          "main": {"url": "http://x", "apikey": "leak1", "rescan": True},
          "kids": {"url": "http://y", "apikey": "leak2"},
        },
        "plex": {"main": {"url": "http://z", "token": "leak3"}},
      },
    }
    redacted = _strip_secrets(data)
    for instance in redacted["services"]["sonarr"].values():
      for f in SERVICE_SECRET_FIELDS:
        assert f not in instance
      assert "url" in instance  # non-secret stays
    assert "token" not in redacted["services"]["plex"]["main"]

  def test_redaction_is_non_destructive(self):
    """_strip_secrets returns a deep copy — original input is unchanged."""
    data = {"daemon": {"api_key": "x"}, "services": {"sonarr": {"main": {"apikey": "y"}}}}
    _ = _strip_secrets(data)
    assert data["daemon"]["api_key"] == "x"
    assert data["services"]["sonarr"]["main"]["apikey"] == "y"

  def test_handles_missing_blocks_gracefully(self):
    """Empty/partial inputs don't raise."""
    assert _strip_secrets({}) == {}
    assert _strip_secrets({"daemon": {}}) == {"daemon": {}}
    assert _strip_secrets({"services": {}}) == {"services": {}}


class TestSchemaDefaultsContract:
  """Spot-check that schema defaults match the documented operator-facing
  defaults — these values are quoted across docs and any drift is a public
  API change that needs a migration note.
  """

  def test_daemon_defaults(self):
    cfg = SmaConfig()
    assert cfg.daemon.host == "0.0.0.0"
    assert cfg.daemon.port == 8585
    assert cfg.daemon.workers == 4
    assert cfg.daemon.smoke_test is True
    assert cfg.daemon.media_extensions[:3] == [".mkv", ".mp4", ".m4v"]

  def test_base_defaults(self):
    cfg = SmaConfig()
    assert cfg.base.converter.ffmpeg == "ffmpeg"
    assert cfg.base.video.codec == ["h265"]
    assert cfg.base.audio.codec == ["ac3"]
    assert cfg.base.metadata.tag is True
    assert cfg.base.permissions.chmod == "0664"

  def test_routing_rule_requires_dotted_service_ref(self):
    with pytest.raises(Exception, match=r"<type>\.<instance>"):
      SmaConfig.model_validate(
        {
          "services": {"sonarr": {"main": {"url": "http://x"}}},
          "daemon": {"routing": [{"match": "/tv", "services": ["sonarr"]}]},
        }
      )
