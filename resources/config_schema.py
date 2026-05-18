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

from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class FallbackPolicy(str, Enum):
  """Policy for the QSV/HW → SW fallback ladder in MediaProcessor.

  Values are stable strings consumed by /health metrics and ops dashboards;
  do not rename existing entries.

  - AGGRESSIVE: try hw → sw_decode → full_sw (legacy default behaviour
    selected by the deprecated ``software-fallback: true``).
  - SW_DECODE_ONLY: try hw → sw_decode; never swap encoder to software.
  - HW_ONLY: surface hw failures immediately; no retries. Equivalent to
    the deprecated ``software-fallback: false``.
  """

  AGGRESSIVE = "aggressive"
  SW_DECODE_ONLY = "sw_decode_only"
  HW_ONLY = "hw_only"


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
  # Fallback ladder policy for hardware-accelerated conversions. Three
  # tiers exist in MediaProcessor: hw → sw_decode → full_sw. The policy
  # selects how far the ladder is allowed to descend on failure:
  #   - hw_only        — surface hw failures immediately (recommended on
  #                      production nodes where a /dev/dri or QSV runtime
  #                      problem should fail loudly, not get masked by a
  #                      silent CPU encode);
  #   - sw_decode_only — try hw, then sw decode; never swap the encoder;
  #   - aggressive     — full legacy ladder (hw → sw_decode → full_sw).
  # Default mirrors the prior `software-fallback: true` semantics so
  # operators upgrading from the boolean see no behaviour change.
  fallback_policy: FallbackPolicy = FallbackPolicy.AGGRESSIVE
  # Deprecated alias: legacy `software-fallback: bool` is migrated to
  # `fallback-policy` by the model validator below. Removed in a future
  # minor release.
  software_fallback: bool | None = Field(default=None, exclude=True)
  preopts: list[str] = Field(default_factory=list)
  postopts: list[str] = Field(default_factory=list)
  regex_directory_replace: str = r"[^\w\-_\. ]"

  @model_validator(mode="before")
  @classmethod
  def _migrate_software_fallback(cls, data):
    """Map the deprecated boolean ``software-fallback`` onto ``fallback-policy``.

    Skipped when ``fallback-policy`` is already provided (new key wins).
    Sets a sentinel ``_software_fallback_deprecated`` so ReadSettings can
    emit a single load-time deprecation warning.
    """
    if not isinstance(data, dict):
      return data
    # Accept both YAML kebab-case and Python snake_case input keys.
    has_new = "fallback-policy" in data or "fallback_policy" in data
    legacy_key = None
    for k in ("software-fallback", "software_fallback"):
      if k in data:
        legacy_key = k
        break
    if has_new or legacy_key is None:
      return data
    legacy_value = data.pop(legacy_key)
    if legacy_value is None:
      return data
    data["fallback-policy"] = FallbackPolicy.AGGRESSIVE.value if bool(legacy_value) else FallbackPolicy.HW_ONLY.value
    data["_software_fallback_deprecated"] = True
    return data


class PermissionSettings(_Base):
  # `mode` is accepted as an alias for `chmod` — Linux/Unix users
  # naturally reach for "mode" when describing file-permission bits.
  # Bare integers in YAML (`mode: 777`, `chmod: 664`) are interpreted as
  # the user's typed *octal digits*, matching how operators talk about
  # POSIX file modes — never as decimal-encoded mode bits.
  chmod: str = Field(default="0664", validation_alias=AliasChoices("chmod", "mode"))
  uid: int = -1
  gid: int = -1

  @model_validator(mode="before")
  @classmethod
  def _stringify_chmod(cls, data):
    if not isinstance(data, dict):
      return data
    if "mode" in data and "chmod" not in data:
      data["chmod"] = data.pop("mode")
    v = data.get("chmod")
    if isinstance(v, int):
      # Treat the int as the operator's typed octal digits: `777` → "0777",
      # `664` → "0664". Reject anything that isn't a valid 3-or-4-digit
      # octal mode so silent typos like `mode: 999` don't slip through.
      s = str(v)
      if v < 0 or any(c not in "01234567" for c in s) or len(s) > 4:
        raise ValueError(f"chmod must be a 3-4 digit octal mode, got {v!r}")
      data["chmod"] = s.zfill(4) if len(s) == 4 else "0" + s.zfill(3)
    return data


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
  global_quality: int = 0
  b_frames: int = -1
  ref_frames: int = -1
  # QSV `-extra_hw_frames` pool size (input/device scope). 0 = auto: derive
  # from look-ahead-depth + 4 with a floor of 20. Any positive value is
  # used verbatim, clamped to ffmpeg's QSV ceiling of 100. Only applied
  # when `gpu: qsv`. Profiles can override per path (profiles.<name>.video.extra-hw-frames).
  extra_hw_frames: int = 0


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
  global_quality: int = 0
  b_frames: int = -1
  ref_frames: int = -1
  # See VideoSettings.extra_hw_frames — same semantics for HDR encodes.
  extra_hw_frames: int = 0
  # HDR-specific override of video.max-bitrate. Set to a positive kbps value
  # to cap HDR sources independently of the SDR ceiling. Set to 0 to disable
  # the cap entirely for HDR sources (useful on the 4K profile so HDR remuxes
  # copy through instead of being re-encoded just because the source bitrate
  # exceeds the SDR target). Negative leaves the SDR cap in effect.
  max_bitrate: int = -1


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
  # Section-level on/off shortcut. ``universal-audio: false`` is a
  # convenience alias for ``universal.enabled: false`` — kept in the
  # schema because it's the form most operators reach for first.
  # When set, it overrides ``universal.enabled`` (see model_validator).
  universal_audio: bool | None = None
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
  # Legacy fields read by post_process/plex.py for the older
  # username/password auth path. Keep them in the schema so configs
  # carrying them don't trigger Unknown-config-key warnings.
  username: str = ""
  servername: str = ""


class AutoscanInstance(_Base):
  url: str
  username: str = ""
  password: str = ""
  path_mapping: str = ""
  ignore_certs: bool = False
  enabled: bool = True


class EmbyInstance(_Base):
  """Emby Media Server instance.

  ``apikey`` is generated under the Emby admin dashboard at
  Settings → Advanced → API Keys. The refresh path uses
  ``POST /emby/Library/Media/Updated`` (the same call Sonarr/Radarr make).
  """

  url: str
  apikey: str = ""
  refresh: bool = False
  ignore_certs: bool = False
  path_mapping: str = ""
  enabled: bool = True


class JellyfinInstance(_Base):
  """Jellyfin Media Server instance.

  ``apikey`` is created under the Jellyfin admin dashboard at
  Dashboard → API Keys. The refresh path uses
  ``POST /Library/Media/Updated`` — Jellyfin retained the Emby endpoint
  shape after the fork. ``token`` is accepted as a legacy alias for
  ``apikey`` so older stamped configs validate without warnings.
  """

  url: str
  apikey: str = Field(default="", validation_alias=AliasChoices("apikey", "token"))
  refresh: bool = False
  ignore_certs: bool = False
  path_mapping: str = ""
  enabled: bool = True


class Services(_Base):
  sonarr: dict[str, SonarrInstance] = Field(default_factory=dict)
  radarr: dict[str, RadarrInstance] = Field(default_factory=dict)
  plex: dict[str, PlexInstance] = Field(default_factory=dict)
  emby: dict[str, EmbyInstance] = Field(default_factory=dict)
  jellyfin: dict[str, JellyfinInstance] = Field(default_factory=dict)
  autoscan: dict[str, AutoscanInstance] = Field(default_factory=dict)


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


class ConfigWatchSettings(_Base):
  enabled: bool = True
  interval_seconds: int = 5
  debounce_seconds: int = 2


# ---------------------------------------------------------------------------
# Library audit (errors / orphan sidecars / leftover tmp / preconv originals /
# tmdb-tvdb duplicates). Distributed across cluster via PG queue + claim.
# ---------------------------------------------------------------------------


_DEFAULT_AUDIT_SKIP_DIRS = [
  "Extras",
  "Featurettes",
  "Behind The Scenes",
  "Deleted Scenes",
  "Interviews",
  "Other",
  "Specials",
  "Trailers",
]


class AuditPath(_Base):
  path: str
  enabled: bool = True
  rewrite_from: str = ""
  rewrite_to: str = ""


class AuditAutoFix(_Base):
  ffprobe_failed: bool = False
  orphan_sidecar: bool = False
  leftover_tmp: bool = False
  preconv_original: bool = False


class AuditSettings(_Base):
  enabled: bool = False
  paths: list[AuditPath] = Field(default_factory=list)
  interval_seconds: int = 86400
  skip_dirs: list[str] = Field(default_factory=lambda: list(_DEFAULT_AUDIT_SKIP_DIRS))
  concurrency: int = 2
  batch_size: int = 50
  claim_stale_seconds: int = 600
  dry_run: bool = True
  auto_fix: AuditAutoFix = Field(default_factory=AuditAutoFix)


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
  media_extensions: list[str] = Field(default_factory=lambda: [".mkv", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".flv", ".webm"])
  config_watch: ConfigWatchSettings = Field(default_factory=ConfigWatchSettings)
  audit: AuditSettings = Field(default_factory=AuditSettings)


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


class SmaConfig(_Base):
  daemon: DaemonConfig = Field(default_factory=DaemonConfig)
  base: BaseConfig = Field(default_factory=BaseConfig)
  profiles: dict[str, ProfileOverlay] = Field(default_factory=dict)
  services: Services = Field(default_factory=Services)

  @model_validator(mode="after")
  def _propagate_universal_audio_shortcut(self) -> SmaConfig:
    """When ``base.audio.universal-audio`` is set, mirror it onto
    ``base.audio.universal.enabled`` (and the same for every profile
    overlay) so the shortcut behaves like operators expect."""
    if self.base.audio.universal_audio is not None:
      self.base.audio.universal.enabled = bool(self.base.audio.universal_audio)
    for prof in self.profiles.values():
      if prof.audio is not None and prof.audio.universal_audio is not None:
        prof.audio.universal.enabled = bool(prof.audio.universal_audio)
    return self

  @model_validator(mode="after")
  def _validate_routing_references(self) -> SmaConfig:
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
  "AnalyzerSettings",
  "AudioSettings",
  "AudioSorting",
  "AuditAutoFix",
  "AuditPath",
  "AuditSettings",
  "AutoscanInstance",
  "BaseConfig",
  "CleanitSettings",
  "ConverterSettings",
  "DaemonConfig",
  "EmbyInstance",
  "FFSubsyncSettings",
  "FallbackPolicy",
  "HDRSettings",
  "JellyfinInstance",
  "MetadataSettings",
  "NamingSettings",
  "PathRewrite",
  "PermissionSettings",
  "PlexInstance",
  "ProfileOverlay",
  "RadarrInstance",
  "RoutingRule",
  "ScanPath",
  "Services",
  "SmaConfig",
  "SonarrInstance",
  "SubliminalAuth",
  "SubliminalSettings",
  "SubtitleSettings",
  "SubtitleSorting",
  "UniversalAudio",
  "VideoSettings",
]
