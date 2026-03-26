"""Tests for resources/naming.py - template-based file naming engine."""
import os
import pytest
from unittest.mock import MagicMock, patch

from resources.naming import (
    NamingData, apply_template, rename_file, sanitize_filename,
    generate_name, DEFAULT_TV_TEMPLATE, DEFAULT_MOVIE_TEMPLATE,
)
from resources.metadata import update_plexmatch, MediaType
from converter.ffmpeg import MediaInfo


class TestSanitizeFilename:
    def test_removes_unsafe_chars(self):
        assert sanitize_filename('movie: the "sequel"') == 'movie the sequel'

    def test_removes_path_separators(self):
        assert sanitize_filename('a/b\\c') == 'abc'

    def test_strips_dots_and_spaces(self):
        assert sanitize_filename('  title. ') == 'title'

    def test_preserves_normal_chars(self):
        assert sanitize_filename('The Matrix (1999)') == 'The Matrix (1999)'


class TestApplyTemplate:
    def _tv_data(self, **overrides):
        d = NamingData()
        d.series_title = 'Doc (US)'
        d.series_year = '2025'
        d.series_titleyear = 'Doc (US) (2025)'
        d.season = 2
        d.episode = 18
        d.episode_title = 'Orientation'
        d.episode_cleantitle = 'Orientation'
        d.quality = '1080p'
        d.quality_full = 'HDTV-1080p'
        d.source = 'HDTV'
        d.video_codec = 'x265'
        d.audio_codec = 'EAC3'
        d.audio_channels = '5.1'
        d.hdr = ''
        d.release_group = 'MeGusta'
        for k, v in overrides.items():
            setattr(d, k, v)
        return d

    def _movie_data(self, **overrides):
        d = NamingData()
        d.movie_title = 'The Matrix'
        d.movie_cleantitle = 'The Matrix'
        d.movie_year = '1999'
        d.quality = '1080p'
        d.quality_full = 'BluRay-1080p'
        d.source = 'BluRay'
        d.video_codec = 'x265'
        d.audio_codec = 'DTS-HD MA'
        d.audio_channels = '7.1'
        d.hdr = ''
        d.release_group = 'FGT'
        for k, v in overrides.items():
            setattr(d, k, v)
        return d

    def test_tv_default_template(self):
        data = self._tv_data()
        result = apply_template(DEFAULT_TV_TEMPLATE, data)
        assert 'Doc (US) (2025)' in result
        assert 'S02E18' in result
        assert 'Orientation' in result
        assert 'HDTV-1080p' in result
        assert 'EAC3 5.1' in result
        assert 'x265' in result
        assert '-MeGusta' in result

    def test_movie_default_template(self):
        data = self._movie_data()
        result = apply_template(DEFAULT_MOVIE_TEMPLATE, data)
        assert 'The Matrix' in result
        assert '1999' in result
        assert 'BluRay-1080p' in result
        assert 'DTS-HD MA 7.1' in result
        assert 'x265' in result
        assert '-FGT' in result

    def test_zero_padded_season_episode(self):
        data = self._tv_data(season=1, episode=5)
        result = apply_template('S{season:00}E{episode:00}', data)
        assert result == 'S01E05'

    def test_optional_release_group_present(self):
        data = self._tv_data(release_group='LOL')
        result = apply_template('{Series TitleYear}{-ReleaseGroup}', data)
        assert result == 'Doc (US) (2025)-LOL'

    def test_optional_release_group_absent(self):
        data = self._tv_data(release_group='')
        result = apply_template('{Series TitleYear}{-ReleaseGroup}', data)
        assert result == 'Doc (US) (2025)'

    def test_bracket_optional_present(self):
        data = self._tv_data()
        result = apply_template('{[Quality Full]}', data)
        assert result == '[HDTV-1080p]'

    def test_bracket_optional_absent(self):
        data = self._tv_data(quality_full='')
        result = apply_template('{[Quality Full]}', data)
        assert result == ''

    def test_hdr_token_present(self):
        data = self._tv_data(hdr='HDR')
        result = apply_template('{[VideoDynamicRangeType]}', data)
        assert result == '[HDR]'

    def test_hdr_token_absent(self):
        data = self._tv_data(hdr='')
        result = apply_template('{[VideoDynamicRangeType]}', data)
        assert result == ''

    def test_full_example_output(self):
        """Verify the exact example from the requirements."""
        data = self._tv_data()
        template = '{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Quality Full}][{AudioCodec} {AudioChannels}][{VideoCodec}]{-ReleaseGroup}'
        result = apply_template(template, data)
        expected = 'Doc (US) (2025) - S02E18 - Orientation [HDTV-1080p][EAC3 5.1][x265]-MeGusta'
        assert result == expected

    def test_cleans_double_spaces(self):
        data = self._tv_data(episode_cleantitle='')
        result = apply_template('{Series TitleYear} - {Episode CleanTitle} test', data)
        assert '  ' not in result

    def test_sanitizes_result(self):
        data = self._tv_data(episode_cleantitle='What: "Really"?')
        result = apply_template('{Episode CleanTitle}', data)
        assert ':' not in result
        assert '"' not in result


class TestNamingDataFromMediaInfo:
    def test_video_codec_mapping(self, make_media_info):
        info = make_media_info(video_codec='h264', video_width=1920)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.video_codec == 'x264'
        assert data.quality == '1080p'

    def test_audio_codec_mapping(self, make_media_info):
        info = make_media_info(audio_codec='eac3', audio_channels=6)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.audio_codec == 'EAC3'
        assert data.audio_channels == '5.1'

    def test_4k_quality(self, make_media_info):
        info = make_media_info(video_width=3840)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.quality == '4K'

    def test_720p_quality(self, make_media_info):
        info = make_media_info(video_width=1280)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.quality == '720p'

    def test_guessit_source(self, make_media_info):
        info = make_media_info()
        data = NamingData()
        data.from_mediainfo(info, guess_data={'source': 'bluray', 'release_group': 'FGT'})
        assert data.source == 'BluRay'
        assert data.release_group == 'FGT'


class TestNamingDataFromTagdata:
    def test_tv_metadata(self):
        tagdata = MagicMock()
        tagdata.mediatype = MagicMock()
        tagdata.mediatype.name = 'TV'
        # Make MediaType.TV comparison work
        from resources.metadata import MediaType
        tagdata.mediatype = MediaType.TV
        tagdata.showname = 'Breaking Bad'
        tagdata.showdata = {'first_air_date': '2008-01-20'}
        tagdata.season = 3
        tagdata.episode = 10
        tagdata.title = 'Fly'

        data = NamingData()
        data.from_tagdata(tagdata)
        assert data.series_title == 'Breaking Bad'
        assert data.series_year == '2008'
        assert data.series_titleyear == 'Breaking Bad (2008)'
        assert data.season == 3
        assert data.episode == 10
        assert data.episode_cleantitle == 'Fly'

    def test_movie_metadata(self):
        from resources.metadata import MediaType
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.Movie
        tagdata.title = 'The Matrix'
        tagdata.date = '1999-03-31'

        data = NamingData()
        data.from_tagdata(tagdata)
        assert data.movie_title == 'The Matrix'
        assert data.movie_cleantitle == 'The Matrix'
        assert data.movie_year == '1999'


class TestRenameFile:
    def test_rename_success(self, tmp_path):
        src = tmp_path / 'old_name.mp4'
        src.touch()
        result = rename_file(str(src), 'new_name')
        assert os.path.basename(result) == 'new_name.mp4'
        assert os.path.exists(result)
        assert not os.path.exists(str(src))

    def test_preserves_extension(self, tmp_path):
        src = tmp_path / 'file.mkv'
        src.touch()
        result = rename_file(str(src), 'renamed')
        assert result.endswith('.mkv')

    def test_no_change_if_same_name(self, tmp_path):
        src = tmp_path / 'same.mp4'
        src.touch()
        result = rename_file(str(src), 'same')
        assert result == str(src)

    def test_no_overwrite_existing(self, tmp_path):
        src = tmp_path / 'file1.mp4'
        existing = tmp_path / 'file2.mp4'
        src.touch()
        existing.touch()
        result = rename_file(str(src), 'file2')
        # Should NOT overwrite, returns original
        assert result == str(src)


class TestGenerateName:
    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_disabled_returns_none(self, mock_validate, make_media_info):
        settings = MagicMock()
        settings.naming_enabled = False
        result = generate_name('/file.mp4', make_media_info(), None, settings)
        assert result is None

    @patch('resources.readsettings.ReadSettings._validate_binaries')
    def test_generates_tv_name(self, mock_validate, make_media_info):
        from resources.metadata import MediaType
        settings = MagicMock()
        settings.naming_enabled = True
        settings.naming_tv_template = DEFAULT_TV_TEMPLATE
        settings.sonarr_instances = []

        tagdata = MagicMock()
        tagdata.mediatype = MediaType.TV
        tagdata.showname = 'Test Show'
        tagdata.showdata = {'first_air_date': '2020-01-01'}
        tagdata.season = 1
        tagdata.episode = 1
        tagdata.title = 'Pilot'

        info = make_media_info(video_codec='hevc', video_width=1920, audio_codec='aac', audio_channels=2)
        result = generate_name('/tv/test.mp4', info, tagdata, settings)
        assert result is not None
        assert 'Test Show' in result
        assert 'S01E01' in result
        assert 'Pilot' in result


class TestPlexmatch:
    """Test .plexmatch file generation."""

    def _tv_tagdata(self):
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.TV
        tagdata.showname = 'Detectorists'
        tagdata.showdata = {'first_air_date': '2014-10-02'}
        tagdata.season = 1
        tagdata.episode = 3
        tagdata.title = 'Episode 3'
        tagdata.tmdbid = '61855'
        tagdata.tvdbid = '280847'
        tagdata.imdbid = 'tt4082744'
        return tagdata

    def _movie_tagdata(self):
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.Movie
        tagdata.title = 'The Matrix'
        tagdata.date = '1999-03-31'
        tagdata.tmdbid = '603'
        return tagdata

    def _settings(self, enabled=True):
        s = MagicMock()
        s.plexmatch_enabled = enabled
        return s

    def test_tv_creates_plexmatch(self, tmp_path):
        show_dir = tmp_path / 'Detectorists'
        season_dir = show_dir / 'Season 01'
        season_dir.mkdir(parents=True)
        ep_file = season_dir / 'Detectorists S01E03.mp4'
        ep_file.touch()

        update_plexmatch(str(ep_file), self._tv_tagdata(), self._settings())

        plexmatch = show_dir / '.plexmatch'
        assert plexmatch.exists()
        content = plexmatch.read_text()
        assert 'title: Detectorists' in content
        assert 'year: 2014' in content
        assert 'TvdbId: 280847' in content
        assert 'ImdbId: tt4082744' in content
        assert 'Episode: S01E03:' in content

    def test_tv_accumulates_episodes(self, tmp_path):
        show_dir = tmp_path / 'Show'
        s1_dir = show_dir / 'Season 01'
        s1_dir.mkdir(parents=True)

        # First episode
        ep1 = s1_dir / 'ep01.mp4'
        ep1.touch()
        td1 = self._tv_tagdata()
        td1.season = 1
        td1.episode = 1
        update_plexmatch(str(ep1), td1, self._settings())

        # Second episode
        ep2 = s1_dir / 'ep02.mp4'
        ep2.touch()
        td2 = self._tv_tagdata()
        td2.season = 1
        td2.episode = 2
        update_plexmatch(str(ep2), td2, self._settings())

        content = (show_dir / '.plexmatch').read_text()
        assert 'Episode: S01E01:' in content
        assert 'Episode: S01E02:' in content

    def test_tv_episodes_sorted(self, tmp_path):
        show_dir = tmp_path / 'Show'
        s_dir = show_dir / 'Season 02'
        s_dir.mkdir(parents=True)

        for ep_num in [5, 2, 8]:
            f = s_dir / ('ep%02d.mp4' % ep_num)
            f.touch()
            td = self._tv_tagdata()
            td.season = 2
            td.episode = ep_num
            update_plexmatch(str(f), td, self._settings())

        lines = (show_dir / '.plexmatch').read_text().strip().split('\n')
        ep_lines = [l for l in lines if l.startswith('Episode:')]
        keys = [l.split(':')[1].strip() for l in ep_lines]
        assert keys == sorted(keys)

    def test_movie_creates_plexmatch(self, tmp_path):
        movie_dir = tmp_path / 'The Matrix (1999)'
        movie_dir.mkdir()
        movie_file = movie_dir / 'The Matrix.mp4'
        movie_file.touch()

        update_plexmatch(str(movie_file), self._movie_tagdata(), self._settings())

        plexmatch = movie_dir / '.plexmatch'
        assert plexmatch.exists()
        content = plexmatch.read_text()
        assert 'title: The Matrix' in content
        assert 'year: 1999' in content
        assert 'guid: tmdb://603' in content

    def test_disabled_no_file(self, tmp_path):
        movie_dir = tmp_path / 'Movie'
        movie_dir.mkdir()
        f = movie_dir / 'movie.mp4'
        f.touch()

        update_plexmatch(str(f), self._movie_tagdata(), self._settings(enabled=False))
        assert not (movie_dir / '.plexmatch').exists()

    def test_no_tagdata_no_file(self, tmp_path):
        movie_dir = tmp_path / 'Movie'
        movie_dir.mkdir()
        f = movie_dir / 'movie.mp4'
        f.touch()

        update_plexmatch(str(f), None, self._settings())
        assert not (movie_dir / '.plexmatch').exists()


class TestGetQualityLabel:
    from resources.naming import _get_quality_label

    def test_8k(self):
        from resources.naming import _get_quality_label
        assert _get_quality_label(7680) == '8K'

    def test_4k(self):
        from resources.naming import _get_quality_label
        assert _get_quality_label(3840) == '4K'

    def test_1080p(self):
        from resources.naming import _get_quality_label
        assert _get_quality_label(1920) == '1080p'

    def test_720p(self):
        from resources.naming import _get_quality_label
        assert _get_quality_label(1280) == '720p'

    def test_sd(self):
        from resources.naming import _get_quality_label
        assert _get_quality_label(640) == 'SD'

    def test_zero(self):
        from resources.naming import _get_quality_label
        assert _get_quality_label(0) == 'SD'


class TestGetSource:
    def test_known_source(self):
        from resources.naming import _get_source
        assert _get_source({'source': 'bluray'}) == 'BluRay'

    def test_unknown_source_passthrough(self):
        from resources.naming import _get_source
        assert _get_source({'source': 'unknown'}) == 'unknown'

    def test_empty_source(self):
        from resources.naming import _get_source
        assert _get_source({}) == ''

    def test_webrip(self):
        from resources.naming import _get_source
        assert _get_source({'source': 'webrip'}) == 'WEBRip'


class TestGetReleaseGroup:
    def test_present(self):
        from resources.naming import _get_release_group
        assert _get_release_group({'release_group': 'FGT'}) == 'FGT'

    def test_absent(self):
        from resources.naming import _get_release_group
        assert _get_release_group({}) == ''


class TestApplyTemplateFormatSpec:
    def test_zero_pad_three_digits(self):
        data = NamingData()
        data.season = 1
        data.episode = 5
        result = apply_template('S{season:000}E{episode:000}', data)
        assert result == 'S001E005'

    def test_truncation(self):
        data = NamingData()
        data.movie_title = 'A Very Long Title'
        result = apply_template('{Movie Title:10}', data)
        assert result == 'A Very Lon'


class TestNamingDataFromTagdataEdgeCases:
    def test_none_tagdata(self):
        data = NamingData()
        data.from_tagdata(None)
        assert data.series_title == ''

    def test_tv_no_year(self):
        from resources.metadata import MediaType
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.TV
        tagdata.showname = 'Show'
        tagdata.showdata = {}
        tagdata.season = 1
        tagdata.episode = 1
        tagdata.title = 'Pilot'
        data = NamingData()
        data.from_tagdata(tagdata)
        assert data.series_titleyear == 'Show'

    def test_movie_no_date(self):
        from resources.metadata import MediaType
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.Movie
        tagdata.title = 'Movie'
        tagdata.date = ''
        data = NamingData()
        data.from_tagdata(tagdata)
        assert data.movie_year == ''


class TestNamingDataFromMediaInfoEdgeCases:
    def test_no_video(self):
        info = MediaInfo()
        data = NamingData()
        data.from_mediainfo(info)
        assert data.video_codec == ''
        assert data.quality == ''

    def test_no_audio(self, make_stream):
        info = MediaInfo()
        v = make_stream(type='video', codec='h264', video_width=1920)
        v.framedata = {}
        info.streams.append(v)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.audio_codec == ''

    def test_atmos_detection(self, make_stream):
        info = MediaInfo()
        v = make_stream(type='video', codec='h264', video_width=1920)
        v.framedata = {}
        info.streams.append(v)
        a = make_stream(type='audio', codec='eac3', index=1, audio_channels=8, audio_samplerate=48000, profile='Atmos')
        info.streams.append(a)
        data = NamingData()
        data.from_mediainfo(info)
        assert 'Atmos' in data.audio_codec

    def test_dts_hd_ma_detection(self, make_stream):
        info = MediaInfo()
        v = make_stream(type='video', codec='h264', video_width=1920)
        v.framedata = {}
        info.streams.append(v)
        a = make_stream(type='audio', codec='dts', index=1, audio_channels=8, audio_samplerate=48000, profile='DTS-HD MA')
        info.streams.append(a)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.audio_codec == 'DTS-HD MA'

    def test_hdr_detection(self, make_stream):
        info = MediaInfo()
        v = make_stream(type='video', codec='h265', video_width=3840)
        v.framedata = {'color_transfer': 'smpte2084'}
        v.color = {}
        info.streams.append(v)
        data = NamingData()
        data.from_mediainfo(info)
        assert data.hdr == 'HDR'


class TestRenameFileEdgeCases:
    def test_oserror_returns_original(self, tmp_path):
        src = tmp_path / 'file.mp4'
        src.touch()
        with patch('os.rename', side_effect=OSError("permission denied")):
            result = rename_file(str(src), 'new_name')
        assert result == str(src)


class TestNamingDataFromArrApi:
    def test_no_requests_returns_false(self):
        data = NamingData()
        with patch('resources.naming._requests', None):
            assert data._from_arr_api(None, '/file.mkv', 'sonarr', MagicMock()) is False

    def test_no_apikey_returns_false(self):
        data = NamingData()
        assert data._from_arr_api({'host': 'localhost', 'port': 8989}, '/file.mkv', 'sonarr', MagicMock()) is False

    @patch('resources.naming._requests')
    def test_sonarr_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'series': {'title': 'My Show', 'year': 2023},
            'episodes': [{'seasonNumber': 1, 'episodeNumber': 5, 'title': 'The One'}],
            'quality': {'quality': {'resolution': '1080p', 'source': 'HDTV'}},
            'releaseGroup': 'LOL'
        }
        mock_requests.get.return_value = mock_resp
        data = NamingData()
        result = data._from_arr_api(
            {'host': 'localhost', 'port': 8989, 'apikey': 'key'},
            '/tv/show.mkv', 'sonarr', MagicMock()
        )
        assert result is True
        assert data.series_title == 'My Show'
        assert data.episode == 5

    @patch('resources.naming._requests')
    def test_radarr_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'movie': {'title': 'Movie Title', 'year': 2020},
            'quality': {'quality': {'resolution': '4K', 'source': 'BluRay'}},
            'releaseGroup': 'FGT'
        }
        mock_requests.get.return_value = mock_resp
        data = NamingData()
        result = data._from_arr_api(
            {'host': 'localhost', 'port': 7878, 'apikey': 'key'},
            '/movies/film.mkv', 'radarr', MagicMock()
        )
        assert result is True
        assert data.movie_title == 'Movie Title'
