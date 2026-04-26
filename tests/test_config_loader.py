"""Tests for resources.config_loader.

Covers:
- INI rejection
- old flat-shape rejection
- unknown-key warnings (warn-and-continue)
- pydantic validation hard-fail
- profile shallow-merge semantics (mirrors legacy _apply_profile)
- longest-prefix routing with bare-base fallback
- service-reference parsing
"""

from __future__ import annotations

import logging

import pytest

from resources.config_loader import ConfigError, ConfigLoader, RoutingResolution


@pytest.fixture
def write_yaml(tmp_path):
  """Return a helper that writes YAML text to a tmp file and returns the path."""

  def _write(text: str, name: str = "sma-ng.yml") -> str:
    target = tmp_path / name
    target.write_text(text)
    return str(target)

  return _write


@pytest.fixture
def loader():
  return ConfigLoader(logger=logging.getLogger("test.config"))


# ---------------------------------------------------------------------------
# load() — error paths
# ---------------------------------------------------------------------------


class TestLoadErrors:
  def test_rejects_ini_extension(self, loader, tmp_path):
    p = tmp_path / "autoProcess.ini"
    p.write_text("[Converter]\nffmpeg = ffmpeg\n")
    with pytest.raises(ConfigError, match="autoProcess.ini is no longer supported"):
      loader.load(str(p))

  def test_rejects_ini_extension_uppercase(self, loader, tmp_path):
    p = tmp_path / "thing.INI"
    p.write_text("")
    with pytest.raises(ConfigError, match="autoProcess.ini"):
      loader.load(str(p))

  def test_rejects_old_flat_shape(self, loader, write_yaml):
    p = write_yaml("converter:\n  ffmpeg: ffmpeg\nvideo:\n  codec: [h264]\n")
    with pytest.raises(ConfigError, match="Old flat-shape config detected"):
      loader.load(p)

  def test_rejects_old_flat_shape_with_partial_keys(self, loader, write_yaml):
    p = write_yaml("audio:\n  codec: [aac]\n")
    with pytest.raises(ConfigError, match=r"Old flat-shape.*audio"):
      loader.load(p)

  def test_old_shape_reject_lists_offending_keys(self, loader, write_yaml):
    p = write_yaml("converter: {}\nvideo: {}\nsubtitle: {}\n")
    with pytest.raises(ConfigError) as exc:
      loader.load(p)
    msg = str(exc.value)
    assert "converter" in msg
    assert "video" in msg
    assert "subtitle" in msg

  def test_accepts_empty_yaml_as_all_defaults(self, loader, write_yaml):
    p = write_yaml("")
    cfg = loader.load(p)
    assert cfg.daemon.host == "0.0.0.0"
    assert cfg.base.video.codec == ["h265"]

  def test_accepts_empty_base_block(self, loader, write_yaml):
    p = write_yaml("base: {}\n")
    cfg = loader.load(p)
    assert cfg.base.video.codec == ["h265"]

  def test_rejects_non_mapping_top_level(self, loader, write_yaml):
    p = write_yaml("- a\n- b\n")
    with pytest.raises(ConfigError, match="not a YAML mapping"):
      loader.load(p)

  def test_rejects_invalid_type(self, loader, write_yaml):
    p = write_yaml("daemon:\n  port: not-a-number\n")
    with pytest.raises(ConfigError, match="Config validation failed"):
      loader.load(p)

  def test_rejects_unknown_routing_service_ref(self, loader, write_yaml):
    p = write_yaml("daemon:\n  routing:\n    - match: /tv\n      services: [sonarr.ghost]\nservices:\n  sonarr:\n    main: {url: 'http://x'}\n")
    with pytest.raises(ConfigError, match="sonarr.ghost"):
      loader.load(p)


# ---------------------------------------------------------------------------
# load() — unknown-key warnings
# ---------------------------------------------------------------------------


class TestUnknownKeyWarnings:
  def test_warns_top_level_extra(self, loader, write_yaml, caplog):
    p = write_yaml("nonsense_key: 1\n")
    with caplog.at_level(logging.WARNING, logger="test.config"):
      loader.load(p)
    assert any("nonsense_key" in r.getMessage() for r in caplog.records)

  def test_warns_nested_extra_with_dotted_path(self, loader, write_yaml, caplog):
    p = write_yaml("base:\n  video:\n    fbrames: 5\n")
    with caplog.at_level(logging.WARNING, logger="test.config"):
      loader.load(p)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("base.video" in m and "fbrames" in m for m in msgs)

  def test_warns_extra_inside_dict_of_models(self, loader, write_yaml, caplog):
    p = write_yaml("services:\n  sonarr:\n    main:\n      url: 'http://x'\n      fbrogus: true\n")
    with caplog.at_level(logging.WARNING, logger="test.config"):
      loader.load(p)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("services.sonarr.main" in m and "fbrogus" in m for m in msgs)


# ---------------------------------------------------------------------------
# apply_profile()
# ---------------------------------------------------------------------------


class TestApplyProfile:
  def test_none_profile_returns_base_unchanged(self, loader, write_yaml):
    p = write_yaml("base:\n  video:\n    codec: [hevc]\n")
    cfg = loader.load(p)
    base = loader.apply_profile(cfg, None)
    assert base.video.codec == ["hevc"]

  def test_unknown_profile_raises(self, loader, write_yaml):
    p = write_yaml("base: {}\n")
    cfg = loader.load(p)
    with pytest.raises(ConfigError, match="Unknown profile"):
      loader.apply_profile(cfg, "ghost")

  def test_profile_overrides_only_set_fields_in_section(self, loader, write_yaml):
    """rq profile overrides video.codec; other video fields stay at base values."""
    p = write_yaml("base:\n  video:\n    codec: [h265]\n    max-bitrate: 10000\n    preset: slow\nprofiles:\n  rq:\n    video:\n      codec: [h264]\n")
    cfg = loader.load(p)
    resolved = loader.apply_profile(cfg, "rq")
    assert resolved.video.codec == ["h264"]
    assert resolved.video.max_bitrate == 10000
    assert resolved.video.preset == "slow"

  def test_unmentioned_section_passes_through(self, loader, write_yaml):
    p = write_yaml("base:\n  video:\n    codec: [h265]\n  audio:\n    codec: [ac3]\nprofiles:\n  rq:\n    video:\n      codec: [h264]\n")
    cfg = loader.load(p)
    resolved = loader.apply_profile(cfg, "rq")
    assert resolved.video.codec == ["h264"]
    assert resolved.audio.codec == ["ac3"]  # untouched by overlay


# ---------------------------------------------------------------------------
# resolve_routing()
# ---------------------------------------------------------------------------


class TestResolveRouting:
  def _yaml_with_two_rules(self):
    return (
      "base: {}\n"
      "profiles:\n"
      "  rq: {}\n"
      "  hq: {}\n"
      "services:\n"
      "  sonarr:\n"
      "    main: {url: 'http://main'}\n"
      "    kids: {url: 'http://kids'}\n"
      "  plex:\n"
      "    main: {url: 'http://plex'}\n"
      "daemon:\n"
      "  routing:\n"
      "    - match: /media/tv/kids\n"
      "      profile: hq\n"
      "      services: [sonarr.kids, plex.main]\n"
      "    - match: /media/tv\n"
      "      profile: rq\n"
      "      services: [sonarr.main, plex.main]\n"
    )

  def test_longest_prefix_wins(self, loader, write_yaml):
    p = write_yaml(self._yaml_with_two_rules())
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/media/tv/kids/show.mkv")
    assert isinstance(res, RoutingResolution)
    assert res.profile == "hq"
    assert ("sonarr", "kids") in res.services
    assert ("plex", "main") in res.services

  def test_shorter_prefix_when_no_longer_match(self, loader, write_yaml):
    p = write_yaml(self._yaml_with_two_rules())
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/media/tv/4k/movie.mkv")
    assert res.profile == "rq"
    assert ("sonarr", "main") in res.services

  def test_no_match_returns_bare_base(self, loader, write_yaml):
    p = write_yaml(self._yaml_with_two_rules())
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/media/movies/foo.mkv")
    assert res.profile is None
    assert res.services == []

  def test_directory_boundary_avoids_overmatch(self, loader, write_yaml):
    """`/media/tv` rule must NOT match `/media/tvshow/...`."""
    p = write_yaml("base: {}\nprofiles:\n  rq: {}\nservices:\n  sonarr:\n    main: {url: 'http://x'}\ndaemon:\n  routing:\n    - match: /media/tv\n      profile: rq\n      services: [sonarr.main]\n")
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/media/tvshow/foo.mkv")
    assert res.profile is None  # falls through

  def test_trailing_glob_stripped(self, loader, write_yaml):
    p = write_yaml(
      "base: {}\nprofiles:\n  rq: {}\nservices:\n  sonarr:\n    main: {url: 'http://x'}\ndaemon:\n  routing:\n    - match: '/media/tv/**'\n      profile: rq\n      services: [sonarr.main]\n"
    )
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/media/tv/foo.mkv")
    assert res.profile == "rq"

  def test_omitted_services_means_no_notify(self, loader, write_yaml):
    p = write_yaml("base: {}\nprofiles:\n  rq: {}\nservices: {}\ndaemon:\n  routing:\n    - match: /media/tv\n      profile: rq\n")
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/media/tv/foo.mkv")
    assert res.profile == "rq"
    assert res.services == []

  def test_path_rewrite_applied_before_match(self, loader, write_yaml):
    p = write_yaml(
      "base: {}\n"
      "profiles:\n"
      "  rq: {}\n"
      "services:\n"
      "  sonarr:\n"
      "    main: {url: 'http://x'}\n"
      "daemon:\n"
      "  path-rewrites:\n"
      "    - {from: /downloads, to: /media}\n"
      "  routing:\n"
      "    - match: /media/tv\n"
      "      profile: rq\n"
      "      services: [sonarr.main]\n"
    )
    cfg = loader.load(p)
    res = loader.resolve_routing(cfg, "/downloads/tv/show.mkv")
    assert res.profile == "rq"
    assert ("sonarr", "main") in res.services


class TestLoadReturnsPlainTypes:
  """Regression: yamlconfig.load must return plain dict/list, not ruamel
  CommentedMap/CommentedSeq.

  The dashboard's "push config from this node" button hits
  ``/admin/nodes/<id>/push-config``, which calls ``db.set_cluster_config``
  → ``yaml.safe_dump``. PyYAML's safe_dump cannot represent ruamel's
  comment-aware containers, so leaking those types out of ``load`` made
  every push fail with a server-side RepresenterError that the browser
  reported as a generic "Failed to fetch".
  """

  def test_nested_values_are_plain(self, write_yaml):
    from resources.yamlconfig import load

    p = write_yaml("daemon:\n  api_key: k\n  routing:\n    - match: /tv\n      profile: rq\n")
    data = load(p)
    assert type(data) is dict
    assert type(data["daemon"]) is dict
    assert type(data["daemon"]["routing"]) is list
    assert type(data["daemon"]["routing"][0]) is dict

  def test_safe_dump_round_trip(self, write_yaml):
    import yaml as _yaml

    from resources.yamlconfig import load

    p = write_yaml("daemon:\n  api_key: k\n  routing:\n    - match: /tv\n      profile: rq\n")
    data = load(p)
    # Should not raise RepresenterError.
    assert _yaml.safe_dump(data)

  def test_dedup_path_returns_plain_types(self, write_yaml):
    """Same guarantee even when the dedup branch fires."""
    import yaml as _yaml

    from resources.yamlconfig import load

    p = write_yaml("daemon:\n  api-key:\n  port: 8585\nbase:\n  converter:\n    ffmpeg: ffmpeg\ndaemon:\n  api_key: realsecret\n")
    data = load(p)
    assert type(data) is dict
    assert type(data["daemon"]) is dict
    assert data["daemon"]["api_key"] == "realsecret"
    assert _yaml.safe_dump(data)
