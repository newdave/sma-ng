"""Unit tests for resources/library_audit (probes + engine + enumerator)."""

import unittest.mock as mock

from resources.library_audit.engine import AuditEngine, Finding, run_audit_inline
from resources.library_audit.enumerator import enumerate_paths
from resources.library_audit.kinds import (
  KIND_HINT_MEDIA,
  KIND_HINT_PRECONV,
  KIND_HINT_SIDECAR,
  KIND_HINT_TMP,
  FindingKind,
)
from resources.library_audit.probes import (
  ffprobe_check,
  preconv_original_check,
  sidecar_orphan_check,
  tmp_artifact_check,
)
from resources.library_audit.tag_reader import derive_media_id

# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def test_ffprobe_check_missing_file(tmp_path):
  result = ffprobe_check(str(tmp_path / "nope.mp4"))
  assert result == {"reason": "missing", "size_bytes": 0}


def test_ffprobe_check_empty_file(tmp_path):
  p = tmp_path / "empty.mp4"
  p.write_bytes(b"")
  result = ffprobe_check(str(p))
  assert result == {"reason": "empty", "size_bytes": 0}


def test_ffprobe_check_non_media_extension(tmp_path):
  p = tmp_path / "readme.txt"
  p.write_text("hello")
  assert ffprobe_check(str(p)) is None


def test_ffprobe_check_returns_none_when_probe_succeeds(tmp_path, monkeypatch):
  p = tmp_path / "ok.mp4"
  p.write_bytes(b"\x00" * 1024)
  fake_ffmpeg = mock.MagicMock()
  fake_ffmpeg.probe.return_value = mock.MagicMock(format=mock.MagicMock(format="mov"), streams=[1, 2])
  monkeypatch.setattr("resources.library_audit.probes.FFMpeg", lambda **_: fake_ffmpeg)
  assert ffprobe_check(str(p)) is None


def test_ffprobe_check_reports_failure_when_probe_returns_none(tmp_path, monkeypatch):
  p = tmp_path / "bad.mp4"
  p.write_bytes(b"\x00" * 1024)
  fake_ffmpeg = mock.MagicMock()
  fake_ffmpeg.probe.return_value = None
  monkeypatch.setattr("resources.library_audit.probes.FFMpeg", lambda **_: fake_ffmpeg)
  result = ffprobe_check(str(p))
  assert result is not None
  assert result["reason"] == "probe_returned_none"


def test_sidecar_orphan_with_parent(tmp_path):
  (tmp_path / "show.mkv").write_bytes(b"")
  (tmp_path / "show.en.srt").write_text("subs")
  assert sidecar_orphan_check(str(tmp_path / "show.en.srt")) is None


def test_sidecar_orphan_without_parent(tmp_path):
  (tmp_path / "ghost.en.srt").write_text("subs")
  result = sidecar_orphan_check(str(tmp_path / "ghost.en.srt"))
  assert result is not None
  assert result["reason"] == "no_parent_media"


def test_sidecar_orphan_skips_non_sidecars(tmp_path):
  (tmp_path / "movie.mp4").write_bytes(b"")
  assert sidecar_orphan_check(str(tmp_path / "movie.mp4")) is None


def test_tmp_artifact_check(tmp_path):
  p1 = tmp_path / "x.tmp"
  p1.write_text("")
  p2 = tmp_path / "movie.2.mp4"
  p2.write_bytes(b"")
  p3 = tmp_path / "fine.mp4"
  p3.write_bytes(b"")
  assert tmp_artifact_check(str(p1))["reason"] == "leftover_artifact"
  assert tmp_artifact_check(str(p2))["reason"] == "leftover_artifact"
  assert tmp_artifact_check(str(p3)) is None


def test_preconv_original_with_mp4_sibling(tmp_path):
  (tmp_path / "movie.mkv").write_bytes(b"")
  (tmp_path / "movie.mp4").write_bytes(b"")
  result = preconv_original_check(str(tmp_path / "movie.mkv"))
  assert result is not None
  assert result["mp4_sibling"].endswith("movie.mp4")


def test_preconv_original_without_mp4_sibling(tmp_path):
  (tmp_path / "lonely.mkv").write_bytes(b"")
  assert preconv_original_check(str(tmp_path / "lonely.mkv")) is None


# ---------------------------------------------------------------------------
# Enumerator
# ---------------------------------------------------------------------------


def test_enumerate_classifies_files(tmp_path):
  (tmp_path / "show.mp4").write_bytes(b"")
  (tmp_path / "show.en.srt").write_text("")
  (tmp_path / "x.tmp").write_text("")
  (tmp_path / "y.partial").write_text("")
  (tmp_path / "z.mkv").write_bytes(b"")
  units = dict(enumerate_paths([str(tmp_path)]))
  hints = set(units.values())
  assert KIND_HINT_MEDIA in hints
  assert KIND_HINT_SIDECAR in hints
  assert KIND_HINT_TMP in hints
  assert KIND_HINT_PRECONV in hints  # .mkv when no mp4 sibling


def test_enumerate_skips_extras_dirs(tmp_path):
  (tmp_path / "Movie.mp4").write_bytes(b"")
  extras = tmp_path / "Extras"
  extras.mkdir()
  (extras / "trailer.mp4").write_bytes(b"")
  units = list(enumerate_paths([str(tmp_path)], skip_dirs=["Extras"]))
  paths = [u[0] for u in units]
  assert any("Movie.mp4" in p for p in paths)
  assert not any("trailer" in p for p in paths)


def test_enumerate_skips_recycle_bin(tmp_path):
  (tmp_path / "movie.mp4").write_bytes(b"")
  bin_dir = tmp_path / ".bin"
  bin_dir.mkdir()
  (bin_dir / "ghost.mp4").write_bytes(b"")
  units = list(enumerate_paths([str(tmp_path)], is_recycle_bin_path=lambda p: p == str(bin_dir)))
  paths = [u[0] for u in units]
  assert any("movie.mp4" in p for p in paths)
  assert not any("ghost" in p for p in paths)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _engine(dry_run=True, auto_fix=None):
  job_db = mock.MagicMock()
  pcm = mock.MagicMock()
  pcm.default_config = "/tmp/sma.yml"
  pcm.get_recycle_bin.return_value = None
  pcm.get_config_for_path.return_value = "/tmp/sma.yml"
  pcm.get_args_for_path.return_value = []
  log = mock.MagicMock()
  engine = AuditEngine(job_db, pcm, log, dry_run=dry_run, auto_fix=auto_fix)
  return engine, job_db, pcm


def test_engine_probe_one_returns_finding_for_failed_ffprobe(tmp_path, monkeypatch):
  bad = tmp_path / "bad.mp4"
  bad.write_bytes(b"\x00" * 100)
  monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: {"reason": "probe_returned_none"})
  engine, _db, _pcm = _engine()
  finding = engine.probe_one({"path": str(bad), "kind_hint": KIND_HINT_MEDIA, "audit_id": 1})
  assert finding is not None
  assert finding.kind == FindingKind.FFPROBE_FAILED


def test_engine_probe_one_records_media_id_for_clean_mp4(tmp_path, monkeypatch):
  good = tmp_path / "ok.mp4"
  good.write_bytes(b"")
  monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
  monkeypatch.setattr("resources.library_audit.engine.read_media_ids", lambda p: {"tmdb_id": "603", "media_type": "movie"})
  engine, db, _pcm = _engine()
  result = engine.probe_one({"path": str(good), "kind_hint": KIND_HINT_MEDIA, "audit_id": 7})
  assert result is None
  db.record_media_id.assert_called_once_with(7, str(good), "movie:tmdb:603")


def test_engine_maybe_auto_fix_dry_run_returns_dry_run():
  engine, _db, _pcm = _engine(dry_run=True)
  finding = Finding(FindingKind.ORPHAN_SIDECAR, "/tmp/x.srt")
  assert engine.maybe_auto_fix(finding) == "dry_run"


def test_engine_maybe_auto_fix_skips_when_disabled():
  auto = mock.MagicMock(orphan_sidecar=False)
  engine, _db, _pcm = _engine(dry_run=False, auto_fix=auto)
  finding = Finding(FindingKind.ORPHAN_SIDECAR, "/tmp/x.srt")
  assert engine.maybe_auto_fix(finding) == "skipped"


def test_engine_auto_fix_queues_conversion_for_ffprobe_failed():
  auto = mock.MagicMock(ffprobe_failed=True, orphan_sidecar=False, leftover_tmp=False, preconv_original=False)
  engine, db, _pcm = _engine(dry_run=False, auto_fix=auto)
  db.add_job.return_value = 42
  finding = Finding(FindingKind.FFPROBE_FAILED, "/tmp/bad.mp4")
  assert engine.maybe_auto_fix(finding) == "queued"
  db.add_job.assert_called_once()


def test_engine_rollup_writes_duplicate_findings():
  engine, db, _pcm = _engine()
  db.find_duplicate_media_ids.return_value = {"movie:tmdb:603": ["/a/m.mp4", "/b/m.mp4"]}
  written = engine.rollup_duplicate_ids(99)
  assert written == 2
  assert db.upsert_finding.call_count == 2
  db.purge_audit_media_ids.assert_called_once_with(99)


# ---------------------------------------------------------------------------
# tag_reader
# ---------------------------------------------------------------------------


def test_derive_media_id_movie_tmdb():
  assert derive_media_id({"media_type": "movie", "tmdb_id": "603"}) == "movie:tmdb:603"


def test_derive_media_id_tv_tmdb_with_episode():
  assert derive_media_id({"media_type": "tv", "tmdb_id": "1399", "season": 3, "episode": 9}) == "tv:tmdb:1399:s03e09"


def test_derive_media_id_tv_without_episode_returns_none():
  assert derive_media_id({"media_type": "tv", "tmdb_id": "1399"}) is None


def test_derive_media_id_empty():
  assert derive_media_id({}) is None


# ---------------------------------------------------------------------------
# CLI inline path
# ---------------------------------------------------------------------------


def test_run_audit_inline_returns_nonzero_when_findings(tmp_path, monkeypatch, caplog):
  (tmp_path / "ghost.srt").write_text("")
  monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
  log = mock.MagicMock()
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), log)
  assert rc == 1


def test_run_audit_inline_returns_zero_when_clean(tmp_path):
  log = mock.MagicMock()
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), log)
  assert rc == 0
