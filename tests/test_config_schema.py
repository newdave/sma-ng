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
        "autoscan": {"main": {"url": "http://a", "username": "u", "password": "leak4"}},
      },
    }
    redacted = _strip_secrets(data)
    for instance in redacted["services"]["sonarr"].values():
      for f in SERVICE_SECRET_FIELDS:
        assert f not in instance
      assert "url" in instance  # non-secret stays
    assert "token" not in redacted["services"]["plex"]["main"]
    assert "password" not in redacted["services"]["autoscan"]["main"]
    assert redacted["services"]["autoscan"]["main"]["url"] == "http://a"

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
    assert cfg.daemon.media_extensions[:3] == [".mkv", ".m4v", ".avi"]
    assert ".mp4" not in cfg.daemon.media_extensions

  def test_base_defaults(self):
    cfg = SmaConfig()
    assert cfg.base.converter.ffmpeg == "ffmpeg"
    assert cfg.base.video.codec == ["h265"]
    assert cfg.base.audio.codec == ["ac3"]
    assert cfg.base.metadata.tag is True
    assert cfg.base.permissions.chmod == "0664"

  def test_chmod_octal_int_777_is_not_decimal_1411(self):
    # Operators reach for `mode: 777` meaning rwxrwxrwx (octal 0o777).
    # The validator must store that as "0777" so int("0777", 8) → 0o777,
    # not 0o1411 which would leave files unwritable.
    cfg = SmaConfig.model_validate({"base": {"permissions": {"mode": 777}}})
    assert cfg.base.permissions.chmod == "0777"
    assert int(cfg.base.permissions.chmod, 8) == 0o777

  def test_chmod_octal_int_664(self):
    cfg = SmaConfig.model_validate({"base": {"permissions": {"chmod": 664}}})
    assert cfg.base.permissions.chmod == "0664"
    assert int(cfg.base.permissions.chmod, 8) == 0o664

  def test_chmod_string_passthrough(self):
    cfg = SmaConfig.model_validate({"base": {"permissions": {"chmod": "0664"}}})
    assert cfg.base.permissions.chmod == "0664"

  def test_chmod_rejects_non_octal_digit(self):
    # `999` looks like a mode but has decimal-only digits; reject it
    # instead of silently producing a nonsense bit pattern.
    with pytest.raises(Exception, match="octal"):
      SmaConfig.model_validate({"base": {"permissions": {"mode": 999}}})

  def test_mode_alias_does_not_warn_as_unknown(self, caplog):
    import logging

    from resources.config_loader import ConfigLoader

    loader = ConfigLoader()
    cfg = SmaConfig.model_validate({"base": {"permissions": {"mode": 664}}})
    with caplog.at_level(logging.WARNING):
      loader._warn_extras(cfg, "")
    assert not any("base.permissions.mode" in r.message for r in caplog.records)

  def test_routing_rule_requires_dotted_service_ref(self):
    with pytest.raises(Exception, match=r"<type>\.<instance>"):
      SmaConfig.model_validate(
        {
          "services": {"sonarr": {"main": {"url": "http://x"}}},
          "daemon": {"routing": [{"match": "/tv", "services": ["sonarr"]}]},
        }
      )

  def test_routing_accepts_autoscan_reference(self):
    cfg = SmaConfig.model_validate(
      {
        "services": {"autoscan": {"main": {"url": "http://localhost:3030"}}},
        "daemon": {"routing": [{"match": "/tv", "services": ["autoscan.main"]}]},
      }
    )
    assert "main" in cfg.services.autoscan
    assert cfg.services.autoscan["main"].enabled is True
    assert cfg.services.autoscan["main"].url == "http://localhost:3030"
