"""Tests for resources/readsettings.py - configuration parsing."""
import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resources.readsettings import SMAConfigParser, ReadSettings


class TestSMAConfigParserGetList:
    """Test getlist method for parsing comma-separated values."""

    def _make_parser(self, section, option, value):
        p = SMAConfigParser()
        p.add_section(section)
        p.set(section, option, value)
        return p

    def test_basic_list(self):
        p = self._make_parser('test', 'items', 'a, b, c')
        result = p.getlist('test', 'items')
        assert result == ['a', 'b', 'c']

    def test_empty_string_returns_default(self):
        p = self._make_parser('test', 'items', '')
        result = p.getlist('test', 'items', default=['fallback'])
        assert result == ['fallback']

    def test_single_item(self):
        p = self._make_parser('test', 'items', 'single')
        result = p.getlist('test', 'items')
        assert result == ['single']

    def test_lowercase_by_default(self):
        p = self._make_parser('test', 'items', 'AAC, AC3')
        result = p.getlist('test', 'items')
        assert result == ['aac', 'ac3']

    def test_preserve_case(self):
        p = self._make_parser('test', 'items', 'AAC, AC3')
        result = p.getlist('test', 'items', lower=False)
        assert result == ['AAC', 'AC3']

    def test_custom_separator(self):
        p = self._make_parser('test', 'items', 'a|b|c')
        result = p.getlist('test', 'items', separator='|')
        assert result == ['a', 'b', 'c']

    def test_strip_spaces(self):
        p = self._make_parser('test', 'items', '  a  ,  b  ,  c  ')
        result = p.getlist('test', 'items')
        assert result == ['a', 'b', 'c']


class TestSMAConfigParserGetDict:
    """Test getdict method for parsing key:value pairs."""

    def _make_parser(self, section, option, value):
        p = SMAConfigParser()
        p.add_section(section)
        p.set(section, option, value)
        return p

    def test_basic_dict(self):
        p = self._make_parser('test', 'mapping', 'qsv:/dev/dri/renderD128')
        result = p.getdict('test', 'mapping', lower=False, replace=[])
        assert result == {'qsv': '/dev/dri/renderD128'}

    def test_multiple_entries(self):
        p = self._make_parser('test', 'mapping', 'hevc:1.0, h264:0.65')
        result = p.getdict('test', 'mapping')
        assert result == {'hevc': '1.0', 'h264': '0.65'}

    def test_value_modifier(self):
        p = self._make_parser('test', 'mapping', 'hevc:1.0, h264:0.65')
        result = p.getdict('test', 'mapping', valueModifier=float)
        assert result['hevc'] == pytest.approx(1.0)
        assert result['h264'] == pytest.approx(0.65)

    def test_empty_returns_default(self):
        p = self._make_parser('test', 'mapping', '')
        result = p.getdict('test', 'mapping', default={'key': 'val'})
        assert result == {'key': 'val'}

    def test_invalid_format_skipped(self):
        """Values without separator are skipped."""
        p = self._make_parser('test', 'mapping', 'just_a_value')
        result = p.getdict('test', 'mapping')
        assert result == {}

    def test_custom_separators(self):
        p = self._make_parser('test', 'mapping', '/downloads=/mnt/unionfs/downloads')
        result = p.getdict('test', 'mapping', dictseparator='=', lower=False, replace=[])
        assert result == {'/downloads': '/mnt/unionfs/downloads'}

    def test_hwaccel_output_format_dict(self):
        """Verify hwaccel-output-format parses correctly in dict format."""
        p = self._make_parser('test', 'fmt', 'qsv:qsv')
        result = p.getdict('test', 'fmt')
        assert result == {'qsv': 'qsv'}

    def test_hwaccel_output_format_bare_fails(self):
        """Bare value (no colon) should produce empty dict."""
        p = self._make_parser('test', 'fmt', 'qsv')
        result = p.getdict('test', 'fmt')
        assert result == {}


class TestReadSettingsMultiInstance:
    """Test multi-instance Sonarr/Radarr discovery."""

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_discovers_sonarr_instances(self, mock_validate, tmp_ini):
        ini = tmp_ini("""[Converter]
ffmpeg = ffmpeg
ffprobe = ffprobe
threads = 0
gpu =
hwaccels =
hwaccel-decoders =
hwdevices =
hwaccel-output-format =
output-directory =
output-format = mp4
output-extension = mp4
temp-extension =
minimum-size = 0
ignored-extensions =
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
regex-directory-replace = x
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
codec = h265
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
space =
transfer =
primaries =
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
sorting = language
default-sorting = channels.d
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
sorting = language
codecs =
burn-sorting = language

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
host = sonarr.local
port = 8989
apikey = abc123
ssl = false
webroot =
path = /tv
force-rename = false
rescan = true
block-reprocess = false
in-progress-check = true

[Sonarr-Kids]
host = sonarr-kids.local
port = 8989
apikey = def456
ssl = false
webroot =
path = /tv-kids
force-rename = false
rescan = true
block-reprocess = false
in-progress-check = true

[Radarr]
host = radarr.local
port = 7878
apikey = ghi789
ssl = false
webroot =
path = /movies
force-rename = false
rescan = true
block-reprocess = false
in-progress-check = true

[Radarr-4K]
host = radarr-4k.local
port = 7878
apikey = jkl012
ssl = false
webroot =
path = /movies/4k
force-rename = false
rescan = true
block-reprocess = false
in-progress-check = true

[SABNZBD]
convert = true
sonarr-category = sonarr
radarr-category = radarr
bypass-category = bypass
output-directory =
path-mapping =

[Deluge]


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
""")
        settings = ReadSettings(ini)
        assert len(settings.sonarr_instances) == 2
        assert len(settings.radarr_instances) == 2

        sonarr_sections = [i['section'] for i in settings.sonarr_instances]
        assert 'Sonarr' in sonarr_sections
        assert 'Sonarr-Kids' in sonarr_sections

        radarr_sections = [i['section'] for i in settings.radarr_instances]
        assert 'Radarr' in radarr_sections
        assert 'Radarr-4K' in radarr_sections

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_sorted_by_path_length(self, mock_validate, tmp_ini):
        """Instances should be sorted longest-path-first."""
        ini = tmp_ini()
        settings = ReadSettings(ini)
        for instances in [settings.sonarr_instances, settings.radarr_instances]:
            paths = [i.get('path', '') for i in instances]
            path_lens = [len(p) for p in paths]
            assert path_lens == sorted(path_lens, reverse=True)

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_backward_compat(self, mock_validate, tmp_ini):
        """self.Sonarr and self.Radarr should still reference base instances."""
        ini = tmp_ini()
        settings = ReadSettings(ini)
        assert settings.Sonarr.get('host') == 'localhost'
        assert settings.Radarr.get('host') == 'localhost'


class TestGpuProfile:
    """Test gpu shorthand auto-derives all HW acceleration settings."""

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_qsv_profile(self, mock_validate, tmp_ini):
        settings = ReadSettings(tmp_ini(gpu='qsv'))
        assert settings.gpu == 'qsv'
        assert 'qsv' in settings.hwaccels
        assert 'hevc_qsv' in settings.hwaccel_decoders
        assert 'h264_qsv' in settings.hwaccel_decoders
        assert settings.hwdevices.get('qsv') == '/dev/dri/renderD128'
        assert settings.hwoutputfmt.get('qsv') == 'qsv'

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_nvenc_profile(self, mock_validate, tmp_ini):
        settings = ReadSettings(tmp_ini(gpu='nvenc'))
        assert settings.gpu == 'nvenc'
        assert 'cuda' in settings.hwaccels
        assert 'hevc_cuvid' in settings.hwaccel_decoders
        assert 'h264_cuvid' in settings.hwaccel_decoders
        assert settings.hwoutputfmt.get('cuda') == 'cuda'

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_vaapi_profile(self, mock_validate, tmp_ini):
        settings = ReadSettings(tmp_ini(gpu='vaapi'))
        assert settings.gpu == 'vaapi'
        assert 'vaapi' in settings.hwaccels
        assert 'hevc_vaapi' in settings.hwaccel_decoders
        assert settings.hwdevices.get('vaapi') == '/dev/dri/renderD128'
        assert settings.hwoutputfmt.get('vaapi') == 'vaapi'

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_videotoolbox_profile(self, mock_validate, tmp_ini):
        settings = ReadSettings(tmp_ini(gpu='videotoolbox'))
        assert settings.gpu == 'videotoolbox'
        assert 'videotoolbox' in settings.hwaccels

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_no_gpu(self, mock_validate, tmp_ini):
        """Empty gpu should not populate any HW settings."""
        settings = ReadSettings(tmp_ini())
        assert settings.gpu == ''
        assert settings.hwaccels == []
        assert settings.hwaccel_decoders == []

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_explicit_override_preserved(self, mock_validate, tmp_ini):
        """If user explicitly sets hwaccels alongside gpu, the explicit value wins."""
        ini = tmp_ini(gpu='qsv')
        with open(ini, 'r') as f:
            content = f.read()
        content = content.replace('hwaccels =\n', 'hwaccels = cuda\n')
        with open(ini, 'w') as f:
            f.write(content)
        settings = ReadSettings(ini)
        assert 'cuda' in settings.hwaccels

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_codec_mapping_qsv(self, mock_validate, tmp_ini):
        """gpu=qsv should map hevc→h265qsv with software fallback."""
        ini = tmp_ini(gpu='qsv')
        with open(ini, 'r') as f:
            content = f.read()
        content = content.replace('codec = h265, h264', 'codec = hevc, h264')
        with open(ini, 'w') as f:
            f.write(content)

        settings = ReadSettings(ini)
        assert 'h265qsv' in settings.vcodec
        assert 'hevc' in settings.vcodec
        assert 'h264qsv' in settings.vcodec
        assert 'h264' in settings.vcodec
        assert settings.vcodec.index('h265qsv') < settings.vcodec.index('hevc')

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_codec_mapping_vaapi(self, mock_validate, tmp_ini):
        """gpu=vaapi should map hevc→h265vaapi."""
        ini = tmp_ini(gpu='vaapi')
        with open(ini, 'r') as f:
            content = f.read()
        content = content.replace('codec = h265, h264', 'codec = hevc')
        with open(ini, 'w') as f:
            f.write(content)

        settings = ReadSettings(ini)
        assert 'h265vaapi' in settings.vcodec
        assert 'hevc' in settings.vcodec


class TestValidateBinaries:
    """Test FFmpeg/FFprobe binary validation."""

    @patch('shutil.which', return_value='/usr/bin/ffmpeg')
    def test_valid_binary_in_path(self, mock_which, tmp_ini):
        """Should not exit when binary is found via which."""
        ini = tmp_ini()
        # Should not raise
        ReadSettings(ini)

    @patch('shutil.which', return_value=None)
    @patch('os.path.isfile', return_value=False)
    def test_missing_binary_exits(self, mock_isfile, mock_which, tmp_ini):
        """Should sys.exit when binary not found."""
        ini = tmp_ini()
        with pytest.raises(SystemExit):
            ReadSettings(ini)
