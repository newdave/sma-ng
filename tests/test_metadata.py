"""Tests for resources/metadata.py - metadata tagging and TMDB integration."""
import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from resources.metadata import (
    Metadata, MediaType, TMDBIDError,
    update_plexmatch, _write_tv_plexmatch, _write_movie_plexmatch
)


class TestResolveTmdbID:
    def test_returns_int_tmdbid_directly(self):
        log = MagicMock()
        assert Metadata.resolveTmdbID(MediaType.Movie, log, tmdbid='603') == 603

    def test_returns_int_tmdbid_as_int(self):
        log = MagicMock()
        assert Metadata.resolveTmdbID(MediaType.Movie, log, tmdbid=603) == 603

    def test_invalid_tmdbid_logs_error(self):
        log = MagicMock()
        result = Metadata.resolveTmdbID(MediaType.Movie, log, tmdbid='not_a_number')
        log.error.assert_called()
        # Falls through after ValueError - returns the original tmdbid string
        assert result == 'not_a_number'

    @patch('resources.metadata.tmdb.Find')
    def test_movie_from_imdbid(self, mock_find_cls):
        log = MagicMock()
        mock_find = MagicMock()
        mock_find.movie_results = [{'id': 550}]
        mock_find_cls.return_value = mock_find
        result = Metadata.resolveTmdbID(MediaType.Movie, log, imdbid='tt0137523')
        assert result == 550

    @patch('resources.metadata.tmdb.Find')
    def test_movie_from_imdbid_no_prefix(self, mock_find_cls):
        log = MagicMock()
        mock_find = MagicMock()
        mock_find.movie_results = [{'id': 550}]
        mock_find_cls.return_value = mock_find
        result = Metadata.resolveTmdbID(MediaType.Movie, log, imdbid='0137523')
        mock_find_cls.assert_called_with('tt0137523')
        assert result == 550

    @patch('resources.metadata.tmdb.Find')
    def test_tv_from_imdbid(self, mock_find_cls):
        log = MagicMock()
        mock_find = MagicMock()
        mock_find.tv_results = [{'id': 1396}]
        mock_find_cls.return_value = mock_find
        result = Metadata.resolveTmdbID(MediaType.TV, log, imdbid='tt0903747')
        assert result == 1396

    @patch('resources.metadata.tmdb.Find')
    def test_tv_from_tvdbid(self, mock_find_cls):
        log = MagicMock()
        mock_find = MagicMock()
        mock_find.tv_results = [{'id': 1396}]
        mock_find_cls.return_value = mock_find
        result = Metadata.resolveTmdbID(MediaType.TV, log, tvdbid=81189)
        assert result == 1396

    @patch('resources.metadata.tmdb.Find')
    def test_tv_imdbid_fallback_to_tvdbid(self, mock_find_cls):
        log = MagicMock()
        mock_find_imdb = MagicMock()
        mock_find_imdb.tv_results = []
        mock_find_tvdb = MagicMock()
        mock_find_tvdb.tv_results = [{'id': 999}]
        mock_find_cls.side_effect = [mock_find_imdb, mock_find_tvdb]
        result = Metadata.resolveTmdbID(MediaType.TV, log, imdbid='tt0000001', tvdbid=12345)
        assert result == 999

    def test_no_ids_returns_none(self):
        log = MagicMock()
        result = Metadata.resolveTmdbID(MediaType.Movie, log)
        assert result is None


class TestMultiEpisodeMetadata:
    """Test multi-episode support in Metadata class."""

    @patch('resources.metadata.tmdb.TV_Episodes')
    @patch('resources.metadata.tmdb.TV_Seasons')
    @patch('resources.metadata.tmdb.TV')
    @patch('resources.metadata.Metadata.resolveTmdbID', return_value=1396)
    def test_single_episode_as_int(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
        mock_tv.return_value.info.return_value = {'name': 'Breaking Bad', 'genres': [], 'networks': [], 'original_language': 'en'}
        mock_tv.return_value.external_ids.return_value = {}
        mock_tv.return_value.content_ratings.return_value = {'results': []}
        mock_seasons.return_value.info.return_value = {'episodes': []}
        mock_episodes.return_value.info.return_value = {'name': 'Fly', 'overview': 'A fly...', 'air_date': '2010-05-23', 'episode_number': 10}
        mock_episodes.return_value.credits.return_value = {'cast': [], 'crew': []}

        m = Metadata(MediaType.TV, tmdbid=1396, season=3, episode=10)
        assert m.episode == 10
        assert m.episodes == [10]
        assert m.title == 'Fly'

    @patch('resources.metadata.tmdb.TV_Episodes')
    @patch('resources.metadata.tmdb.TV_Seasons')
    @patch('resources.metadata.tmdb.TV')
    @patch('resources.metadata.Metadata.resolveTmdbID', return_value=1396)
    def test_multi_episode_list(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
        mock_tv.return_value.info.return_value = {'name': 'Breaking Bad', 'genres': [], 'networks': [], 'original_language': 'en'}
        mock_tv.return_value.external_ids.return_value = {}
        mock_tv.return_value.content_ratings.return_value = {'results': []}
        mock_seasons.return_value.info.return_value = {'episodes': []}

        ep1_data = {'name': 'Pilot', 'overview': 'A teacher...', 'air_date': '2008-01-20', 'episode_number': 1}
        ep2_data = {'name': "Cat's in the Bag...", 'overview': 'Walt and Jesse...', 'air_date': '2008-01-27', 'episode_number': 2}

        call_count = [0]
        def episode_info(language=None):
            result = [ep1_data, ep2_data][call_count[0]]
            call_count[0] += 1
            return result

        mock_ep_instance = MagicMock()
        mock_ep_instance.info.side_effect = episode_info
        mock_ep_instance.credits.return_value = {'cast': [], 'crew': []}
        mock_episodes.return_value = mock_ep_instance

        m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=[1, 2])
        assert m.episode == 1
        assert m.episodes == [1, 2]
        assert 'Pilot' in m.title
        assert "Cat's in the Bag..." in m.title
        assert ' / ' in m.title
        assert 'A teacher...' in m.description
        assert 'Walt and Jesse...' in m.description

    @patch('resources.metadata.tmdb.TV_Episodes')
    @patch('resources.metadata.tmdb.TV_Seasons')
    @patch('resources.metadata.tmdb.TV')
    @patch('resources.metadata.Metadata.resolveTmdbID', return_value=1396)
    def test_multi_episode_string_list(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
        """Test that string episode numbers are converted to int."""
        mock_tv.return_value.info.return_value = {'name': 'Show', 'genres': [], 'networks': [], 'original_language': 'en'}
        mock_tv.return_value.external_ids.return_value = {}
        mock_tv.return_value.content_ratings.return_value = {'results': []}
        mock_seasons.return_value.info.return_value = {'episodes': []}
        mock_episodes.return_value.info.return_value = {'name': 'Ep', 'overview': '', 'air_date': '2020-01-01', 'episode_number': 1}
        mock_episodes.return_value.credits.return_value = {'cast': [], 'crew': []}

        m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=['3', '4'])
        assert m.episodes == [3, 4]
        assert m.episode == 3

    @patch('resources.metadata.tmdb.TV_Episodes')
    @patch('resources.metadata.tmdb.TV_Seasons')
    @patch('resources.metadata.tmdb.TV')
    @patch('resources.metadata.Metadata.resolveTmdbID', return_value=1396)
    def test_single_episode_backwards_compat(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
        """Ensure single-episode usage still works identically."""
        mock_tv.return_value.info.return_value = {'name': 'Show', 'genres': [], 'networks': [], 'original_language': 'en'}
        mock_tv.return_value.external_ids.return_value = {}
        mock_tv.return_value.content_ratings.return_value = {'results': []}
        mock_seasons.return_value.info.return_value = {'episodes': [{'name': 'E5'}]}
        mock_episodes.return_value.info.return_value = {'name': 'Episode 5', 'overview': 'Desc', 'air_date': '2020-01-05', 'episode_number': 5}
        mock_episodes.return_value.credits.return_value = {'cast': [], 'crew': []}

        m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=5)
        assert m.episode == 5
        assert m.episodes == [5]
        assert m.title == 'Episode 5'
        assert m.description == 'Desc'


class TestSetHD:
    def _make_metadata(self):
        """Create a Metadata instance without __init__ for testing helper methods."""
        m = Metadata.__new__(Metadata)
        m.HD = None
        return m

    def test_4k(self):
        m = self._make_metadata()
        m.setHD(3840, 2160)
        assert m.HD == [3]

    def test_1080p(self):
        m = self._make_metadata()
        m.setHD(1920, 1080)
        assert m.HD == [2]

    def test_720p(self):
        m = self._make_metadata()
        m.setHD(1280, 720)
        assert m.HD == [1]

    def test_sd(self):
        m = self._make_metadata()
        m.setHD(640, 480)
        assert m.HD == [0]

    def test_uhd_by_height(self):
        m = self._make_metadata()
        m.setHD(1000, 2160)
        assert m.HD == [3]

    def test_fhd_by_height(self):
        m = self._make_metadata()
        m.setHD(1000, 1080)
        assert m.HD == [2]


class TestShortDescription:
    def _make_metadata(self):
        m = Metadata.__new__(Metadata)
        return m

    def test_short_description_under_limit(self):
        m = self._make_metadata()
        m.description = "A short description."
        assert m.shortDescription == "A short description."

    def test_short_description_over_limit(self):
        m = self._make_metadata()
        m.description = "First sentence. Second sentence. " * 20
        result = m.shortDescription
        assert len(result) <= 300  # Should be truncated
        assert result.endswith('.')

    def test_short_description_empty(self):
        m = self._make_metadata()
        m.description = ""
        assert m.shortDescription == ""

    def test_short_description_none(self):
        m = self._make_metadata()
        m.description = None
        assert m.shortDescription == ""

    def test_get_short_description_exact_limit(self):
        m = self._make_metadata()
        desc = "A" * 255
        assert m.getShortDescription(desc) == desc


class TestGetRating:
    def _make_metadata(self):
        m = Metadata.__new__(Metadata)
        m.mediatype = MediaType.Movie
        return m

    def test_known_rating(self):
        m = self._make_metadata()
        assert m.getRating('PG-13') == 'mpaa|PG-13|300'

    def test_known_rating_case_insensitive(self):
        m = self._make_metadata()
        assert m.getRating('pg-13') == 'mpaa|PG-13|300'

    def test_tv_rating(self):
        m = self._make_metadata()
        assert m.getRating('TV-MA') == 'us-tv|TV-MA|600'

    def test_unknown_rating_movie(self):
        m = self._make_metadata()
        m.mediatype = MediaType.Movie
        assert m.getRating('UNKNOWN') == 'mpaa|Not Rated|000'

    def test_unknown_rating_tv(self):
        m = self._make_metadata()
        m.mediatype = MediaType.TV
        assert m.getRating('UNKNOWN') == 'us-tv|Not Rated|000'


class TestXml:
    def _make_metadata(self):
        m = Metadata.__new__(Metadata)
        m.credit = {
            'cast': [
                {'name': 'Actor One'},
                {'name': 'Actor Two'},
            ],
            'crew': [
                {'name': 'Writer One', 'department': 'Writing'},
                {'name': 'Director One', 'department': 'Directing'},
                {'name': 'Producer One', 'department': 'Production'},
            ]
        }
        return m

    def test_xml_contains_cast(self):
        m = self._make_metadata()
        xml = m.xml
        assert 'Actor One' in xml
        assert 'Actor Two' in xml

    def test_xml_contains_crew(self):
        m = self._make_metadata()
        xml = m.xml
        assert 'Writer One' in xml
        assert 'Director One' in xml
        assert 'Producer One' in xml

    def test_xml_structure(self):
        m = self._make_metadata()
        xml = m.xml
        assert xml.startswith('<?xml')
        assert '</plist>' in xml

    def test_xml_no_credit(self):
        m = self._make_metadata()
        m.credit = None
        xml = m.xml
        assert '</plist>' in xml
        assert 'cast' not in xml


class TestUrlretrieve:
    def _make_metadata(self):
        m = Metadata.__new__(Metadata)
        return m

    @patch('resources.metadata.requests.get')
    def test_downloads_to_file(self, mock_get, tmp_path):
        mock_get.return_value.content = b'image data'
        m = self._make_metadata()
        fn = str(tmp_path / "poster.jpg")
        result = m.urlretrieve("https://example.com/poster.jpg", fn)
        assert result[0] == fn
        with open(fn, 'rb') as f:
            assert f.read() == b'image data'


class TestGetArtwork:
    def _make_metadata(self):
        m = Metadata.__new__(Metadata)
        m.log = MagicMock()
        m.mediatype = MediaType.Movie
        m.moviedata = {'poster_path': '/abc.jpg'}
        m.tmdbid = 603
        return m

    def test_local_artwork_found(self, tmp_path):
        m = self._make_metadata()
        src = tmp_path / "movie.mkv"
        src.write_text("x")
        poster = tmp_path / "movie.jpg"
        poster.write_text("image")
        result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
        assert result == str(poster)

    def test_local_artwork_png(self, tmp_path):
        m = self._make_metadata()
        src = tmp_path / "movie.mkv"
        src.write_text("x")
        poster = tmp_path / "movie.png"
        poster.write_text("image")
        result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
        assert result == str(poster)

    def test_smaposter_found(self, tmp_path):
        m = self._make_metadata()
        src = tmp_path / "movie.mkv"
        src.write_text("x")
        poster = tmp_path / "smaposter.jpg"
        poster.write_text("image")
        result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
        assert result == str(poster)

    @patch('resources.metadata.Metadata.urlretrieve')
    def test_downloads_artwork(self, mock_url, tmp_path):
        m = self._make_metadata()
        src = tmp_path / "movie.mkv"
        src.write_text("x")
        mock_url.return_value = ('/tmp/poster-603.jpg', None)
        result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
        assert result == '/tmp/poster-603.jpg'

    def test_no_poster_path_returns_none(self, tmp_path):
        m = self._make_metadata()
        m.moviedata = {'poster_path': None}
        src = tmp_path / "movie.mkv"
        src.write_text("x")
        result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
        assert result is None


class TestGetDefaultLanguage:
    @patch('resources.metadata.tmdb.Movies')
    def test_movie_language(self, mock_movies):
        mock_query = MagicMock()
        mock_query.info.return_value = {'original_language': 'en'}
        mock_movies.return_value = mock_query
        result = Metadata.getDefaultLanguage(603, MediaType.Movie)
        assert result == 'eng'

    @patch('resources.metadata.tmdb.TV')
    def test_tv_language(self, mock_tv):
        mock_query = MagicMock()
        mock_query.info.return_value = {'original_language': 'ja'}
        mock_tv.return_value = mock_query
        result = Metadata.getDefaultLanguage(1396, MediaType.TV)
        assert result == 'jpn'

    def test_invalid_mediatype(self):
        result = Metadata.getDefaultLanguage(123, 'invalid')
        assert result is None

    def test_no_tmdbid(self):
        result = Metadata.getDefaultLanguage(None, MediaType.Movie)
        assert result is None


class TestWriteTvPlexmatch:
    def test_creates_plexmatch(self, tmp_path):
        show_root = tmp_path / "Show Name"
        season_dir = show_root / "Season 01"
        season_dir.mkdir(parents=True)
        ep_file = season_dir / "episode.mp4"
        ep_file.write_text("x")

        tagdata = MagicMock()
        tagdata.mediatype = MediaType.TV
        tagdata.showname = "Show Name"
        tagdata.showdata = {'first_air_date': '2020-01-15'}
        tagdata.tvdbid = 12345
        tagdata.imdbid = 'tt1234567'
        tagdata.tmdbid = 99999
        tagdata.season = 1
        tagdata.episode = 3
        tagdata.episodes = [3]

        _write_tv_plexmatch(str(ep_file), tagdata, MagicMock())

        plexmatch = show_root / ".plexmatch"
        assert plexmatch.exists()
        content = plexmatch.read_text()
        assert 'title: Show Name' in content
        assert 'S01E03' in content

    def test_updates_existing_plexmatch(self, tmp_path):
        show_root = tmp_path / "Show"
        season_dir = show_root / "Season 01"
        season_dir.mkdir(parents=True)

        # Create existing plexmatch
        plexmatch = show_root / ".plexmatch"
        plexmatch.write_text("title: Show\nEpisode: S01E01: Season 01/ep1.mp4\n")

        tagdata = MagicMock()
        tagdata.showname = "Show"
        tagdata.showdata = {}
        tagdata.tvdbid = None
        tagdata.imdbid = None
        tagdata.tmdbid = 100
        tagdata.season = 1
        tagdata.episode = 2
        tagdata.episodes = [2]

        _write_tv_plexmatch(str(season_dir / "ep2.mp4"), tagdata, MagicMock())

        content = plexmatch.read_text()
        assert 'S01E01' in content
        assert 'S01E02' in content


class TestWriteMoviePlexmatch:
    def test_creates_movie_plexmatch(self, tmp_path):
        movie_dir = tmp_path / "Movie (2020)"
        movie_dir.mkdir()
        filepath = movie_dir / "movie.mp4"
        filepath.write_text("x")

        tagdata = MagicMock()
        tagdata.title = "The Movie"
        tagdata.date = "2020-05-15"
        tagdata.tmdbid = 603

        _write_movie_plexmatch(str(filepath), tagdata, MagicMock())

        plexmatch = movie_dir / ".plexmatch"
        assert plexmatch.exists()
        content = plexmatch.read_text()
        assert 'title: The Movie' in content
        assert 'year: 2020' in content
        assert 'guid: tmdb://603' in content


class TestUpdatePlexmatch:
    def test_skips_when_disabled(self):
        settings = MagicMock()
        settings.plexmatch_enabled = False
        update_plexmatch('/path/file.mp4', MagicMock(), settings)
        # Should return without writing anything

    def test_skips_when_no_tagdata(self):
        settings = MagicMock()
        settings.plexmatch_enabled = True
        update_plexmatch('/path/file.mp4', None, settings)

    @patch('resources.metadata._write_tv_plexmatch')
    def test_dispatches_tv(self, mock_write):
        settings = MagicMock()
        settings.plexmatch_enabled = True
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.TV
        update_plexmatch('/path/file.mp4', tagdata, settings)
        mock_write.assert_called_once()

    @patch('resources.metadata._write_movie_plexmatch')
    def test_dispatches_movie(self, mock_write):
        settings = MagicMock()
        settings.plexmatch_enabled = True
        tagdata = MagicMock()
        tagdata.mediatype = MediaType.Movie
        update_plexmatch('/path/file.mp4', tagdata, settings)
        mock_write.assert_called_once()
