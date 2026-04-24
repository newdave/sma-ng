"""Configuration file parser for SMA-NG.

Reads sma-ng.yml, auto-migrating a sibling autoProcess.ini on first use,
and exposes all settings as attributes on the ReadSettings instance.
"""

import logging
import os
import shutil
import sys

from resources.extensions import *
from resources.yamlconfig import cfg_getdirectories, cfg_getdirectory, cfg_getextension, cfg_getextensions, cfg_getpath


class ReadSettings:
  """Parses ``sma-ng.yml`` and exposes all settings as typed attributes.

  On construction, reads the YAML file (creating it from ``DEFAULTS`` if
  absent), validates codec and hardware-acceleration options, and populates
  attributes such as ``Video``, ``Audio``, ``Subtitle``, ``Plex``, etc.
  """

  DEFAULTS = {
    "Converter": {
      "ffmpeg": "ffmpeg" if os.name != "nt" else "ffmpeg.exe",
      "ffprobe": "ffprobe" if os.name != "nt" else "ffprobe.exe",
      "threads": 0,
      "hwaccels": [],
      "hwaccel-decoders": [],
      "hwdevices": {},
      "hwaccel-output-format": {},
      "output-directory": "",
      "output-directory-space-ratio": 0.0,
      "output-format": "mp4",
      "output-extension": "mp4",
      "temp-extension": "",
      "minimum-size": 0,
      "ignored-extensions": ["nfo", "ds_store"],
      "copy-to": [],
      "move-to": "",
      "delete-original": True,
      "recycle-bin": "",
      "process-same-extensions": False,
      "bypass-if-copying-all": False,
      "force-convert": False,
      "post-process": False,
      "wait-post-process": False,
      "detailed-progress": False,
      "preopts": [],
      "postopts": [],
      "regex-directory-replace": r"[^\w\-_\. ]",
    },
    "Permissions": {
      "chmod": "0664",
      "uid": -1,
      "gid": -1,
    },
    "Metadata": {
      "relocate-moov": True,
      "full-path-guess": True,
      "tag": True,
      "tag-language": "eng",
      "download-artwork": "poster",
      "sanitize-disposition": "",
      "strip-metadata": False,
      "keep-titles": False,
    },
    "Video": {
      "gpu": "",
      "codec": ["h265"],
      "max-bitrate": 0,
      "bitrate-ratio": {},
      "crf-profiles": "",
      "crf-profiles-hd": "",
      "preset": "",
      "codec-parameters": "",
      "dynamic-parameters": False,
      "max-width": 0,
      "profile": [],
      "max-level": 0.0,
      "pix-fmt": [],
      "prioritize-source-pix-fmt": True,
      "filter": "",
      "force-filter": False,
      "look-ahead-depth": 0,
      "b-frames": -1,
      "ref-frames": -1,
    },
    "HDR": {
      "codec": [],
      "pix-fmt": [],
      "space": ["bt2020nc"],
      "transfer": ["smpte2084"],
      "primaries": ["bt2020"],
      "preset": "",
      "codec-parameters": "",
      "filter": "",
      "force-filter": False,
      "profile": [],
      "look-ahead-depth": 0,
      "b-frames": -1,
      "ref-frames": -1,
    },
    "Analyzer": {
      "enabled": False,
      "backend": "openvino",
      "device": "AUTO",
      "model-dir": "",
      "cache-dir": "",
      "max-frames": 12,
      "target-width": 960,
      "allow-codec-reorder": True,
      "allow-bitrate-adjustments": True,
      "allow-preset-adjustments": True,
      "allow-filter-adjustments": True,
      "allow-force-reencode": True,
    },
    "Naming": {
      "enabled": False,
      "tv-template": "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}",
      "tv-airdate-template": "{Series TitleYear} - {Air-Date} - {Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}",
      "movie-template": "{Movie CleanTitle} ({Release Year}) [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}",
    },
    "Audio": {
      "codec": ["ac3"],
      "languages": [],
      "default-language": "",
      "include-original-language": True,
      "first-stream-of-language": False,
      "channel-bitrate": 128,
      "variable-bitrate": 0,
      "max-bitrate": 0,
      "max-channels": 0,
      "filter": "",
      "profile": "",
      "force-filter": False,
      "sample-rates": [],
      "sample-format": "",
      "atmos-force-copy": False,
      "copy-original": False,
      "aac-adtstoasc": False,
      "ignored-dispositions": [],
      "force-default": False,
      "unique-dispositions": False,
      "stream-codec-combinations": [],
    },
    "Audio.Sorting": {
      "sorting": ["language", "channels.d", "map", "d.comment"],
      "default-sorting": ["channels.d", "map", "d.comment"],
      "codecs": [],
    },
    "Universal Audio": {
      "enabled": False,
      "codec": ["aac"],
      "channel-bitrate": 128,
      "variable-bitrate": 0,
      "first-stream-only": False,
      "filter": "",
      "profile": "",
      "force-filter": False,
    },
    "Audio.ChannelFilters": {
      "6-2": "pan=stereo|FL=0.5*FC+0.707*FL+0.707*BL+0.5*LFE|FR=0.5*FC+0.707*FR+0.707*BR+0.5*LFE",
    },
    "Subtitle": {
      "codec": ["mov_text"],
      "codec-image-based": [],
      "languages": [],
      "default-language": "",
      "force-default": False,
      "include-original-language": False,
      "first-stream-of-language": False,
      "encoding": "",
      "burn-subtitles": False,
      "burn-dispositions": [],
      "embed-subs": True,
      "embed-image-subs": False,
      "embed-only-internal-subs": False,
      "filename-dispositions": "forced",
      "ignore-embedded-subs": False,
      "ignored-dispositions": [],
      "unique-dispositions": False,
      "attachment-codec": [],
      "remove-bitstream-subs": False,
    },
    "Subtitle.Sorting": {
      "sorting": ["language", "d.comment", "d.default.d", "d.forced.d"],
      "codecs": [],
      "burn-sorting": ["language", "d.comment", "d.default.d", "d.forced.d"],
    },
    "Subtitle.CleanIt": {
      "enabled": False,
      "config-path": "",
      "tags": [],
    },
    "Subtitle.FFSubsync": {
      "enabled": False,
    },
    "Subtitle.Subliminal": {
      "download-subs": False,
      "download-forced-subs": False,
      "include-hearing-impaired-subs": False,
      "providers": [],
    },
    "Subtitle.Subliminal.Auth": {
      "opensubtitles": "",
      "tvsubtitles": "",
    },
    "Sonarr": {
      "host": "localhost",
      "port": 8989,
      "apikey": "",
      "ssl": False,
      "webroot": "",
      "path": "",
      "force-rename": False,
      "rescan": True,
      "in-progress-check": True,
      "block-reprocess": False,
    },
    "Radarr": {
      "host": "localhost",
      "port": 7878,
      "apikey": "",
      "ssl": False,
      "webroot": "",
      "path": "",
      "force-rename": False,
      "rescan": True,
      "in-progress-check": True,
      "block-reprocess": False,
    },
    "SABNZBD": {
      "convert": True,
      "sonarr-category": "sonarr",
      "radarr-category": "radarr",
      "bypass-category": "bypass",
      "output-directory": "",
      "path-mapping": "",
    },
    "Deluge": {
      "sonarr-label": "sonarr",
      "radarr-label": "radarr",
      "bypass-label": "bypass",
      "convert": True,
      "host": "localhost",
      "port": 58846,
      "username": "",
      "password": "",
      "output-directory": "",
      "remove": False,
      "path-mapping": "",
    },
    "qBittorrent": {
      "sonarr-label": "sonarr",
      "radarr-label": "radarr",
      "bypass-label": "bypass",
      "convert": True,
      "action-before": "",
      "action-after": "",
      "host": "localhost",
      "port": 8080,
      "ssl": False,
      "username": "",
      "password": "",
      "output-directory": "",
      "path-mapping": "",
    },
    "uTorrent": {
      "sonarr-label": "sonarr",
      "radarr-label": "radarr",
      "bypass-label": "bypass",
      "convert": True,
      "webui": False,
      "action-before": "",
      "action-after": "",
      "host": "localhost",
      "ssl": False,
      "port": 8080,
      "username": "",
      "password": "",
      "output-directory": "",
      "path-mapping": "",
    },
    "Plex": {
      "host": "localhost",
      "port": 32400,
      "refresh": False,
      "token": "",
      "ssl": True,
      "ignore-certs": False,
      "path-mapping": "",
      "plexmatch": True,
    },
  }

  CONFIG_DEFAULT = "sma-ng.yml"
  CONFIG_DIRECTORY = "./config"
  RESOURCE_DIRECTORY = "./resources"
  RELATIVE_TO_ROOT = "../"
  ENV_CONFIG_VAR = "SMA_CONFIG"
  DYNAMIC_SECTIONS = ["Audio.ChannelFilters", "Subtitle.Subliminal.Auth"]

  @property
  def CONFIG_RELATIVEPATH(self):
    return os.path.join(self.CONFIG_DIRECTORY, self.CONFIG_DEFAULT)

  def __init__(self, configFile=None, logger=None, profile=None):
    """Load and parse the SMA-NG configuration file.

    Resolves the config path in priority order: explicit ``configFile``
    argument, ``$SMA_CONFIG`` environment variable, then the default
    ``config/sma-ng.yml`` relative to the SMA root. If a sibling INI
    exists and YAML does not, the INI is migrated and kept as ``.bak``.
    Missing keys in an existing file are backfilled and written. After
    parsing, binary paths are validated via ``_validate_binaries()``.

    Args:
        configFile: Path to an ``sma-ng.yml`` file, or a directory
            containing one. Defaults to the standard location.
        logger: Optional logger instance. Defaults to the module logger.
        profile: Optional named profile from the ``Profiles`` section.
    """
    self.log = logger or logging.getLogger(__name__)

    self.log.debug(sys.executable)

    rootpath = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), self.RELATIVE_TO_ROOT))

    defaultConfigFile = os.path.normpath(os.path.join(rootpath, self.CONFIG_RELATIVEPATH))
    envConfigFile = os.environ.get(self.ENV_CONFIG_VAR)

    if envConfigFile and os.path.exists(os.path.realpath(envConfigFile)):
      configFile = os.path.realpath(envConfigFile)
      self.log.debug("%s environment variable override found." % (self.ENV_CONFIG_VAR))
    elif not configFile:
      legacy_yaml = os.path.join(os.path.dirname(defaultConfigFile), "autoProcess.yaml")
      configFile = legacy_yaml if not os.path.exists(defaultConfigFile) and os.path.exists(legacy_yaml) else defaultConfigFile
      self.log.debug("Loading default config file.")

    if os.path.isdir(configFile):
      configFile = os.path.realpath(os.path.join(configFile, self.CONFIG_RELATIVEPATH))
      self.log.debug("Configuration file specified is a directory, joining with %s." % (self.CONFIG_DEFAULT))

    self.log.debug("Loading config file %s." % configFile)

    write = False  # Will be changed to true if a value is missing from the config file and needs to be written

    from resources.yamlconfig import load as _yaml_load
    from resources.yamlconfig import migrate_ini_to_yaml as _migrate

    if configFile.endswith(".ini"):
      yaml_path = os.path.splitext(configFile)[0] + ".yaml"
      ini_path = configFile
    else:
      yaml_path = configFile
      ini_path = os.path.splitext(configFile)[0] + ".ini"
    if not os.path.isfile(yaml_path) and os.path.isfile(ini_path):
      bak_path = ini_path + ".bak"
      self.log.info("Migrating %s -> %s (backup: %s)" % (ini_path, yaml_path, bak_path))
      _migrate(ini_path, yaml_path, bak_path, self.DEFAULTS)

    data = {}
    if os.path.isfile(yaml_path):
      try:
        data = _yaml_load(yaml_path) or {}
      except Exception:
        self.log.exception("Error reading config file %s." % yaml_path)
        sys.exit(1)
    else:
      self.log.error("Config file not found, creating %s." % yaml_path)
      write = True

    # Make sure all sections and all keys for each section are present
    for s in self.DEFAULTS:
      if s not in data:
        data[s] = {}
        write = True
      if s in self.DYNAMIC_SECTIONS:
        continue
      for k, v in self.DEFAULTS[s].items():
        if k not in data[s]:
          data[s][k] = v
          write = True

    # If any keys are missing from the config file, write them
    if write:
      self.writeConfig(data, yaml_path)

    if profile:
      data = self._apply_profile(data, profile)
    self._profile = profile

    data = self.migrateFromOld(data, yaml_path)

    self.readConfig(data)

    self._config = data
    self._configFile = yaml_path

    self._validate_binaries()

  # Hardware acceleration profiles: maps a single hwaccel value to all derived settings
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

  # Codec name aliases — normalized to canonical names before GPU encoder lookup
  CODEC_ALIASES = {
    "hevc": "h265",
    "x265": "h265",
    "x264": "h264",
  }

  # Maps canonical codec names to GPU-specific encoder names
  HWACCEL_CODEC_MAP = {
    "qsv": {"h265": "h265qsv", "h264": "h264qsv", "av1": "av1qsv", "vp9": "vp9qsv"},
    "vaapi": {"h265": "h265vaapi", "h264": "h264vaapi", "av1": "av1vaapi"},
    "nvenc": {"h265": "h265_nvenc", "h264": "h264_nvenc", "av1": "av1_nvenc"},
    "videotoolbox": {"h265": "h265_videotoolbox", "h264": "h264_videotoolbox"},
  }

  def _apply_hwaccel_profile(self, gpu):
    """Apply hardware acceleration profile, setting derived values."""
    profile = self.HWACCEL_PROFILES.get(gpu)
    if not profile:
      return

    self.log.debug("Applying hwaccel profile: %s" % gpu)

    # Only override if not explicitly set by the user
    if not self.hwaccels:
      self.hwaccels = profile["hwaccels"]
    if not self.hwaccel_decoders:
      self.hwaccel_decoders = profile["hwaccel-decoders"]
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

  @staticmethod
  def _apply_profile(data, profile):
    profiles = data.get("Profiles") or {}
    if profile not in profiles:
      raise KeyError("Profile %r not found in config (available: %s)" % (profile, ", ".join(profiles) or "none"))
    for section, overrides in profiles[profile].items():
      data.setdefault(section, {}).update(overrides)
    return data

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
      key, val = [x.strip() for x in item.split(key_separator, 1)]
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

  def readConfig(self, data):
    """Parse all sections of ``data`` and populate instance attributes.

    Reads the ``[Video]`` ``gpu`` key first because it affects both the
    converter (hwaccel profile) and video codec (GPU encoder mapping). Then
    delegates to private helpers in this order:

    - ``_read_converter``  — ``[Converter]`` section
    - ``_read_permissions`` — ``[Permissions]`` section
    - ``_read_metadata``   — ``[Metadata]`` section
    - ``_read_video``      — ``[Video]``, ``[HDR]``, and ``[Naming]`` sections
    - ``_read_analyzer``   — ``[Analyzer]`` section
    - ``_read_audio``      — ``[Audio]``, ``[Audio.Sorting]``, ``[Audio.ChannelFilters]``, and ``[Universal Audio]`` sections
    - ``_read_subtitles``  — ``[Subtitle]`` and its sub-sections
    - ``_read_sonarr_radarr`` — all ``[Sonarr*]`` and ``[Radarr*]`` sections
    - ``_read_downloaders`` — ``[SABNZBD]``, ``[Deluge]``, ``[qBittorrent]``, and ``[uTorrent]`` sections
    - ``_read_plex``       — ``[Plex]`` section

    Args:
        data: A populated configuration dictionary.
    """
    # GPU is in [Video] but affects both converter (hwaccel profile) and video (codec mapping)
    self.gpu = str(data.get("Video", {}).get("gpu", "")).strip().lower()

    self._read_converter(data)
    self._read_permissions(data)
    self._read_metadata(data)
    self._read_video(data)
    self._read_analyzer(data)
    self._read_audio(data)
    self._read_subtitles(data)
    self._read_sonarr_radarr(data)
    self._read_downloaders(data)
    self._read_plex(data)

  def _read_converter(self, data):
    """Parse ``[Converter]`` and set FFmpeg paths, output format, threading, and file-disposition attributes."""
    section = "Converter"
    cfg = data[section]
    self.ffmpeg = cfg_getpath(cfg["ffmpeg"])
    self.ffprobe = cfg_getpath(cfg["ffprobe"])
    self.threads = cfg["threads"]
    self.hwaccels = self._as_list(cfg["hwaccels"])
    self.hwaccel_decoders = self._as_list(cfg["hwaccel-decoders"])
    self.hwdevices = self._as_dict(cfg["hwdevices"])
    self.hwoutputfmt = self._as_dict(cfg["hwaccel-output-format"])
    self.output_dir = cfg_getdirectory(cfg["output-directory"])
    self.output_dir_ratio = cfg["output-directory-space-ratio"]
    self.output_format = cfg["output-format"]
    self.output_extension = cfg_getextension(cfg["output-extension"])
    self.temp_extension = cfg_getextension(cfg["temp-extension"])
    self.minimum_size = cfg["minimum-size"]
    self.ignored_extensions = cfg_getextensions(self._as_list(cfg["ignored-extensions"]))
    self.copyto = cfg_getdirectories(self._as_list(cfg["copy-to"], separator="|", lower=False))
    self.moveto = cfg_getdirectory(cfg["move-to"])
    self.delete = cfg["delete-original"]
    self.recycle_bin = str(cfg["recycle-bin"]).strip() or None
    self.process_same_extensions = cfg["process-same-extensions"]
    self.bypass_copy_all = cfg["bypass-if-copying-all"]
    self.force_convert = cfg["force-convert"]
    self.postprocess = cfg["post-process"]
    self.waitpostprocess = cfg["wait-post-process"]
    self.detailedprogress = cfg["detailed-progress"]
    self.preopts = self._as_list(cfg["preopts"], lower=False, replace=[])
    self.postopts = self._as_list(cfg["postopts"], lower=False, replace=[])
    self.regex = cfg["regex-directory-replace"]

    if self.gpu:
      self._apply_hwaccel_profile(self.gpu)

    if self.force_convert:
      self.process_same_extensions = True
      self.log.warning("Force-convert is true, so process-same-extensions is being overridden to true as well")

  def _read_permissions(self, data):
    """Parse ``[Permissions]`` and set the ``permissions`` dict (``chmod``, ``uid``, ``gid``)."""
    section = "Permissions"
    cfg = data[section]
    self.permissions = {}
    self.permissions["chmod"] = cfg["chmod"]
    try:
      self.permissions["chmod"] = int(self.permissions["chmod"], 8)
    except (ValueError, TypeError):
      self.log.exception("Invalid permissions, defaulting to 664.")
      self.permissions["chmod"] = int("0664", 8)
    self.permissions["uid"] = cfg["uid"]
    self.permissions["gid"] = cfg["gid"]

  def _read_metadata(self, data):
    """Parse ``[Metadata]`` and set tagging, artwork, moov-relocation, and disposition-sanitisation attributes."""
    section = "Metadata"
    cfg = data[section]
    self.relocate_moov = cfg["relocate-moov"]
    self.fullpathguess = cfg["full-path-guess"]
    self.tagfile = cfg["tag"]
    self.taglanguage = cfg["tag-language"].lower()
    artwork = str(cfg["download-artwork"]).lower()
    if artwork == "poster":
      self.artwork = True
      self.thumbnail = False
    elif "thumb" in artwork:
      self.artwork = True
      self.thumbnail = True
    else:
      self.thumbnail = False
      try:
        self.artwork = bool(cfg["download-artwork"])
      except (ValueError, TypeError):
        self.artwork = True
        self.log.error("Invalid download-artwork value, defaulting to 'poster'.")
    self.sanitize_disposition = self._as_list(cfg["sanitize-disposition"])
    self.strip_metadata = cfg["strip-metadata"]
    self.keep_titles = cfg["keep-titles"]

  def _read_video(self, data):
    """Parse ``[Video]``, ``[HDR]``, and ``[Naming]`` and set video codec, bitrate, HDR, and naming attributes."""
    section = "Video"
    cfg = data[section]
    self.vcodec = self._as_list(cfg["codec"])
    self.vmaxbitrate = cfg["max-bitrate"]
    self.vbitrateratio = self._as_dict(cfg["bitrate-ratio"], value_modifier=float)
    self.vbitrate_profiles = self._parse_bitrate_profiles(cfg["crf-profiles"])
    self.vbitrate_profiles_hd = self._parse_bitrate_profiles(cfg["crf-profiles-hd"])
    self.preset = cfg["preset"]
    self.codec_params = cfg["codec-parameters"]
    self.dynamic_params = cfg["dynamic-parameters"]
    self.vfilter = cfg["filter"]
    self.vforcefilter = cfg["force-filter"]
    self.vwidth = cfg["max-width"]
    self.video_level = cfg["max-level"]
    self.vprofile = self._as_list(cfg["profile"])
    self.pix_fmt = self._as_list(cfg["pix-fmt"])
    self.keep_source_pix_fmt = cfg["prioritize-source-pix-fmt"]
    self.look_ahead_depth = cfg["look-ahead-depth"]
    self.b_frames = cfg["b-frames"]
    self.ref_frames = cfg["ref-frames"]

    # HDR
    section = "HDR"
    cfg = data[section]
    self.hdr = {}
    self.hdr["codec"] = self._as_list(cfg["codec"])
    self.hdr["pix_fmt"] = self._as_list(cfg["pix-fmt"])
    self.hdr["space"] = self._as_list(cfg["space"])
    self.hdr["transfer"] = self._as_list(cfg["transfer"])
    self.hdr["primaries"] = self._as_list(cfg["primaries"])
    self.hdr["preset"] = cfg["preset"]
    self.hdr["codec_params"] = cfg["codec-parameters"]
    self.hdr["filter"] = cfg["filter"]
    self.hdr["forcefilter"] = cfg["force-filter"]
    self.hdr["profile"] = self._as_list(cfg["profile"])
    self.hdr["look_ahead_depth"] = cfg["look-ahead-depth"]
    self.hdr["b_frames"] = cfg["b-frames"]
    self.hdr["ref_frames"] = cfg["ref-frames"]

    # Naming
    section = "Naming"
    cfg = data[section]
    self.naming_enabled = cfg["enabled"]
    self.naming_tv_template = cfg["tv-template"]
    self.naming_tv_airdate_template = cfg["tv-airdate-template"]
    self.naming_movie_template = cfg["movie-template"]

    if self.gpu:
      self._apply_hwaccel_codec_map(self.gpu)

    # codec-parameters may contain QSV-specific flags (e.g. -low_power, -extbrc).
    # Clear them when not using QSV so they don't get passed to other encoders.
    if self.codec_params and self.gpu != "qsv":
      self.log.debug("Clearing codec-parameters (not QSV, gpu=%s)." % self.gpu)
      self.codec_params = ""
    if self.hdr.get("codec_params") and self.gpu != "qsv":
      self.hdr["codec_params"] = ""

  @staticmethod
  def _parse_bitrate_profiles(raw):
    """Parse a ``crf-profiles`` string into a sorted list of profile dicts.

    Format: ``source_kbps:quality:target_bitrate:max_bitrate`` entries
    separated by commas.  Bitrate values may use ``M`` (megabits) or ``k``
    (kilobits) suffixes; bare numbers are treated as kilobits.

    Example::

        0:22:1M:3M, 3000:22:2M:4M, 8000:22:5M:10M

    Returns a list sorted by ``source_kbps`` ascending so that the lookup
    in :meth:`mediaprocessor.MediaProcessor._video_bitrate_profile` can use
    a simple linear scan.  Returns an empty list when ``raw`` is blank.
    """
    profiles = []
    for entry in raw.split(","):
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
    """Convert a bitrate string like ``5M``, ``3000k``, or ``3000`` to kbps int."""
    s = s.strip().upper()
    if s.endswith("M"):
      return int(float(s[:-1]) * 1000)
    if s.endswith("K"):
      return int(float(s[:-1]))
    return int(s)

  def _read_analyzer(self, data):
    """Parse ``[Analyzer]`` and set optional per-job recommendation controls."""
    section = "Analyzer"
    cfg = data[section]
    self.analyzer = {}
    self.analyzer["enabled"] = cfg["enabled"]
    self.analyzer["backend"] = cfg["backend"].strip().lower()
    self.analyzer["device"] = cfg["device"].strip() or "AUTO"
    self.analyzer["model_dir"] = cfg_getpath(cfg["model-dir"])
    self.analyzer["cache_dir"] = cfg_getpath(cfg["cache-dir"])
    self.analyzer["max_frames"] = cfg["max-frames"]
    self.analyzer["target_width"] = cfg["target-width"]
    self.analyzer["allow_codec_reorder"] = cfg["allow-codec-reorder"]
    self.analyzer["allow_bitrate_adjustments"] = cfg["allow-bitrate-adjustments"]
    self.analyzer["allow_preset_adjustments"] = cfg["allow-preset-adjustments"]
    self.analyzer["allow_filter_adjustments"] = cfg["allow-filter-adjustments"]
    self.analyzer["allow_force_reencode"] = cfg["allow-force-reencode"]

  def _read_audio(self, data):
    """Parse ``[Audio]``, ``[Audio.Sorting]``, ``[Audio.ChannelFilters]``, and ``[Universal Audio]`` and set audio codec, language, bitrate, and sorting attributes."""
    section = "Audio"
    cfg = data[section]
    self.acodec = self._as_list(cfg["codec"])
    self.awl = self._as_list(cfg["languages"])
    self.adl = cfg["default-language"].lower()
    self.audio_original_language = cfg["include-original-language"]
    self.abitrate = cfg["channel-bitrate"]
    self.avbr = cfg["variable-bitrate"]
    self.amaxbitrate = cfg["max-bitrate"]
    self.maxchannels = cfg["max-channels"]
    self.aprofile = cfg["profile"].lower()
    self.afilter = cfg["filter"]
    self.aforcefilter = cfg["force-filter"]
    self.audio_samplerates = [int(x) for x in self._as_list(cfg["sample-rates"]) if str(x).isdigit()]
    self.audio_sampleformat = cfg["sample-format"]
    self.audio_atmos_force_copy = cfg["atmos-force-copy"]
    self.audio_copyoriginal = cfg["copy-original"]
    self.audio_first_language_stream = cfg["first-stream-of-language"]
    self.aac_adtstoasc = cfg["aac-adtstoasc"]
    self.ignored_audio_dispositions = self._as_list(cfg["ignored-dispositions"])
    self.force_audio_defaults = cfg["force-default"]
    self.unique_audio_dispositions = cfg["unique-dispositions"]
    self.stream_codec_combinations = sorted([x.split(":") for x in self._as_list(cfg["stream-codec-combinations"])], key=lambda x: len(x), reverse=True)

    section = "Audio.Sorting"
    cfg = data[section]
    self.audio_sorting = self._as_list(cfg["sorting"])
    self.audio_sorting_default = self._as_list(cfg["default-sorting"])
    self.audio_sorting_codecs = self._as_list(cfg["codecs"])

    section = "Audio.ChannelFilters"
    self.afilterchannels = {}
    if section in data:
      for key, value in data.get(section, {}).items():
        if value:
          try:
            channels = [int(x) for x in key.split("-", 1)]
            self.afilterchannels[channels[0]] = {channels[1]: value}
          except (ValueError, IndexError):
            self.log.exception("Unable to parse %s %s, skipping." % (section, key))
            continue

    # Universal Audio
    section = "Universal Audio"
    cfg = data[section]
    self.ua_enabled = cfg["enabled"]
    self.ua = self._as_list(cfg["codec"])
    self.ua_bitrate = cfg["channel-bitrate"]
    self.ua_vbr = cfg["variable-bitrate"]
    self.ua_first_only = cfg["first-stream-only"]
    self.ua_profile = cfg["profile"].lower()
    self.ua_filter = cfg["filter"]
    self.ua_forcefilter = cfg["force-filter"]

  def _read_subtitles(self, data):
    """Parse ``[Subtitle]`` and its sub-sections and set subtitle codec, language, embed, burn, and subliminal download attributes."""
    section = "Subtitle"
    cfg = data[section]
    self.scodec = self._as_list(cfg["codec"])
    self.scodec_image = self._as_list(cfg["codec-image-based"])
    self.swl = self._as_list(cfg["languages"])
    self.sdl = cfg["default-language"].lower()
    self.subtitle_original_language = cfg["include-original-language"]
    self.sub_first_language_stream = cfg["first-stream-of-language"]
    self.subencoding = cfg["encoding"]
    self.burn_subtitles = cfg["burn-subtitles"]
    self.burn_dispositions = self._as_list(cfg["burn-dispositions"])
    self.embedsubs = cfg["embed-subs"]
    self.embedimgsubs = cfg["embed-image-subs"]
    self.embedonlyinternalsubs = cfg["embed-only-internal-subs"]
    self.filename_dispositions = self._as_list(cfg["filename-dispositions"])
    self.ignore_embedded_subs = cfg["ignore-embedded-subs"]
    self.ignored_subtitle_dispositions = self._as_list(cfg["ignored-dispositions"])
    self.force_subtitle_defaults = cfg["force-default"]
    self.unique_subtitle_dispositions = cfg["unique-dispositions"]
    self.attachmentcodec = self._as_list(cfg["attachment-codec"])
    self.removebvs = cfg["remove-bitstream-subs"]

    section = "Subtitle.Sorting"
    cfg = data[section]
    self.sub_sorting = self._as_list(cfg["sorting"])
    self.sub_sorting_codecs = self._as_list(cfg["codecs"])
    self.burn_sorting = self._as_list(cfg["burn-sorting"])

    section = "Subtitle.CleanIt"
    cfg = data[section]
    self.cleanit = cfg["enabled"]
    self.cleanit_config = cfg["config-path"]
    self.cleanit_tags = self._as_list(cfg["tags"])

    section = "Subtitle.FFSubsync"
    cfg = data[section]
    self.ffsubsync = cfg["enabled"]

    section = "Subtitle.Subliminal"
    cfg = data[section]
    self.downloadsubs = cfg["download-subs"]
    self.downloadforcedsubs = cfg["download-forced-subs"]
    self.hearing_impaired = cfg["include-hearing-impaired-subs"]
    self.subproviders = self._as_list(cfg["providers"])

    section = "Subtitle.Subliminal.Auth"
    self.subproviders_auth = {}
    if section in data:
      for key, value in data.get(section, {}).items():
        if value:
          try:
            credentials = [x.strip() for x in value.split(":", 1)]
            if len(credentials) < 2:
              self.log.error("Unable to parse %s %s, skipping." % (section, key))
              continue
            self.subproviders_auth[key.strip()] = {"username": credentials[0], "password": credentials[1]}
          except (ValueError, AttributeError):
            self.log.exception("Unable to parse %s %s, skipping." % (section, key))
            continue

  def _read_sonarr_radarr(self, data):
    """Parse all ``[Sonarr*]`` and ``[Radarr*]`` sections and populate ``sonarr_instances``, ``radarr_instances``, ``Sonarr``, and ``Radarr`` attributes."""
    self.sonarr_instances = []
    self.radarr_instances = []
    for section in list(data.keys()):
      if section.lower().startswith("sonarr") or section.lower().startswith("radarr"):
        is_sonarr = section.lower().startswith("sonarr")
        base = "Sonarr" if is_sonarr else "Radarr"
        defaults = self.DEFAULTS[base]
        cfg = data.get(section, {})
        instance = {"section": section}
        instance["host"] = cfg.get("host", defaults["host"])
        instance["port"] = cfg.get("port", defaults["port"])
        instance["apikey"] = cfg.get("apikey", defaults["apikey"])
        instance["ssl"] = cfg.get("ssl", defaults["ssl"])
        instance["webroot"] = cfg.get("webroot", defaults["webroot"])
        if not instance["webroot"].startswith("/"):
          instance["webroot"] = "/" + instance["webroot"]
        if instance["webroot"].endswith("/"):
          instance["webroot"] = instance["webroot"][:-1]
        instance["path"] = cfg.get("path", defaults.get("path", ""))
        instance["rename"] = cfg.get("force-rename", defaults["force-rename"])
        instance["rescan"] = cfg.get("rescan", defaults["rescan"])
        instance["in-progress-check"] = cfg.get("in-progress-check", defaults["in-progress-check"])
        instance["blockreprocess"] = cfg.get("block-reprocess", defaults["block-reprocess"])
        if is_sonarr:
          self.sonarr_instances.append(instance)
        else:
          self.radarr_instances.append(instance)

    self.sonarr_instances.sort(key=lambda x: len(x.get("path", "")), reverse=True)
    self.radarr_instances.sort(key=lambda x: len(x.get("path", "")), reverse=True)

    self.Sonarr = next((i for i in self.sonarr_instances if i["section"] == "Sonarr"), {})
    self.Radarr = next((i for i in self.radarr_instances if i["section"] == "Radarr"), {})

  def _read_downloader_labels(self, data, section, label_key="label"):
    """Read the common sonarr/radarr/bypass label fields for a downloader section."""
    cfg = data[section]
    return {
      "sonarr": cfg["sonarr-%s" % label_key].lower(),
      "radarr": cfg["radarr-%s" % label_key].lower(),
      "bypass": self._as_list(cfg["bypass-%s" % label_key]),
      "convert": cfg["convert"],
      "output-dir": cfg_getdirectory(cfg["output-directory"]),
      "path-mapping": self._as_dict(cfg["path-mapping"], key_separator="="),
    }

  def _read_downloaders(self, data):
    """Parse ``[SABNZBD]``, ``[Deluge]``, ``[qBittorrent]``, and ``[uTorrent]`` and set the ``SAB``, ``deluge``, ``qBittorrent``, and ``uTorrent`` dicts."""
    # SAB uses "category" instead of "label"
    section = "SABNZBD"
    self.SAB = self._read_downloader_labels(data, section, label_key="category")

    # Deluge
    section = "Deluge"
    cfg = data[section]
    self.deluge = self._read_downloader_labels(data, section)
    self.deluge["host"] = cfg["host"]
    self.deluge["port"] = cfg["port"]
    self.deluge["user"] = cfg["username"]
    self.deluge["pass"] = cfg["password"]
    self.deluge["remove"] = cfg["remove"]

    # qBittorrent
    section = "qBittorrent"
    cfg = data[section]
    self.qBittorrent = self._read_downloader_labels(data, section)
    self.qBittorrent["actionbefore"] = cfg["action-before"]
    self.qBittorrent["actionafter"] = cfg["action-after"]
    self.qBittorrent["host"] = cfg["host"]
    self.qBittorrent["port"] = cfg["port"]
    self.qBittorrent["ssl"] = cfg["ssl"]
    self.qBittorrent["username"] = cfg["username"]
    self.qBittorrent["password"] = cfg["password"]

    # uTorrent
    section = "uTorrent"
    cfg = data[section]
    self.uTorrent = self._read_downloader_labels(data, section)
    self.uTorrent["webui"] = cfg["webui"]
    self.uTorrent["actionbefore"] = cfg["action-before"]
    self.uTorrent["actionafter"] = cfg["action-after"]
    self.uTorrent["host"] = cfg["host"]
    self.uTorrent["port"] = cfg["port"]
    self.uTorrent["ssl"] = cfg["ssl"]
    self.uTorrent["username"] = cfg["username"]
    self.uTorrent["password"] = cfg["password"]

  def _read_plex(self, data):
    """Parse ``[Plex]`` and set the ``Plex`` connection dict and ``plexmatch_enabled`` flag."""
    section = "Plex"
    cfg = data[section]
    self.Plex = {}
    self.Plex["host"] = cfg["host"]
    self.Plex["port"] = cfg["port"]
    self.Plex["refresh"] = cfg["refresh"]
    self.Plex["token"] = cfg["token"]
    self.Plex["ssl"] = cfg["ssl"]
    self.Plex["ignore-certs"] = cfg["ignore-certs"]
    self.Plex["path-mapping"] = self._as_dict(cfg["path-mapping"], key_separator="=")
    self.Plex["plexmatch"] = cfg["plexmatch"]

    self.plexmatch_enabled = bool(self.Plex.get("host") and self.Plex.get("plexmatch", True))

  def _validate_binaries(self):
    """Validate that ffmpeg and ffprobe binaries exist and are executable."""
    for name, path in [("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe)]:
      if not path:
        self.log.error("%s path is not configured. Set it in sma-ng.yml [Converter] section." % name)
        sys.exit(1)
      resolved = shutil.which(path)
      if resolved:
        self.log.debug("%s found at %s" % (name, resolved))
      elif os.path.isfile(path) and os.access(path, os.X_OK):
        self.log.debug("%s found at %s" % (name, path))
      else:
        self.log.error("%s not found: '%s'. Verify the path in sma-ng.yml [Converter] section or ensure it is installed and in PATH." % (name, path))
        sys.exit(1)

  def writeConfig(self, data, cfgfile):
    from resources.yamlconfig import write as _yaml_write

    try:
      _yaml_write(cfgfile, data)
    except (OSError, PermissionError, IOError):
      self.log.exception("Error writing to %s due to permissions." % cfgfile)

  def migrateFromOld(self, data, configFile):
    try:
      write = False
      if "sort-streams" in data.get("Converter", {}):
        if not self._as_bool(data["Converter"].get("sort-streams")):
          data["Converter"].pop("sort-streams", None)
          data["Audio.Sorting"]["sorting"] = []
          data["Subtitle.Sorting"]["sorting"] = []
          write = True
      elif "prefer-more-channels" in data.get("Audio", {}):
        asorting = self._as_list(data["Audio.Sorting"]["sorting"])
        if self._as_bool(data["Audio"].get("prefer-more-channels")):
          if "channels" in asorting and "channels.a" not in asorting and "channels.d" not in asorting:
            asorting = ["channels.d" if x == "channels" else x for x in asorting]
            self.log.debug("Replacing channels with channels.d based on deprecated settings [prefer-more-channels: True].")
          else:
            asorting = ["channels.d" if x == "channels.a" else x for x in asorting]
            self.log.debug("Replacing channels.a with channels.d based on deprecated settings [prefer-more-channels: True].")
        else:
          asorting = ["channels.a" if x == "channels.d" else x for x in asorting]
          self.log.debug("Replacing channels.d with channels.a based on deprecated settings [prefer-more-channels: False].")
        data["Audio"].pop("prefer-more-channels", None)
        data["Audio.Sorting"]["sorting"] = asorting
        write = True

      if "default-more-channels" in data.get("Audio", {}):
        adsorting = self._as_list(data["Audio.Sorting"]["default-sorting"])
        if self._as_bool(data["Audio"].get("default-more-channels")):
          if "channels" in adsorting and "channels.a" not in adsorting and "channels.d" not in adsorting:
            adsorting = ["channels.d" if x == "channels" else x for x in adsorting]
            self.log.debug("Replacing channels with channels.d based on deprecated settings [default-more-channels: True].")
          else:
            adsorting = ["channels.d" if x == "channels.a" else x for x in adsorting]
            self.log.debug("Replacing channels.a with channels.d based on deprecated settings [default-more-channels: True].")
        else:
          adsorting = ["channels.a" if x == "channels.d" else x for x in adsorting]
          self.log.debug("Replacing channels.d with channels.a based on deprecated settings [default-more-channels: False].")
        data["Audio"].pop("default-more-channels", None)
        data["Audio.Sorting"]["default-sorting"] = adsorting
        write = True

      if "final-sort" in data.get("Audio.Sorting", {}) and "sorting" in data.get("Audio.Sorting", {}) and self._as_bool(data["Audio.Sorting"].get("final-sort")):
        data["Audio.Sorting"].pop("final-sort", None)
        asort = self._as_list(data["Audio.Sorting"]["sorting"])
        if "map" not in asort:
          asort.append("map")
          data["Audio.Sorting"]["sorting"] = asort
          self.log.debug("Final-sort is deprecated, adding to sorting list [audio.sorting-final-sort: True].")
        else:
          self.log.debug("Final-sort is deprecated, removing [audio.sorting-final-sort: True].")
        write = True
      elif "final-sort" in data.get("Audio.Sorting", {}):
        data["Audio.Sorting"].pop("final-sort", None)
        self.log.debug("Final-sort is deprecated, removing [audio.sorting-final-sort: False].")
        write = True

      if "copy-original-before" in data.get("Audio", {}):
        data["Audio"].pop("copy-original-before", None)
        write = True

      if "move-after" in data.get("Universal Audio", {}):
        data["Universal Audio"].pop("move-after", None)
        write = True

      # gpu moved from [Converter] to [Video]
      if "gpu" in data.get("Converter", {}):
        gpu_val = data["Converter"].pop("gpu")
        if not str(data.get("Video", {}).get("gpu", "")).strip():
          data.setdefault("Video", {})["gpu"] = gpu_val
        write = True

      if write:
        self.writeConfig(data, configFile)
    except Exception:
      self.log.exception("Unable to migrate old sorting options.")
    return data
