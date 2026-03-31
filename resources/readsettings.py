"""Configuration file parser for SMA-NG. Reads autoProcess.ini (or the file at $SMA_CONFIG) using SMAConfigParser, a thin ConfigParser subclass, and exposes all settings as attributes on the ReadSettings instance."""

import logging
import os
import shutil
import sys
from configparser import ConfigParser

from resources.extensions import *


class SMAConfigParser(ConfigParser, object):
    """ConfigParser subclass with additional typed helpers for SMA-NG config values.

    Extends the standard :class:`configparser.ConfigParser` with methods that
    parse comma-separated lists, key:value dictionaries, filesystem paths and
    directories, and file extensions directly from INI values.
    """

    def getlist(self, section, option, vars=None, separator=",", default=[], lower=True, replace=[" "]):
        """Return an INI value as a list, splitting on ``separator`` (default ``","``).

        Empty values return ``default``. Items are stripped of leading/trailing
        whitespace; pass ``lower=True`` (the default) to also lowercase them.
        Characters in ``replace`` are removed from every item before returning.
        """
        value = self.get(section, option, vars=vars)

        if not isinstance(value, str) and isinstance(value, list):
            return value

        if value == "":
            return list(default)

        value = value.split(separator)

        for r in replace:
            value = [x.replace(r, "") for x in value]
        if lower:
            value = [x.lower() for x in value]

        value = [x.strip() for x in value]
        return value

    def getdict(self, section, option, vars=None, listseparator=",", dictseparator=":", default={}, lower=True, replace=[" "], valueModifier=None):
        """Return an INI value as a dict, splitting items by ``listseparator`` then each item by ``dictseparator``.

        Items without the ``dictseparator`` are ignored. ``valueModifier``, when
        provided, is called on each value; entries that raise ``ValueError`` or
        ``TypeError`` are skipped. The ``default`` dict is used as the base.
        """
        l = self.getlist(section, option, vars, listseparator, [], lower, replace)
        output = dict(default)
        for listitem in l:
            split = listitem.split(dictseparator, 1)
            if len(split) > 1:
                if valueModifier:
                    try:
                        split[1] = valueModifier(split[1])
                    except (ValueError, TypeError):
                        self.log.exception("Invalid value for getdict")
                        continue
                output[split[0]] = split[1]
        return output

    def getpath(self, section, option, vars=None):
        """Return an INI value as a normalised filesystem path, or ``None`` if the value is empty."""
        path = self.get(section, option, vars=vars).strip()
        if path == "":
            return None
        return os.path.normpath(path)

    def getdirectory(self, section, option, vars=None):
        """Return an INI value as a path, creating the directory if it does not exist. Returns ``None`` for empty values."""
        directory = self.getpath(section, option, vars)
        try:
            os.makedirs(directory)
        except (OSError, TypeError):
            pass
        return directory

    def getdirectories(self, section, option, vars=None, separator=",", default=[]):
        """Return a list of paths from a comma-separated INI value, creating each directory if it does not exist."""
        directories = self.getlist(section, option, vars=vars, separator=separator, default=default, lower=False)
        directories = [os.path.normpath(x) for x in directories]
        for d in directories:
            if not os.path.isdir(d):
                try:
                    os.makedirs(d)
                except (OSError, TypeError):
                    pass
        return directories

    def getextension(self, section, option, vars=None):
        """Return a single normalised file extension (lowercase, no leading dot/spaces), or ``None`` for empty values."""
        extension = self.get(section, option, vars=vars).lower().replace(" ", "").replace(".", "")
        if extension == "":
            return None
        return extension

    def getextensions(self, section, option, separator=",", vars=None):
        """Return a list of normalised file extensions (dots and spaces stripped) from a comma-separated INI value."""
        return self.getlist(section, option, vars, separator, replace=[" ", "."])

    def getint(self, section, option, vars=None, fallback=0):
        """Return an INI value as an integer, defaulting to ``0`` instead of raising when the key is absent."""
        return super(SMAConfigParser, self).getint(section, option, vars=vars, fallback=fallback)

    def getboolean(self, section, option, vars=None, fallback=False):
        """Return an INI value as a boolean, defaulting to ``False`` instead of raising when the key is absent."""
        return super(SMAConfigParser, self).getboolean(section, option, vars=vars, fallback=fallback)


class ReadSettings:
    """Parses ``autoProcess.ini`` and exposes all settings as typed attributes.

    On construction, reads the INI file (creating it from ``DEFAULTS`` if
    absent), validates codec and hardware-acceleration options, and populates
    attributes such as ``Video``, ``Audio``, ``Subtitle``, ``Plex``, etc.
    """

    DEFAULTS = {
        "Converter": {
            "ffmpeg": "ffmpeg" if os.name != "nt" else "ffmpeg.exe",
            "ffprobe": "ffprobe" if os.name != "nt" else "ffprobe.exe",
            "threads": 0,
            "hwaccels": "",
            "hwaccel-decoders": "",
            "hwdevices": "",
            "hwaccel-output-format": "",
            "output-directory": "",
            "output-directory-space-ratio": 0.0,
            "output-format": "mp4",
            "output-extension": "mp4",
            "temp-extension": "",
            "minimum-size": "0",
            "ignored-extensions": "nfo, ds_store",
            "copy-to": "",
            "move-to": "",
            "delete-original": True,
            "recycle-bin": "",
            "process-same-extensions": False,
            "bypass-if-copying-all": False,
            "force-convert": False,
            "post-process": False,
            "wait-post-process": False,
            "detailed-progress": False,
            "opts-separator": ",",
            "preopts": "",
            "postopts": "",
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
            "codec": "h265",
            "max-bitrate": 0,
            "bitrate-ratio": "",
            "crf": -1,
            "crf-profiles": "",
            "preset": "",
            "codec-parameters": "",
            "dynamic-parameters": False,
            "max-width": 0,
            "profile": "",
            "max-level": 0.0,
            "pix-fmt": "",
            "prioritize-source-pix-fmt": True,
            "filter": "",
            "force-filter": False,
        },
        "HDR": {
            "codec": "",
            "pix-fmt": "",
            "space": "bt2020nc",
            "transfer": "smpte2084",
            "primaries": "bt2020",
            "preset": "",
            "codec-parameters": "",
            "filter": "",
            "force-filter": False,
            "profile": "",
        },
        "Naming": {
            "enabled": False,
            "tv-template": "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}",
            "movie-template": "{Movie CleanTitle} ({Release Year}) [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}",
        },
        "Audio": {
            "codec": "ac3",
            "languages": "",
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
            "sample-rates": "",
            "sample-format": "",
            "atmos-force-copy": False,
            "copy-original": False,
            "aac-adtstoasc": False,
            "ignored-dispositions": "",
            "force-default": False,
            "unique-dispositions": False,
            "stream-codec-combinations": "",
        },
        "Audio.Sorting": {
            "sorting": "language, channels.d, map, d.comment",
            "default-sorting": "channels.d, map, d.comment",
            "codecs": "",
        },
        "Universal Audio": {
            "codec": "aac",
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
            "codec": "mov_text",
            "codec-image-based": "",
            "languages": "",
            "default-language": "",
            "force-default": True,
            "include-original-language": False,
            "first-stream-of-language": False,
            "encoding": "",
            "burn-subtitles": False,
            "burn-dispositions": "",
            "embed-subs": True,
            "embed-image-subs": False,
            "embed-only-internal-subs": False,
            "filename-dispositions": "forced",
            "ignore-embedded-subs": False,
            "ignored-dispositions": "",
            "force-default": False,
            "unique-dispositions": False,
            "attachment-codec": "",
            "remove-bitstream-subs": False,
        },
        "Subtitle.Sorting": {
            "sorting": "language, d.comment, d.default.d, d.forced.d",
            "codecs": "",
            "burn-sorting": "language, d.comment, d.default.d, d.forced.d",
        },
        "Subtitle.CleanIt": {
            "enabled": False,
            "config-path": "",
            "tags": "",
        },
        "Subtitle.FFSubsync": {
            "enabled": False,
        },
        "Subtitle.Subliminal": {
            "download-subs": False,
            "download-forced-subs": False,
            "include-hearing-impaired-subs": False,
            "providers": "",
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
            "username": "",
            "password": "",
            "servername": "",
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

    CONFIG_DEFAULT = "autoProcess.ini"
    CONFIG_DIRECTORY = "./config"
    RESOURCE_DIRECTORY = "./resources"
    RELATIVE_TO_ROOT = "../"
    ENV_CONFIG_VAR = "SMA_CONFIG"
    DYNAMIC_SECTIONS = ["Audio.ChannelFilters", "Subtitle.Subliminal.Auth"]

    @property
    def CONFIG_RELATIVEPATH(self):
        return os.path.join(self.CONFIG_DIRECTORY, self.CONFIG_DEFAULT)

    def __init__(self, configFile=None, logger=None):
        """Load and parse the SMA-NG configuration file.

        Resolves the config path in priority order: explicit ``configFile``
        argument, ``$SMA_CONFIG`` environment variable, then the default
        ``config/autoProcess.ini`` relative to the SMA root. If the file does
        not exist it is created with all ``DEFAULTS`` values. Missing keys in
        an existing file are backfilled and written. After parsing, binary paths
        are validated via ``_validate_binaries()``.

        Args:
            configFile: Path to an ``autoProcess.ini`` file, or a directory
                containing one. Defaults to the standard location.
            logger: Optional logger instance. Defaults to the module logger.
        """
        self.log = logger or logging.getLogger(__name__)

        self.log.info(sys.executable)

        rootpath = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), self.RELATIVE_TO_ROOT))

        defaultConfigFile = os.path.normpath(os.path.join(rootpath, self.CONFIG_RELATIVEPATH))
        oldConfigFile = os.path.normpath(os.path.join(rootpath, self.CONFIG_DEFAULT))
        envConfigFile = os.environ.get(self.ENV_CONFIG_VAR)

        if envConfigFile and os.path.exists(os.path.realpath(envConfigFile)):
            configFile = os.path.realpath(envConfigFile)
            self.log.debug("%s environment variable override found." % (self.ENV_CONFIG_VAR))
        elif not configFile:
            if not os.path.exists(defaultConfigFile) and os.path.exists(oldConfigFile):
                try:
                    os.rename(oldConfigFile, defaultConfigFile)
                    self.log.info("Moved configuration file to new default location %s." % defaultConfigFile)
                    configFile = defaultConfigFile
                except OSError:
                    configFile = oldConfigFile
                    self.log.debug("Unable to move configuration file to new location, using old location.")
            else:
                configFile = defaultConfigFile
            self.log.debug("Loading default config file.")

        if os.path.isdir(configFile):
            new = os.path.realpath(os.path.join(configFile, self.CONFIG_RELATIVEPATH))
            old = os.path.realpath(os.path.join(configFile, self.CONFIG_DEFAULT))
            if not os.path.exists(new) and os.path.exists(old):
                configFile = old
            else:
                configFile = new
            self.log.debug("Configuration file specified is a directory, joining with %s." % (self.CONFIG_DEFAULT))

        self.log.info("Loading config file %s." % configFile)

        write = False  # Will be changed to true if a value is missing from the config file and needs to be written

        config = SMAConfigParser()
        if os.path.isfile(configFile):
            try:
                config.read(configFile)
            except Exception:
                self.log.exception("Error reading config file %s." % configFile)
                sys.exit(1)
        else:
            self.log.error("Config file not found, creating %s." % configFile)
            # config.filename = filename
            write = True

        # Make sure all sections and all keys for each section are present
        for s in self.DEFAULTS:
            if not config.has_section(s):
                config.add_section(s)
                write = True
            if s in self.DYNAMIC_SECTIONS:
                continue
            for k in self.DEFAULTS[s]:
                if not config.has_option(s, k):
                    config.set(s, k, str(self.DEFAULTS[s][k]))
                    write = True

        # If any keys are missing from the config file, write them
        if write:
            self.writeConfig(config, configFile)

        config = self.migrateFromOld(config, configFile)

        self.readConfig(config)

        self._config = config
        self._configFile = configFile

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

        self.log.info("Applying hwaccel profile: %s" % gpu)

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
            self.log.info("Video codecs mapped for %s: %s -> %s" % (gpu, self.vcodec, mapped))
            self.vcodec = mapped

        if self.hdr.get("codec"):
            hdr_mapped = self._map_codecs_with_fallback(self.hdr["codec"], codec_map)
            if hdr_mapped != self.hdr["codec"]:
                self.hdr["codec"] = hdr_mapped

    def readConfig(self, config):
        """Parse all sections of ``config`` and populate instance attributes.

        Reads the ``[Video]`` ``gpu`` key first because it affects both the
        converter (hwaccel profile) and video codec (GPU encoder mapping). Then
        delegates to private helpers in this order:

        - ``_read_converter``  — ``[Converter]`` section
        - ``_read_permissions`` — ``[Permissions]`` section
        - ``_read_metadata``   — ``[Metadata]`` section
        - ``_read_video``      — ``[Video]``, ``[HDR]``, and ``[Naming]`` sections
        - ``_read_audio``      — ``[Audio]``, ``[Audio.Sorting]``, ``[Audio.ChannelFilters]``, and ``[Universal Audio]`` sections
        - ``_read_subtitles``  — ``[Subtitle]`` and its sub-sections
        - ``_read_sonarr_radarr`` — all ``[Sonarr*]`` and ``[Radarr*]`` sections
        - ``_read_downloaders`` — ``[SABNZBD]``, ``[Deluge]``, ``[qBittorrent]``, and ``[uTorrent]`` sections
        - ``_read_plex``       — ``[Plex]`` section

        Args:
            config: A populated ``SMAConfigParser`` instance.
        """
        # GPU is in [Video] but affects both converter (hwaccel profile) and video (codec mapping)
        self.gpu = config.get("Video", "gpu").strip().lower() if config.has_option("Video", "gpu") else ""

        self._read_converter(config)
        self._read_permissions(config)
        self._read_metadata(config)
        self._read_video(config)
        self._read_audio(config)
        self._read_subtitles(config)
        self._read_sonarr_radarr(config)
        self._read_downloaders(config)
        self._read_plex(config)

    def _read_converter(self, config):
        """Parse ``[Converter]`` and set FFmpeg paths, output format, threading, and file-disposition attributes."""
        section = "Converter"
        self.ffmpeg = config.getpath(section, "ffmpeg", vars=os.environ)
        self.ffprobe = config.getpath(section, "ffprobe", vars=os.environ)
        self.threads = config.getint(section, "threads")
        self.hwaccels = config.getlist(section, "hwaccels")
        self.hwaccel_decoders = config.getlist(section, "hwaccel-decoders")
        self.hwdevices = config.getdict(section, "hwdevices", lower=False, replace=[])
        self.hwoutputfmt = config.getdict(section, "hwaccel-output-format")
        self.output_dir = config.getdirectory(section, "output-directory")
        self.output_dir_ratio = config.getfloat(section, "output-directory-space-ratio")
        self.output_format = config.get(section, "output-format")
        self.output_extension = config.getextension(section, "output-extension")
        self.temp_extension = config.getextension(section, "temp-extension")
        self.minimum_size = config.getint(section, "minimum-size")
        self.ignored_extensions = config.getextensions(section, "ignored-extensions")
        self.copyto = config.getdirectories(section, "copy-to", separator="|")
        self.moveto = config.getdirectory(section, "move-to")
        self.delete = config.getboolean(section, "delete-original")
        self.recycle_bin = config.get(section, "recycle-bin").strip() or None
        self.process_same_extensions = config.getboolean(section, "process-same-extensions")
        self.bypass_copy_all = config.getboolean(section, "bypass-if-copying-all")
        self.force_convert = config.getboolean(section, "force-convert")
        self.postprocess = config.getboolean(section, "post-process")
        self.waitpostprocess = config.getboolean(section, "wait-post-process")
        self.detailedprogress = config.getboolean(section, "detailed-progress")
        self.opts_sep = config.get(section, "opts-separator")
        self.preopts = config.getlist(section, "preopts", separator=self.opts_sep)
        self.postopts = config.getlist(section, "postopts", separator=self.opts_sep)
        self.regex = config.get(section, "regex-directory-replace", raw=True)

        if self.gpu:
            self._apply_hwaccel_profile(self.gpu)

        if self.force_convert:
            self.process_same_extensions = True
            self.log.warning("Force-convert is true, so process-same-extensions is being overridden to true as well")

    def _read_permissions(self, config):
        """Parse ``[Permissions]`` and set the ``permissions`` dict (``chmod``, ``uid``, ``gid``)."""
        section = "Permissions"
        self.permissions = {}
        self.permissions["chmod"] = config.get(section, "chmod")
        try:
            self.permissions["chmod"] = int(self.permissions["chmod"], 8)
        except (ValueError, TypeError):
            self.log.exception("Invalid permissions, defaulting to 664.")
            self.permissions["chmod"] = int("0664", 8)
        self.permissions["uid"] = config.getint(section, "uid", vars=os.environ)
        self.permissions["gid"] = config.getint(section, "gid", vars=os.environ)

    def _read_metadata(self, config):
        """Parse ``[Metadata]`` and set tagging, artwork, moov-relocation, and disposition-sanitisation attributes."""
        section = "Metadata"
        self.relocate_moov = config.getboolean(section, "relocate-moov")
        self.fullpathguess = config.getboolean(section, "full-path-guess")
        self.tagfile = config.getboolean(section, "tag")
        self.taglanguage = config.get(section, "tag-language").lower()
        artwork = config.get(section, "download-artwork").lower()
        if artwork == "poster":
            self.artwork = True
            self.thumbnail = False
        elif "thumb" in artwork:
            self.artwork = True
            self.thumbnail = True
        else:
            self.thumbnail = False
            try:
                self.artwork = config.getboolean(section, "download-artwork")
            except (ValueError, TypeError):
                self.artwork = True
                self.log.error("Invalid download-artwork value, defaulting to 'poster'.")
        self.sanitize_disposition = config.getlist(section, "sanitize-disposition")
        self.strip_metadata = config.getboolean(section, "strip-metadata")
        self.keep_titles = config.getboolean(section, "keep-titles")

    def _read_video(self, config):
        """Parse ``[Video]``, ``[HDR]``, and ``[Naming]`` and set video codec, bitrate, CRF, HDR, and naming attributes."""
        section = "Video"
        self.vcodec = config.getlist(section, "codec")
        self.vmaxbitrate = config.getint(section, "max-bitrate")
        self.vbitrateratio = config.getdict(section, "bitrate-ratio", lower=True, valueModifier=float)
        self.vcrf = config.getint(section, "crf")

        self.vcrf_profiles = []
        vcrf_profiles = config.getlist(section, "crf-profiles")
        for vcrfp_raw in vcrf_profiles:
            vcrfp = vcrfp_raw.split(":")
            if len(vcrfp) == 4:
                try:
                    p = {"source_bitrate": int(vcrfp[0]), "crf": int(vcrfp[1]), "maxrate": vcrfp[2], "bufsize": vcrfp[3]}
                    self.vcrf_profiles.append(p)
                except (ValueError, TypeError):
                    self.log.exception("Error parsing video-crf-profile '%s'." % vcrfp_raw)
            else:
                self.log.error("Invalid video-crf-profile length '%s'." % vcrfp_raw)
        self.vcrf_profiles.sort(key=lambda x: x["source_bitrate"], reverse=True)
        self.preset = config.get(section, "preset")
        self.codec_params = config.get(section, "codec-parameters")
        self.dynamic_params = config.getboolean(section, "dynamic-parameters")
        self.vfilter = config.get(section, "filter")
        self.vforcefilter = config.getboolean(section, "force-filter")
        self.vwidth = config.getint(section, "max-width")
        self.video_level = config.getfloat(section, "max-level")
        self.vprofile = config.getlist(section, "profile")
        self.pix_fmt = config.getlist(section, "pix-fmt")
        self.keep_source_pix_fmt = config.getboolean(section, "prioritize-source-pix-fmt")

        # HDR
        section = "HDR"
        self.hdr = {}
        self.hdr["codec"] = config.getlist(section, "codec")
        self.hdr["pix_fmt"] = config.getlist(section, "pix-fmt")
        self.hdr["space"] = config.getlist(section, "space")
        self.hdr["transfer"] = config.getlist(section, "transfer")
        self.hdr["primaries"] = config.getlist(section, "primaries")
        self.hdr["preset"] = config.get(section, "preset")
        self.hdr["codec_params"] = config.get(section, "codec-parameters")
        self.hdr["filter"] = config.get(section, "filter")
        self.hdr["forcefilter"] = config.getboolean(section, "force-filter")
        self.hdr["profile"] = config.getlist(section, "profile")

        # Naming
        section = "Naming"
        self.naming_enabled = config.getboolean(section, "enabled")
        self.naming_tv_template = config.get(section, "tv-template")
        self.naming_movie_template = config.get(section, "movie-template")

        if self.gpu:
            self._apply_hwaccel_codec_map(self.gpu)

    def _read_audio(self, config):
        """Parse ``[Audio]``, ``[Audio.Sorting]``, ``[Audio.ChannelFilters]``, and ``[Universal Audio]`` and set audio codec, language, bitrate, and sorting attributes."""
        section = "Audio"
        self.acodec = config.getlist(section, "codec")
        self.awl = config.getlist(section, "languages")
        self.adl = config.get(section, "default-language").lower()
        self.audio_original_language = config.getboolean(section, "include-original-language")
        self.abitrate = config.getint(section, "channel-bitrate")
        self.avbr = config.getint(section, "variable-bitrate")
        self.amaxbitrate = config.getint(section, "max-bitrate")
        self.maxchannels = config.getint(section, "max-channels")
        self.aprofile = config.get(section, "profile").lower()
        self.afilter = config.get(section, "filter")
        self.aforcefilter = config.getboolean(section, "force-filter")
        self.audio_samplerates = [int(x) for x in config.getlist(section, "sample-rates") if x.isdigit()]
        self.audio_sampleformat = config.get(section, "sample-format")
        self.audio_atmos_force_copy = config.getboolean(section, "atmos-force-copy")
        self.audio_copyoriginal = config.getboolean(section, "copy-original")
        self.audio_first_language_stream = config.getboolean(section, "first-stream-of-language")
        self.aac_adtstoasc = config.getboolean(section, "aac-adtstoasc")
        self.ignored_audio_dispositions = config.getlist(section, "ignored-dispositions")
        self.force_audio_defaults = config.getboolean(section, "force-default")
        self.unique_audio_dispositions = config.getboolean(section, "unique-dispositions")
        self.stream_codec_combinations = sorted([x.split(":") for x in config.getlist(section, "stream-codec-combinations")], key=lambda x: len(x), reverse=True)

        section = "Audio.Sorting"
        self.audio_sorting = config.getlist(section, "sorting")
        self.audio_sorting_default = config.getlist(section, "default-sorting")
        self.audio_sorting_codecs = config.getlist(section, "codecs")

        section = "Audio.ChannelFilters"
        self.afilterchannels = {}
        if config.has_section(section):
            for key, value in config.items(section):
                if value:
                    try:
                        channels = [int(x) for x in key.split("-", 1)]
                        self.afilterchannels[channels[0]] = {channels[1]: config.get(section, key)}
                    except (ValueError, IndexError):
                        self.log.exception("Unable to parse %s %s, skipping." % (section, key))
                        continue

        # Universal Audio
        section = "Universal Audio"
        self.ua = config.getlist(section, "codec")
        self.ua_bitrate = config.getint(section, "channel-bitrate")
        self.ua_vbr = config.getint(section, "variable-bitrate")
        self.ua_first_only = config.getboolean(section, "first-stream-only")
        self.ua_profile = config.get(section, "profile").lower()
        self.ua_filter = config.get(section, "filter")
        self.ua_forcefilter = config.getboolean(section, "force-filter")

    def _read_subtitles(self, config):
        """Parse ``[Subtitle]`` and its sub-sections and set subtitle codec, language, embed, burn, and subliminal download attributes."""
        section = "Subtitle"
        self.scodec = config.getlist(section, "codec")
        self.scodec_image = config.getlist(section, "codec-image-based")
        self.swl = config.getlist(section, "languages")
        self.sdl = config.get(section, "default-language").lower()
        self.sforcedefault = config.getboolean(section, "force-default")
        self.subtitle_original_language = config.getboolean(section, "include-original-language")
        self.sub_first_language_stream = config.getboolean(section, "first-stream-of-language")
        self.subencoding = config.get(section, "encoding")
        self.burn_subtitles = config.getboolean(section, "burn-subtitles")
        self.burn_dispositions = config.getlist(section, "burn-dispositions")
        self.embedsubs = config.getboolean(section, "embed-subs")
        self.embedimgsubs = config.getboolean(section, "embed-image-subs")
        self.embedonlyinternalsubs = config.getboolean(section, "embed-only-internal-subs")
        self.filename_dispositions = config.getlist(section, "filename-dispositions")
        self.ignore_embedded_subs = config.getboolean(section, "ignore-embedded-subs")
        self.ignored_subtitle_dispositions = config.getlist(section, "ignored-dispositions")
        self.force_subtitle_defaults = config.getboolean(section, "force-default")
        self.unique_subtitle_dispositions = config.getboolean(section, "unique-dispositions")
        self.attachmentcodec = config.getlist(section, "attachment-codec")
        self.removebvs = config.getlist(section, "remove-bitstream-subs")

        section = "Subtitle.Sorting"
        self.sub_sorting = config.getlist(section, "sorting")
        self.sub_sorting_codecs = config.getlist(section, "codecs")
        self.burn_sorting = config.getlist(section, "burn-sorting")

        section = "Subtitle.CleanIt"
        self.cleanit = config.getboolean(section, "enabled")
        self.cleanit_config = config.get(section, "config-path")
        self.cleanit_tags = config.getlist(section, "tags")

        section = "Subtitle.FFSubsync"
        self.ffsubsync = config.getboolean(section, "enabled")

        section = "Subtitle.Subliminal"
        self.downloadsubs = config.getboolean(section, "download-subs")
        self.downloadforcedsubs = config.getboolean(section, "download-forced-subs")
        self.hearing_impaired = config.getboolean(section, "include-hearing-impaired-subs")
        self.subproviders = config.getlist(section, "providers")

        section = "Subtitle.Subliminal.Auth"
        self.subproviders_auth = {}
        if config.has_section(section):
            for key, value in config.items(section, raw=True):
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

    def _read_sonarr_radarr(self, config):
        """Parse all ``[Sonarr*]`` and ``[Radarr*]`` sections and populate ``sonarr_instances``, ``radarr_instances``, ``Sonarr``, and ``Radarr`` attributes."""
        self.sonarr_instances = []
        self.radarr_instances = []
        for section in config.sections():
            if section.lower().startswith("sonarr") or section.lower().startswith("radarr"):
                is_sonarr = section.lower().startswith("sonarr")
                base = "Sonarr" if is_sonarr else "Radarr"
                defaults = self.DEFAULTS[base]
                instance = {"section": section}
                instance["host"] = config.get(section, "host", fallback=defaults["host"])
                instance["port"] = config.getint(section, "port", fallback=defaults["port"])
                instance["apikey"] = config.get(section, "apikey", fallback=defaults["apikey"])
                instance["ssl"] = config.getboolean(section, "ssl", fallback=defaults["ssl"])
                instance["webroot"] = config.get(section, "webroot", fallback=defaults["webroot"])
                if not instance["webroot"].startswith("/"):
                    instance["webroot"] = "/" + instance["webroot"]
                if instance["webroot"].endswith("/"):
                    instance["webroot"] = instance["webroot"][:-1]
                instance["path"] = config.get(section, "path", fallback=defaults.get("path", ""))
                instance["rename"] = config.getboolean(section, "force-rename", fallback=defaults["force-rename"])
                instance["rescan"] = config.getboolean(section, "rescan", fallback=defaults["rescan"])
                instance["in-progress-check"] = config.getboolean(section, "in-progress-check", fallback=defaults["in-progress-check"])
                instance["blockreprocess"] = config.getboolean(section, "block-reprocess", fallback=defaults["block-reprocess"])
                if is_sonarr:
                    self.sonarr_instances.append(instance)
                else:
                    self.radarr_instances.append(instance)

        self.sonarr_instances.sort(key=lambda x: len(x.get("path", "")), reverse=True)
        self.radarr_instances.sort(key=lambda x: len(x.get("path", "")), reverse=True)

        self.Sonarr = next((i for i in self.sonarr_instances if i["section"] == "Sonarr"), {})
        self.Radarr = next((i for i in self.radarr_instances if i["section"] == "Radarr"), {})

    def _read_downloader_labels(self, config, section, label_key="label"):
        """Read the common sonarr/radarr/bypass label fields for a downloader section."""
        return {
            "sonarr": config.get(section, "sonarr-%s" % label_key).lower(),
            "radarr": config.get(section, "radarr-%s" % label_key).lower(),
            "bypass": config.getlist(section, "bypass-%s" % label_key),
            "convert": config.getboolean(section, "convert"),
            "output-dir": config.getdirectory(section, "output-directory"),
            "path-mapping": config.getdict(section, "path-mapping", dictseparator="=", lower=False, replace=[]),
        }

    def _read_downloaders(self, config):
        """Parse ``[SABNZBD]``, ``[Deluge]``, ``[qBittorrent]``, and ``[uTorrent]`` and set the ``SAB``, ``deluge``, ``qBittorrent``, and ``uTorrent`` dicts."""
        # SAB uses "category" instead of "label"
        section = "SABNZBD"
        self.SAB = self._read_downloader_labels(config, section, label_key="category")

        # Deluge
        section = "Deluge"
        self.deluge = self._read_downloader_labels(config, section)
        self.deluge["host"] = config.get(section, "host")
        self.deluge["port"] = config.getint(section, "port")
        self.deluge["user"] = config.get(section, "username")
        self.deluge["pass"] = config.get(section, "password")
        self.deluge["remove"] = config.getboolean(section, "remove")

        # qBittorrent
        section = "qBittorrent"
        self.qBittorrent = self._read_downloader_labels(config, section)
        self.qBittorrent["actionbefore"] = config.get(section, "action-before")
        self.qBittorrent["actionafter"] = config.get(section, "action-after")
        self.qBittorrent["host"] = config.get(section, "host")
        self.qBittorrent["port"] = config.get(section, "port")
        self.qBittorrent["ssl"] = config.getboolean(section, "ssl")
        self.qBittorrent["username"] = config.get(section, "username")
        self.qBittorrent["password"] = config.get(section, "password")

        # uTorrent
        section = "uTorrent"
        self.uTorrent = self._read_downloader_labels(config, section)
        self.uTorrent["webui"] = config.getboolean(section, "webui")
        self.uTorrent["actionbefore"] = config.get(section, "action-before")
        self.uTorrent["actionafter"] = config.get(section, "action-after")
        self.uTorrent["host"] = config.get(section, "host")
        self.uTorrent["port"] = config.get(section, "port")
        self.uTorrent["ssl"] = config.getboolean(section, "ssl")
        self.uTorrent["username"] = config.get(section, "username")
        self.uTorrent["password"] = config.get(section, "password")

    def _read_plex(self, config):
        """Parse ``[Plex]`` and set the ``Plex`` connection dict and ``plexmatch_enabled`` flag."""
        section = "Plex"
        self.Plex = {}
        self.Plex["username"] = config.get(section, "username")
        self.Plex["password"] = config.get(section, "password")
        self.Plex["servername"] = config.get(section, "servername")
        self.Plex["host"] = config.get(section, "host")
        self.Plex["port"] = config.getint(section, "port")
        self.Plex["refresh"] = config.getboolean(section, "refresh")
        self.Plex["token"] = config.get(section, "token")
        self.Plex["ssl"] = config.getboolean(section, "ssl")
        self.Plex["ignore-certs"] = config.getboolean(section, "ignore-certs")
        self.Plex["path-mapping"] = config.getdict(section, "path-mapping", dictseparator="=", lower=False, replace=[])
        self.Plex["plexmatch"] = config.getboolean(section, "plexmatch")

        self.plexmatch_enabled = bool(self.Plex.get("host") and self.Plex.get("plexmatch", True))

    def _validate_binaries(self):
        """Validate that ffmpeg and ffprobe binaries exist and are executable."""
        for name, path in [("ffmpeg", self.ffmpeg), ("ffprobe", self.ffprobe)]:
            if not path:
                self.log.error("%s path is not configured. Set it in autoProcess.ini [Converter] section." % name)
                sys.exit(1)
            resolved = shutil.which(path)
            if resolved:
                self.log.debug("%s found at %s" % (name, resolved))
            elif os.path.isfile(path) and os.access(path, os.X_OK):
                self.log.debug("%s found at %s" % (name, path))
            else:
                self.log.error("%s not found: '%s'. Verify the path in autoProcess.ini [Converter] section or ensure it is installed and in PATH." % (name, path))
                sys.exit(1)

    def writeConfig(self, config, cfgfile):
        if not os.path.isdir(os.path.dirname(cfgfile)):
            os.makedirs(os.path.dirname(cfgfile))
        try:
            fp = open(cfgfile, "w")
            config.write(fp)
            fp.close()
        except (OSError, PermissionError, IOError):
            self.log.exception("Error writing to %s due to permissions." % (self.CONFIG_DEFAULT))

    def migrateFromOld(self, config, configFile):
        try:
            write = False
            if config.has_option("Converter", "sort-streams"):
                if not config.getboolean("Converter", "sort-streams"):
                    config.remove_option("Converter", "sort-streams")
                    config.set("Audio.Sorting", "sorting", "")
                    config.set("Subtitle.Sorting", "sorting", "")
                    write = True
            elif config.has_option("Audio", "prefer-more-channels"):
                asorting = config.get("Audio.Sorting", "sorting").lower()
                if config.getboolean("Audio", "prefer-more-channels"):
                    if "channels" in asorting and "channels.a" not in asorting and "channels.d" not in asorting:
                        asorting = asorting.replace("channels", "channels.d")
                        self.log.debug("Replacing channels with channels.d based on deprecated settings [prefer-more-channels: True].")
                    else:
                        asorting = asorting.replace("channels.a", "channels.d")
                        self.log.debug("Replacing channels.a with channels.d based on deprecated settings [prefer-more-channels: True].")
                else:
                    asorting = asorting.replace("channels.d", "channels.a")
                    self.log.debug("Replacing channels.d with channels.a based on deprecated settings [prefer-more-channels: False].")
                config.remove_option("Audio", "prefer-more-channels")
                config.set("Audio.Sorting", "sorting", asorting)
                write = True

            if config.has_option("Audio", "default-more-channels"):
                adsorting = config.get("Audio.Sorting", "default-sorting").lower()
                if config.getboolean("Audio", "default-more-channels"):
                    if "channels" in adsorting and "channels.a" not in adsorting and "channels.d" not in adsorting:
                        adsorting = adsorting.replace("channels", "channels.d")
                        self.log.debug("Replacing channels with channels.d based on deprecated settings [default-more-channels: True].")
                    else:
                        adsorting = adsorting.replace("channels.a", "channels.d")
                        self.log.debug("Replacing channels.a with channels.d based on deprecated settings [default-more-channels: True].")
                else:
                    adsorting = adsorting.replace("channels.d", "channels.a")
                    self.log.debug("Replacing channels.d with channels.a based on deprecated settings [default-more-channels: False].")
                config.remove_option("Audio", "default-more-channels")
                config.set("Audio.Sorting", "default-sorting", adsorting)
                write = True

            if config.has_option("Audio.Sorting", "final-sort") and config.has_option("Audio.Sorting", "sorting") and config.getboolean("Audio.Sorting", "final-sort"):
                config.remove_option("Audio.Sorting", "final-sort")
                asort = config.getlist("Audio.Sorting", "sorting")
                if "map" not in asort:
                    asort.append("map")
                    config.set("Audio.Sorting", "sorting", "".join("%s, " % x for x in asort)[:-2])
                    self.log.debug("Final-sort is deprecated, adding to sorting list [audio.sorting-final-sort: True].")
                else:
                    self.log.debug("Final-sort is deprecated, removing [audio.sorting-final-sort: True].")
                write = True
            elif config.has_option("Audio.Sorting", "final-sort"):
                config.remove_option("Audio.Sorting", "final-sort")
                self.log.debug("Final-sort is deprecated, removing [audio.sorting-final-sort: False].")
                write = True

            if config.has_option("Audio", "copy-original-before"):
                config.remove_option("Audio", "copy-original-before")
                write = True

            if config.has_option("Universal Audio", "move-after"):
                config.remove_option("Universal Audio", "move-after")
                write = True

            # gpu moved from [Converter] to [Video]
            if config.has_option("Converter", "gpu"):
                gpu_val = config.get("Converter", "gpu")
                config.remove_option("Converter", "gpu")
                if not config.has_option("Video", "gpu") or not config.get("Video", "gpu").strip():
                    config.set("Video", "gpu", gpu_val)
                write = True

            if write:
                self.writeConfig(config, configFile)
        except Exception:
            self.log.exception("Unable to migrate old sorting options.")
        return config
