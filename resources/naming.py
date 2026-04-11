"""
SMA-NG File Naming Engine

Template-based file renaming after conversion. Gathers data from:
1. Sonarr/Radarr API (preferred, if available)
2. FFprobe + guessit + TMDB metadata (fallback)

Default templates follow Sonarr/Radarr naming conventions.
"""

import logging
import os
import re

try:
    import requests as _requests
except ImportError:
    _requests = None

# Codec display names
VIDEO_CODEC_DISPLAY = {
    "h264": "x264",
    "x264": "x264",
    "avc": "x264",
    "h265": "x265",
    "x265": "x265",
    "hevc": "x265",
    "av1": "AV1",
    "vp9": "VP9",
    "mpeg4": "XviD",
    "msmpeg4v2": "XviD",
    "msmpeg4v3": "XviD",
    "mpeg2video": "MPEG2",
    "vc1": "VC1",
}

AUDIO_CODEC_DISPLAY = {
    "aac": "AAC",
    "ac3": "AC3",
    "eac3": "EAC3",
    "dts": "DTS",
    "truehd": "TrueHD",
    "flac": "FLAC",
    "mp3": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "pcm_s16le": "PCM",
    "pcm_s24le": "PCM",
    "dts-hd ma": "DTS-HD MA",
    "dts-hd hra": "DTS-HD HRA",
}

AUDIO_CHANNELS_DISPLAY = {
    1: "1.0",
    2: "2.0",
    3: "2.1",
    6: "5.1",
    7: "6.1",
    8: "7.1",
}

QUALITY_MAP = {
    (7600, 99999): "8K",
    (3800, 7599): "4K",
    (1900, 3799): "1080p",
    (1260, 1899): "720p",
    (0, 1259): "SD",
}

SOURCE_MAP = {
    "bluray": "BluRay",
    "blu-ray": "BluRay",
    "bdrip": "BluRay",
    "brrip": "BluRay",
    "web-dl": "WEB-DL",
    "webdl": "WEB-DL",
    "web": "WEB-DL",
    "webrip": "WEBRip",
    "web-rip": "WEBRip",
    "hdtv": "HDTV",
    "pdtv": "PDTV",
    "dsr": "DSR",
    "dvdrip": "DVDRip",
    "dvd": "DVD",
    "remux": "Remux",
}

HDR_DISPLAY = {
    "smpte2084": "HDR",
    "arib-std-b67": "HLG",
    "bt2020": "HDR10",
}

# Default naming templates
DEFAULT_TV_TEMPLATE = "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}"
DEFAULT_MOVIE_TEMPLATE = "{Movie CleanTitle} ({Release Year}) [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}"

# Characters unsafe for filenames
UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name):
    """Remove characters unsafe for filenames."""
    name = UNSAFE_CHARS.sub("", name)
    name = name.strip(". ")
    return name


def _get_quality_label(width):
    """Get quality label from video width."""
    for (lo, hi), label in QUALITY_MAP.items():
        if lo <= width <= hi:
            return label
    return "SD"


def _get_source(guess_data):
    """Extract source type from guessit data."""
    source = guess_data.get("source", "") or guess_data.get("screen_size", "")
    if isinstance(source, str):
        return SOURCE_MAP.get(source.lower(), source)
    return ""


def _get_release_group(guess_data):
    """Extract release group from guessit data."""
    return guess_data.get("release_group", "")


class NamingData:
    """Container for all data needed to build a filename."""

    def __init__(self):
        # Common
        self.quality = ""  # e.g., '1080p'
        self.quality_full = ""  # e.g., 'HDTV-1080p'
        self.source = ""  # e.g., 'HDTV', 'BluRay', 'WEB-DL'
        self.video_codec = ""  # e.g., 'x265'
        self.audio_codec = ""  # e.g., 'EAC3'
        self.audio_channels = ""  # e.g., '5.1'
        self.hdr = ""  # e.g., 'HDR'
        self.release_group = ""  # e.g., 'MeGusta'

        # TV
        self.series_title = ""
        self.series_year = ""
        self.series_titleyear = ""
        self.season = 0
        self.episode = 0
        self.episodes = []
        self.episode_title = ""
        self.episode_cleantitle = ""

        # Movie
        self.movie_title = ""
        self.movie_cleantitle = ""
        self.movie_year = ""

    def from_mediainfo(self, info, guess_data=None):
        """Populate from FFprobe MediaInfo and optional guessit data."""
        if info.video:
            width = info.video.video_width or 0
            self.quality = _get_quality_label(width)
            codec_name = (info.video.codec or "").lower()
            self.video_codec = VIDEO_CODEC_DISPLAY.get(codec_name, codec_name.upper())

            # HDR detection
            transfer = info.video.framedata.get("color_transfer", "") or info.video.color.get("transfer", "")
            if transfer:
                self.hdr = HDR_DISPLAY.get(transfer.lower(), "")

        if info.audio and len(info.audio) > 0:
            audio = info.audio[0]
            codec_name = (audio.codec or "").lower()
            profile = (audio.profile or "").lower()
            if "atmos" in profile:
                self.audio_codec = "TrueHD Atmos" if "truehd" in codec_name else "EAC3 Atmos"
            elif codec_name == "dts" and "ma" in profile:
                self.audio_codec = "DTS-HD MA"
            else:
                self.audio_codec = AUDIO_CODEC_DISPLAY.get(codec_name, codec_name.upper())
            channels = audio.audio_channels or 0
            self.audio_channels = AUDIO_CHANNELS_DISPLAY.get(channels, "%d.0" % channels if channels else "")

        if guess_data:
            self.source = _get_source(guess_data)
            self.release_group = _get_release_group(guess_data)

        self.quality_full = ("%s-%s" % (self.source, self.quality)) if self.source else self.quality

    def from_tagdata(self, tagdata):
        """Populate from TMDB Metadata object."""
        if not tagdata:
            return

        from resources.metadata import MediaType

        if tagdata.mediatype == MediaType.TV:
            self.series_title = tagdata.showname or ""
            year = ""
            if hasattr(tagdata, "showdata") and tagdata.showdata:
                first_air = tagdata.showdata.get("first_air_date", "")
                if first_air and len(first_air) >= 4:
                    year = first_air[:4]
            self.series_year = year
            self.series_titleyear = "%s (%s)" % (self.series_title, year) if year else self.series_title
            self.season = int(tagdata.season or 0)
            self.episode = int(tagdata.episode or 0)
            self.episodes = sorted(tagdata.episodes) if getattr(tagdata, "episodes", None) else [self.episode]
            self.episode_title = tagdata.title or ""
            self.episode_cleantitle = sanitize_filename(self.episode_title)
        elif tagdata.mediatype == MediaType.Movie:
            self.movie_title = tagdata.title or ""
            self.movie_cleantitle = sanitize_filename(self.movie_title)
            date = getattr(tagdata, "date", "") or ""
            self.movie_year = date[:4] if len(date) >= 4 else ""

    def from_sonarr(self, instance, filepath, log):
        """Try to get naming data from Sonarr API. Returns True on success."""
        return self._from_arr_api(instance, filepath, "sonarr", log)

    def from_radarr(self, instance, filepath, log):
        """Try to get naming data from Radarr API. Returns True on success."""
        return self._from_arr_api(instance, filepath, "radarr", log)

    def _from_arr_api(self, instance, filepath, arr_type, log):
        """Query Sonarr/Radarr API for file info to get quality/codec data."""
        if not _requests or not instance or not instance.get("apikey"):
            return False

        try:
            ssl = instance.get("ssl", False)
            protocol = "https://" if ssl else "http://"
            base_url = protocol + instance["host"] + ":" + str(instance["port"]) + instance.get("webroot", "")
            headers = {"X-Api-Key": instance["apikey"], "User-Agent": "SMA-NG naming"}

            if arr_type == "sonarr":
                return self._parse_sonarr_response(base_url, headers, filepath, log)
            else:
                return self._parse_radarr_response(base_url, headers, filepath, log)
        except Exception:
            log.debug("Failed to query %s API for naming data" % arr_type)
            return False

    def _apply_arr_quality(self, data):
        """Extract quality/source/release-group from a Sonarr or Radarr parse response."""
        if data.get("quality"):
            q = data["quality"].get("quality", {})
            self.quality = q.get("resolution", self.quality) or self.quality
            self.source = q.get("source", self.source) or self.source
        if data.get("releaseGroup"):
            self.release_group = data["releaseGroup"]
        self.quality_full = ("%s-%s" % (self.source, self.quality)) if self.source else self.quality

    def _parse_sonarr_response(self, base_url, headers, filepath, log):
        """Query Sonarr for episode file info and extract naming data."""
        try:
            r = _requests.get(base_url + "/api/v3/parse", headers=headers, params={"title": os.path.basename(filepath)}, timeout=10)
            data = r.json()

            if data.get("series"):
                series = data["series"]
                self.series_title = series.get("title", self.series_title)
                self.series_year = str(series.get("year", self.series_year))
                self.series_titleyear = "%s (%s)" % (self.series_title, self.series_year) if self.series_year else self.series_title

            if data.get("episodes") and len(data["episodes"]) > 0:
                eps = data["episodes"]
                self.season = eps[0].get("seasonNumber", self.season)
                self.episode = eps[0].get("episodeNumber", self.episode)
                self.episodes = sorted(ep.get("episodeNumber") for ep in eps if ep.get("episodeNumber") is not None)
                titles = [ep.get("title", "") for ep in eps if ep.get("title")]
                self.episode_title = " / ".join(titles) if titles else self.episode_title
                self.episode_cleantitle = sanitize_filename(self.episode_title)

            self._apply_arr_quality(data)

            ep_display = "-E".join("%02d" % e for e in self.episodes) if self.episodes else "%02d" % self.episode
            log.debug("Got naming data from Sonarr: %s S%02dE%s" % (self.series_title, self.season, ep_display))
            return True
        except Exception:
            return False

    def _parse_radarr_response(self, base_url, headers, filepath, log):
        """Query Radarr for movie file info and extract naming data."""
        try:
            r = _requests.get(base_url + "/api/v3/parse", headers=headers, params={"title": os.path.basename(filepath)}, timeout=10)
            data = r.json()

            if data.get("movie"):
                movie = data["movie"]
                self.movie_title = movie.get("title", self.movie_title)
                self.movie_cleantitle = sanitize_filename(self.movie_title)
                self.movie_year = str(movie.get("year", self.movie_year))

            self._apply_arr_quality(data)

            log.debug("Got naming data from Radarr: %s (%s)" % (self.movie_title, self.movie_year))
            return True
        except Exception:
            return False


def apply_template(template, data):
    """
    Apply a naming template with NamingData.

    Template syntax:
    - {token} - required, blank if missing
    - {token:00} - zero-padded number
    - {[token]} - optional bracket-wrapped section (omitted if token is empty)
    - {-token} - optional dash-prefixed (omitted if token is empty)
    - { token} - optional space-prefixed (omitted if token is empty)

    Supported tokens:
    TV: Series TitleYear, season, episode, Episode CleanTitle, Episode Title
    Movie: Movie Title, Movie CleanTitle, Release Year
    Common: Quality, Quality Full, Source, VideoCodec, AudioCodec, AudioChannels,
            VideoDynamicRangeType, ReleaseGroup, Custom Formats
    """
    token_map = {
        "series titleyear": data.series_titleyear,
        "series title": data.series_title,
        "series year": data.series_year,
        "season": data.season,
        "episode": data.episodes if len(data.episodes) > 1 else data.episode,
        "episode title": data.episode_title,
        "episode cleantitle": data.episode_cleantitle,
        "movie title": data.movie_title,
        "movie cleantitle": data.movie_cleantitle,
        "release year": data.movie_year,
        "quality": data.quality,
        "quality full": data.quality_full,
        "source": data.source,
        "videocodec": data.video_codec,
        "mediainfo videocodec": data.video_codec,
        "audiocodec": data.audio_codec,
        "mediainfo audiocodec": data.audio_codec,
        "audiochannels": data.audio_channels,
        "mediainfo audiochannels": data.audio_channels,
        "videodynamicrangetype": data.hdr,
        "mediainfo videodynamicrangetype": data.hdr,
        "releasegroup": data.release_group,
        "release group": data.release_group,
        "custom formats": "",  # Not available without Sonarr/Radarr
    }

    result = template

    # Handle bracket-wrapped optional tokens: {[Token]}
    def replace_bracket(m):
        inner = m.group(1).strip().lower()
        val = token_map.get(inner, "")
        return "[%s]" % val if val else ""

    result = re.sub(r"\{\[([^\]]+)\]\}", replace_bracket, result)

    def _apply_format(val, fmt):
        """Apply format spec to a value. :00 = pad to 2 digits, :000 = pad to 3, :90 = truncate to 90 chars."""
        if isinstance(val, list):
            # Multi-episode range: first-last, e.g. [1,2,3,4] with :00 → "01-E04"
            sorted_eps = sorted(val)
            first = _apply_format(sorted_eps[0], fmt)
            if len(sorted_eps) == 1:
                return first
            last = _apply_format(sorted_eps[-1], fmt)
            return "%s-E%s" % (first, last)
        if not fmt:
            return str(val)
        fmt_spec = fmt[1:]  # strip the ":"
        if all(c == "0" for c in fmt_spec) and len(fmt_spec) > 0:
            # Zero-padding: :00 = 2 digits, :000 = 3 digits
            pad = len(fmt_spec)
            if isinstance(val, int):
                return str(val).zfill(pad)
            elif isinstance(val, str) and val.isdigit():
                return val.zfill(pad)
        elif fmt_spec.isdigit():
            # Truncation: :90 = max 90 chars
            return str(val)[: int(fmt_spec)]
        return str(val)

    # Handle prefix-optional tokens: {-Token} or { Token}
    def replace_prefixed(m):
        prefix = m.group(1)
        key = m.group(2).strip().lower()
        fmt = m.group(3)
        val = token_map.get(key, "")
        if not val and val != 0:
            return ""
        return prefix + _apply_format(val, fmt)

    result = re.sub(r"\{([-\s])([^}:]+)(:[^}]*)?\}", replace_prefixed, result)

    # Handle standard tokens: {Token} or {Token:00}
    def replace_standard(m):
        key = m.group(1).strip().lower()
        fmt = m.group(2)
        val = token_map.get(key, "")
        return _apply_format(val, fmt)

    result = re.sub(r"\{([^}\[\]-][^}:]*)(:[^}]*)?\}", replace_standard, result)

    # Clean up empty brackets and double spaces
    result = re.sub(r"\[\]", "", result)
    result = re.sub(r"\s{2,}", " ", result)
    result = result.strip(" -")

    return sanitize_filename(result)


def rename_file(filepath, new_name, log=None):
    """
    Rename a file, preserving directory and extension.

    Args:
        filepath: Current absolute path to file
        new_name: New filename (without extension)
        log: Optional logger

    Returns:
        New absolute path, or original path if rename fails
    """
    log = log or logging.getLogger(__name__)
    directory = os.path.dirname(filepath)
    ext = os.path.splitext(filepath)[1]
    new_path = os.path.join(directory, new_name + ext)

    if new_path == filepath:
        log.debug("Filename unchanged, skipping rename.")
        return filepath

    if os.path.exists(new_path):
        log.warning("Target filename already exists: %s" % new_path)
        return filepath

    try:
        os.rename(filepath, new_path)
        log.info("Renamed: %s -> %s" % (os.path.basename(filepath), os.path.basename(new_path)))
        return new_path
    except OSError:
        log.exception("Failed to rename file")
        return filepath


def generate_name(filepath, info, tagdata, settings, guess_data=None, log=None):
    """
    Generate a new filename using the naming template.

    Tries Sonarr/Radarr API first, falls back to local data.

    Args:
        filepath: Current file path
        info: MediaInfo from FFprobe
        tagdata: TMDB Metadata object (or None)
        settings: ReadSettings instance
        guess_data: Optional guessit result dict
        log: Optional logger

    Returns:
        New filename (without extension), or None if naming disabled
    """
    log = log or logging.getLogger(__name__)

    if not getattr(settings, "naming_enabled", False):
        return None

    from resources.metadata import MediaType

    is_tv = tagdata and tagdata.mediatype == MediaType.TV
    template = settings.naming_tv_template if is_tv else settings.naming_movie_template

    data = NamingData()

    # Populate from local sources
    data.from_mediainfo(info, guess_data)
    data.from_tagdata(tagdata)

    # Try Sonarr/Radarr API for richer naming data
    api_success = False
    if is_tv:
        for instance in getattr(settings, "sonarr_instances", []):
            ipath = instance.get("path", "")
            if ipath and filepath.startswith(ipath):
                api_success = data.from_sonarr(instance, filepath, log)
                if api_success:
                    break
    else:
        for instance in getattr(settings, "radarr_instances", []):
            ipath = instance.get("path", "")
            if ipath and filepath.startswith(ipath):
                api_success = data.from_radarr(instance, filepath, log)
                if api_success:
                    break

    if not api_success:
        log.debug("Using local data for naming (no API match)")

    new_name = apply_template(template, data)
    if not new_name:
        log.warning("Naming template produced empty result, skipping rename")
        return None

    return new_name
