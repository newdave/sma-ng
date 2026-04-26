"""Pydantic v2 schema for sma-ng.yml.

Single source of truth for the four-bucket config layout:
``daemon`` / ``base`` / ``profiles`` / ``services``. The legacy flat layout
(top-level ``converter:``, ``video:`` etc.) is rejected by ConfigLoader,
not by this schema.

Field names are snake_case Python identifiers; YAML aliases are kebab-case
via ``alias_generator``. Both forms are accepted on input
(``populate_by_name=True``); ``model_dump(by_alias=True)`` emits kebab-case
for the sample generator.

Unknown keys are allowed (``extra="allow"``) and surfaced as warnings by
ConfigLoader after validation — see brainstorming/2026-04-26-config-restructure.md
"warn-and-continue on unknown keys" decision.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _to_kebab(name: str) -> str:
  return name.replace("_", "-")


class _Base(BaseModel):
  model_config = ConfigDict(
    extra="allow",
    populate_by_name=True,
    alias_generator=_to_kebab,
  )


# ---------------------------------------------------------------------------
# Converter / video / hdr / analyzer / naming / metadata / permissions
# ---------------------------------------------------------------------------


class ConverterSettings(_Base):
  ffmpeg: str = "ffmpeg"
  ffprobe: str = "ffprobe"
  threads: int = 0
  hwaccels: list[str] = Field(default_factory=list)
  hwaccel_decoders: list[str] = Field(default_factory=list)
  hwdevices: dict[str, str] = Field(default_factory=dict)
  hwaccel_output_format: dict[str, str] = Field(default_factory=dict)
  output_directory: str = ""
  output_directory_space_ratio: float = 0.0
  output_format: str = "mp4"
  output_extension: str = "mp4"
  temp_extension: str = ""
  minimum_size: int = 0
  ignored_extensions: list[str] = Field(default_factory=lambda: ["nfo", "ds_store"])
  copy_to: list[str] = Field(default_factory=list)
  move_to: str = ""
  delete_original: bool = True
  recycle_bin: str = ""
  process_same_extensions: bool = False
  bypass_if_copying_all: bool = False
  force_convert: bool = False
  post_process: bool = False
  wait_post_process: bool = False
  detailed_progress: bool = False
  preopts: list[str] = Field(default_factory=list)
  postopts: list[str] = Field(default_factory=list)
  regex_directory_replace: str = r"[^\w\-_\. ]"


class PermissionSettings(_Base):
  chmod: str = "0664"
  uid: int = -1
  gid: int = -1


class MetadataSettings(_Base):
  relocate_moov: bool = True
  full_path_guess: bool = True
  tag: bool = True
  tag_language: str = "eng"
  download_artwork: str = "poster"
  sanitize_disposition: str = ""
  strip_metadata: bool = False
  keep_titles: bool = False


class VideoSettings(_Base):
  gpu: str = ""
  codec: list[str] = Field(default_factory=lambda: ["h265"])
  max_bitrate: int = 0
  bitrate_ratio: dict[str, float] = Field(default_factory=dict)
  crf_profiles: str = ""
  crf_profiles_hd: str = ""
  preset: str = ""
  codec_parameters: str = ""
  dynamic_parameters: bool = False
  max_width: int = 0
  profile: list[str] = Field(default_factory=list)
  max_level: float = 0.0
  pix_fmt: list[str] = Field(default_factory=list)
  prioritize_source_pix_fmt: bool = True
  filter: str = ""
  force_filter: bool = False
  look_ahead_depth: int = 0
  b_frames: int = -1
  ref_frames: int = -1


class HDRSettings(_Base):
  codec: list[str] = Field(default_factory=list)
  pix_fmt: list[str] = Field(default_factory=list)
  space: list[str] = Field(default_factory=lambda: ["bt2020nc"])
  transfer: list[str] = Field(default_factory=lambda: ["smpte2084"])
  primaries: list[str] = Field(default_factory=lambda: ["bt2020"])
  preset: str = ""
  codec_parameters: str = ""
  filter: str = ""
  force_filter: bool = False
  profile: list[str] = Field(default_factory=list)
  look_ahead_depth: int = 0
  b_frames: int = -1
  ref_frames: int = -1


class AnalyzerSettings(_Base):
  enabled: bool = False
  backend: str = "openvino"
  device: str = "AUTO"
  model_dir: str = ""
  cache_dir: str = ""
  max_frames: int = 12
  target_width: int = 960
  allow_codec_reorder: bool = True
  allow_bitrate_adjustments: bool = True
  allow_preset_adjustments: bool = True
  allow_filter_adjustments: bool = True
  allow_force_reencode: bool = True


class NamingSettings(_Base):
  enabled: bool = False
  tv_template: str = "{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}"
  tv_airdate_template: str = (
    "{Series TitleYear} - {Air-Date} - {Episode CleanTitle:90}"
    " {[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}"
  )
  movie_template: str = "{Movie CleanTitle} ({Release Year}) [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}"


# ---------------------------------------------------------------------------
# Audio (with nested sorting / universal / channel-filters)
# ---------------------------------------------------------------------------


class AudioSorting(_Base):
  sorting: list[str] = Field(default_factory=lambda: ["language", "channels.d", "map", "d.comment"])
  default_sorting: list[str] = Field(default_factory=lambda: ["channels.d", "map", "d.comment"])
  codecs: list[str] = Field(default_factory=list)


class UniversalAudio(_Base):
  enabled: bool = False
  codec: list[str] = Field(default_factory=lambda: ["aac"])
  channel_bitrate: int = 128
  variable_bitrate: int = 0
  first_stream_only: bool = False
  filter: str = ""
  profile: str = ""
  force_filter: bool = False


class AudioSettings(_Base):
  codec: list[str] = Field(default_factory=lambda: ["ac3"])
  languages: list[str] = Field(default_factory=list)
  default_language: str = ""
  include_original_language: bool = True
  first_stream_of_language: bool = False
  channel_bitrate: int = 128
  variable_bitrate: int = 0
  max_bitrate: int = 0
  max_channels: int = 0
  filter: str = ""
  profile: str = ""
  force_filter: bool = False
  sample_rates: list[int] = Field(default_factory=list)
  sample_format: str = ""
  atmos_force_copy: bool = False
  copy_original: bool = False
  aac_adtstoasc: bool = False
  ignored_dispositions: list[str] = Field(default_factory=list)
  force_default: bool = False
  unique_dispositions: bool = False
  stream_codec_combinations: list[Any] = Field(default_factory=list)
  sorting: AudioSorting = Field(default_factory=AudioSorting)
  universal: UniversalAudio = Field(default_factory=UniversalAudio)
  channel_filters: dict[str, str] = Field(
    default_factory=lambda: {
      "6-2": "pan=stereo|FL=0.5*FC+0.707*FL+0.707*BL+0.5*LFE|FR=0.5*FC+0.707*FR+0.707*BR+0.5*LFE",
    }
  )


# ---------------------------------------------------------------------------
# Subtitle (with nested sorting / cleanit / ffsubsync / subliminal)
# ---------------------------------------------------------------------------


class SubtitleSorting(_Base):
  sorting: list[str] = Field(default_factory=lambda: ["language", "d.comment", "d.default.d", "d.forced.d"])
  codecs: list[str] = Field(default_factory=list)
  burn_sorting: list[str] = Field(default_factory=lambda: ["language", "d.comment", "d.default.d", "d.forced.d"])


class CleanitSettings(_Base):
  enabled: bool = False
  config_path: str = ""
  tags: list[str] = Field(default_factory=list)


class FFSubsyncSettings(_Base):
  enabled: bool = False


class SubliminalAuth(_Base):
  opensubtitles: str = ""
  tvsubtitles: str = ""


class SubliminalSettings(_Base):
  download_subs: bool = False
  download_forced_subs: bool = False
  include_hearing_impaired_subs: bool = False
  providers: list[str] = Field(default_factory=list)
  auth: SubliminalAuth = Field(default_factory=SubliminalAuth)


class SubtitleSettings(_Base):
  codec: list[str] = Field(default_factory=lambda: ["mov_text"])
  codec_image_based: list[str] = Field(default_factory=list)
  languages: list[str] = Field(default_factory=list)
  default_language: str = ""
  force_default: bool = False
  include_original_language: bool = False
  first_stream_of_language: bool = False
  encoding: str = ""
  burn_subtitles: bool = False
  burn_dispositions: list[str] = Field(default_factory=list)
  embed_subs: bool = True
  embed_image_subs: bool = False
  embed_only_internal_subs: bool = False
  filename_dispositions: str = "forced"
  ignore_embedded_subs: bool = False
  ignored_dispositions: list[str] = Field(default_factory=list)
  unique_dispositions: bool = False
  attachment_codec: list[str] = Field(default_factory=list)
  remove_bitstream_subs: bool = False
  sorting: SubtitleSorting = Field(default_factory=SubtitleSorting)
  cleanit: CleanitSettings = Field(default_factory=CleanitSettings)
  ffsubsync: FFSubsyncSettings = Field(default_factory=FFSubsyncSettings)
  subliminal: SubliminalSettings = Field(default_factory=SubliminalSettings)


# ---------------------------------------------------------------------------
# BaseConfig + ProfileOverlay
# ---------------------------------------------------------------------------


class BaseConfig(_Base):
  converter: ConverterSettings = Field(default_factory=ConverterSettings)
  permissions: PermissionSettings = Field(default_factory=PermissionSettings)
  metadata: MetadataSettings = Field(default_factory=MetadataSettings)
  video: VideoSettings = Field(default_factory=VideoSettings)
  hdr: HDRSettings = Field(default_factory=HDRSettings)
  analyzer: AnalyzerSettings = Field(default_factory=AnalyzerSettings)
  naming: NamingSettings = Field(default_factory=NamingSettings)
  audio: AudioSettings = Field(default_factory=AudioSettings)
  subtitle: SubtitleSettings = Field(default_factory=SubtitleSettings)


class ProfileOverlay(_Base):
  """Mirror of BaseConfig with every section optional.

  Used as a shallow-per-section overlay on top of ``base`` (matches the
  existing _apply_profile semantic in readsettings.py:497-504).
  """

  converter: ConverterSettings | None = None
  permissions: PermissionSettings | None = None
  metadata: MetadataSettings | None = None
  video: VideoSettings | None = None
  hdr: HDRSettings | None = None
  analyzer: AnalyzerSettings | None = None
  naming: NamingSettings | None = None
  audio: AudioSettings | None = None
  subtitle: SubtitleSettings | None = None


# ---------------------------------------------------------------------------
# Services (named instances)
# ---------------------------------------------------------------------------


class SonarrInstance(_Base):
  url: str
  apikey: str = ""
  force_rename: bool = False
  rescan: bool = True
  in_progress_check: bool = True
  block_reprocess: bool = False


class RadarrInstance(SonarrInstance):
  pass


class PlexInstance(_Base):
  url: str
  token: str = ""
  refresh: bool = False
  ignore_certs: bool = False
  path_mapping: str = ""
  plexmatch: bool = True


class Services(_Base):
  sonarr: dict[str, SonarrInstance] = Field(default_factory=dict)
  radarr: dict[str, RadarrInstance] = Field(default_factory=dict)
  plex: dict[str, PlexInstance] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Daemon (incl. routing, scan_paths, path_rewrites)
# ---------------------------------------------------------------------------


class ScanPath(_Base):
  path: str
  interval: int = 3600
  enabled: bool = True
  rewrite_from: str = ""
  rewrite_to: str = ""


class PathRewrite(_Base):
  # ``from`` is a Python keyword; expose the field as ``from_`` and alias
  # back to the kebab/yaml key ``from``.
  from_: str = Field(alias="from")
  to: str


class RoutingRule(_Base):
  match: str
  profile: str | None = None
  services: list[str] = Field(default_factory=list)


class DaemonConfig(_Base):
  host: str = "0.0.0.0"
  port: int = 8585
  workers: int = 4
  api_key: str | None = None
  db_url: str | None = None
  ffmpeg_dir: str | None = None
  node_id: str | None = None
  username: str = ""
  password: str = ""
  job_timeout_seconds: int = 7200
  progress_log_interval: int = 30
  smoke_test: bool = True
  recycle_bin_max_age_days: int = 30
  recycle_bin_min_free_gb: int = 50
  log_ttl_days: int = 30
  node_expiry_days: int = 7
  log_archive_dir: str = ""
  log_archive_after_days: int = 7
  log_delete_after_days: int = 30
  default_args: list[str] | str = Field(default_factory=list)
  scan_paths: list[ScanPath] = Field(default_factory=list)
  path_rewrites: list[PathRewrite] = Field(default_factory=list)
  routing: list[RoutingRule] = Field(default_factory=list)
  media_extensions: list[str] = Field(default_factory=lambda: [".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".flv", ".webm"])


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


class SmaConfig(_Base):
  daemon: DaemonConfig = Field(default_factory=DaemonConfig)
  base: BaseConfig = Field(default_factory=BaseConfig)
  profiles: dict[str, ProfileOverlay] = Field(default_factory=dict)
  services: Services = Field(default_factory=Services)

  @model_validator(mode="after")
  def _validate_routing_references(self) -> "SmaConfig":
    """Cross-reference every routing rule against profiles + services.

    A typo here (``sonarr.kid`` vs ``sonarr.kids``) is the most common
    operational failure mode the schema is supposed to catch — fail hard
    with the dotted path so the user sees it at startup.
    """
    for i, rule in enumerate(self.daemon.routing):
      for ref in rule.services:
        if "." not in ref:
          raise ValueError(f"daemon.routing[{i}].services: '{ref}' must be of form '<type>.<instance>'")
        stype, sname = ref.split(".", 1)
        instances = getattr(self.services, stype, None)
        if instances is None:
          raise ValueError(f"daemon.routing[{i}].services: unknown service type '{stype}'")
        if sname not in instances:
          raise ValueError(f"daemon.routing[{i}].services: '{ref}' has no matching services.{stype}.{sname}")
      if rule.profile is not None and rule.profile not in self.profiles:
        raise ValueError(f"daemon.routing[{i}].profile: '{rule.profile}' not defined under profiles")
    return self


__all__ = [
  "SmaConfig",
  "DaemonConfig",
  "BaseConfig",
  "ProfileOverlay",
  "Services",
  "ConverterSettings",
  "PermissionSettings",
  "MetadataSettings",
  "VideoSettings",
  "HDRSettings",
  "AnalyzerSettings",
  "NamingSettings",
  "AudioSettings",
  "AudioSorting",
  "UniversalAudio",
  "SubtitleSettings",
  "SubtitleSorting",
  "CleanitSettings",
  "FFSubsyncSettings",
  "SubliminalSettings",
  "SubliminalAuth",
  "SonarrInstance",
  "RadarrInstance",
  "PlexInstance",
  "ScanPath",
  "PathRewrite",
  "RoutingRule",
]
