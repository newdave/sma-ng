"""Regression tests for integration defects (path matching, arr payloads, metadata, naming, rename)."""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from autoprocess._media_server import path_under, trigger_refresh
from resources.mediamanager import REQUEST_TIMEOUT, _find_file_id, build_api, rename_via_arr
from resources.metadata import MediaType, Metadata
from resources.naming import _rename_no_clobber, rename_file


def _resp(data):
  r = MagicMock()
  r.json.return_value = data
  return r


class TestPathUnder:
  @pytest.mark.parametrize(
    "path,prefix,expected",
    [
      ("/media/TV", "/media/TV", True),
      ("/media/TV/Show", "/media/TV", True),
      ("/media/TV/Show", "/media/TV/", True),
      ("/media/TV-Anime/Show", "/media/TV", False),
      ("/media/TVX", "/media/TV", False),
      ("/anything", "", False),
      ("/anything", "/", True),
    ],
  )
  def test_cases(self, path, prefix, expected):
    assert path_under(path, prefix) is expected

  def test_trigger_refresh_skips_sibling_prefix(self):
    inst = {"path": "/media/TV", "apikey": "k", "host": "h", "port": 1}
    with patch("autoprocess._media_server.requests.post") as post:
      trigger_refresh([inst], "/media/TV-Anime/Show/e.mp4", product_label="Emby")
    post.assert_not_called()

  def test_trigger_refresh_reports_deleted_path(self):
    inst = {"path": "/media/TV", "apikey": "k", "host": "h", "port": 1, "section": "main"}
    with patch("autoprocess._media_server.requests.post") as post:
      post.return_value.status_code = 204
      trigger_refresh([inst], "/media/TV/S/e.mp4", product_label="Emby", deleted_path="/media/TV/S/e.mkv")
    updates = post.call_args[1]["json"]["Updates"]
    assert {"Path": "/media/TV/S/e.mkv", "UpdateType": "Deleted"} in updates
    assert updates[0]["UpdateType"] == "Modified"


class TestPlexSectionMatch:
  def test_sibling_directory_not_refreshed(self):
    from autoprocess.plex import _refresh_with_connected_server

    plex = MagicMock()
    section = MagicMock()
    section.locations = ["/media/TV"]
    plex.library.sections.return_value = [section]
    _refresh_with_connected_server(plex, {}, "/media/TV-Anime/Show/e.mp4", logging.getLogger("t"), "plex")
    section.update.assert_not_called()

  def test_child_directory_refreshed(self):
    from autoprocess.plex import _refresh_with_connected_server

    plex = MagicMock()
    section = MagicMock()
    section.locations = ["/media/TV"]
    plex.library.sections.return_value = [section]
    _refresh_with_connected_server(plex, {}, "/media/TV/Show/e.mp4", logging.getLogger("t"), "plex")
    section.update.assert_called_once_with(path="/media/TV/Show")


class TestMediaManager:
  def test_build_api_webroot_none(self):
    url, _ = build_api({"host": "h", "port": 1, "apikey": "k", "webroot": None}, "ua")
    assert url == "http://h:1"

  def test_find_file_id_uses_path_mapping(self):
    records = [{"id": 7, "path": "/data/tv/Show/e.mp4"}, {"id": 8, "path": "/data/tv/Show/f.mp4"}]
    assert _find_file_id(records, "/media/tv/Show/e.mp4", {"/media/tv": "/data/tv"}) == 7

  def test_find_file_id_unique_basename_fallback(self):
    records = [{"id": 7, "path": "/x/Show/e.mp4"}, {"id": 8, "path": "/x/Show/f.mp4"}]
    assert _find_file_id(records, "/y/Show/e.mp4") == 7

  def test_find_file_id_ambiguous_basename(self):
    records = [{"id": 7, "path": "/x/a/e.mp4"}, {"id": 8, "path": "/x/b/e.mp4"}]
    assert _find_file_id(records, "/y/e.mp4") is None

  @patch("resources.mediamanager.time.sleep")
  @patch("resources.mediamanager.requests.post")
  @patch("resources.mediamanager.requests.get")
  def test_radarr_rename_files_payload(self, mock_get, mock_post, _sleep):
    mock_get.side_effect = [
      _resp({"movie": {"id": 5}}),
      _resp([{"id": 11, "path": "/data/m/F/F.mp4"}]),
      _resp({"status": "completed"}),
      _resp({"path": "/data/m/F/F (2020).mp4"}),
    ]
    mock_post.return_value = _resp({"id": 3, "status": "queued"})
    out = rename_via_arr("http://r:7878", {}, "radarr", "/m/F/F.mp4", MagicMock(), path_mapping={"/m": "/data/m"})
    assert out == "/data/m/F/F (2020).mp4"
    body = mock_post.call_args[1]["json"]
    assert body == {"name": "RenameFiles", "movieId": 5, "files": [11]}
    assert mock_post.call_args[1]["timeout"] == REQUEST_TIMEOUT
    for call in mock_get.call_args_list:
      assert call[1]["timeout"] == REQUEST_TIMEOUT


class TestMetadataFixes:
  def _meta(self):
    m = Metadata.__new__(Metadata)
    m.log = MagicMock()
    m.mediatype = MediaType.Movie
    m.title = "T"
    m.description = "D"
    m.date = "2020"
    m.tagline = None
    m.genre = []
    m.HD = None
    return m

  @patch("resources.metadata.MP4", side_effect=KeyError("not mp4"))
  def test_fallback_ffmpeg_error_on_next_returns_false(self, _mp4, tmp_path):
    from converter.ffmpeg import FFMpegConvertError

    def gen(*_a, **_k):
      raise FFMpegConvertError("boom", "cmd", "out")
      yield  # pragma: no cover

    converter = MagicMock()
    converter.tag.side_effect = gen
    path = str(tmp_path / "m.mp4")
    open(path, "w").close()
    assert self._meta().writeTags(path, path, converter) is False

  @patch("resources.metadata.tmdb.TV_Episodes")
  @patch("resources.metadata.tmdb.TV_Seasons")
  @patch("resources.metadata.tmdb.TV")
  @patch("resources.metadata.Metadata.resolveTmdbID", return_value=1)
  def test_multi_episode_lists_stay_aligned_when_credits_fail(self, _r, mock_tv, mock_seasons, mock_eps):
    mock_tv.return_value.info.return_value = {"name": "S", "genres": [], "networks": [], "original_language": "en"}
    mock_tv.return_value.external_ids.return_value = {}
    mock_tv.return_value.content_ratings.return_value = {"results": []}
    mock_seasons.return_value.info.return_value = {"episodes": []}

    def make(_tmdbid, _season, ep):
      inst = MagicMock()
      inst.info.return_value = {"name": "E%d" % ep, "overview": "o", "air_date": "2020-01-0%d" % ep, "episode_number": ep}
      if ep == 1:
        inst.credits.side_effect = Exception("404")
      else:
        inst.credits.return_value = {"cast": [], "crew": []}
      return inst

    mock_eps.side_effect = make
    m = Metadata(MediaType.TV, tmdbid=1, season=1, episode=[1, 2])
    assert len(m.episodedata_list) == 2
    assert m.episodedata_list[1]["name"] == "E2"

  def test_iTunMOVI_escapes_names(self):
    m = self._meta()
    m.credit = {"cast": [{"name": "A & <B>"}], "crew": []}
    xml = m.xml
    assert "A &amp; &lt;B&gt;" in xml
    assert "A & <B>" not in xml

  def test_short_description_keeps_sentence_punctuation(self):
    m = self._meta()
    text = "First sentence. Second one here. " + ("word " * 100)
    out = m.getShortDescription(text, length=60)
    assert out == "First sentence. Second one here."

  def test_short_description_no_period_uses_ellipsis(self):
    m = self._meta()
    out = m.getShortDescription("word " * 100, length=50)
    assert len(out) <= 50
    assert out.endswith("…")
    assert out != "."

  def test_short_description_preserves_internal_periods(self):
    m = self._meta()
    out = m.getShortDescription("Dr. Who arrives. " + "x" * 300, length=40)
    assert out == "Dr. Who arrives."


class TestNamingNoClobber:
  def test_no_overwrite_when_target_appears(self, tmp_path):
    src = tmp_path / "a.mp4"
    dst = tmp_path / "b.mp4"
    src.write_text("src")
    dst.write_text("dst")
    with pytest.raises(FileExistsError):
      _rename_no_clobber(str(src), str(dst))
    assert dst.read_text() == "dst"
    assert src.exists()

  def test_fallback_without_hardlinks(self, tmp_path):
    src = tmp_path / "a.mp4"
    dst = tmp_path / "b.mp4"
    src.write_text("src")
    with patch("os.link", side_effect=OSError("no links")):
      _rename_no_clobber(str(src), str(dst))
    assert dst.read_text() == "src"
    assert not src.exists()

  def test_fallback_does_not_clobber(self, tmp_path):
    src = tmp_path / "a.mp4"
    dst = tmp_path / "b.mp4"
    src.write_text("src")
    dst.write_text("dst")
    with patch("os.link", side_effect=OSError("no links")), pytest.raises(FileExistsError):
      _rename_no_clobber(str(src), str(dst))
    assert dst.read_text() == "dst"

  def test_sidecar_subtitles_renamed(self, tmp_path):
    (tmp_path / "Old.mp4").write_text("v")
    (tmp_path / "Old.srt").write_text("s")
    (tmp_path / "Old.en.srt").write_text("s")
    (tmp_path / "Old.en.forced.ass").write_text("s")
    (tmp_path / "Older.srt").write_text("s")
    result = rename_file(str(tmp_path / "Old.mp4"), "New")
    assert result == str(tmp_path / "New.mp4")
    names = sorted(os.listdir(tmp_path))
    assert names == ["New.en.forced.ass", "New.en.srt", "New.mp4", "New.srt", "Older.srt"]


class TestRenamePlexmatchAndRefresh:
  def _proc(self, settings):
    from resources.rename_util import RenameProcessor

    with patch("resources.rename_util.MediaProcessor"):
      return RenameProcessor(settings, logger=logging.getLogger("t"))

  def test_stub_tagdata_skipped(self):
    from resources.rename_util import _TypeStub

    settings = MagicMock()
    settings.plexmatch_enabled = True
    proc = self._proc(settings)
    results = [{"changed": True, "dry_run": False, "new": "/m/a.mp4", "tagdata": _TypeStub(MediaType.TV)}]
    with patch("resources.rename_util.update_plexmatch") as pm:
      proc.update_plexmatch_for_results(results)
    pm.assert_not_called()

  def test_refresh_honours_plex_instances(self):
    settings = MagicMock()
    settings.plex_instances = [{"host": "h", "token": "t", "refresh": True}]
    settings.Plex = {"host": None}
    proc = self._proc(settings)
    with patch("resources.rename_util.refreshPlex") as rp:
      proc.refresh_plex_for_results([{"changed": True, "dry_run": False, "new": "/m/a.mp4"}])
    rp.assert_called_once()

  def test_refresh_skipped_when_no_refresh_instances(self):
    settings = MagicMock()
    settings.plex_instances = [{"host": "h", "token": "t", "refresh": False}]
    proc = self._proc(settings)
    with patch("resources.rename_util.refreshPlex") as rp:
      proc.refresh_plex_for_results([{"changed": True, "dry_run": False, "new": "/m/a.mp4"}])
    rp.assert_not_called()

  def test_arr_instance_match_is_separator_aware(self):
    settings = MagicMock()
    settings.sonarr_instances = [{"path": "/media/TV", "apikey": "k"}]
    settings.radarr_instances = []
    proc = self._proc(settings)
    with patch("resources.mediamanager.rename_via_arr") as rv:
      proc._rename_file_via_arr("/media/TV-Anime/S/e.mkv", {"new": "x", "changed": False})
    rv.assert_not_called()


class TestGroupResults:
  def test_groups_by_processor(self):
    import rename

    a, b = object(), object()
    mapping = {"/a/1": a, "/a/2": a, "/b/1": b}
    groups = rename._group_results_by_processor([{"old": p} for p in mapping], mapping.get)
    assert [(g[0], len(g[1])) for g in groups] == [(a, 2), (b, 1)]
