"""Tests for resources/metadata.py - metadata tagging and TMDB integration."""

from unittest.mock import MagicMock, PropertyMock, patch

from resources.metadata import (
  MediaType,
  Metadata,
  _build_plexmatch_header,
  _parse_plexmatch,
  _write_movie_plexmatch,
  _write_plexmatch,
  _write_tv_plexmatch,
  update_plexmatch,
)


class TestResolveTmdbID:
  def test_returns_int_tmdbid_directly(self):
    log = MagicMock()
    assert Metadata.resolveTmdbID(MediaType.Movie, log, tmdbid="603") == 603

  def test_returns_int_tmdbid_as_int(self):
    log = MagicMock()
    assert Metadata.resolveTmdbID(MediaType.Movie, log, tmdbid=603) == 603

  def test_invalid_tmdbid_logs_error(self):
    log = MagicMock()
    result = Metadata.resolveTmdbID(MediaType.Movie, log, tmdbid="not_a_number")
    log.error.assert_called()
    # Falls through after ValueError - returns the original tmdbid string
    assert result == "not_a_number"

  @patch("resources.metadata.tmdb.Find")
  def test_movie_from_imdbid(self, mock_find_cls):
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.movie_results = [{"id": 550}]
    mock_find_cls.return_value = mock_find
    result = Metadata.resolveTmdbID(MediaType.Movie, log, imdbid="tt0137523")
    assert result == 550

  @patch("resources.metadata.tmdb.Find")
  def test_movie_from_imdbid_no_prefix(self, mock_find_cls):
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.movie_results = [{"id": 550}]
    mock_find_cls.return_value = mock_find
    result = Metadata.resolveTmdbID(MediaType.Movie, log, imdbid="0137523")
    mock_find_cls.assert_called_with("tt0137523")
    assert result == 550

  @patch("resources.metadata.tmdb.Find")
  def test_tv_from_imdbid(self, mock_find_cls):
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.tv_results = [{"id": 1396}]
    mock_find_cls.return_value = mock_find
    result = Metadata.resolveTmdbID(MediaType.TV, log, imdbid="tt0903747")
    assert result == 1396

  @patch("resources.metadata.tmdb.Find")
  def test_tv_from_tvdbid(self, mock_find_cls):
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.tv_results = [{"id": 1396}]
    mock_find_cls.return_value = mock_find
    result = Metadata.resolveTmdbID(MediaType.TV, log, tvdbid=81189)
    assert result == 1396

  @patch("resources.metadata.tmdb.Find")
  def test_tv_imdbid_fallback_to_tvdbid(self, mock_find_cls):
    log = MagicMock()
    mock_find_imdb = MagicMock()
    mock_find_imdb.tv_results = []
    mock_find_tvdb = MagicMock()
    mock_find_tvdb.tv_results = [{"id": 999}]
    mock_find_cls.side_effect = [mock_find_imdb, mock_find_tvdb]
    result = Metadata.resolveTmdbID(MediaType.TV, log, imdbid="tt0000001", tvdbid=12345)
    assert result == 999

  def test_no_ids_returns_none(self):
    log = MagicMock()
    result = Metadata.resolveTmdbID(MediaType.Movie, log)
    assert result is None


class TestMultiEpisodeMetadata:
  """Test multi-episode support in Metadata class."""

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=1396)
  def test_single_episode_as_int(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
    mock_tv.return_value.info.return_value = {"name": "Breaking Bad", "genres": [], "networks": [], "original_language": "en"}
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = {"episodes": []}
    mock_episodes.return_value.info.return_value = {"name": "Fly", "overview": "A fly...", "air_date": "2010-05-23", "episode_number": 10}
    mock_episodes.return_value.credits.return_value = {"cast": [], "crew": []}

    m = Metadata(MediaType.TV, tmdbid=1396, season=3, episode=10)
    assert m.episode == 10
    assert m.episodes == [10]
    assert m.title == "Fly"

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=1396)
  def test_multi_episode_list(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
    mock_tv.return_value.info.return_value = {"name": "Breaking Bad", "genres": [], "networks": [], "original_language": "en"}
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = {"episodes": []}

    ep1_data = {"name": "Pilot", "overview": "A teacher...", "air_date": "2008-01-20", "episode_number": 1}
    ep2_data = {"name": "Cat's in the Bag...", "overview": "Walt and Jesse...", "air_date": "2008-01-27", "episode_number": 2}

    call_count = [0]

    def episode_info(language=None):
      result = [ep1_data, ep2_data][call_count[0]]
      call_count[0] += 1
      return result

    mock_ep_instance = MagicMock()
    mock_ep_instance.info.side_effect = episode_info
    mock_ep_instance.credits.return_value = {"cast": [], "crew": []}
    mock_episodes.return_value = mock_ep_instance

    m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=[1, 2])
    assert m.episode == 1
    assert m.episodes == [1, 2]
    assert "Pilot" in m.title
    assert "Cat's in the Bag..." in m.title
    assert " / " in m.title
    assert "A teacher..." in m.description
    assert "Walt and Jesse..." in m.description

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=1396)
  def test_multi_episode_string_list(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
    """Test that string episode numbers are converted to int."""
    mock_tv.return_value.info.return_value = {"name": "Show", "genres": [], "networks": [], "original_language": "en"}
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = {"episodes": []}
    mock_episodes.return_value.info.return_value = {"name": "Ep", "overview": "", "air_date": "2020-01-01", "episode_number": 1}
    mock_episodes.return_value.credits.return_value = {"cast": [], "crew": []}

    m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=["3", "4"])
    assert m.episodes == [3, 4]
    assert m.episode == 3

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=1396)
  def test_single_episode_backwards_compat(self, mock_resolve, mock_tv, mock_seasons, mock_episodes):
    """Ensure single-episode usage still works identically."""
    mock_tv.return_value.info.return_value = {"name": "Show", "genres": [], "networks": [], "original_language": "en"}
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = {"episodes": [{"name": "E5"}]}
    mock_episodes.return_value.info.return_value = {"name": "Episode 5", "overview": "Desc", "air_date": "2020-01-05", "episode_number": 5}
    mock_episodes.return_value.credits.return_value = {"cast": [], "crew": []}

    m = Metadata(MediaType.TV, tmdbid=1396, season=1, episode=5)
    assert m.episode == 5
    assert m.episodes == [5]
    assert m.title == "Episode 5"
    assert m.description == "Desc"


class TestAirDateEpisodeFallback:
  """Test TMDB air-date fallback for shows like The Late Show that use air dates
  instead of traditional episode numbers.  When a direct episode fetch (S11E0)
  returns 404, the season episode list is searched by air date to resolve the
  real episode number and title.
  """

  _SHOW_DATA = {
    "name": "The Late Show with Stephen Colbert",
    "genres": [],
    "networks": [],
    "original_language": "en",
  }
  # Season 11 episode list from /tv/63770/season/11 — one real episode and
  # some neighbours to confirm we match on date, not position.
  _SEASON_DATA = {
    "episodes": [
      {"episode_number": 46, "name": "Timothée Chalamet", "overview": "...", "air_date": "2025-11-10"},
      {"episode_number": 47, "name": "Claire Danes Rep. James Clyburn", "overview": "A great show.", "air_date": "2025-11-11"},
      {"episode_number": 48, "name": "Another Guest", "overview": "...", "air_date": "2025-11-12"},
    ]
  }
  _SOURCE_FILE = "The Late Show with Stephen Colbert (2015) - 2025-11-11 - Claire Danes Rep. James Clyburn [HDTV-1080p]{[EAC3 2.0]}[x265]-Z-B.mkv"

  def _setup_mocks(self, mock_tv, mock_seasons, mock_episodes_cls):
    mock_tv.return_value.info.return_value = self._SHOW_DATA
    mock_tv.return_value.external_ids.return_value = {"tvdb_id": 289574}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = self._SEASON_DATA

    # Episode 0 fetch raises 404; episode 47 fetch returns real data.
    def ep_factory(tmdbid, season, ep_num):
      inst = MagicMock()
      if ep_num == 0:
        inst.info.side_effect = Exception("404 Client Error: Not Found")
        inst.credits.side_effect = Exception("404 Client Error: Not Found")
      else:
        inst.info.return_value = {
          "name": "Claire Danes Rep. James Clyburn",
          "overview": "A great show.",
          "air_date": "2025-11-11",
          "episode_number": 47,
        }
        inst.credits.return_value = {"cast": [], "crew": []}
      return inst

    mock_episodes_cls.side_effect = ep_factory

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=63770)
  def test_episode0_resolves_to_real_episode_via_air_date(self, mock_resolve, mock_tv, mock_seasons, mock_episodes_cls):
    """When S11E0 returns 404, the season list is searched by the air date in
    the filename and the real episode (S11E47) is used instead."""
    self._setup_mocks(mock_tv, mock_seasons, mock_episodes_cls)

    m = Metadata(
      MediaType.TV,
      tmdbid=63770,
      season=11,
      episode=0,
      original=self._SOURCE_FILE,
    )

    assert m.episode == 47, "episode should be remapped from 0 to 47"
    assert m.episodes == [47]
    assert m.title == "Claire Danes Rep. James Clyburn"
    assert m.date == "2025-11-11"
    assert m.description == "A great show."

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=63770)
  def test_episode0_no_air_date_match_leaves_empty_title(self, mock_resolve, mock_tv, mock_seasons, mock_episodes_cls):
    """When S11E0 returns 404 and no season episode matches the air date,
    the title is left empty (not 'Episode 0') and air_date comes from the filename."""
    mock_tv.return_value.info.return_value = self._SHOW_DATA
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    # Season has no episode matching 2025-11-11
    mock_seasons.return_value.info.return_value = {
      "episodes": [
        {"episode_number": 1, "name": "Pilot", "overview": "", "air_date": "2015-09-08"},
      ]
    }
    ep_inst = MagicMock()
    ep_inst.info.side_effect = Exception("404")
    ep_inst.credits.side_effect = Exception("404")
    mock_episodes_cls.return_value = ep_inst

    m = Metadata(
      MediaType.TV,
      tmdbid=63770,
      season=11,
      episode=0,
      original=self._SOURCE_FILE,
    )

    assert m.episode == 0, "episode stays 0 when no match found"
    assert m.title == "", "title must be empty, not 'Episode 0'"
    assert m.date == "2025-11-11", "air date extracted from filename"

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=63770)
  def test_episode0_no_original_no_filename_date(self, mock_resolve, mock_tv, mock_seasons, mock_episodes_cls):
    """When no original path is provided and the episode 0 fetch fails,
    the title and air_date are both empty — never 'Episode 0'."""
    mock_tv.return_value.info.return_value = self._SHOW_DATA
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = {"episodes": []}
    ep_inst = MagicMock()
    ep_inst.info.side_effect = Exception("404")
    ep_inst.credits.side_effect = Exception("404")
    mock_episodes_cls.return_value = ep_inst

    m = Metadata(MediaType.TV, tmdbid=63770, season=11, episode=0)

    assert m.title == "", "title must be empty, never 'Episode 0'"
    assert m.date is None


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
    assert result.endswith(".")

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
    assert m.getRating("PG-13") == "mpaa|PG-13|300"

  def test_known_rating_case_insensitive(self):
    m = self._make_metadata()
    assert m.getRating("pg-13") == "mpaa|PG-13|300"

  def test_tv_rating(self):
    m = self._make_metadata()
    assert m.getRating("TV-MA") == "us-tv|TV-MA|600"

  def test_unknown_rating_movie(self):
    m = self._make_metadata()
    m.mediatype = MediaType.Movie
    assert m.getRating("UNKNOWN") == "mpaa|Not Rated|000"

  def test_unknown_rating_tv(self):
    m = self._make_metadata()
    m.mediatype = MediaType.TV
    assert m.getRating("UNKNOWN") == "us-tv|Not Rated|000"


class TestXml:
  def _make_metadata(self):
    m = Metadata.__new__(Metadata)
    m.credit = {
      "cast": [
        {"name": "Actor One"},
        {"name": "Actor Two"},
      ],
      "crew": [
        {"name": "Writer One", "department": "Writing"},
        {"name": "Director One", "department": "Directing"},
        {"name": "Producer One", "department": "Production"},
      ],
    }
    return m

  def test_xml_contains_cast(self):
    m = self._make_metadata()
    xml = m.xml
    assert "Actor One" in xml
    assert "Actor Two" in xml

  def test_xml_contains_crew(self):
    m = self._make_metadata()
    xml = m.xml
    assert "Writer One" in xml
    assert "Director One" in xml
    assert "Producer One" in xml

  def test_xml_structure(self):
    m = self._make_metadata()
    xml = m.xml
    assert xml.startswith("<?xml")
    assert "</plist>" in xml

  def test_xml_no_credit(self):
    m = self._make_metadata()
    m.credit = None
    xml = m.xml
    assert "</plist>" in xml
    assert "cast" not in xml


class TestUrlretrieve:
  def _make_metadata(self):
    m = Metadata.__new__(Metadata)
    return m

  @patch("resources.metadata.requests.get")
  def test_downloads_to_file(self, mock_get, tmp_path):
    mock_get.return_value.content = b"image data"
    m = self._make_metadata()
    fn = str(tmp_path / "poster.jpg")
    result = m.urlretrieve("https://example.com/poster.jpg", fn)
    assert result[0] == fn
    with open(fn, "rb") as f:
      assert f.read() == b"image data"


class TestGetArtwork:
  def _make_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.Movie
    m.moviedata = {"poster_path": "/abc.jpg"}
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

  @patch("resources.metadata.Metadata.urlretrieve")
  def test_downloads_artwork(self, mock_url, tmp_path):
    m = self._make_metadata()
    src = tmp_path / "movie.mkv"
    src.write_text("x")
    mock_url.return_value = ("/tmp/poster-603.jpg", None)
    result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
    assert result == "/tmp/poster-603.jpg"

  def test_no_poster_path_returns_none(self, tmp_path):
    m = self._make_metadata()
    m.moviedata = {"poster_path": None}
    src = tmp_path / "movie.mkv"
    src.write_text("x")
    result = m.getArtwork(str(tmp_path / "movie.mp4"), str(src))
    assert result is None


class TestGetDefaultLanguage:
  @patch("resources.metadata.tmdb.Movies")
  def test_movie_language(self, mock_movies):
    mock_query = MagicMock()
    mock_query.info.return_value = {"original_language": "en"}
    mock_movies.return_value = mock_query
    result = Metadata.getDefaultLanguage(603, MediaType.Movie)
    assert result == "eng"

  @patch("resources.metadata.tmdb.TV")
  def test_tv_language(self, mock_tv):
    mock_query = MagicMock()
    mock_query.info.return_value = {"original_language": "ja"}
    mock_tv.return_value = mock_query
    result = Metadata.getDefaultLanguage(1396, MediaType.TV)
    assert result == "jpn"

  def test_invalid_mediatype(self):
    result = Metadata.getDefaultLanguage(123, "invalid")
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
    tagdata.showdata = {"first_air_date": "2020-01-15"}
    tagdata.tvdbid = 12345
    tagdata.imdbid = "tt1234567"
    tagdata.tmdbid = 99999
    tagdata.season = 1
    tagdata.episode = 3
    tagdata.episodes = [3]

    _write_tv_plexmatch(str(ep_file), tagdata, MagicMock())

    plexmatch = show_root / ".plexmatch"
    assert plexmatch.exists()
    content = plexmatch.read_text()
    assert "Title: Show Name" in content
    assert "S01E03" in content

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
    assert "S01E01" in content
    assert "S01E02" in content


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
    tagdata.imdbid = None

    _write_movie_plexmatch(str(filepath), tagdata, MagicMock())

    plexmatch = movie_dir / ".plexmatch"
    assert plexmatch.exists()
    content = plexmatch.read_text()
    assert "Title: The Movie" in content
    assert "Year: 2020" in content
    assert "Guid: tmdb://603" in content


class TestWriteTags:
  """Tests for Metadata.writeTags covering the mutagen MP4 tagging path."""

  def _make_metadata(self, mediatype=MediaType.Movie):
    """Build a bare Metadata instance without calling __init__ (no TMDB calls)."""
    import logging

    m = Metadata.__new__(Metadata)
    m.log = logging.getLogger("test")
    m.mediatype = mediatype
    m.HD = None
    m.original = None
    m.title = "The Matrix"
    m.tagline = "Welcome to the Real World"
    m.description = "A hacker discovers reality is a simulation."
    m.date = "1999-03-31"
    m.genre = [{"name": "Action"}]
    m.rating = "mpaa|R|400"
    # TV-specific
    m.showname = "Breaking Bad"
    m.season = 3
    m.episode = 10
    m.episodes = [10]
    m.seasondata = {"episodes": list(range(13))}
    m.network = [{"name": "AMC"}]
    return m

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_write_movie_tags(self, mock_mp4_cls, mock_cover_cls):
    """writeTags sets Movie-specific MP4 tags and returns True on success."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.Movie)
    with patch.object(m, "getArtwork", return_value=None), patch.object(m, "setHD"), patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())

    assert result is True
    mock_video.__setitem__.assert_any_call("\xa9nam", "The Matrix")
    mock_video.__setitem__.assert_any_call("stik", [9])
    mock_video.save.assert_called_once()

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_write_tv_tags(self, mock_mp4_cls, mock_cover_cls):
    """writeTags sets TV-specific MP4 tags and returns True on success."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.TV)
    with (
      patch.object(m, "getArtwork", return_value=None),
      patch.object(m, "setHD"),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
      patch.object(type(m), "shortDescription", new_callable=PropertyMock, return_value="Short."),
    ):
      result = m.writeTags("/fake/tv.mp4", "/fake/tv.mp4", MagicMock())

    assert result is True
    mock_video.__setitem__.assert_any_call("tvsh", "Breaking Bad")
    mock_video.__setitem__.assert_any_call("stik", [10])
    mock_video.__setitem__.assert_any_call("tvsn", [3])

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_artwork_jpeg_embedded(self, mock_mp4_cls, mock_cover_cls, tmp_path):
    """writeTags embeds JPEG artwork when getArtwork returns a path."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video
    cover_path = str(tmp_path / "cover.jpg")
    with open(cover_path, "wb") as f:
      f.write(b"\xff\xd8\xff")  # JPEG magic bytes

    mock_cover_cls.FORMAT_JPEG = 13
    mock_cover_cls.return_value = MagicMock()

    m = self._make_metadata(MediaType.Movie)
    with patch.object(m, "getArtwork", return_value=cover_path), patch.object(m, "setHD"), patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock(), artwork=True)

    assert result is True
    mock_video.__setitem__.assert_any_call("covr", [mock_cover_cls.return_value])

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_artwork_png_embedded(self, mock_mp4_cls, mock_cover_cls, tmp_path):
    """writeTags embeds PNG artwork when cover path ends with .png."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video
    cover_path = str(tmp_path / "cover.png")
    with open(cover_path, "wb") as f:
      f.write(b"\x89PNG")

    mock_cover_cls.FORMAT_PNG = 14
    mock_cover_cls.return_value = MagicMock()

    m = self._make_metadata(MediaType.Movie)
    with patch.object(m, "getArtwork", return_value=cover_path), patch.object(m, "setHD"), patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock(), artwork=True)

    assert result is True

  @patch("resources.metadata.MP4")
  def test_fallback_ffmpeg_on_invalid_mp4(self, mock_mp4_cls):
    """writeTags falls back to FFmpeg tagging when MP4 raises MP4StreamInfoError."""
    from resources.metadata import MP4StreamInfoError

    mock_mp4_cls.side_effect = MP4StreamInfoError("not an mp4")

    mock_converter = MagicMock()
    mock_converter.tag.return_value = iter(
      [
        (None, ["ffmpeg", "-i", "input", "output"]),
      ]
    )

    m = self._make_metadata(MediaType.Movie)
    with patch.object(m, "getArtwork", return_value=None):
      result = m.writeTags("/fake/file.mp4", "/fake/file.mp4", mock_converter)

    mock_converter.tag.assert_called_once()
    assert result is True

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_save_failure_returns_false(self, mock_mp4_cls, mock_cover_cls):
    """writeTags returns False when video.save() raises."""
    mock_video = MagicMock()
    mock_video.save.side_effect = OSError("disk full")
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.Movie)
    with patch.object(m, "getArtwork", return_value=None), patch.object(m, "setHD"), patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())

    assert result is False

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_hd_tag_set_when_provided(self, mock_mp4_cls, mock_cover_cls):
    """writeTags calls setHD and writes hdvd tag when width/height provided."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.Movie)
    with patch.object(m, "getArtwork", return_value=None), patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"):
      m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock(), width=1920, height=1080)

    mock_video.__setitem__.assert_any_call("hdvd", m.HD)

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_original_tool_tag(self, mock_mp4_cls, mock_cover_cls):
    """writeTags embeds original filename in the tool tag when original is set."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.Movie)
    m.original = "/source/original.mkv"
    with patch.object(m, "getArtwork", return_value=None), patch.object(m, "setHD"), patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"):
      m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())

    mock_video.__setitem__.assert_any_call("\xa9too", "SMA-NG:original.mkv")

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_multi_episode_omits_tves_trkn(self, mock_mp4_cls, mock_cover_cls):
    """tves/trkn are omitted for multi-episode files so Plex uses the filename."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.TV)
    m.episodes = [1, 2, 3]
    m.episode = 1
    with (
      patch.object(m, "getArtwork", return_value=None),
      patch.object(m, "setHD"),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
      patch.object(type(m), "shortDescription", new_callable=PropertyMock, return_value="Short."),
    ):
      m.writeTags("/fake/tv.mp4", "/fake/tv.mp4", MagicMock())

    set_keys = [call[0][0] for call in mock_video.__setitem__.call_args_list]
    assert "tves" not in set_keys
    assert "trkn" not in set_keys

  @patch("resources.metadata.MP4Cover")
  @patch("resources.metadata.MP4")
  def test_single_episode_includes_tves_trkn(self, mock_mp4_cls, mock_cover_cls):
    """tves/trkn are written normally for single-episode files."""
    mock_video = MagicMock()
    mock_mp4_cls.return_value = mock_video

    m = self._make_metadata(MediaType.TV)
    m.episodes = [5]
    m.episode = 5
    with (
      patch.object(m, "getArtwork", return_value=None),
      patch.object(m, "setHD"),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
      patch.object(type(m), "shortDescription", new_callable=PropertyMock, return_value="Short."),
    ):
      m.writeTags("/fake/tv.mp4", "/fake/tv.mp4", MagicMock())

    set_keys = [call[0][0] for call in mock_video.__setitem__.call_args_list]
    assert "tves" in set_keys
    assert "trkn" in set_keys


class TestUpdatePlexmatch:
  def test_skips_when_disabled(self):
    settings = MagicMock()
    settings.plexmatch_enabled = False
    update_plexmatch("/path/file.mp4", MagicMock(), settings)
    # Should return without writing anything

  def test_skips_when_no_tagdata(self):
    settings = MagicMock()
    settings.plexmatch_enabled = True
    update_plexmatch("/path/file.mp4", None, settings)

  @patch("resources.metadata._write_tv_plexmatch")
  def test_dispatches_tv(self, mock_write):
    settings = MagicMock()
    settings.plexmatch_enabled = True
    tagdata = MagicMock()
    tagdata.mediatype = MediaType.TV
    update_plexmatch("/path/file.mp4", tagdata, settings)
    mock_write.assert_called_once()

  @patch("resources.metadata._write_movie_plexmatch")
  def test_dispatches_movie(self, mock_write):
    settings = MagicMock()
    settings.plexmatch_enabled = True
    tagdata = MagicMock()
    tagdata.mediatype = MediaType.Movie
    update_plexmatch("/path/file.mp4", tagdata, settings)
    mock_write.assert_called_once()

  def test_logs_exception_when_writer_fails(self):
    settings = MagicMock()
    settings.plexmatch_enabled = True
    tagdata = MagicMock()
    tagdata.mediatype = MediaType.Movie
    log = MagicMock()
    with patch("resources.metadata._write_movie_plexmatch", side_effect=RuntimeError("boom")):
      update_plexmatch("/path/file.mp4", tagdata, settings, log=log)
    log.exception.assert_called_once()


class TestPlexmatchHelpers:
  def test_parse_plexmatch_returns_empty_when_missing(self, tmp_path):
    header, episodes = _parse_plexmatch(str(tmp_path / ".plexmatch"))
    assert header == {}
    assert episodes == {}

  def test_parse_plexmatch_keeps_header_and_valid_episodes(self, tmp_path):
    plexmatch = tmp_path / ".plexmatch"
    plexmatch.write_text(
      "\n".join(
        [
          "Title: Example Show",
          "Year: 2024",
          "Episode: S01E02: Season 01/Episode 02.mkv",
          "Episode: S01E01-E02: old-range-format.mkv",
          "# comment",
        ]
      )
    )
    header, episodes = _parse_plexmatch(str(plexmatch))
    assert header == {"title": "Example Show", "year": "2024"}
    assert episodes == {"S01E02": "Season 01/Episode 02.mkv"}

  def test_build_plexmatch_header_adds_and_removes_ids(self):
    header = {"tvdbid": "old", "tmdbid": "old", "imdbid": "old", "guid": "tmdb://old"}
    result = _build_plexmatch_header(header, title="Movie", year="2024", tmdbid=603, tvdbid=None, imdbid=None)
    assert result["title"] == "Movie"
    assert result["year"] == "2024"
    assert result["tmdbid"] == "603"
    assert result["guid"] == "tmdb://603"
    assert "tvdbid" not in result
    assert "imdbid" not in result

  def test_write_plexmatch_uses_expected_field_order(self, tmp_path):
    plexmatch = tmp_path / ".plexmatch"
    _write_plexmatch(
      str(plexmatch),
      {
        "title": "Movie",
        "year": "2024",
        "tvdbid": "123",
        "tmdbid": "603",
        "imdbid": "tt0133093",
        "guid": "tmdb://603",
      },
      {"S01E02": "Season 01/Episode 02.mkv", "S01E01": "Season 01/Episode 01.mkv"},
    )
    lines = plexmatch.read_text().splitlines()
    assert lines[:6] == [
      "Title: Movie",
      "Year: 2024",
      "tvdbid: 123",
      "tmdbid: 603",
      "imdbid: tt0133093",
      "Guid: tmdb://603",
    ]
    assert lines[6:] == [
      "Episode: S01E01: Season 01/Episode 01.mkv",
      "Episode: S01E02: Season 01/Episode 02.mkv",
    ]

  def test_write_tv_plexmatch_uses_show_root_for_season_directory(self, tmp_path):
    show_root = tmp_path / "Show Name"
    season_dir = show_root / "Season 01"
    season_dir.mkdir(parents=True)
    filepath = season_dir / "Episode 01.mkv"
    filepath.write_text("x")
    tagdata = MagicMock()
    tagdata.showname = "Show Name"
    tagdata.tmdbid = 123
    tagdata.tvdbid = 456
    tagdata.imdbid = "tt1234567"
    tagdata.showdata = {"first_air_date": "2020-01-01"}
    tagdata.season = 1
    tagdata.episodes = [2, 1]
    tagdata.episode = 1
    log = MagicMock()

    _write_tv_plexmatch(str(filepath), tagdata, log)

    plexmatch = show_root / ".plexmatch"
    contents = plexmatch.read_text()
    assert "Title: Show Name" in contents
    assert "Year: 2020" in contents
    assert "Episode: S01E01: Season 01/Episode 01.mkv" in contents
    assert "Episode: S01E02: Season 01/Episode 01.mkv" in contents
    log.info.assert_called_once()

  def test_write_movie_plexmatch_skips_when_directory_missing(self, tmp_path):
    filepath = tmp_path / "missing-dir" / "Movie.mp4"
    log = MagicMock()
    tagdata = MagicMock(title="Movie", year=2024, tmdbid=603, imdbid="tt0133093")
    _write_movie_plexmatch(str(filepath), tagdata, log)
    assert not (tmp_path / "missing-dir" / ".plexmatch").exists()


class TestSetHD:
  def _make_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.HD = None
    return m

  def test_4k_by_width(self):
    m = self._make_metadata()
    m.setHD(3840, 1080)
    assert m.HD == [3]

  def test_4k_by_height(self):
    m = self._make_metadata()
    m.setHD(1920, 2160)
    assert m.HD == [3]

  def test_1080p_by_width(self):
    m = self._make_metadata()
    m.setHD(1920, 800)
    assert m.HD == [2]

  def test_1080p_by_height(self):
    m = self._make_metadata()
    m.setHD(1280, 1080)
    assert m.HD == [2]

  def test_720p_by_width(self):
    m = self._make_metadata()
    m.setHD(1280, 480)
    assert m.HD == [1]

  def test_720p_by_height(self):
    m = self._make_metadata()
    m.setHD(640, 720)
    assert m.HD == [1]

  def test_sd(self):
    m = self._make_metadata()
    m.setHD(640, 480)
    assert m.HD == [0]


class TestGetShortDescription:
  def _make_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.description = None
    return m

  def test_short_description_returned_as_is(self):
    m = self._make_metadata()
    assert m.getShortDescription("Short.") == "Short."

  def test_long_description_truncated_at_sentence(self):
    m = self._make_metadata()
    text = "First sentence. " + "x" * 300
    result = m.getShortDescription(text)
    assert len(result) <= 256
    assert result.endswith(".")

  def test_exactly_255_chars_returned_as_is(self):
    m = self._make_metadata()
    text = "a" * 255
    assert m.getShortDescription(text) == text

  def test_short_description_property_empty_when_no_description(self):
    m = self._make_metadata()
    m.description = None
    assert m.shortDescription == ""

  def test_short_description_property_delegates(self):
    m = self._make_metadata()
    m.description = "Hello world."
    assert m.shortDescription == "Hello world."


class TestGetRating:
  def _make_movie_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.Movie
    return m

  def _make_tv_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.TV
    return m

  def test_known_mpaa_rating(self):
    m = self._make_movie_metadata()
    assert m.getRating("PG-13") == "mpaa|PG-13|300"

  def test_known_tv_rating(self):
    m = self._make_tv_metadata()
    assert m.getRating("TV-MA") == "us-tv|TV-MA|600"

  def test_unknown_rating_movie_returns_not_rated(self):
    m = self._make_movie_metadata()
    result = m.getRating("X")
    assert "mpaa" in result
    assert "Not Rated" in result

  def test_unknown_rating_tv_returns_not_rated(self):
    m = self._make_tv_metadata()
    result = m.getRating("UNKNOWN")
    assert "us-tv" in result

  def test_case_insensitive(self):
    m = self._make_movie_metadata()
    assert m.getRating("pg-13") == m.getRating("PG-13")

  def test_r_rating(self):
    m = self._make_movie_metadata()
    assert m.getRating("R") == "mpaa|R|400"


class TestXmlProperty:
  def _make_metadata_with_credit(self, cast=None, crew=None):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.credit = {"cast": cast or [], "crew": crew or []}
    return m

  def test_contains_plist_structure(self):
    m = self._make_metadata_with_credit()
    xml = m.xml
    assert "<?xml" in xml
    assert "<plist" in xml
    assert "</plist>" in xml

  def test_includes_cast(self):
    m = self._make_metadata_with_credit(cast=[{"name": "John Doe"}, {"name": "Jane Smith"}])
    xml = m.xml
    assert "John Doe" in xml
    assert "Jane Smith" in xml

  def test_max_5_cast_members(self):
    m = self._make_metadata_with_credit(cast=[{"name": "Actor %d" % i} for i in range(10)])
    xml = m.xml
    assert xml.count("<key>name</key>") <= 20  # 5 cast + 5 crew max
    assert "Actor 5" not in xml
    assert "Actor 4" in xml

  def test_includes_director(self):
    m = self._make_metadata_with_credit(crew=[{"name": "Kubrick", "department": "Directing"}])
    assert "Kubrick" in m.xml

  def test_includes_writer(self):
    m = self._make_metadata_with_credit(crew=[{"name": "Sorkin", "department": "Writing"}])
    assert "Sorkin" in m.xml

  def test_includes_producer(self):
    m = self._make_metadata_with_credit(crew=[{"name": "Bruckheimer", "department": "Production"}])
    assert "Bruckheimer" in m.xml

  def test_no_credit_still_valid_xml(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.credit = None
    xml = m.xml
    assert "<?xml" in xml
    assert "</plist>" in xml

  def test_crew_department_case_insensitive(self):
    m = self._make_metadata_with_credit(crew=[{"name": "Nolan", "department": "directing"}])
    assert "Nolan" in m.xml


class TestGetDefaultLanguage:
  @patch("resources.metadata.tmdb.Movies")
  def test_movie_returns_language(self, mock_movies_cls):
    mock_query = MagicMock()
    mock_query.info.return_value = {"original_language": "en"}
    mock_movies_cls.return_value = mock_query
    with patch("resources.metadata.getAlpha3TCode", return_value="eng"):
      result = Metadata.getDefaultLanguage(603, MediaType.Movie)
    assert result == "eng"

  @patch("resources.metadata.tmdb.TV")
  def test_tv_returns_language(self, mock_tv_cls):
    mock_query = MagicMock()
    mock_query.info.return_value = {"original_language": "ja"}
    mock_tv_cls.return_value = mock_query
    with patch("resources.metadata.getAlpha3TCode", return_value="jpn"):
      result = Metadata.getDefaultLanguage(1396, MediaType.TV)
    assert result == "jpn"

  def test_none_tmdbid_returns_none(self):
    result = Metadata.getDefaultLanguage(None, MediaType.Movie)
    assert result is None

  def test_invalid_mediatype_returns_none(self):
    result = Metadata.getDefaultLanguage(603, "invalid")
    assert result is None


class TestWriteTagsMutagen:
  def _make_movie_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.Movie
    m.title = "The Matrix"
    m.tagline = "Welcome to the Real World"
    m.description = "A hacker discovers reality is a simulation."
    m.date = "1999-03-31"
    m.genre = [{"name": "Action"}]
    m.rating = "mpaa|R|400"
    m.HD = None
    m.original = None
    m.tmdbid = 603
    m.credit = None
    return m

  def _make_tv_metadata(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.TV
    m.title = "Pilot"
    m.showname = "Breaking Bad"
    m.description = "Walter White begins his journey."
    m.date = "2008-01-20"
    m.genre = [{"name": "Drama"}]
    m.rating = "us-tv|TV-MA|600"
    m.HD = None
    m.original = None
    m.tmdbid = 1396
    m.credit = None
    m.season = 1
    m.episode = 1
    m.episodes = [1]
    m.network = [{"name": "AMC"}]
    m.seasondata = {"episodes": [{}] * 7}
    return m

  def test_movie_writes_title_and_stik(self):
    m = self._make_movie_metadata()
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
    ):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())
    assert result is True
    keys_set = {call[0][0] for call in mock_video.__setitem__.call_args_list}
    assert "\xa9nam" in keys_set
    assert "stik" in keys_set

  def test_movie_stik_is_9(self):
    m = self._make_movie_metadata()
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
    ):
      m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())
    stik_call = next(c for c in mock_video.__setitem__.call_args_list if c[0][0] == "stik")
    assert stik_call[0][1] == [9]

  def test_tv_stik_is_10(self):
    m = self._make_tv_metadata()
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
      patch.object(type(m), "shortDescription", new_callable=PropertyMock, return_value="Short."),
    ):
      m.writeTags("/fake/tv.mp4", "/fake/tv.mp4", MagicMock())
    stik_call = next(c for c in mock_video.__setitem__.call_args_list if c[0][0] == "stik")
    assert stik_call[0][1] == [10]

  def test_tv_single_episode_includes_tves_trkn(self):
    m = self._make_tv_metadata()
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
      patch.object(type(m), "shortDescription", new_callable=PropertyMock, return_value="Short."),
    ):
      m.writeTags("/fake/tv.mp4", "/fake/tv.mp4", MagicMock())
    keys_set = {c[0][0] for c in mock_video.__setitem__.call_args_list}
    assert "tves" in keys_set
    assert "trkn" in keys_set

  def test_tv_multi_episode_omits_tves_trkn(self):
    m = self._make_tv_metadata()
    m.episodes = [1, 2]  # multi-episode
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
      patch.object(type(m), "shortDescription", new_callable=PropertyMock, return_value="Short."),
    ):
      m.writeTags("/fake/tv.mp4", "/fake/tv.mp4", MagicMock())
    keys_set = {c[0][0] for c in mock_video.__setitem__.call_args_list}
    assert "tves" not in keys_set
    assert "trkn" not in keys_set

  def test_hd_tag_set_when_hd(self):
    m = self._make_movie_metadata()
    m.HD = [2]
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
    ):
      m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())
    keys_set = {c[0][0] for c in mock_video.__setitem__.call_args_list}
    assert "hdvd" in keys_set

  def test_rating_tag_set(self):
    m = self._make_movie_metadata()
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
    ):
      m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())
    keys_set = {c[0][0] for c in mock_video.__setitem__.call_args_list}
    assert "----:com.apple.iTunes:iTunEXTC" in keys_set

  def test_sets_hd_from_dimensions(self):
    m = self._make_movie_metadata()
    mock_video = MagicMock()
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
    ):
      m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock(), width=1920, height=1080)
    assert m.HD == [2]

  def test_fallback_to_ffmpeg_on_mp4_error(self):
    from mutagen.mp4 import MP4StreamInfoError

    m = self._make_movie_metadata()
    mock_converter = MagicMock()
    mock_conv = iter([(None, ["ffmpeg", "-i", "x"]), (100, "done")])
    mock_converter.tag.return_value = mock_conv
    with patch("resources.metadata.MP4", side_effect=MP4StreamInfoError), patch.object(m, "getArtwork", return_value=None):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", mock_converter)
    assert result is True

  def test_returns_false_on_save_error(self):
    m = self._make_movie_metadata()
    mock_video = MagicMock()
    mock_video.save.side_effect = Exception("disk full")
    with (
      patch("resources.metadata.MP4", return_value=mock_video),
      patch.object(m, "getArtwork", return_value=None),
      patch.object(type(m), "xml", new_callable=PropertyMock, return_value="<dict/>"),
    ):
      result = m.writeTags("/fake/movie.mp4", "/fake/movie.mp4", MagicMock())
    assert result is False


class TestGetArtwork:
  def _make_metadata(self, mediatype=MediaType.Movie):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = mediatype
    m.tmdbid = 603
    m.moviedata = {"poster_path": "/abc.jpg"}
    m.episodedata = {"still_path": None}
    m.seasondata = {"poster_path": "/season.jpg"}
    m.showdata = {"poster_path": "/show.jpg"}
    return m

  def test_local_poster_file_used(self, tmp_path):
    m = self._make_metadata()
    # Create a local jpg next to inputfile
    inputfile = str(tmp_path / "movie.mkv")
    poster = str(tmp_path / "movie.jpg")
    open(poster, "wb").close()
    result = m.getArtwork(inputfile, inputfile)
    assert result == poster

  def test_smaposter_file_used_when_no_basename_match(self, tmp_path):
    m = self._make_metadata()
    inputfile = str(tmp_path / "movie.mkv")
    smaposter = str(tmp_path / "smaposter.jpg")
    open(smaposter, "wb").close()
    result = m.getArtwork(inputfile, inputfile)
    assert result == smaposter

  def test_downloads_when_no_local_file(self, tmp_path):
    m = self._make_metadata()
    inputfile = str(tmp_path / "movie.mkv")
    with patch.object(m, "urlretrieve", return_value=("/tmp/poster-603.jpg", None)) as mock_url:
      result = m.getArtwork(inputfile, inputfile)
    mock_url.assert_called_once()
    assert result == "/tmp/poster-603.jpg"

  def test_returns_none_when_no_poster_path(self, tmp_path):
    m = self._make_metadata()
    m.moviedata = {"poster_path": None}
    inputfile = str(tmp_path / "movie.mkv")
    result = m.getArtwork(inputfile, inputfile)
    assert result is None

  def test_tv_thumbnail_uses_still_path(self, tmp_path):
    m = self._make_metadata(MediaType.TV)
    m.episodedata = {"still_path": "/still.jpg"}
    inputfile = str(tmp_path / "episode.mkv")
    with patch.object(m, "urlretrieve", return_value=("/tmp/poster-603.jpg", None)):
      result = m.getArtwork(inputfile, inputfile, thumbnail=True)
    assert result == "/tmp/poster-603.jpg"

  def test_tv_falls_back_to_show_poster(self, tmp_path):
    m = self._make_metadata(MediaType.TV)
    m.seasondata = {"poster_path": None}
    m.showdata = {"poster_path": "/show.jpg"}
    inputfile = str(tmp_path / "episode.mkv")
    with patch.object(m, "urlretrieve", return_value=("/tmp/poster-603.jpg", None)):
      result = m.getArtwork(inputfile, inputfile, thumbnail=False)
    assert result == "/tmp/poster-603.jpg"


class TestWriteTvPlexmatch:
  def _make_tagdata(self, showname="Breaking Bad", season=1, episode=1, tmdbid=1396):
    t = MagicMock()
    t.showname = showname
    t.season = season
    t.episode = episode
    t.episodes = [episode]
    t.tmdbid = tmdbid
    t.tvdbid = 81189
    t.imdbid = "tt0903747"
    t.showdata = {"first_air_date": "2008-01-20"}
    return t

  def test_creates_plexmatch_in_show_root(self, tmp_path):
    show_dir = tmp_path / "Breaking Bad"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    filepath = str(season_dir / "S01E01.mp4")

    tagdata = self._make_tagdata()
    _write_tv_plexmatch(filepath, tagdata, MagicMock())

    plexmatch = show_dir / ".plexmatch"
    assert plexmatch.exists()

  def test_writes_title_and_year(self, tmp_path):
    show_dir = tmp_path / "Breaking Bad"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    filepath = str(season_dir / "S01E01.mp4")

    tagdata = self._make_tagdata()
    _write_tv_plexmatch(filepath, tagdata, MagicMock())

    content = (show_dir / ".plexmatch").read_text()
    assert "Title: Breaking Bad" in content
    assert "Year: 2008" in content

  def test_writes_episode_entry(self, tmp_path):
    show_dir = tmp_path / "Breaking Bad"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)
    filepath = str(season_dir / "S01E01.mp4")

    tagdata = self._make_tagdata(episode=3)
    tagdata.episodes = [3]
    _write_tv_plexmatch(filepath, tagdata, MagicMock())

    content = (show_dir / ".plexmatch").read_text()
    assert "Episode: S01E03:" in content

  def test_accumulates_episodes_across_calls(self, tmp_path):
    show_dir = tmp_path / "Breaking Bad"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True)

    for ep in [1, 2, 3]:
      filepath = str(season_dir / ("S01E0%d.mp4" % ep))
      tagdata = self._make_tagdata(episode=ep)
      tagdata.episodes = [ep]
      _write_tv_plexmatch(filepath, tagdata, MagicMock())

    content = (show_dir / ".plexmatch").read_text()
    assert "S01E01" in content
    assert "S01E02" in content
    assert "S01E03" in content

  def test_tmdb_guid_written(self, tmp_path):
    show_dir = tmp_path / "Show"
    show_dir.mkdir()
    filepath = str(show_dir / "S01E01.mp4")

    tagdata = self._make_tagdata(tmdbid=9999)
    _write_tv_plexmatch(filepath, tagdata, MagicMock())

    content = (show_dir / ".plexmatch").read_text()
    assert "Guid: tmdb://9999" in content


class TestWriteMoviePlexmatch:
  def _make_tagdata(self, title="The Matrix", date="1999-03-31", tmdbid=603, imdbid=None):
    t = MagicMock()
    t.title = title
    t.date = date
    t.tmdbid = tmdbid
    t.imdbid = imdbid
    return t

  def test_creates_plexmatch_in_movie_dir(self, tmp_path):
    movie_dir = tmp_path / "The Matrix (1999)"
    movie_dir.mkdir()
    filepath = str(movie_dir / "The Matrix.mp4")

    _write_movie_plexmatch(filepath, self._make_tagdata(), MagicMock())
    assert (movie_dir / ".plexmatch").exists()

  def test_writes_title_year_guid(self, tmp_path):
    movie_dir = tmp_path / "The Matrix (1999)"
    movie_dir.mkdir()
    filepath = str(movie_dir / "The Matrix.mp4")

    _write_movie_plexmatch(filepath, self._make_tagdata(), MagicMock())
    content = (movie_dir / ".plexmatch").read_text()
    assert "Title: The Matrix" in content
    assert "Year: 1999" in content
    assert "Guid: tmdb://603" in content

  def test_missing_date_omits_year(self, tmp_path):
    movie_dir = tmp_path / "Movie"
    movie_dir.mkdir()
    filepath = str(movie_dir / "movie.mp4")

    _write_movie_plexmatch(filepath, self._make_tagdata(date=""), MagicMock())
    content = (movie_dir / ".plexmatch").read_text()
    assert "year" not in content

  def test_no_tmdbid_omits_guid(self, tmp_path):
    movie_dir = tmp_path / "Movie"
    movie_dir.mkdir()
    filepath = str(movie_dir / "movie.mp4")

    _write_movie_plexmatch(filepath, self._make_tagdata(tmdbid=None), MagicMock())
    content = (movie_dir / ".plexmatch").read_text()
    assert "guid" not in content


# ---------------------------------------------------------------------------
# writeTags — FFmpeg fallback path
# ---------------------------------------------------------------------------


class TestWriteTagsFFmpegFallback:
  """Tests for the FFmpeg fallback tagging path triggered by MP4StreamInfoError."""

  def _make_metadata(self, mediatype=MediaType.Movie):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = mediatype
    m.title = "Test Title"
    m.description = "A description"
    m.date = "2024-01-01"
    m.tagline = "Short tagline"
    m.showname = "Test Show"
    m.season = 1
    m.episode = 1
    m.genre = [{"name": "Action"}]
    m.HD = None
    return m

  def _make_converter(self):
    converter = MagicMock()
    # converter.tag returns an iterator: first yields (None, cmds), then done
    converter.tag.return_value = iter([(None, ["ffmpeg", "-i", "in.mp4"]), (None, None)])
    return converter

  @patch("resources.metadata.MP4", side_effect=KeyError("not an mp4"))
  def test_movie_ffmpeg_fallback_calls_converter_tag(self, mock_mp4, tmp_path):
    m = self._make_metadata(MediaType.Movie)
    converter = self._make_converter()
    path = str(tmp_path / "movie.mp4")
    open(path, "w").close()

    with patch.object(m, "getArtwork", return_value=None):
      m.writeTags(path, path, converter, artwork=False)

    converter.tag.assert_called_once()
    call_kwargs = converter.tag.call_args
    metadata_arg = call_kwargs[0][1]
    assert metadata_arg["TITLE"] == "Test Title"
    assert metadata_arg["ENCODER"] == "SMA-NG"

  @patch("resources.metadata.MP4", side_effect=KeyError("not an mp4"))
  def test_tv_ffmpeg_fallback_includes_album(self, mock_mp4, tmp_path):
    m = self._make_metadata(MediaType.TV)
    converter = self._make_converter()
    path = str(tmp_path / "episode.mp4")
    open(path, "w").close()

    with patch.object(m, "getArtwork", return_value=None):
      m.writeTags(path, path, converter, artwork=False)

    metadata_arg = converter.tag.call_args[0][1]
    assert "ALBUM" in metadata_arg
    assert "Season 1" in metadata_arg["ALBUM"]

  @patch("resources.metadata.MP4", side_effect=KeyError("not an mp4"))
  def test_ffmpeg_fallback_returns_true_on_success(self, mock_mp4, tmp_path):
    m = self._make_metadata()
    converter = self._make_converter()
    path = str(tmp_path / "movie.mp4")
    open(path, "w").close()

    with patch.object(m, "getArtwork", return_value=None):
      result = m.writeTags(path, path, converter, artwork=False)

    assert result is True

  @patch("resources.metadata.MP4", side_effect=KeyError("not an mp4"))
  def test_ffmpeg_fallback_returns_false_on_converter_error(self, mock_mp4, tmp_path):
    from converter.ffmpeg import FFMpegConvertError

    m = self._make_metadata()
    converter = MagicMock()
    converter.tag.side_effect = FFMpegConvertError("ffmpeg", [], "error output", 1)
    path = str(tmp_path / "movie.mp4")
    open(path, "w").close()

    with patch.object(m, "getArtwork", return_value=None):
      result = m.writeTags(path, path, converter, artwork=False)

    assert result is False

  @patch("resources.metadata.MP4", side_effect=KeyError("not an mp4"))
  def test_ffmpeg_fallback_uses_genre_name(self, mock_mp4, tmp_path):
    m = self._make_metadata()
    converter = self._make_converter()
    path = str(tmp_path / "movie.mp4")
    open(path, "w").close()

    with patch.object(m, "getArtwork", return_value=None):
      m.writeTags(path, path, converter, artwork=False)

    metadata_arg = converter.tag.call_args[0][1]
    assert metadata_arg.get("GENRE") == "Action"


# ---------------------------------------------------------------------------
# getArtwork edge cases
# ---------------------------------------------------------------------------


class TestGetArtworkEdgeCases:
  def _make_metadata(self, poster_path="/poster.jpg"):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.Movie
    m.tmdbid = 603
    m.moviedata = {"poster_path": poster_path}
    return m

  def test_returns_none_when_no_poster_and_no_local(self, tmp_path):
    m = self._make_metadata(poster_path=None)
    path = str(tmp_path / "movie.mp4")
    result = m.getArtwork(path, path, thumbnail=False)
    assert result is None

  def test_local_jpg_takes_priority(self, tmp_path):
    m = self._make_metadata()
    path = str(tmp_path / "movie.mp4")
    poster = tmp_path / "movie.jpg"
    poster.write_bytes(b"fake jpeg")
    result = m.getArtwork(path, path, thumbnail=False)
    assert result == str(poster)

  def test_smaposter_used_when_no_basename_match(self, tmp_path):
    m = self._make_metadata()
    path = str(tmp_path / "movie.mp4")
    # No movie.jpg — smaposter.jpg should be found as fallback
    smaposter = tmp_path / "smaposter.jpg"
    smaposter.write_bytes(b"sma poster")
    result = m.getArtwork(path, path, thumbnail=False)
    assert result == str(smaposter)


# ---------------------------------------------------------------------------
# resolveTmdbID — error / edge cases
# ---------------------------------------------------------------------------


class TestResolveTmdbIDEdgeCases:
  @patch("resources.metadata.tmdb.Find")
  def test_empty_movie_results_returns_none(self, mock_find_cls):
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.movie_results = []
    mock_find_cls.return_value = mock_find
    result = Metadata.resolveTmdbID(MediaType.Movie, log, imdbid="tt9999999")
    assert result is None

  @patch("resources.metadata.tmdb.Find")
  def test_empty_tv_results_returns_none(self, mock_find_cls):
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.tv_results = []
    mock_find_cls.return_value = mock_find
    result = Metadata.resolveTmdbID(MediaType.TV, log, tvdbid=99999)
    assert result is None

  @patch("resources.metadata.tmdb.Find")
  def test_network_exception_propagates(self, mock_find_cls):
    # resolveTmdbID has no try/except — network errors propagate to the caller
    log = MagicMock()
    mock_find = MagicMock()
    mock_find.info.side_effect = Exception("network error")
    mock_find_cls.return_value = mock_find
    import pytest

    with pytest.raises(Exception, match="network error"):
      Metadata.resolveTmdbID(MediaType.Movie, log, imdbid="tt0000001")

  def test_no_ids_provided_returns_none(self):
    log = MagicMock()
    result = Metadata.resolveTmdbID(MediaType.Movie, log)
    assert result is None
