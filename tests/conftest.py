"""Shared fixtures for SMA-NG test suite."""
import os
import sys
import tempfile
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.ffmpeg import MediaStreamInfo, MediaFormatInfo, MediaInfo


@pytest.fixture
def make_stream():
    """Factory fixture for creating MediaStreamInfo objects."""
    def _make(type='video', codec='h264', index=0, **kwargs):
        s = MediaStreamInfo()
        s.type = type
        s.codec = codec
        s.index = index
        s.metadata = kwargs.pop('metadata', {'language': 'eng'})
        s.disposition = kwargs.pop('disposition', {'default': True, 'forced': False})
        for k, v in kwargs.items():
            setattr(s, k, v)
        return s
    return _make


@pytest.fixture
def make_format():
    """Factory fixture for creating MediaFormatInfo objects."""
    def _make(**kwargs):
        f = MediaFormatInfo()
        f.format = kwargs.get('format', 'matroska,webm')
        f.bitrate = kwargs.get('bitrate', 10000000.0)
        f.duration = kwargs.get('duration', 7200.0)
        return f
    return _make


@pytest.fixture
def make_media_info(make_stream, make_format):
    """Factory fixture for creating MediaInfo objects with sensible defaults."""
    def _make(video_codec='h264', video_bitrate=8000000, video_width=1920, video_height=1080,
              audio_codec='aac', audio_channels=2, audio_bitrate=128000,
              subtitle_codec=None, total_bitrate=10000000):
        info = MediaInfo()
        info.format = make_format(bitrate=total_bitrate)

        video = make_stream(
            type='video', codec=video_codec, index=0,
            bitrate=video_bitrate, video_width=video_width, video_height=video_height,
            fps=23.976, pix_fmt='yuv420p', profile='main', video_level=4.1,
            field_order='progressive',
            metadata={}, disposition={'default': True, 'forced': False}
        )
        video.framedata = {}
        info.streams.append(video)

        audio = make_stream(
            type='audio', codec=audio_codec, index=1,
            bitrate=audio_bitrate, audio_channels=audio_channels, audio_samplerate=48000,
            metadata={'language': 'eng'}, disposition={'default': True, 'forced': False}
        )
        info.streams.append(audio)

        if subtitle_codec:
            sub = make_stream(
                type='subtitle', codec=subtitle_codec, index=2,
                metadata={'language': 'eng'}, disposition={'default': False, 'forced': False}
            )
            info.streams.append(sub)

        return info
    return _make


@pytest.fixture
def tmp_ini(tmp_path):
    """Create a temporary autoProcess.ini with minimal valid config."""
    def _make(content=None):
        if content is None:
            content = """[Converter]
ffmpeg = ffmpeg
ffprobe = ffprobe
threads = 0
hwaccel =
hwaccels =
hwaccel-decoders =
hwdevices =
hwaccel-output-format =
output-directory =
output-format = mp4
output-extension = mp4
temp-extension =
minimum-size = 0
ignored-extensions = nfo, ds_store
copy-to =
move-to =
delete-original = true
process-same-extensions = false
bypass-if-copying-all = false
force-convert = false
post-process = false
wait-post-process = false
detailed-progress = false
opts-separator = ,
preopts =
postopts =
regex-directory-replace = [^\\w\\-_\\. ]
output-directory-space-ratio = 0.0

[Permissions]
chmod = 0664
uid = -1
gid = -1

[Metadata]
relocate-moov = true
full-path-guess = true
tag = true
tag-language = eng
download-artwork = false
sanitize-disposition =
strip-metadata = true
keep-titles = false

[Video]
codec = h265, h264
max-bitrate = 0
preset = medium
dynamic-parameters = false
profile =
prioritize-source-pix-fmt = true
crf = 23
max-width = 0
pix-fmt =
max-level = 0
filter =
force-filter = false
crf-profiles =
bitrate-ratio =
codec-parameters =

[HDR]
codec =
pix-fmt =
space = bt2020nc
transfer = smpte2084
primaries = bt2020
preset =
codec-parameters =
filter =
force-filter = false
profile =

[Audio]
codec = aac
languages =
default-language = eng
first-stream-of-language = false
allow-language-relax = true
channel-bitrate = 128
variable-bitrate = 0
max-bitrate = 0
max-channels = 0
filter =
profile =
force-filter = false
sample-rates =
sample-format =
copy-original = false
aac-adtstoasc = true
ignored-dispositions =
unique-dispositions = false
stream-codec-combinations =
ignore-trudhd = true
relax-to-default = false
force-default = false
include-original-language = false
atmos-force-copy = false

[Audio.Sorting]
sorting = language, channels.d, map, d.comment
default-sorting = channels.d, map, d.comment
codecs =

[Universal Audio]
codec =
channel-bitrate = 128
variable-bitrate = 0
first-stream-only = true
filter =
profile =
force-filter = false

[Audio.ChannelFilters]

[Naming]
enabled = false
tv-template = {Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}
movie-template = {Movie CleanTitle} ({Release Year}) [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}

[Subtitle]
codec = mov_text
codec-image-based =
languages =
default-language = eng
first-stream-of-language = false
encoding =
burn-subtitles = false
burn-dispositions =
embed-subs = true
embed-image-subs = false
embed-only-internal-subs = false
filename-dispositions =
ignore-embedded-subs = false
ignored-dispositions =
unique-dispositions = false
attachment-codec =
remove-bitstream-subs = true
force-default = false
include-original-language = false

[Subtitle.Sorting]
sorting = language, d.forced.d, d.comment, d.default.d
codecs =
burn-sorting = language, d.forced.d, d.comment, d.default.d

[Subtitle.CleanIt]
enabled = false
config-path =
tags = default

[Subtitle.Subliminal]
download-subs = false
download-hearing-impaired-subs = false
providers =
download-forced-subs = false
include-hearing-impaired-subs = false

[Subtitle.Subliminal.Auth]

[Subtitle.FFSubsync]
enabled = false

[Sonarr]
host = localhost
port = 8989
apikey =
ssl = false
webroot =
path = /tv
force-rename = false
rescan = true
block-reprocess = false
in-progress-check = true

[Radarr]
host = localhost
port = 7878
apikey =
ssl = false
webroot =
path = /movies
force-rename = false
rescan = true
block-reprocess = false
in-progress-check = true

[Sickbeard]
host = localhost
port = 8081
ssl = false
apikey =
webroot =
username =
password =

[Sickrage]
host = localhost
port = 8081
ssl = false
apikey =
webroot =
username =
password =

[SABNZBD]
convert = true
sickbeard-category = sickbeard
sickrage-category = sickrage
sonarr-category = sonarr
radarr-category = radarr
bypass-category = bypass
output-directory =
path-mapping =

[Deluge]
sickbeard-label =
sickrage-label =
sonarr-label = sonarr
radarr-label = radarr
bypass-label = bypass
convert = true
host = localhost
port = 58846
username =
password =
output-directory =
remove = false
path-mapping =

[qBittorrent]
sickbeard-label = sickbeard
sickrage-label = sickrage
sonarr-label = sonarr
radarr-label = radarr
bypass-label = bypass
convert = true
action-before =
action-after =
host = localhost
port = 8080
ssl = false
username =
password =
output-directory =
path-mapping =

[uTorrent]
sickbeard-label = sickbeard
sickrage-label = sickrage
sonarr-label = sonarr
radarr-label = radarr
bypass-label = bypass
convert = true
webui = false
action-before =
action-after =
host = localhost
ssl = false
port = 8080
username =
password =
output-directory =
path-mapping =

[Plex]
host = localhost
port = 32400
refresh = false
token =
username =
password =
servername =
ssl = false
ignore-certs = false
path-mapping =
plexmatch = true
"""
        ini_path = str(tmp_path / "autoProcess.ini")
        with open(ini_path, 'w') as f:
            f.write(content)
        return ini_path
    return _make


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database path."""
    return str(tmp_path / "test_daemon.db")
