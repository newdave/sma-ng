"""Tests for resources/subtitles.py SubtitleProcessor."""

import os
from unittest.mock import MagicMock, patch

import pytest

from converter import ConverterError, FFMpegConvertError
from resources.subtitles import SubtitleProcessor


def _make_sp(sdl="eng"):
  """Create a SubtitleProcessor backed by a mock MediaProcessor."""
  mp = MagicMock()
  mp.settings.sdl = sdl
  mp.settings.burn_subtitles = False
  mp.settings.force_subtitle_defaults = False
  mp.settings.embedsubs = False
  mp.settings.cleanit = False
  mp.settings.ffsubsync = False
  mp.settings.downloadsubs = False
  mp.settings.downloadforcedsubs = False
  mp.settings.sdl = sdl
  mp.settings.ignore_embedded_subs = False
  mp.settings.subproviders = []
  mp.settings.subproviders_auth = {}
  mp.settings.hearing_impaired = False
  mp.settings.ffmpeg = "/usr/bin/ffmpeg"
  sp = SubtitleProcessor(mp)
  return sp, mp


def _make_sub_info(path, lang="und"):
  """Return a minimal MediaInfo-like object for an external subtitle file."""
  from converter.ffmpeg import MediaInfo, MediaStreamInfo

  sub_stream = MediaStreamInfo()
  sub_stream.type = "subtitle"
  sub_stream.codec = "srt"
  sub_stream.index = 0
  sub_stream.metadata = {"language": lang}
  sub_stream.disposition = {}

  info = MediaInfo()
  info.streams.append(sub_stream)
  info.path = path
  return info


def _make_stream_mock(lang="eng", index=0, disposition=None):
  """Create a mock subtitle stream."""
  s = MagicMock()
  s.metadata = {"language": lang}
  s.disposition = disposition or {"default": False, "forced": False}
  s.index = index
  return s


class TestProcessExternalSub:
  def test_none_input_returns_none(self):
    sp, mp = _make_sp()
    assert sp.processExternalSub(None, "/path/movie.mkv") is None

  def test_language_extracted_from_suffix(self):
    sp, mp = _make_sp()
    mp.parseFile.side_effect = lambda p: (
      "/path",
      p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
      p.rsplit(".", 1)[-1],
    )
    sub_info = _make_sub_info("/path/movie.eng.srt")
    result = sp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].metadata["language"] == "eng"

  def test_sdl_used_when_no_language_in_filename(self):
    sp, mp = _make_sp(sdl="fra")
    mp.parseFile.side_effect = lambda p: (
      "/path",
      p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
      p.rsplit(".", 1)[-1],
    )
    sub_info = _make_sub_info("/path/movie.srt")
    result = sp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].metadata["language"] == "fra"

  def test_forced_disposition_set_from_suffix(self):
    sp, mp = _make_sp()
    mp.parseFile.side_effect = lambda p: (
      "/path",
      p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
      p.rsplit(".", 1)[-1],
    )
    sub_info = _make_sub_info("/path/movie.eng.forced.srt")
    result = sp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].disposition.get("forced") is True

  def test_dispo_alt_commentary_mapped(self):
    """'commentary' should map to 'comment' disposition via DISPO_ALTS."""
    sp, mp = _make_sp()
    mp.parseFile.side_effect = lambda p: (
      "/path",
      p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
      p.rsplit(".", 1)[-1],
    )
    sub_info = _make_sub_info("/path/movie.eng.commentary.srt")
    result = sp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].disposition.get("comment") is True

  def test_sdh_maps_to_hearing_impaired(self):
    """'sdh' should map to 'hearing_impaired' disposition via DISPO_ALTS."""
    sp, mp = _make_sp()
    mp.parseFile.side_effect = lambda p: (
      "/path",
      p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
      p.rsplit(".", 1)[-1],
    )
    sub_info = _make_sub_info("/path/movie.eng.sdh.srt")
    result = sp.processExternalSub(sub_info, "/path/movie.mkv")
    assert result.subtitle[0].disposition.get("hearing_impaired") is True

  def test_no_sdl_no_language_stays_undefined(self):
    """When no SDL and no language in filename, lang stays UNDEFINED."""
    sp, mp = _make_sp(sdl=None)
    mp.settings.sdl = None
    mp.parseFile.side_effect = lambda p: (
      "/path",
      p.rsplit("/", 1)[-1].rsplit(".", 1)[0],
      p.rsplit(".", 1)[-1],
    )
    sub_info = _make_sub_info("/path/movie.srt")
    result = sp.processExternalSub(sub_info, "/path/movie.mkv")
    from converter.avcodecs import BaseCodec

    assert result.subtitle[0].metadata["language"] == BaseCodec.UNDEFINED


class TestScanForExternalSubs:
  def _sub_info_mock(self, path, lang, default=False):
    m = MagicMock()
    m.subtitle = [MagicMock()]
    m.subtitle[0].metadata = {"language": lang}
    m.subtitle[0].disposition = {"default": default}
    m.path = path
    return m

  def test_empty_directory_returns_empty(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], [])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"])
    assert result == []

  def test_finds_valid_subtitle_in_language(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    sub = self._sub_info_mock("/path/movie.eng.srt", "eng")
    mp.isValidSubtitleSource.return_value = sub
    mp.validLanguage.return_value = True
    sp.processExternalSub = MagicMock(return_value=sub)
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["movie.eng.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"])
    assert len(result) == 1
    assert result[0].path == "/path/movie.eng.srt"

  def test_ignores_invalid_subtitle_source(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    mp.isValidSubtitleSource.return_value = None
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["movie.eng.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"])
    assert result == []

  def test_ignores_file_not_starting_with_filename(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["othermovie.eng.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"])
    assert result == []
    mp.isValidSubtitleSource.assert_not_called()

  def test_skips_already_loaded_sub(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    existing = MagicMock()
    existing.path = "/path/movie.eng.srt"
    existing.subtitle = [MagicMock()]
    existing.subtitle[0].metadata = {"language": "eng"}
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["movie.eng.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"], valid_external_subs=[existing])
    # Should still contain the original
    assert len(result) >= 1

  def test_invalid_language_excluded(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    sub = self._sub_info_mock("/path/movie.fra.srt", "fra")
    mp.isValidSubtitleSource.return_value = sub
    sp.processExternalSub = MagicMock(return_value=sub)
    mp.validLanguage.return_value = False
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["movie.fra.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"])
    assert result == []

  def test_force_subtitle_defaults_includes_default_sub(self):
    sp, mp = _make_sp()
    mp.settings.force_subtitle_defaults = True
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    sub = self._sub_info_mock("/path/movie.fra.srt", "fra", default=True)
    mp.isValidSubtitleSource.return_value = sub
    sp.processExternalSub = MagicMock(return_value=sub)
    mp.validLanguage.return_value = False
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["movie.fra.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng"])
    assert len(result) == 1

  def test_results_sorted_by_language_preference(self):
    sp, mp = _make_sp()
    mp.parseFile.return_value = ("/path", "movie", "mkv")
    sub_fra = self._sub_info_mock("/path/movie.fra.srt", "fra")
    sub_eng = self._sub_info_mock("/path/movie.eng.srt", "eng")
    mp.isValidSubtitleSource.side_effect = [sub_fra, sub_eng]
    sp.processExternalSub = MagicMock(side_effect=[sub_fra, sub_eng])
    mp.validLanguage.return_value = True
    with patch("os.walk") as mock_walk:
      mock_walk.return_value = [("/path", [], ["movie.fra.srt", "movie.eng.srt"])]
      result = sp.scanForExternalSubs("/path/movie.mkv", ["eng", "fra"])
    # eng should come first as it's index 0 in swl
    assert result[0].subtitle[0].metadata["language"] == "eng"


class TestBurnSubtitleFilter:
  def test_burn_subtitles_disabled_returns_none(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = False
    info = MagicMock()
    info.subtitle = []
    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"])
    assert result is None

  def test_no_valid_streams_returns_none(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    info = MagicMock()
    info.subtitle = []
    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"])
    assert result is None

  def test_valid_embedded_sub_returns_filter(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    mp.settings.burn_dispositions = {}
    mp.settings.burn_sorting = []
    mp.settings.sub_sorting_codecs = None
    mp.settings.scodec = []
    mp.settings.scodec_image = []

    stream = _make_stream_mock("eng", index=3)
    info = MagicMock()
    info.subtitle = [stream]

    mp.validLanguage.return_value = True
    mp.checkDisposition.return_value = True
    mp.isImageBasedSubtitle.return_value = False
    mp.sortStreams.return_value = [stream]
    mp.raw.side_effect = lambda x: x

    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"])
    assert result is not None
    assert "subtitles=" in result

  def test_image_based_sub_removed_from_candidates(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    mp.settings.burn_dispositions = {}
    mp.settings.embedsubs = False

    stream = _make_stream_mock("eng", index=2)
    info = MagicMock()
    info.subtitle = [stream]

    mp.validLanguage.return_value = True
    mp.checkDisposition.return_value = True
    mp.isImageBasedSubtitle.return_value = True  # Image-based, should be removed

    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"])
    assert result is None

  def test_image_check_exception_removes_candidate(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    mp.settings.burn_dispositions = {}
    mp.settings.embedsubs = False

    stream = _make_stream_mock("eng", index=2)
    info = MagicMock()
    info.subtitle = [stream]

    mp.validLanguage.return_value = True
    mp.checkDisposition.return_value = True
    mp.isImageBasedSubtitle.side_effect = Exception("error")

    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"])
    assert result is None

  def test_external_subs_used_when_embedsubs_enabled(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    mp.settings.burn_dispositions = {}
    mp.settings.burn_sorting = []
    mp.settings.sub_sorting_codecs = None
    mp.settings.scodec = []
    mp.settings.scodec_image = []
    mp.settings.embedsubs = True

    info = MagicMock()
    info.subtitle = []  # No embedded subs

    ext_sub = MagicMock()
    ext_sub.subtitle = [MagicMock()]
    ext_sub.subtitle[0].disposition = {"default": False}
    ext_sub.subtitle[0].metadata = {"language": "eng"}
    ext_sub.path = "/path/movie.eng.srt"

    mp.checkDisposition.return_value = True
    mp.isImageBasedSubtitle.return_value = False
    mp.sortStreams.return_value = [ext_sub]
    mp.raw.side_effect = lambda x: x

    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"], valid_external_subs=[ext_sub])
    assert result is not None
    assert "subtitles=" in result

  def test_external_sub_image_exception_removed(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    mp.settings.burn_dispositions = {}
    mp.settings.embedsubs = True

    info = MagicMock()
    info.subtitle = []

    ext_sub = MagicMock()
    ext_sub.subtitle = [MagicMock()]
    ext_sub.subtitle[0].disposition = {"default": False}
    ext_sub.path = "/path/movie.eng.srt"

    mp.checkDisposition.return_value = True
    mp.isImageBasedSubtitle.side_effect = Exception("corrupt")

    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"], valid_external_subs=[ext_sub])
    assert result is None

  def test_no_external_subs_no_candidates_returns_none(self):
    sp, mp = _make_sp()
    mp.settings.burn_subtitles = True
    mp.settings.cleanit = False
    mp.settings.burn_dispositions = {}
    mp.settings.embedsubs = True

    info = MagicMock()
    info.subtitle = []

    mp.checkDisposition.return_value = False
    sp.scanForExternalSubs = MagicMock(return_value=[])

    result = sp.burnSubtitleFilter("/path/movie.mkv", info, ["eng"])
    assert result is None


class TestSyncExternalSub:
  def test_disabled_does_nothing(self):
    sp, mp = _make_sp()
    mp.settings.ffsubsync = False
    sp.syncExternalSub("/path/movie.eng.srt", "/path/movie.mkv")
    # No exception, no action

  def test_ffsubsync_none_does_nothing(self):
    sp, mp = _make_sp()
    mp.settings.ffsubsync = True
    import resources.subtitles as sub_module

    original = sub_module.ffsubsync
    sub_module.ffsubsync = None
    try:
      sp.syncExternalSub("/path/movie.eng.srt", "/path/movie.mkv")
    finally:
      sub_module.ffsubsync = original

  def test_syncs_when_enabled(self, tmp_path):
    sp, mp = _make_sp()
    mp.settings.ffsubsync = True
    mp.settings.ffmpeg = "/usr/bin/ffmpeg"

    mock_ffsubsync = MagicMock()
    mock_parser = MagicMock()
    mock_args = MagicMock()
    mock_ffsubsync.make_parser.return_value = mock_parser
    mock_parser.parse_args.return_value = mock_args
    mock_ffsubsync.run.return_value = {"succeeded": True}

    sub_path = str(tmp_path / "movie.eng.srt")
    synced = sub_path + ".sync.srt"

    import resources.subtitles as sub_module

    original = sub_module.ffsubsync
    sub_module.ffsubsync = mock_ffsubsync
    try:
      with patch("os.path.isfile", return_value=False), patch("os.path.exists", return_value=False):
        sp.syncExternalSub(sub_path, "/path/movie.mkv")
    finally:
      sub_module.ffsubsync = original

    mock_ffsubsync.run.assert_called_once()

  def test_synced_file_replaces_original(self, tmp_path):
    sp, mp = _make_sp()
    mp.settings.ffsubsync = True
    mp.settings.ffmpeg = "/usr/bin/ffmpeg"

    mock_ffsubsync = MagicMock()
    mock_parser = MagicMock()
    mock_args = MagicMock()
    mock_ffsubsync.make_parser.return_value = mock_parser
    mock_parser.parse_args.return_value = mock_args
    mock_ffsubsync.run.return_value = {}

    sub_path = "/path/movie.eng.srt"

    import resources.subtitles as sub_module

    original = sub_module.ffsubsync
    sub_module.ffsubsync = mock_ffsubsync
    try:
      with patch("os.path.isfile", return_value=False), patch("os.path.exists", return_value=True) as mock_exists, patch("os.remove") as mock_remove, patch("os.rename") as mock_rename:
        sp.syncExternalSub(sub_path, "/path/movie.mkv")
        mock_remove.assert_called_once_with(sub_path)
        mock_rename.assert_called_once()
    finally:
      sub_module.ffsubsync = original

  def test_exception_logged_does_not_raise(self):
    sp, mp = _make_sp()
    mp.settings.ffsubsync = True
    mp.settings.ffmpeg = "/usr/bin/ffmpeg"

    mock_ffsubsync = MagicMock()
    mock_ffsubsync.make_parser.side_effect = Exception("unexpected")

    import resources.subtitles as sub_module

    original = sub_module.ffsubsync
    sub_module.ffsubsync = mock_ffsubsync
    try:
      sp.syncExternalSub("/path/movie.eng.srt", "/path/movie.mkv")
    finally:
      sub_module.ffsubsync = original
    mp.log.exception.assert_called()


class TestCustomScanVideo:
  def _setup_mocks(self):
    mock_subliminal = MagicMock()
    mock_subliminal.VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi")
    mock_subliminal.Video.fromguess.return_value = MagicMock()
    return mock_subliminal

  def test_nonexistent_path_raises(self):
    import resources.subtitles as sub_module

    original_sub = sub_module.subliminal
    mock_subliminal = self._setup_mocks()
    sub_module.subliminal = mock_subliminal
    try:
      with patch("os.path.exists", return_value=False):
        with pytest.raises(ValueError, match="does not exist"):
          SubtitleProcessor.custom_scan_video("/nonexistent/file.mkv")
    finally:
      sub_module.subliminal = original_sub

  def test_invalid_extension_raises(self):
    import resources.subtitles as sub_module

    original_sub = sub_module.subliminal
    original_gi = sub_module.guessit
    mock_subliminal = self._setup_mocks()
    mock_guessit = MagicMock(return_value={})
    sub_module.subliminal = mock_subliminal
    sub_module.guessit = mock_guessit
    try:
      with patch("os.path.exists", return_value=True):
        with pytest.raises(ValueError, match="not a valid video extension"):
          SubtitleProcessor.custom_scan_video("/path/file.txt")
    finally:
      sub_module.subliminal = original_sub
      sub_module.guessit = original_gi

  def test_basic_movie_scan(self):
    import resources.subtitles as sub_module

    original_sub = sub_module.subliminal
    original_gi = sub_module.guessit
    mock_subliminal = self._setup_mocks()
    mock_video = MagicMock()
    mock_subliminal.Video.fromguess.return_value = mock_video
    mock_guessit = MagicMock(return_value={"title": "Movie"})
    sub_module.subliminal = mock_subliminal
    sub_module.guessit = mock_guessit
    try:
      with patch("os.path.exists", return_value=True), patch("os.path.getsize", return_value=1000000):
        result = SubtitleProcessor.custom_scan_video("/path/movie.mkv")
    finally:
      sub_module.subliminal = original_sub
      sub_module.guessit = original_gi
    assert result is mock_video
    assert mock_video.size == 1000000

  def test_tv_episode_with_tagdata(self):
    import resources.subtitles as sub_module
    from resources.metadata import MediaType

    original_sub = sub_module.subliminal
    original_gi = sub_module.guessit
    mock_subliminal = self._setup_mocks()
    mock_video = MagicMock()
    mock_subliminal.Video.fromguess.return_value = mock_video
    mock_guessit = MagicMock(return_value={"type": "episode"})
    sub_module.subliminal = mock_subliminal
    sub_module.guessit = mock_guessit
    tagdata = MagicMock()
    tagdata.mediatype = MediaType.TV
    tagdata.title = "Show Name"
    tagdata.season = 1
    tagdata.episode = 3
    tagdata.episodes = [3]
    try:
      with patch("os.path.exists", return_value=True), patch("os.path.getsize", return_value=500000):
        result = SubtitleProcessor.custom_scan_video("/path/show.s01e03.mkv", tagdata=tagdata)
    finally:
      sub_module.subliminal = original_sub
      sub_module.guessit = original_gi
    assert result is mock_video

  def test_movie_with_tagdata(self):
    import resources.subtitles as sub_module
    from resources.metadata import MediaType

    original_sub = sub_module.subliminal
    original_gi = sub_module.guessit
    mock_subliminal = self._setup_mocks()
    mock_video = MagicMock()
    mock_subliminal.Video.fromguess.return_value = mock_video
    mock_guessit = MagicMock(return_value={"type": "movie"})
    sub_module.subliminal = mock_subliminal
    sub_module.guessit = mock_guessit
    tagdata = MagicMock()
    tagdata.mediatype = MediaType.Movie
    tagdata.title = "Great Movie"
    try:
      with patch("os.path.exists", return_value=True), patch("os.path.getsize", return_value=2000000):
        result = SubtitleProcessor.custom_scan_video("/path/movie.mkv", tagdata=tagdata)
    finally:
      sub_module.subliminal = original_sub
      sub_module.guessit = original_gi
    assert result is mock_video


class TestDownloadSubtitles:
  def test_returns_empty_when_disabled(self):
    sp, mp = _make_sp()
    mp.settings.downloadsubs = False
    mp.settings.downloadforcedsubs = False
    result = sp.downloadSubtitles("/path/movie.mkv", [], ["eng"])
    assert result == []

  def test_returns_empty_when_subliminal_none(self):
    import resources.subtitles as sub_module

    original = sub_module.subliminal
    sub_module.subliminal = None
    sp, mp = _make_sp()
    mp.settings.downloadsubs = True
    try:
      result = sp.downloadSubtitles("/path/movie.mkv", [], ["eng"])
    finally:
      sub_module.subliminal = original
    assert result == []

  def test_returns_empty_when_no_valid_languages(self):
    import resources.subtitles as sub_module

    original_sub = sub_module.subliminal
    original_lang = sub_module.Language
    original_gi = sub_module.guessit
    mock_subliminal = MagicMock()
    # Language constructor always raises
    mock_language = MagicMock(side_effect=Exception("bad language"))
    mock_gi = MagicMock()
    sub_module.subliminal = mock_subliminal
    sub_module.Language = mock_language
    sub_module.guessit = mock_gi
    sp, mp = _make_sp(sdl=None)
    mp.settings.downloadsubs = True
    mp.settings.sdl = None
    try:
      result = sp.downloadSubtitles("/path/movie.mkv", [], ["bad"])
    finally:
      sub_module.subliminal = original_sub
      sub_module.Language = original_lang
      sub_module.guessit = original_gi
    assert result == []

  def test_downloads_best_subtitles(self):
    import resources.subtitles as sub_module
    from resources.metadata import MediaType

    original_sub = sub_module.subliminal
    original_lang = sub_module.Language
    original_gi = sub_module.guessit

    mock_subliminal = MagicMock()
    mock_subliminal.region.configure = MagicMock()
    mock_video = MagicMock()
    mock_video.name = "/path/movie.mkv"
    mock_video.year = 2020
    mock_video.imdb_id = None

    mock_sub = MagicMock()
    mock_sub.language = MagicMock()
    mock_sub.info = "opensubtitles"

    mock_subliminal.download_best_subtitles.return_value = {mock_video: [mock_sub]}
    mock_subliminal.save_subtitles.return_value = [mock_sub]
    mock_subliminal.subtitle.get_subtitle_path.return_value = "/path/movie.eng.srt"

    mock_language = MagicMock(side_effect=lambda x: MagicMock())
    mock_gi = MagicMock()

    sub_module.subliminal = mock_subliminal
    sub_module.Language = mock_language
    sub_module.guessit = mock_gi

    sp, mp = _make_sp()
    mp.settings.downloadsubs = True
    mp.settings.downloadforcedsubs = False
    mp.settings.sdl = None
    mp.settings.ignore_embedded_subs = False

    with patch.object(SubtitleProcessor, "custom_scan_video", return_value=mock_video):
      try:
        result = sp.downloadSubtitles("/path/movie.mkv", [], ["eng"])
      finally:
        sub_module.subliminal = original_sub
        sub_module.Language = original_lang
        sub_module.guessit = original_gi

    assert "/path/movie.eng.srt" in result

  def test_exception_in_scan_returns_empty(self):
    import resources.subtitles as sub_module

    original_sub = sub_module.subliminal
    original_lang = sub_module.Language
    original_gi = sub_module.guessit

    mock_subliminal = MagicMock()
    mock_subliminal.region.configure = MagicMock()
    mock_language = MagicMock(side_effect=lambda x: MagicMock())
    mock_gi = MagicMock()

    sub_module.subliminal = mock_subliminal
    sub_module.Language = mock_language
    sub_module.guessit = mock_gi

    sp, mp = _make_sp()
    mp.settings.downloadsubs = True
    mp.settings.downloadforcedsubs = False
    mp.settings.sdl = None

    with patch.object(SubtitleProcessor, "custom_scan_video", side_effect=Exception("scan failed")):
      try:
        result = sp.downloadSubtitles("/path/movie.mkv", [], ["eng"])
      finally:
        sub_module.subliminal = original_sub
        sub_module.Language = original_lang
        sub_module.guessit = original_gi

    assert result == []


class TestRipSubs:
  def test_single_options_converted_to_list(self):
    sp, mp = _make_sp()
    sp.converter = MagicMock()
    mp.getSubExtensionFromCodec.return_value = "srt"
    mp.getSubOutputFileFromOptions.return_value = "/path/movie.eng.srt"

    mock_conv = iter([(None, ["ffmpeg", "-i", "in"]), (None, "debug line")])
    sp.converter.convert.return_value = mock_conv

    opts = {"format": "srt", "language": "eng", "index": 0}
    result = sp.ripSubs("/path/movie.mkv", opts)
    assert "/path/movie.eng.srt" in result

  def test_ffmpeg_error_skips_and_removes_file(self):
    sp, mp = _make_sp()
    sp.converter = MagicMock()
    mp.getSubExtensionFromCodec.return_value = "srt"
    mp.getSubOutputFileFromOptions.return_value = "/path/movie.eng.srt"

    sp.converter.convert.side_effect = FFMpegConvertError("ffmpeg error", None, None, None)

    opts = {"format": "srt", "language": "eng", "index": 0}
    result = sp.ripSubs("/path/movie.mkv", opts)
    assert result == []
    mp.removeFile.assert_called_once_with("/path/movie.eng.srt")

  def test_converter_error_skips(self):
    sp, mp = _make_sp()
    sp.converter = MagicMock()
    mp.getSubExtensionFromCodec.return_value = "srt"
    mp.getSubOutputFileFromOptions.return_value = "/path/out.srt"

    sp.converter.convert.side_effect = ConverterError("converter error")

    opts = {"format": "srt", "language": "eng", "index": 0}
    result = sp.ripSubs("/path/movie.mkv", opts)
    assert result == []

  def test_generic_exception_logged(self):
    sp, mp = _make_sp()
    sp.converter = MagicMock()
    mp.getSubExtensionFromCodec.return_value = "srt"
    mp.getSubOutputFileFromOptions.return_value = "/path/out.srt"

    sp.converter.convert.side_effect = RuntimeError("unexpected")

    opts = {"format": "srt", "language": "eng", "index": 0}
    result = sp.ripSubs("/path/movie.mkv", opts)
    assert result == []
    mp.log.exception.assert_called()

  def test_multiple_options_multiple_rips(self):
    sp, mp = _make_sp()
    sp.converter = MagicMock()
    mp.getSubExtensionFromCodec.return_value = "srt"
    mp.getSubOutputFileFromOptions.side_effect = ["/path/out.eng.srt", "/path/out.fra.srt"]

    def make_conv(*a, **kw):
      return iter([(None, ["ffmpeg"]), (None, "debug")])

    sp.converter.convert.side_effect = make_conv

    opts_list = [
      {"format": "srt", "language": "eng", "index": 0},
      {"format": "srt", "language": "fra", "index": 1},
    ]
    result = sp.ripSubs("/path/movie.mkv", opts_list)
    assert len(result) == 2

  def test_keyboard_interrupt_propagates(self):
    sp, mp = _make_sp()
    sp.converter = MagicMock()
    mp.getSubExtensionFromCodec.return_value = "srt"
    mp.getSubOutputFileFromOptions.return_value = "/path/out.srt"
    sp.converter.convert.side_effect = KeyboardInterrupt()

    opts = {"format": "srt", "language": "eng", "index": 0}
    with pytest.raises(KeyboardInterrupt):
      sp.ripSubs("/path/movie.mkv", opts)
