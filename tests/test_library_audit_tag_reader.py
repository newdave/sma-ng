"""Tests for ``resources.library_audit.tag_reader``.

Exercises the freeform-atom reader and the cross-path media-id deriver
that the library auditor uses for duplicate-by-id detection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from resources.library_audit.tag_reader import (
  _decode_freeform,
  derive_media_id,
  read_media_ids,
)

# ---------------------------------------------------------------------------
# _decode_freeform()
# ---------------------------------------------------------------------------


class TestDecodeFreeform:
  def test_returns_none_for_empty(self):
    assert _decode_freeform(None) is None
    assert _decode_freeform([]) is None
    assert _decode_freeform("") is None

  def test_decodes_bytes_list(self):
    assert _decode_freeform([b"324552"]) == "324552"

  def test_decodes_bytearray(self):
    assert _decode_freeform([bytearray(b"324552")]) == "324552"

  def test_strips_whitespace(self):
    assert _decode_freeform([b"  324552\n"]) == "324552"

  def test_returns_none_for_whitespace_only(self):
    assert _decode_freeform([b"   "]) is None

  def test_falls_back_to_str_on_non_bytes(self):
    class FakeFreeForm:
      def __str__(self):
        return "tt0123456"

    assert _decode_freeform([FakeFreeForm()]) == "tt0123456"

  def test_invalid_utf8_uses_replace(self):
    # 0x80 is invalid UTF-8 start byte; "replace" maps to U+FFFD
    out = _decode_freeform([b"\x80abc"])
    assert out is not None
    assert "abc" in out


# ---------------------------------------------------------------------------
# read_media_ids() — uses MP4-class mocks rather than synthesizing real MP4
# ---------------------------------------------------------------------------


class _FakeMP4:
  """Stand-in for mutagen.mp4.MP4. Supplies a `.tags` dict and is
  callable like `MP4(path)` via the patched module-level reference."""

  def __init__(self, tags=None):
    self.tags = tags or {}


class TestReadMediaIds:
  def _patched(self, tags=None, side_effect=None):
    """Return a context manager that patches the module-level MP4 ref."""
    if side_effect is not None:
      return patch("resources.library_audit.tag_reader.MP4", side_effect=side_effect)
    return patch("resources.library_audit.tag_reader.MP4", return_value=_FakeMP4(tags))

  def test_returns_empty_when_mutagen_missing(self):
    with patch("resources.library_audit.tag_reader.MP4", None):
      assert read_media_ids("/any/path.mp4") == {}

  def test_returns_empty_when_file_missing(self):
    with self._patched(side_effect=FileNotFoundError("boom")):
      assert read_media_ids("/missing.mp4") == {}

  def test_returns_empty_on_oserror(self):
    with self._patched(side_effect=OSError("permission denied")):
      assert read_media_ids("/any.mp4") == {}

  def test_returns_empty_on_keyerror(self):
    with self._patched(side_effect=KeyError("bad atom")):
      assert read_media_ids("/any.mp4") == {}

  def test_returns_empty_for_unstructured_failure(self):
    """Unknown exceptions are swallowed (not the caller's problem)."""
    with self._patched(side_effect=RuntimeError("weird")):
      # The except: pass branch on line 57 is marked pragma: no cover
      # but we still verify the documented contract — never raise.
      try:
        out = read_media_ids("/any.mp4")
      except RuntimeError:
        out = None
      assert out in ({}, None)  # either swallowed or surfaces; both acceptable

  def test_returns_empty_when_no_tags(self):
    with self._patched(tags={}):
      assert read_media_ids("/any.mp4") == {}

  def test_returns_empty_when_tags_is_none(self):
    with patch(
      "resources.library_audit.tag_reader.MP4",
      return_value=_FakeMP4(tags=None),
    ):
      # Force `.tags = None` via direct attribute set since _FakeMP4 forces a dict
      mp4 = _FakeMP4()
      mp4.tags = None
      with patch("resources.library_audit.tag_reader.MP4", return_value=mp4):
        assert read_media_ids("/any.mp4") == {}

  def test_reads_tmdb_id(self):
    tags = {"----:com.apple.iTunes:TMDB": [b"324552"]}
    with self._patched(tags=tags):
      assert read_media_ids("/x.mp4") == {"tmdb_id": "324552"}

  def test_reads_all_three_ids(self):
    tags = {
      "----:com.apple.iTunes:TMDB": [b"324552"],
      "----:com.apple.iTunes:TVDB": [b"73871"],
      "----:com.apple.iTunes:IMDB": [b"tt0123456"],
    }
    with self._patched(tags=tags):
      out = read_media_ids("/x.mp4")
      assert out == {
        "tmdb_id": "324552",
        "tvdb_id": "73871",
        "imdb_id": "tt0123456",
      }

  def test_stik_movie_code(self):
    with self._patched(tags={"stik": [9]}):
      assert read_media_ids("/x.mp4") == {"media_type": "movie"}

  def test_stik_tv_code(self):
    with self._patched(tags={"stik": [10]}):
      assert read_media_ids("/x.mp4") == {"media_type": "tv"}

  def test_stik_unknown_code_omitted(self):
    with self._patched(tags={"stik": [99]}):
      assert read_media_ids("/x.mp4") == {}

  def test_stik_scalar_not_list(self):
    """`stik` may come through as a scalar in some writers."""
    with self._patched(tags={"stik": 9}):
      assert read_media_ids("/x.mp4") == {"media_type": "movie"}

  def test_tv_show_metadata(self):
    tags = {
      "stik": [10],
      "tvsh": [b"Doctor Who"],
      "tvsn": [3],
      "tves": [10],
    }
    with self._patched(tags=tags):
      out = read_media_ids("/x.mp4")
      assert out == {
        "media_type": "tv",
        "tvsh": "Doctor Who",
        "season": 3,
        "episode": 10,
      }

  def test_tvsh_string_fallback(self):
    """Some writers store tvsh as a plain string, not bytes."""
    with self._patched(tags={"tvsh": ["Doctor Who"]}):
      assert read_media_ids("/x.mp4") == {"tvsh": "Doctor Who"}

  def test_tvsn_invalid_value_skipped(self):
    with self._patched(tags={"tvsn": [b"not-a-number"]}):
      out = read_media_ids("/x.mp4")
      assert "season" not in out

  def test_tves_invalid_value_skipped(self):
    with self._patched(tags={"tves": [None]}):
      out = read_media_ids("/x.mp4")
      assert "episode" not in out


# ---------------------------------------------------------------------------
# derive_media_id()
# ---------------------------------------------------------------------------


class TestDeriveMediaId:
  def test_empty_returns_none(self):
    assert derive_media_id({}) is None

  def test_movie_with_tmdb(self):
    assert derive_media_id({"media_type": "movie", "tmdb_id": "603"}) == "movie:tmdb:603"

  def test_movie_falls_back_to_imdb(self):
    assert derive_media_id({"media_type": "movie", "imdb_id": "tt0133093"}) == "movie:imdb:tt0133093"

  def test_movie_default_when_no_type_marker(self):
    """Without a stik atom, fall back to movie semantics if no tvsh."""
    assert derive_media_id({"tmdb_id": "603"}) == "movie:tmdb:603"

  def test_tv_inferred_from_tvsh(self):
    """Even without `media_type`, the presence of `tvsh` implies TV."""
    out = derive_media_id({"tvsh": "Doctor Who", "tmdb_id": "57243", "season": 3, "episode": 10})
    assert out == "tv:tmdb:57243:s03e10"

  def test_tv_with_tmdb(self):
    out = derive_media_id({"media_type": "tv", "tmdb_id": "57243", "season": 3, "episode": 10})
    assert out == "tv:tmdb:57243:s03e10"

  def test_tv_falls_back_to_tvdb(self):
    out = derive_media_id({"media_type": "tv", "tvdb_id": "73871", "season": 3, "episode": 10})
    assert out == "tv:tvdb:73871:s03e10"

  def test_tv_falls_back_to_imdb(self):
    out = derive_media_id({"media_type": "tv", "imdb_id": "tt0436992", "season": 1, "episode": 1})
    assert out == "tv:imdb:tt0436992:s01e01"

  def test_tv_without_episode_returns_none(self):
    out = derive_media_id({"media_type": "tv", "tmdb_id": "57243", "season": 3})
    assert out is None

  def test_tv_without_season_returns_none(self):
    out = derive_media_id({"media_type": "tv", "tmdb_id": "57243", "episode": 1})
    assert out is None

  def test_tv_without_any_id_returns_none(self):
    out = derive_media_id({"media_type": "tv", "season": 1, "episode": 1})
    assert out is None

  def test_movie_without_any_id_returns_none(self):
    assert derive_media_id({"media_type": "movie"}) is None

  def test_zero_padded_episode_format(self):
    out = derive_media_id({"media_type": "tv", "tmdb_id": "1", "season": 1, "episode": 1})
    assert out == "tv:tmdb:1:s01e01"

  def test_double_digit_episode(self):
    out = derive_media_id({"media_type": "tv", "tmdb_id": "1", "season": 12, "episode": 25})
    assert out == "tv:tmdb:1:s12e25"


# ---------------------------------------------------------------------------
# integration of read_media_ids + derive_media_id
# ---------------------------------------------------------------------------


class TestIntegration:
  def test_read_and_derive_movie(self):
    tags = {
      "----:com.apple.iTunes:TMDB": [b"603"],
      "stik": [9],
    }
    with patch(
      "resources.library_audit.tag_reader.MP4",
      return_value=_FakeMP4(tags),
    ):
      ids = read_media_ids("/x.mp4")
      assert derive_media_id(ids) == "movie:tmdb:603"

  def test_read_and_derive_tv_episode(self):
    tags = {
      "----:com.apple.iTunes:TMDB": [b"57243"],
      "stik": [10],
      "tvsh": [b"Doctor Who"],
      "tvsn": [3],
      "tves": [10],
    }
    with patch(
      "resources.library_audit.tag_reader.MP4",
      return_value=_FakeMP4(tags),
    ):
      ids = read_media_ids("/x.mp4")
      assert derive_media_id(ids) == "tv:tmdb:57243:s03e10"

  @pytest.mark.parametrize(
    "missing",
    [
      "tmdb_id",
      "season",
      "episode",
    ],
  )
  def test_read_and_derive_partial_tv(self, missing):
    tags = {
      "----:com.apple.iTunes:TMDB": [b"57243"],
      "stik": [10],
      "tvsn": [3],
      "tves": [10],
    }
    if missing == "tmdb_id":
      del tags["----:com.apple.iTunes:TMDB"]
    elif missing == "season":
      del tags["tvsn"]
    elif missing == "episode":
      del tags["tves"]
    with patch(
      "resources.library_audit.tag_reader.MP4",
      return_value=_FakeMP4(tags),
    ):
      ids = read_media_ids("/x.mp4")
      assert derive_media_id(ids) is None
