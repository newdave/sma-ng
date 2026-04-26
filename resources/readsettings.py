"""Configuration adapter — exposes sma-ng.yml settings as instance attributes.

This module is a thin adapter over :mod:`resources.config_loader`. The
canonical schema lives in :mod:`resources.config_schema`; this file only
flattens the validated config tree onto the instance attributes that the
rest of the codebase has historically grepped against
(``settings.ffmpeg``, ``settings.vcodec``, ``settings.audio_sorting``,
``settings.sonarr_instances``, ``settings.Plex``, etc.). Preserving those
names is the single most important contract here — every consumer in
``manual.py``, ``resources/mediaprocessor.py``, ``autoprocess/plex.py``,
``resources/rename_util.py``, ``daemon.py``, and the test suite reads
them directly.

INI parsing, the legacy DEFAULTS dict, ``migrateFromOld``, the SAB/Deluge/
qBittorrent/uTorrent attribute population, and ``writeConfig`` are gone.
The schema is the single source of truth for defaults; the YAML file is
the single source of truth for values; users edit it by hand.

For multi-instance Sonarr/Radarr, the per-instance ``path`` field is
derived from ``daemon.routing`` rules — every routing rule that
references an instance contributes one ``sonarr_instances``/
``radarr_instances`` entry with that rule's ``match`` as the path.
``manual.py``'s longest-prefix path-match logic
(``manual.py:_find_arr_instance``) keeps working unchanged.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any
from urllib.parse import urlparse

from resources.config_loader import ConfigError, ConfigLoader
from resources.config_schema import PlexInstance, SmaConfig, SonarrInstance
from resources.extensions import *  # noqa: F401,F403  - legacy wildcard re-exports
from resources.yamlconfig import cfg_getdirectories, cfg_getdirectory, cfg_getextension, cfg_getextensions, cfg_getpath


class ReadSettings:
  """Loads ``sma-ng.yml`` and exposes every setting as an instance attribute.

  Attribute names are kept stable for backward-compatibility with every
  consumer in the repo — see module docstring.
  """

  CONFIG_DEFAULT = "sma-ng.yml"
  CONFIG_DIRECTORY = "./config"
  RELATIVE_TO_ROOT = "../"
  ENV_CONFIG_VAR = "SMA_CONFIG"

  # Hardware acceleration profiles: maps a single hwaccel value to all derived settings.
  HWACCEL_PROFILES = {
    "qsv": {
      "hwaccels": ["qsv"],
      "hwaccel-decoders": ["hevc_qsv", "h264_qsv", "vp9_qsv", "av1_qsv", "vc1_qsv"],
      "hwdevices": {"qsv": "/dev/dri/renderD128"},
      "hwaccel-output-format": {"qsv": "qsv"},
    },
    "vaapi": {
      "hwaccels": ["vaapi"],
      "hwaccel-decoders": ["hevc_vaapi", "h264_vaapi"],
      "hwdevices": {"vaapi": "/dev/dri/renderD128"},
      "hwaccel-output-format": {"vaapi": "vaapi"},
    },
    "nvenc": {
      "hwaccels": ["cuda"],
      "hwaccel-decoders": ["hevc_cuvid", "h264_cuvid", "vp9_cuvid", "av1_cuvid", "vc1_cuvid"],
      "hwdevices": {},
      "hwaccel-output-format": {"cuda": "cuda"},
    },
    "videotoolbox": {
      "hwaccels": ["videotoolbox"],
      "hwaccel-decoders": [],
      "hwdevices": {},
      "hwaccel-output-format": {},
    },
  }

  CODEC_ALIASES = {
    "hevc": "h265",
    "x265": "h265",
    "x264": "h264",
  }

  HWACCEL_CODEC_MAP = {
    "qsv": {"h265": "h265qsv", "h264": "h264qsv", "av1": "av1qsv", "vp9": "vp9qsv"},
    "vaapi": {"h265": "h265vaapi", "h264": "h264vaapi", "av1": "av1vaapi"},
    "nvenc": {"h265": "h265_nvenc", "h264": "h264_nvenc", "av1": "av1_nvenc"},
    "videotoolbox": {"h265": "h265_videotoolbox", "h264": "h264_videotoolbox"},
  }

  @property
  def CONFIG_RELATIVEPATH(self):
    return os.path.join(self.CONFIG_DIRECTORY, self.CONFIG_DEFAULT)

  # ---------------------------------------------------------------------
  # Construction
  # ---------------------------------------------------------------------

  def __init__(self, configFile=None, logger=None, profile=None):
    """Load and parse the SMA-NG configuration file.

    Resolution order for the config path: explicit ``configFile`` arg,
    ``$SMA_CONFIG`` env var, then ``config/sma-ng.yml`` relative to the
    SMA root.

    Args:
        configFile: Path to ``sma-ng.yml`` or a directory containing it.
        logger: Optional logger; defaults to the module logger.
        profile: Optional named profile from the ``profiles`` block.
    """

    self.log = logger or logging.getLogger(__name__)
    self.log.debug(sys.executable)

    config_path = self._resolve_path(configFile)
    self.log.debug("Loading config file %s." % config_path)

    if not os.path.isfile(config_path):
      self.log.error("Config file not found: %s" % config_path)
      sys.exit(1)

    loader = ConfigLoader(logger=self.log)
    try:
      cfg = loader.load(config_path)
    except ConfigError as exc:
      self.log.error(str(exc))
      sys.exit(1)

    self._profile = profile
    if profile:
      try:
        base = loader.apply_profile(cfg, profile)
      except ConfigError as exc:
        self.log.error(str(exc))
        sys.exit(1)
    else:
      base = cfg.base

    self._config = cfg
    self._configFile = config_path

    # GPU is in [video] but affects both converter (hwaccel profile) and
    # video (codec mapping). Read first so downstream sections see it.
    self.gpu = (base.video.gpu or "").strip().lower()

    self._read_converter(base)
    self._read_permissions(base)
    self._read_metadata(base)
    self._read_video(base)
    self._read_analyzer(base)
    self._read_audio(base)
    self._read_subtitles(base)
    self._read_services(cfg)

    self._validate_binaries()

  def _resolve_path(self, configFile):
    """Resolve the config file path: arg > $SMA_CONFIG > default."""
    return self.resolve_config_path(configFile, logger=self.log)

  @classmethod
  def resolve_config_path(cls, configFile=None, logger=None):
    """Resolve the same config path the constructor would, without loading.

    Public so external callers (notably ``manual.py``'s routing-based
    profile auto-selection) can locate the YAML for an independent
    ``ConfigLoader`` lookup before constructing a full ``ReadSettings``.
    """
    log = logger or logging.getLogger(__name__)
    rootpath = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), cls.RELATIVE_TO_ROOT))
    default_path = os.path.normpath(os.path.join(rootpath, cls.CONFIG_DIRECTORY, cls.CONFIG_DEFAULT))

    env_path = os.environ.get(cls.ENV_CONFIG_VAR)
    if env_path and os.path.exists(os.path.realpath(env_path)):
      configFile = os.path.realpath(env_path)
      log.debug("%s environment variable override found." % cls.ENV_CONFIG_VAR)
    elif not configFile:
      configFile = default_path
      log.debug("Loading default config file.")

    if os.path.isdir(configFile):
      configFile = os.path.realpath(os.path.join(configFile, cls.CONFIG_DIRECTORY, cls.CONFIG_DEFAULT))
      log.debug("Configuration file specified is a directory, joining with %s." % cls.CONFIG_DEFAULT)

    return configFile

  # ---------------------------------------------------------------------
  # Hardware acceleration helpers (preserved verbatim)
  # ---------------------------------------------------------------------

  def _apply_hwaccel_profile(self, gpu):
    """Apply hardware acceleration profile, setting derived values."""
    profile = self.HWACCEL_PROFILES.get(gpu)
    if not profile:
      return

    self.log.debug("Applying hwaccel profile: %s" % gpu)

    # Only override if not explicitly set by the user.
    if not self.hwaccels:
      self.hwaccels = list(profile["hwaccels"])
    if not self.hwaccel_decoders:
      self.hwaccel_decoders = list(profile["hwaccel-decoders"])
    if not self.hwdevices:
      self.hwdevices = dict(profile["hwdevices"])
    if not self.hwoutputfmt:
      self.hwoutputfmt = dict(profile["hwaccel-output-format"])

  @staticmethod
  def _map_codecs_with_fallback(codecs, codec_map):
    """Resolve codec names through GPU encoder map, keeping software fallbacks."""
    mapped = []
    seen = set()
    for codec in codecs:
      canonical = ReadSettings.CODEC_ALIASES.get(codec, codec)
      resolved = codec_map.get(canonical, codec)
      if resolved not in seen:
        mapped.append(resolved)
        seen.add(resolved)
      if resolved != codec and codec not in seen:
        mapped.append(codec)
        seen.add(codec)
    return mapped

  def _apply_hwaccel_codec_map(self, gpu):
    """Map generic video codec names to GPU-specific encoder names."""
    codec_map = self.HWACCEL_CODEC_MAP.get(gpu, {})
    if not codec_map:
      return

    mapped = self._map_codecs_with_fallback(self.vcodec, codec_map)
    if mapped != self.vcodec:
      self.log.debug("Video codecs mapped for %s: %s -> %s" % (gpu, self.vcodec, mapped))
      self.vcodec = mapped

    if self.hdr.get("codec"):
      hdr_mapped = self._map_codecs_with_fallback(self.hdr["codec"], codec_map)
      if hdr_mapped != self.hdr["codec"]:
        self.hdr["codec"] = hdr_mapped

  # ---------------------------------------------------------------------
  # Bitrate-profile helpers (preserved verbatim)
  # ---------------------------------------------------------------------

  @staticmethod
  def _parse_bitrate_profiles(raw):
    """Parse a ``crf-profiles`` string into a sorted list of profile dicts.

    Format: ``source_kbps:quality:target_bitrate:max_bitrate`` entries
    separated by commas. Bitrate values may use ``M`` (megabits) or ``k``
    (kilobits) suffixes; bare numbers are treated as kilobits. Returns a
    list sorted by ``source_kbps`` ascending.
    """
    profiles = []
    if not raw:
      return profiles
    for entry in str(raw).split(","):
      entry = entry.strip()
      if not entry:
        continue
      parts = entry.split(":")
      if len(parts) != 4:
        continue
      try:
        source_kbps = int(parts[0])
        target = ReadSettings._parse_bitrate_value(parts[2])
        maxrate = ReadSettings._parse_bitrate_value(parts[3])
      except (ValueError, TypeError):
        continue
      profiles.append({"source_kbps": source_kbps, "target": target, "maxrate": maxrate})
    profiles.sort(key=lambda p: p["source_kbps"])
    return profiles

  @staticmethod
  def _parse_bitrate_value(s):
    """Convert ``5M``, ``3000k``, or ``3000`` to kbps int."""
    s = s.strip().upper()
    if s.endswith("M"):
      return int(float(s[:-1]) * 1000)
    if s.endswith("K"):
      return int(float(s[:-1]))
    return int(s)

  # ---------------------------------------------------------------------
  # Generic value helpers (preserved verbatim — still used to coerce
  # legacy comma-separated string values some users may keep using)
  # ---------------------------------------------------------------------

  @staticmethod
  def _as_list(value, separator=",", lower=True, replace=None):
    if value is None or value == "":
      return []
    if isinstance(value, list):
      items = value
    else:
      items = str(value).split(separator)
    if replace is None:
      replace = [" "]
    output = []
    for item in items:
      item = str(item)
      for char in replace:
        item = item.replace(char, "")
      item = item.strip()
      if lower:
        item = item.lower()
      if item:
        output.append(item)
    return output

  @staticmethod
  def _as_dict(value, item_separator=",", key_separator=":", value_modifier=None):
    if value is None or value == "":
      return {}
    if isinstance(value, dict):
      return dict(value)
    output = {}
    for item in str(value).split(item_separator):
      if key_separator not in item:
        continue
      key, val = (x.strip() for x in item.split(key_separator, 1))
      if value_modifier:
        try:
          val = value_modifier(val)
        except (ValueError, TypeError):
          continue
      output[key] = val
    return output

  @staticmethod
  def _as_bool(value):
    if isinstance(value, bool):
      return value
    if value is None:
      return False
    return str(value).strip().lower() in ["true", "yes", "t", "1", "y", "on"]

  # ---------------------------------------------------------------------
  # Section readers — flatten resolved BaseConfig into legacy attributes
  # ---------------------------------------------------------------------

  def _read_converter(self, base):
    cfg = base.converter
    self.ffmpeg = cfg_getpath(cfg.ffmpeg)
    self.ffprobe = cfg_getpath(cfg.ffprobe)
    self.threads = cfg.threads
    self.hwaccels = list(cfg.hwaccels)
    self.hwaccel_decoders = list(cfg.hwaccel_decoders)
    self.hwdevices = dict(cfg.hwdevices)
    self.hwoutputfmt = dict(cfg.hwaccel_output_format)
    self.output_dir = cfg_getdirectory(cfg.output_directory)
    self.output_dir_ratio = cfg.output_directory_space_ratio
    self.output_format = cfg.output_format
    self.output_extension = cfg_getextension(cfg.output_extension)
    self.temp_extension = cfg_getextension(cfg.temp_extension)
    self.minimum_size = cfg.minimum_size
    self.ignored_extensions = cfg_getextensions(self._as_list(cfg.ignored_extensions))
    self.copyto = cfg_getdirectories(self._as_list(cfg.copy_to, separator="|", lower=False))
    self.moveto = cfg_getdirectory(cfg.move_to)
    self.delete = cfg.delete_original
    self.recycle_bin = (cfg.recycle_bin or "").strip() or None
    self.process_same_extensions = cfg.process_same_extensions
    self.bypass_copy_all = cfg.bypass_if_copying_all
    self.force_convert = cfg.force_convert
    self.postprocess = cfg.post_process
    self.waitpostprocess = cfg.wait_post_process
    self.detailedprogress = cfg.detailed_progress
    self.preopts = self._as_list(cfg.preopts, lower=False, replace=[])
    self.postopts = self._as_list(cfg.postopts, lower=False, replace=[])
    self.regex = cfg.regex_directory_replace

    if self.gpu:
      self._apply_hwaccel_profile(self.gpu)

    if self.force_convert:
      self.process_same_extensions = True
      self.log.warning("Force-convert is true, so process-same-extensions is being overridden to true as well")

  def _read_permissions(self, base):
    cfg = base.permissions
    self.permissions: dict[str, Any] = {}
    chmod_raw = cfg.chmod
    try:
      self.permissions["chmod"] = int(str(chmod_raw), 8)
    except (ValueError, TypeError):
      self.log.exception("Invalid permissions, defaulting to 664.")
      self.permissions["chmod"] = int("0664", 8)
    self.permissions["uid"] = cfg.uid
    self.permissions["gid"] = cfg.gid

  def _read_metadata(self, base):
    cfg = base.metadata
    self.relocate_moov = cfg.relocate_moov
    self.fullpathguess = cfg.full_path_guess
    self.tagfile = cfg.tag
    self.taglanguage = (cfg.tag_language or "").lower()
    artwork = str(cfg.download_artwork).lower()
    if artwork == "poster":
      self.artwork = True
      self.thumbnail = False
    elif "thumb" in artwork:
      self.artwork = True
      self.thumbnail = True
    else:
      self.thumbnail = False
      try:
        self.artwork = bool(cfg.download_artwork)
      except (ValueError, TypeError):
        self.artwork = True
        self.log.error("Invalid download-artwork value, defaulting to 'poster'.")
    self.sanitize_disposition = self._as_list(cfg.sanitize_disposition)
    self.strip_metadata = cfg.strip_metadata
    self.keep_titles = cfg.keep_titles

  def _read_video(self, base):
    cfg = base.video
    self.vcodec = self._as_list(cfg.codec)
    self.vmaxbitrate = cfg.max_bitrate
    self.vbitrateratio = self._as_dict(cfg.bitrate_ratio, value_modifier=float)
    self.vbitrate_profiles = self._parse_bitrate_profiles(cfg.crf_profiles)
    self.vbitrate_profiles_hd = self._parse_bitrate_profiles(cfg.crf_profiles_hd)
    self.preset = cfg.preset
    self.codec_params = cfg.codec_parameters
    self.dynamic_params = cfg.dynamic_parameters
    self.vfilter = cfg.filter
    self.vforcefilter = cfg.force_filter
    self.vwidth = cfg.max_width
    self.video_level = cfg.max_level
    self.vprofile = self._as_list(cfg.profile)
    self.pix_fmt = self._as_list(cfg.pix_fmt)
    self.keep_source_pix_fmt = cfg.prioritize_source_pix_fmt
    self.look_ahead_depth = cfg.look_ahead_depth
    self.b_frames = cfg.b_frames
    self.ref_frames = cfg.ref_frames

    hdr_cfg = base.hdr
    self.hdr: dict[str, Any] = {
      "codec": self._as_list(hdr_cfg.codec),
      "pix_fmt": self._as_list(hdr_cfg.pix_fmt),
      "space": self._as_list(hdr_cfg.space),
      "transfer": self._as_list(hdr_cfg.transfer),
      "primaries": self._as_list(hdr_cfg.primaries),
      "preset": hdr_cfg.preset,
      "codec_params": hdr_cfg.codec_parameters,
      "filter": hdr_cfg.filter,
      "forcefilter": hdr_cfg.force_filter,
      "profile": self._as_list(hdr_cfg.profile),
      "look_ahead_depth": hdr_cfg.look_ahead_depth,
      "b_frames": hdr_cfg.b_frames,
      "ref_frames": hdr_cfg.ref_frames,
    }

    naming = base.naming
    self.naming_enabled = naming.enabled
    self.naming_tv_template = naming.tv_template
    self.naming_tv_airdate_template = naming.tv_airdate_template
    self.naming_movie_template = naming.movie_template

    if self.gpu:
      self._apply_hwaccel_codec_map(self.gpu)

    # codec-parameters may contain QSV-specific flags; clear them when not on QSV.
    if self.codec_params and self.gpu != "qsv":
      self.log.debug("Clearing codec-parameters (not QSV, gpu=%s)." % self.gpu)
      self.codec_params = ""
    if self.hdr.get("codec_params") and self.gpu != "qsv":
      self.hdr["codec_params"] = ""

  def _read_analyzer(self, base):
    cfg = base.analyzer
    self.analyzer: dict[str, Any] = {
      "enabled": cfg.enabled,
      "backend": (cfg.backend or "").strip().lower(),
      "device": (cfg.device or "").strip() or "AUTO",
      "model_dir": cfg_getpath(cfg.model_dir),
      "cache_dir": cfg_getpath(cfg.cache_dir),
      "max_frames": cfg.max_frames,
      "target_width": cfg.target_width,
      "allow_codec_reorder": cfg.allow_codec_reorder,
      "allow_bitrate_adjustments": cfg.allow_bitrate_adjustments,
      "allow_preset_adjustments": cfg.allow_preset_adjustments,
      "allow_filter_adjustments": cfg.allow_filter_adjustments,
      "allow_force_reencode": cfg.allow_force_reencode,
    }

  def _read_audio(self, base):
    cfg = base.audio
    self.acodec = self._as_list(cfg.codec)
    self.awl = self._as_list(cfg.languages)
    self.adl = (cfg.default_language or "").lower()
    self.audio_original_language = cfg.include_original_language
    self.abitrate = cfg.channel_bitrate
    self.avbr = cfg.variable_bitrate
    self.amaxbitrate = cfg.max_bitrate
    self.maxchannels = cfg.max_channels
    self.aprofile = (cfg.profile or "").lower()
    self.afilter = cfg.filter
    self.aforcefilter = cfg.force_filter
    self.audio_samplerates = [int(x) for x in self._as_list(cfg.sample_rates) if str(x).isdigit()]
    self.audio_sampleformat = cfg.sample_format
    self.audio_atmos_force_copy = cfg.atmos_force_copy
    self.audio_copyoriginal = cfg.copy_original
    self.audio_first_language_stream = cfg.first_stream_of_language
    self.aac_adtstoasc = cfg.aac_adtstoasc
    self.ignored_audio_dispositions = self._as_list(cfg.ignored_dispositions)
    self.force_audio_defaults = cfg.force_default
    self.unique_audio_dispositions = cfg.unique_dispositions
    self.stream_codec_combinations = sorted(
      [x.split(":") for x in self._as_list(cfg.stream_codec_combinations)],
      key=len,
      reverse=True,
    )

    sorting = cfg.sorting
    self.audio_sorting = self._as_list(sorting.sorting)
    self.audio_sorting_default = self._as_list(sorting.default_sorting)
    self.audio_sorting_codecs = self._as_list(sorting.codecs)

    self.afilterchannels: dict[int, dict[int, str]] = {}
    for key, value in (cfg.channel_filters or {}).items():
      if not value:
        continue
      try:
        channels = [int(x) for x in str(key).split("-", 1)]
        if len(channels) == 2:
          self.afilterchannels[channels[0]] = {channels[1]: value}
      except (ValueError, IndexError):
        self.log.exception("Unable to parse audio.channel-filters key %s, skipping." % key)

    universal = cfg.universal
    self.ua_enabled = universal.enabled
    self.ua = self._as_list(universal.codec)
    self.ua_bitrate = universal.channel_bitrate
    self.ua_vbr = universal.variable_bitrate
    self.ua_first_only = universal.first_stream_only
    self.ua_profile = (universal.profile or "").lower()
    self.ua_filter = universal.filter
    self.ua_forcefilter = universal.force_filter

  def _read_subtitles(self, base):
    cfg = base.subtitle
    self.scodec = self._as_list(cfg.codec)
    self.scodec_image = self._as_list(cfg.codec_image_based)
    self.swl = self._as_list(cfg.languages)
    self.sdl = (cfg.default_language or "").lower()
    self.subtitle_original_language = cfg.include_original_language
    self.sub_first_language_stream = cfg.first_stream_of_language
    self.subencoding = cfg.encoding
    self.burn_subtitles = cfg.burn_subtitles
    self.burn_dispositions = self._as_list(cfg.burn_dispositions)
    self.embedsubs = cfg.embed_subs
    self.embedimgsubs = cfg.embed_image_subs
    self.embedonlyinternalsubs = cfg.embed_only_internal_subs
    self.filename_dispositions = self._as_list(cfg.filename_dispositions)
    self.ignore_embedded_subs = cfg.ignore_embedded_subs
    self.ignored_subtitle_dispositions = self._as_list(cfg.ignored_dispositions)
    self.force_subtitle_defaults = cfg.force_default
    self.unique_subtitle_dispositions = cfg.unique_dispositions
    self.attachmentcodec = self._as_list(cfg.attachment_codec)
    self.removebvs = cfg.remove_bitstream_subs

    sorting = cfg.sorting
    self.sub_sorting = self._as_list(sorting.sorting)
    self.sub_sorting_codecs = self._as_list(sorting.codecs)
    self.burn_sorting = self._as_list(sorting.burn_sorting)

    cleanit = cfg.cleanit
    self.cleanit = cleanit.enabled
    self.cleanit_config = cleanit.config_path
    self.cleanit_tags = self._as_list(cleanit.tags)

    self.ffsubsync = cfg.ffsubsync.enabled

    subliminal = cfg.subliminal
    self.downloadsubs = subliminal.download_subs
    self.downloadforcedsubs = subliminal.download_forced_subs
    self.hearing_impaired = subliminal.include_hearing_impaired_subs
    self.subproviders = self._as_list(subliminal.providers)

    self.subproviders_auth: dict[str, dict[str, str]] = {}
    auth = subliminal.auth
    for provider, raw in auth.model_dump(by_alias=True).items():
      # The auth field exposes its predefined provider keys (opensubtitles,
      # tvsubtitles) plus any extras the user added via extra="allow".
      if not raw:
        continue
      try:
        parts = [x.strip() for x in str(raw).split(":", 1)]
      except (ValueError, AttributeError):
        self.log.exception("Unable to parse subtitle.subliminal.auth %s, skipping." % provider)
        continue
      if len(parts) < 2:
        self.log.error("Unable to parse subtitle.subliminal.auth %s, skipping." % provider)
        continue
      self.subproviders_auth[provider] = {"username": parts[0], "password": parts[1]}

  # ---------------------------------------------------------------------
  # Service section reader (Sonarr / Radarr / Plex)
  # ---------------------------------------------------------------------

  def _read_services(self, cfg: SmaConfig):
    """Translate ``services.<type>.<name>`` maps + ``daemon.routing`` rules
    into the legacy ``sonarr_instances`` / ``radarr_instances`` / ``Plex``
    attribute shape.

    Per-instance ``path`` is derived from ``daemon.routing`` rules: every
    rule that references ``<type>.<instance>`` contributes one entry with
    that rule's ``match`` as the path. Instances not referenced anywhere
    still get one entry with empty ``path`` (so consumers iterating
    instances can see them, but ``manual.py``'s prefix match skips them).
    """

    self.sonarr_instances = self._build_arr_instances(cfg, "sonarr")
    self.radarr_instances = self._build_arr_instances(cfg, "radarr")
    self.sonarr_instances.sort(key=lambda x: len(x.get("path") or ""), reverse=True)
    self.radarr_instances.sort(key=lambda x: len(x.get("path") or ""), reverse=True)

    # ``Sonarr`` / ``Radarr`` are kept as attributes for backward
    # compatibility; only readsettings itself populated them historically
    # and no external consumer reads them, so an empty dict is fine.
    self.Sonarr: dict[str, Any] = {}
    self.Radarr: dict[str, Any] = {}

    # Plex: pick the first instance (preferring one named "main"), or {}.
    plex_instances = cfg.services.plex
    plex_pick: PlexInstance | None = None
    if plex_instances:
      plex_pick = plex_instances.get("main") or next(iter(plex_instances.values()))
    self.Plex = self._plex_to_dict(plex_pick)
    self.plexmatch_enabled = bool(self.Plex.get("host") and self.Plex.get("plexmatch", True))

  def _build_arr_instances(self, cfg: SmaConfig, kind: str) -> list[dict[str, Any]]:
    """Build legacy-shaped instance dicts for one service type."""
    instances_map: dict[str, SonarrInstance] = getattr(cfg.services, kind)
    rules = cfg.daemon.routing

    # Map instance-name → list of paths it owns (from routing rules).
    paths_by_instance: dict[str, list[str]] = {name: [] for name in instances_map}
    for rule in rules:
      for ref in rule.services:
        stype, sname = ref.split(".", 1)
        if stype == kind and sname in paths_by_instance:
          paths_by_instance[sname].append(rule.match)

    output: list[dict[str, Any]] = []
    for name, instance in instances_map.items():
      paths = paths_by_instance.get(name) or [""]
      for path in paths:
        output.append(self._arr_to_dict(name, instance, path))
    return output

  @staticmethod
  def _arr_to_dict(name: str, instance: SonarrInstance, path: str) -> dict[str, Any]:
    """Translate a SonarrInstance + path into the legacy instance dict shape."""
    parsed = urlparse(instance.url)
    ssl = parsed.scheme == "https"
    host = parsed.hostname or ""
    port = parsed.port if parsed.port else (443 if ssl else 80)
    webroot = parsed.path or ""
    if webroot and not webroot.startswith("/"):
      webroot = "/" + webroot
    if webroot.endswith("/"):
      webroot = webroot[:-1]
    section = name if name == "main" else f"{instance.__class__.__name__.replace('Instance', '').lower()}-{name}"
    # ^ keeps log messages descriptive, e.g. "sonarr-kids", "radarr-main".
    return {
      "section": section,
      "host": host,
      "port": port,
      "apikey": instance.apikey,
      "ssl": ssl,
      "webroot": webroot,
      "path": path,
      "rename": instance.force_rename,
      "rescan": instance.rescan,
      "in-progress-check": instance.in_progress_check,
      "blockreprocess": instance.block_reprocess,
    }

  @staticmethod
  def _plex_to_dict(instance: PlexInstance | None) -> dict[str, Any]:
    """Translate a PlexInstance into the legacy ``settings.Plex`` dict shape."""
    if instance is None:
      return {
        "host": None,
        "port": None,
        "refresh": False,
        "token": "",
        "ssl": False,
        "ignore-certs": False,
        "path-mapping": {},
        "plexmatch": True,
      }
    parsed = urlparse(instance.url)
    return {
      "host": parsed.hostname,
      "port": parsed.port if parsed.port else (443 if parsed.scheme == "https" else 32400),
      "refresh": instance.refresh,
      "token": instance.token,
      "ssl": parsed.scheme == "https",
      "ignore-certs": instance.ignore_certs,
      "path-mapping": ReadSettings._as_dict(instance.path_mapping, key_separator="="),
      "plexmatch": instance.plexmatch,
    }

  # ---------------------------------------------------------------------
  # Binary validation (preserved verbatim — many tests patch this)
  # ---------------------------------------------------------------------

  def _validate_binaries(self):
    """Validate that ffmpeg and ffprobe binaries exist and are executable."""
    for name, path in [("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe)]:
      if not path:
        self.log.error("%s path is not configured. Set it in sma-ng.yml [converter] section." % name)
        sys.exit(1)
      resolved = shutil.which(path)
      if resolved:
        self.log.debug("%s found at %s" % (name, resolved))
      elif os.path.isfile(path) and os.access(path, os.X_OK):
        self.log.debug("%s found at %s" % (name, path))
      else:
        self.log.error("%s not found: '%s'. Verify the path in sma-ng.yml [converter] section or ensure it is installed and in PATH." % (name, path))
        sys.exit(1)
