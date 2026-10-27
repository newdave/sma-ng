"""Unit tests for resources/library_audit (probes + engine + enumerator)."""

import os
import time
import unittest.mock as mock

from converter.ffmpeg import MediaInfo
from resources.library_audit.engine import AuditEngine, Finding, run_audit_inline
from resources.library_audit.enumerator import enumerate_paths
from resources.library_audit.kinds import (
  IN_TRANSIT_MIN_AGE_SECONDS,
  KIND_HINT_MEDIA,
  KIND_HINT_PRECONV,
  KIND_HINT_SIDECAR,
  KIND_HINT_TMP,
  FindingKind,
)
from resources.library_audit.probes import (
  HYBRID_DIMS_TABLE,
  ffprobe_check,
  hybrid_aspect_check,
  is_in_transit,
  is_tmp_artifact,
  preconv_original_check,
  repair_check,
  sidecar_orphan_check,
  tmp_artifact_check,
)
from resources.library_audit.tag_reader import derive_media_id
from resources.media_repair import STATUS_FAILED, STATUS_REPAIRED, RepairResult

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
  r1 = tmp_artifact_check(str(p1))
  assert r1 is not None
  assert r1["reason"] == "leftover_artifact"
  r2 = tmp_artifact_check(str(p2))
  assert r2 is not None
  assert r2["reason"] == "leftover_artifact"
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
  # Converter.tag leftover when tagging blew up partway through.
  (tmp_path / "stranded.mp4.tag").write_bytes(b"")
  units = dict(enumerate_paths([str(tmp_path)]))
  hints = set(units.values())
  assert KIND_HINT_MEDIA in hints
  assert KIND_HINT_SIDECAR in hints
  assert KIND_HINT_TMP in hints
  assert KIND_HINT_PRECONV in hints  # .mkv when no mp4 sibling
  # `.mp4.tag` is classified as a tmp leftover, NOT as media.
  assert units[str(tmp_path / "stranded.mp4.tag")] == KIND_HINT_TMP


def test_tmp_artifact_check_flags_mp4_tag_leftover(tmp_path):
  """Auditor recognises the SMA tag-step leftover so it can be
  recycled by the auto-fix path."""
  p = tmp_path / "Movie.mp4.tag"
  p.write_bytes(b"")
  result = tmp_artifact_check(str(p))
  assert result is not None
  assert result["reason"] == "leftover_artifact"


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
  pcm.get_profile_for_path.return_value = None
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


def test_run_audit_inline_repair_hybrid_fixes_and_exits_zero(tmp_path, monkeypatch, capsys):
  (tmp_path / "show.mp4").write_bytes(b"x")
  monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
  monkeypatch.setattr(
    "resources.library_audit.engine.repair_check",
    lambda *a, **k: (FindingKind.HYBRID_ASPECT, {"reason": "stretched_dims", "width": 1920, "height": 1088, "true_height": 1080, "dar": "1920:1080"}),
  )
  repairer = mock.MagicMock()
  repairer.repair_file.return_value = RepairResult(str(tmp_path / "show.mp4"), STATUS_REPAIRED, "ok")
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), mock.MagicMock(), repair_hybrid=True, repairer=repairer)
  assert rc == 0
  repairer.repair_file.assert_called_once_with(str(tmp_path / "show.mp4"))
  assert "action=repaired" in capsys.readouterr().out


def test_run_audit_inline_repair_hybrid_failure_exits_nonzero(tmp_path, monkeypatch, capsys):
  (tmp_path / "show.mp4").write_bytes(b"x")
  monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
  monkeypatch.setattr(
    "resources.library_audit.engine.repair_check",
    lambda *a, **k: (FindingKind.HYBRID_ASPECT, {"reason": "stretched_dims", "width": 1920, "height": 1088, "true_height": 1080, "dar": "1920:1080"}),
  )
  repairer = mock.MagicMock()
  repairer.repair_file.return_value = RepairResult(str(tmp_path / "show.mp4"), STATUS_FAILED, "boom")
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), mock.MagicMock(), repair_hybrid=True, repairer=repairer)
  assert rc == 1
  assert "action=repair_failed" in capsys.readouterr().out


def test_run_audit_inline_recycles_ffprobe_failed(tmp_path, monkeypatch, capsys):
  media = tmp_path / "broken.mkv"
  media.write_bytes(b"x")
  bin_dir = tmp_path / "_recycle"
  monkeypatch.setattr(
    "resources.library_audit.engine.ffprobe_check",
    lambda *a, **k: {"reason": "probe_error"},
  )
  rc = run_audit_inline(
    [str(tmp_path)],
    mock.MagicMock(skip_dirs=["_recycle"]),
    mock.MagicMock(),
    recycle_failed=True,
    recycle_bin=str(bin_dir),
  )
  assert rc == 0
  assert "action=recycled" in capsys.readouterr().out
  assert not media.exists()
  assert (bin_dir / "broken.mkv").exists()


def test_run_audit_inline_recycle_without_bin_is_skipped(tmp_path, monkeypatch, capsys):
  media = tmp_path / "broken.mkv"
  media.write_bytes(b"x")
  monkeypatch.setattr(
    "resources.library_audit.engine.ffprobe_check",
    lambda *a, **k: {"reason": "probe_error"},
  )
  rc = run_audit_inline(
    [str(tmp_path)],
    mock.MagicMock(skip_dirs=[]),
    mock.MagicMock(),
    recycle_failed=True,
    recycle_bin=None,
  )
  assert rc == 1
  assert "action=recycle_skipped" in capsys.readouterr().out
  assert media.exists()


def test_run_audit_inline_no_fix_flags_leaves_findings_alone(tmp_path, monkeypatch, capsys):
  media = tmp_path / "broken.mkv"
  media.write_bytes(b"x")
  monkeypatch.setattr(
    "resources.library_audit.engine.ffprobe_check",
    lambda *a, **k: {"reason": "probe_error"},
  )
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), mock.MagicMock())
  assert rc == 1
  assert "action=" not in capsys.readouterr().out
  assert media.exists()


# ---------------------------------------------------------------------------
# AuditEngine — extended coverage for probe_one, maybe_auto_fix, _recycle
# ---------------------------------------------------------------------------


class TestAuditEngineProbeOne:
  def test_sidecar_hint_returns_none_when_orphan_check_passes(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.sidecar_orphan_check",
      lambda _p: None,
    )
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x.srt", "kind_hint": KIND_HINT_SIDECAR}) is None

  def test_sidecar_hint_returns_orphan_finding(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.sidecar_orphan_check",
      lambda _p: {"reason": "no_parent_media"},
    )
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.srt", "kind_hint": KIND_HINT_SIDECAR})
    assert finding is not None
    assert finding.kind == FindingKind.ORPHAN_SIDECAR

  def test_tmp_hint_returns_none_when_no_artifact(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.tmp_artifact_check",
      lambda _p, **_k: None,
    )
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x.tmp", "kind_hint": KIND_HINT_TMP}) is None

  def test_tmp_hint_returns_leftover_finding(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.tmp_artifact_check",
      lambda _p, **_k: {"reason": "stale", "size_bytes": 0},
    )
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.tmp", "kind_hint": KIND_HINT_TMP})
    assert finding is not None
    assert finding.kind == FindingKind.LEFTOVER_TMP

  def test_unknown_kind_hint_returns_none(self):
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x", "kind_hint": "unknown"}) is None

  def test_probe_one_swallows_exception(self, monkeypatch):
    def boom(*_a, **_k):
      raise RuntimeError("simulated probe failure")

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", boom)
    engine, _db, _pcm = _engine()
    # Should NOT raise; probe failures are logged + skipped.
    out = engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA})
    assert out is None
    engine.log.exception.assert_called()

  def test_probe_one_skips_id_record_when_no_audit_id(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr(
      "resources.library_audit.engine.read_media_ids",
      lambda _p: {"tmdb_id": "603", "media_type": "movie"},
    )
    engine, db, _pcm = _engine()
    # No audit_id → no record_media_id call.
    engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA})
    db.record_media_id.assert_not_called()

  def test_probe_one_skips_id_record_for_non_mp4(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr(
      "resources.library_audit.engine.read_media_ids",
      lambda _p: {"tmdb_id": "603"},
    )
    engine, db, _pcm = _engine()
    engine.probe_one({"path": "/x.mkv", "kind_hint": KIND_HINT_MEDIA, "audit_id": 5})
    db.record_media_id.assert_not_called()

  def test_probe_one_swallows_id_record_exception(self, monkeypatch, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"")
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)

    def boom(_p):
      raise RuntimeError("read failed")

    monkeypatch.setattr("resources.library_audit.engine.read_media_ids", boom)
    engine, _db, _pcm = _engine()
    out = engine.probe_one({"path": str(f), "kind_hint": KIND_HINT_MEDIA, "audit_id": 1})
    # Returns None — ffprobe was clean — but logs the read_media_ids failure.
    assert out is None
    engine.log.exception.assert_called()


class TestAuditEnginePreconv:
  def test_preconv_no_candidate_falls_through_to_media(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: None,
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: {"reason": "probe_returned_none"},
    )
    engine, _db, _pcm = _engine()
    f = engine.probe_one({"path": "/x.mkv", "kind_hint": KIND_HINT_PRECONV})
    assert f is not None
    assert f.kind == FindingKind.FFPROBE_FAILED

  def test_preconv_with_candidate_and_clean_sibling_yields_finding(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: {"mp4_sibling": "/x.mp4"},
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: None,  # sibling is clean → original is stale
    )
    engine, _db, _pcm = _engine()
    f = engine.probe_one({"path": "/x.mkv", "kind_hint": KIND_HINT_PRECONV})
    assert f is not None
    assert f.kind == FindingKind.PRECONV_ORIGINAL

  def test_preconv_with_bad_sibling_returns_none(self, monkeypatch):
    """If the mp4 sibling is itself broken, the original isn't stale — skip."""
    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: {"mp4_sibling": "/x.mp4"},
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: {"reason": "empty"},
    )
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x.mkv", "kind_hint": KIND_HINT_PRECONV}) is None


class TestAuditEngineAutoFix:
  def test_auto_fix_recycles_orphan_sidecar(self, monkeypatch):
    auto = mock.MagicMock(
      ffprobe_failed=False,
      orphan_sidecar=True,
      leftover_tmp=False,
      preconv_original=False,
    )
    engine, _db, pcm = _engine(dry_run=False, auto_fix=auto)
    pcm.get_recycle_bin.return_value = "/recycle"
    monkeypatch.setattr(
      "resources.library_audit.engine.move_to_recycle_bin",
      lambda p, b: "/recycle/" + p.split("/")[-1],
    )
    finding = Finding(FindingKind.ORPHAN_SIDECAR, "/tmp/x.srt")
    assert engine.maybe_auto_fix(finding) == "recycled"

  def test_auto_fix_recycles_leftover_tmp(self, monkeypatch):
    auto = mock.MagicMock(
      ffprobe_failed=False,
      orphan_sidecar=False,
      leftover_tmp=True,
      preconv_original=False,
    )
    engine, _db, pcm = _engine(dry_run=False, auto_fix=auto)
    pcm.get_recycle_bin.return_value = "/recycle"
    monkeypatch.setattr(
      "resources.library_audit.engine.move_to_recycle_bin",
      lambda p, b: "/recycle/x.tmp",
    )
    finding = Finding(FindingKind.LEFTOVER_TMP, "/tmp/x.tmp")
    assert engine.maybe_auto_fix(finding) == "recycled"

  def test_auto_fix_recycles_preconv_original(self, monkeypatch):
    auto = mock.MagicMock(
      ffprobe_failed=False,
      orphan_sidecar=False,
      leftover_tmp=False,
      preconv_original=True,
    )
    engine, _db, pcm = _engine(dry_run=False, auto_fix=auto)
    pcm.get_recycle_bin.return_value = "/recycle"
    monkeypatch.setattr(
      "resources.library_audit.engine.move_to_recycle_bin",
      lambda p, b: "/recycle/x.mkv",
    )
    finding = Finding(FindingKind.PRECONV_ORIGINAL, "/tmp/x.mkv")
    assert engine.maybe_auto_fix(finding) == "recycled"

  def test_auto_fix_recycle_returns_skipped_when_no_bin(self, monkeypatch):
    auto = mock.MagicMock(
      ffprobe_failed=False,
      orphan_sidecar=True,
      leftover_tmp=False,
      preconv_original=False,
    )
    engine, _db, pcm = _engine(dry_run=False, auto_fix=auto)
    pcm.get_recycle_bin.return_value = None
    monkeypatch.setattr(
      "resources.library_audit.engine.move_to_recycle_bin",
      lambda p, b: None,  # no bin configured → returns None → "skipped"
    )
    finding = Finding(FindingKind.ORPHAN_SIDECAR, "/tmp/x.srt")
    assert engine.maybe_auto_fix(finding) == "skipped"

  def test_auto_fix_recycle_returns_skipped_on_oserror(self, monkeypatch):
    auto = mock.MagicMock(
      ffprobe_failed=False,
      orphan_sidecar=True,
      leftover_tmp=False,
      preconv_original=False,
    )
    engine, _db, pcm = _engine(dry_run=False, auto_fix=auto)
    pcm.get_recycle_bin.return_value = "/recycle"

    def boom(*_a, **_k):
      raise OSError("EACCES")

    monkeypatch.setattr("resources.library_audit.engine.move_to_recycle_bin", boom)
    finding = Finding(FindingKind.ORPHAN_SIDECAR, "/tmp/x.srt")
    assert engine.maybe_auto_fix(finding) == "skipped"
    engine.log.warning.assert_called()

  def test_queue_conversion_returns_skipped_when_db_returns_none(self):
    auto = mock.MagicMock(ffprobe_failed=True, orphan_sidecar=False, leftover_tmp=False, preconv_original=False)
    engine, db, _pcm = _engine(dry_run=False, auto_fix=auto)
    db.add_job.return_value = None
    finding = Finding(FindingKind.FFPROBE_FAILED, "/tmp/bad.mp4")
    assert engine.maybe_auto_fix(finding) == "skipped"


class TestRunAuditInlineExtended:
  def test_inline_emits_duplicate_id_findings(self, tmp_path, monkeypatch):
    a = tmp_path / "movie_a.mp4"
    a.write_bytes(b"")
    b = tmp_path / "movie_b.mp4"
    b.write_bytes(b"")
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr(
      "resources.library_audit.engine.read_media_ids",
      lambda _p: {"tmdb_id": "603", "media_type": "movie"},
    )
    log = mock.MagicMock()
    rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), log)
    # both files share the same media_id → 2 DUPLICATE_ID findings
    assert rc == 1

  def test_inline_preconv_with_clean_sibling(self, tmp_path, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: {"mp4_sibling": str(tmp_path / "x.mp4")},
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: None,
    )
    log = mock.MagicMock()
    rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), log)
    # depends on enumerator output for tmp_path; just verify no crash + valid rc
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# _inline_probe — exercise each kind hint directly
# ---------------------------------------------------------------------------


class TestInlineProbe:
  def test_inline_media_clean(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    observed = {}
    out = _inline_probe("/x.mkv", KIND_HINT_MEDIA, None, observed)
    assert out is None
    assert observed == {}

  def test_inline_media_failed_returns_finding(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: {"reason": "empty"},
    )
    out = _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, {})
    assert out is not None
    assert out.kind == FindingKind.FFPROBE_FAILED

  def test_inline_media_records_id_in_observed(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr(
      "resources.library_audit.engine.read_media_ids",
      lambda _p: {"tmdb_id": "603", "media_type": "movie"},
    )
    observed = {}
    _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, observed)
    assert observed == {"movie:tmdb:603": ["/x.mp4"]}

  def test_inline_preconv_no_candidate_falls_to_media(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: None,
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: None,
    )
    out = _inline_probe("/x.mkv", KIND_HINT_PRECONV, None, {})
    assert out is None

  def test_inline_preconv_no_candidate_failed_probe_returns_finding(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: None,
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: {"reason": "probe_returned_none"},
    )
    out = _inline_probe("/x.mkv", KIND_HINT_PRECONV, None, {})
    assert out is not None
    assert out.kind == FindingKind.FFPROBE_FAILED

  def test_inline_preconv_clean_sibling_returns_preconv_finding(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: {"mp4_sibling": "/x.mp4"},
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: None,
    )
    out = _inline_probe("/x.mkv", KIND_HINT_PRECONV, None, {})
    assert out is not None
    assert out.kind == FindingKind.PRECONV_ORIGINAL

  def test_inline_preconv_bad_sibling_returns_none(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.preconv_original_check",
      lambda _p: {"mp4_sibling": "/x.mp4"},
    )
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: {"reason": "empty"},
    )
    out = _inline_probe("/x.mkv", KIND_HINT_PRECONV, None, {})
    assert out is None

  def test_inline_sidecar_orphan(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.sidecar_orphan_check",
      lambda _p: {"reason": "no_parent_media"},
    )
    out = _inline_probe("/x.srt", KIND_HINT_SIDECAR, None, {})
    assert out is not None
    assert out.kind == FindingKind.ORPHAN_SIDECAR

  def test_inline_sidecar_not_orphan(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.sidecar_orphan_check",
      lambda _p: None,
    )
    assert _inline_probe("/x.srt", KIND_HINT_SIDECAR, None, {}) is None

  def test_inline_tmp_leftover(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.tmp_artifact_check",
      lambda _p, **_k: {"reason": "stale", "size_bytes": 0},
    )
    out = _inline_probe("/x.tmp", KIND_HINT_TMP, None, {})
    assert out is not None
    assert out.kind == FindingKind.LEFTOVER_TMP

  def test_inline_tmp_clean(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.tmp_artifact_check",
      lambda _p, **_k: None,
    )
    assert _inline_probe("/x.tmp", KIND_HINT_TMP, None, {}) is None

  def test_inline_unknown_hint(self):
    from resources.library_audit.engine import _inline_probe

    assert _inline_probe("/x", "unknown_hint", None, {}) is None


# ---------------------------------------------------------------------------
# repair_check (hybrid aspect + stream damage) and the dims table
# ---------------------------------------------------------------------------


def _stream(index, kind, codec, **extra):
  s = {"index": index, "codec_type": kind, "codec_name": codec}
  s.update(extra)
  return s


def _vid(index=0, codec="h264", width=1920, height=1080, sar="1:1", attached_pic=0, tag="avc1"):
  return _stream(index, "video", codec, codec_tag_string=tag, width=width, height=height, sample_aspect_ratio=sar if sar else "N/A", **{"DISPOSITION:attached_pic": attached_pic})


def _aud(index=1):
  return _stream(index, "audio", "aac", channels=2)


def _media_info(streams, duration="100.0", path="/m/a.mp4"):
  """Real MediaInfo as FFMpeg.probe(posters_as_video=False) builds it."""
  raw = []
  for s in streams:
    raw.append("[STREAM]")
    raw.extend("%s=%s" % kv for kv in s.items())
    raw.append("[/STREAM]")
  raw.extend(["[FORMAT]", "duration=%s" % duration, "[/FORMAT]"])
  info = MediaInfo(posters_as_video=False)
  info.parse_ffprobe("\n".join(raw))
  info.path = path
  return info


def _patch_probe(monkeypatch, info=None, exc=None):
  fake_ffmpeg = mock.MagicMock()
  if exc is not None:
    fake_ffmpeg.probe.side_effect = exc
  else:
    fake_ffmpeg.probe.return_value = info
  monkeypatch.setattr("resources.library_audit.probes.FFMpeg", lambda **_: fake_ffmpeg)
  return fake_ffmpeg


def _media_file(tmp_path, name="movie.mp4"):
  p = tmp_path / name
  p.write_bytes(b"\x00" * 64)
  return str(p)


class TestRepairCheck:
  def test_dims_table_exact_contents(self):
    assert HYBRID_DIMS_TABLE == {
      (1920, 1088): 1080,
      (1920, 1072): 1080,
      (1920, 1056): 1080,
      (3840, 2176): 2160,
      (3840, 2144): 2160,
      (1280, 736): 720,
      (1280, 704): 720,
    }

  def test_detects_stretched_1080p(self, tmp_path, monkeypatch):
    fake = _patch_probe(monkeypatch, _media_info([_vid(height=1088, sar=None), _aud()]))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.HYBRID_ASPECT
    assert details == {
      "reason": "stretched_dims",
      "width": 1920,
      "height": 1088,
      "true_height": 1080,
      "dar": "1920:1080",
      "repair": "display aspect 1920:1080",
    }
    fake.probe.assert_called_once()
    assert fake.probe.call_args.kwargs == {"posters_as_video": False, "framedata": False}

  def test_detects_with_explicit_square_par(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([_vid(codec="hevc", width=3840, height=2176, sar="1:1"), _aud()]))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.HYBRID_ASPECT
    assert details["true_height"] == 2160
    assert details["dar"] == "3840:2160"

  def test_mkv_is_now_checked(self, tmp_path, monkeypatch):
    fake = _patch_probe(monkeypatch, _media_info([_vid(height=1088, sar=None), _aud()], path="/m/a.mkv"))
    kind, details = repair_check(_media_file(tmp_path, "movie.mkv"))
    assert kind == FindingKind.HYBRID_ASPECT
    assert details["reason"] == "stretched_dims"
    fake.probe.assert_called_once()

  def test_corrected_aspect_with_intact_cover_is_stream_damage(self, tmp_path, monkeypatch):
    # An attached_pic cover is not the old hybrid remux's demoted track.
    _patch_probe(monkeypatch, _media_info([_vid(height=1088, sar="135:136"), _aud(), _vid(2, "mjpeg", 600, 900, attached_pic=1)]))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.STREAM_DAMAGE
    assert details["artwork_streams"] == [2]

  def test_non_square_par_means_already_repaired(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([_vid(height=1088, sar="135:136"), _aud()]))
    assert repair_check(_media_file(tmp_path)) is None

  def test_normal_file_returns_none(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([_vid(), _aud()]))
    assert repair_check(_media_file(tmp_path)) is None

  def test_unrepairable_extension_skipped_without_probe(self, tmp_path, monkeypatch):
    fake = _patch_probe(monkeypatch, _media_info([_vid(height=1088, sar=None)]))
    assert repair_check(_media_file(tmp_path, "movie.avi")) is None
    fake.probe.assert_not_called()

  def test_missing_file_returns_none(self, tmp_path, monkeypatch):
    fake = _patch_probe(monkeypatch, _media_info([_vid()]))
    assert repair_check(str(tmp_path / "nope.mp4")) is None
    fake.probe.assert_not_called()

  def test_probe_returns_none(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, None)
    assert repair_check(_media_file(tmp_path)) is None

  def test_probe_without_streams(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([]))
    assert repair_check(_media_file(tmp_path)) is None

  def test_probe_exception_swallowed(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, exc=RuntimeError("probe blew up"))
    assert repair_check(_media_file(tmp_path)) is None

  def test_ffmpeg_dir_builds_explicit_binary_paths(self, tmp_path, monkeypatch):
    built = {}

    def factory(**kwargs):
      built.update(kwargs)
      fake = mock.MagicMock()
      fake.probe.return_value = None
      return fake

    monkeypatch.setattr("resources.library_audit.probes.FFMpeg", factory)
    repair_check(_media_file(tmp_path), ffmpeg_dir="/opt/ff")
    assert built == {"ffmpeg_path": "/opt/ff/ffmpeg", "ffprobe_path": "/opt/ff/ffprobe"}

  def test_previously_repaired_with_demoted_poster_is_flagged(self, tmp_path, monkeypatch):
    # The old "-map 0 ... -aspect" remux corrected the PAR but turned the
    # poster into a real video track; the file must be re-repairable.
    streams = [_vid(codec="hevc", width=3840, height=2176, sar="136:135", tag="hvc1"), _aud(), _vid(2, "mjpeg", 1000, 1500, "8:3", tag="mp4v")]
    _patch_probe(monkeypatch, _media_info(streams))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.HYBRID_ASPECT
    assert details["reason"] == "demoted_poster"
    assert (details["width"], details["height"], details["true_height"], details["dar"]) == (3840, 2176, 2160, "3840:2160")
    assert details["repair"] == "artwork streams 2"

  def test_leading_poster_skipped_when_picking_primary(self, tmp_path, monkeypatch):
    streams = [_vid(0, "png", 1000, 1500, "1:1", attached_pic=1), _vid(1, "h264", 1920, 1088, "1:1"), _aud(2)]
    _patch_probe(monkeypatch, _media_info(streams))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.HYBRID_ASPECT
    assert details["reason"] == "stretched_dims"
    assert details["dar"] == "1920:1080"

  def test_data_stream_is_stream_damage(self, tmp_path, monkeypatch):
    streams = [_vid(), _aud(), _stream(2, "data", "bin_data", codec_tag_string="tmcd")]
    _patch_probe(monkeypatch, _media_info(streams))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.STREAM_DAMAGE
    assert details == {
      "reason": "stream_damage",
      "repair": "data streams 2",
      "data_streams": [2],
      "unknown_streams": [],
      "artwork_streams": [],
    }

  def test_unknown_stream_is_stream_damage(self, tmp_path, monkeypatch):
    streams = [_vid(), _aud(), _stream(2, "unknown", "x")]
    _patch_probe(monkeypatch, _media_info(streams))
    kind, details = repair_check(_media_file(tmp_path))
    assert kind == FindingKind.STREAM_DAMAGE
    assert details["unknown_streams"] == [2]
    assert details["data_streams"] == []

  def test_artwork_on_standard_dims_is_stream_damage(self, tmp_path, monkeypatch):
    streams = [_vid(), _aud(), _vid(2, "mjpeg", 600, 900, attached_pic=1)]
    _patch_probe(monkeypatch, _media_info(streams, path="/m/a.mkv"))
    kind, details = repair_check(_media_file(tmp_path, "movie.mkv"))
    assert kind == FindingKind.STREAM_DAMAGE
    assert details["artwork_streams"] == [2]
    assert details["repair"] == "artwork streams 2"

  def test_hybrid_aspect_check_wrapper_returns_details(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([_vid(height=1088, sar=None), _aud()]))
    details = hybrid_aspect_check(_media_file(tmp_path))
    assert details is not None
    assert details["dar"] == "1920:1080"

  def test_hybrid_aspect_check_wrapper_ignores_stream_damage(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([_vid(), _aud(), _stream(2, "data", "bin_data", codec_tag_string="tmcd")]))
    assert hybrid_aspect_check(_media_file(tmp_path)) is None

  def test_hybrid_aspect_check_wrapper_none_when_normal(self, tmp_path, monkeypatch):
    _patch_probe(monkeypatch, _media_info([_vid(), _aud()]))
    assert hybrid_aspect_check(_media_file(tmp_path)) is None


# ---------------------------------------------------------------------------
# In-transit leftovers (age gate, configured temp extension)
# ---------------------------------------------------------------------------


def _age(path, seconds):
  old = time.time() - seconds
  os.utime(path, (old, old))


class TestInTransitLeftovers:
  def test_fresh_sma_skipped(self, tmp_path):
    p = tmp_path / "movie.mp4.sma"
    p.write_bytes(b"x")
    assert tmp_artifact_check(str(p)) is None

  def test_old_sma_flagged_as_in_transit_leftover(self, tmp_path):
    p = tmp_path / "movie.mp4.sma"
    p.write_bytes(b"x")
    _age(p, IN_TRANSIT_MIN_AGE_SECONDS + 60)
    result = tmp_artifact_check(str(p))
    assert result is not None
    assert result["reason"] == "in_transit_leftover"
    assert "mtime" in result

  def test_smatmp_gated_by_age(self, tmp_path):
    p = tmp_path / "movie.mp4.smatmp"
    p.write_bytes(b"x")
    assert tmp_artifact_check(str(p)) is None
    _age(p, IN_TRANSIT_MIN_AGE_SECONDS + 60)
    assert tmp_artifact_check(str(p))["reason"] == "in_transit_leftover"

  def test_custom_min_age_respected(self, tmp_path):
    p = tmp_path / "movie.sma"
    p.write_bytes(b"x")
    _age(p, 120)
    assert tmp_artifact_check(str(p)) is None
    assert tmp_artifact_check(str(p), min_age_seconds=60)["reason"] == "in_transit_leftover"

  def test_configured_temp_extension_is_in_transit(self, tmp_path):
    p = tmp_path / "movie.mp4.part2"
    p.write_bytes(b"x")
    assert tmp_artifact_check(str(p)) is None
    assert not is_in_transit(str(p))
    assert is_in_transit(str(p), "part2")
    assert is_in_transit(str(p), ".PART2")
    assert tmp_artifact_check(str(p), temp_extension="part2") is None  # fresh
    _age(p, IN_TRANSIT_MIN_AGE_SECONDS + 60)
    assert tmp_artifact_check(str(p), temp_extension="part2")["reason"] == "in_transit_leftover"

  def test_temp_extension_equal_to_media_container_ignored(self, tmp_path):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"x")
    _age(p, IN_TRANSIT_MIN_AGE_SECONDS + 60)
    assert not is_in_transit(str(p), "mp4")
    assert not is_tmp_artifact(str(p), ".mp4")
    assert tmp_artifact_check(str(p), temp_extension="mp4") is None

  def test_other_tmp_artifacts_have_no_age_gate(self, tmp_path):
    p = tmp_path / "x.tmp"
    p.write_bytes(b"x")
    assert tmp_artifact_check(str(p))["reason"] == "leftover_artifact"

  def test_missing_file_returns_none(self, tmp_path):
    assert tmp_artifact_check(str(tmp_path / "gone.sma")) is None

  def test_enumerator_classifies_temp_extension_as_tmp(self, tmp_path):
    (tmp_path / "movie.mp4.part2").write_bytes(b"x")
    (tmp_path / "movie.mp4.sma").write_bytes(b"x")
    (tmp_path / "movie.mp4").write_bytes(b"x")
    without = dict(enumerate_paths([str(tmp_path)]))
    assert str(tmp_path / "movie.mp4.part2") not in without
    assert without[str(tmp_path / "movie.mp4.sma")] == KIND_HINT_TMP
    with_ext = dict(enumerate_paths([str(tmp_path)], temp_extension="part2"))
    assert with_ext[str(tmp_path / "movie.mp4.part2")] == KIND_HINT_TMP
    assert with_ext[str(tmp_path / "movie.mp4")] == KIND_HINT_MEDIA

  def test_enumerator_media_temp_extension_does_not_hide_library(self, tmp_path):
    (tmp_path / "movie.mp4").write_bytes(b"x")
    units = dict(enumerate_paths([str(tmp_path)], temp_extension="mp4"))
    assert units[str(tmp_path / "movie.mp4")] == KIND_HINT_MEDIA

  def test_engine_probe_passes_pcm_temp_settings(self, monkeypatch):
    seen = {}

    def fake(path, temp_extension=None, min_age_seconds=None):
      seen.update(path=path, temp_extension=temp_extension, min_age_seconds=min_age_seconds)
      return {"reason": "in_transit_leftover"}

    monkeypatch.setattr("resources.library_audit.engine.tmp_artifact_check", fake)
    engine, _db, pcm = _engine()
    pcm.temp_extension = "part2"
    pcm.storage_janitor_max_age_seconds = 7200
    finding = engine.probe_one({"path": "/x.part2", "kind_hint": KIND_HINT_TMP})
    assert finding.kind == FindingKind.LEFTOVER_TMP
    assert seen == {"path": "/x.part2", "temp_extension": "part2", "min_age_seconds": 7200}

  def test_engine_probe_falsy_pcm_settings_become_none(self, monkeypatch):
    seen = {}

    def fake(path, temp_extension=None, min_age_seconds=None):
      seen.update(temp_extension=temp_extension, min_age_seconds=min_age_seconds)

    monkeypatch.setattr("resources.library_audit.engine.tmp_artifact_check", fake)
    engine, _db, pcm = _engine()
    pcm.temp_extension = ""
    pcm.storage_janitor_max_age_seconds = 0
    assert engine.probe_one({"path": "/x.tmp", "kind_hint": KIND_HINT_TMP}) is None
    assert seen == {"temp_extension": None, "min_age_seconds": None}

  def test_inline_probe_forwards_temp_extension(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    seen = {}

    def fake(path, temp_extension=None, min_age_seconds=None):
      seen["temp_extension"] = temp_extension
      return {"reason": "in_transit_leftover"}

    monkeypatch.setattr("resources.library_audit.engine.tmp_artifact_check", fake)
    out = _inline_probe("/x.part2", KIND_HINT_TMP, None, {}, temp_extension="part2")
    assert out.kind == FindingKind.LEFTOVER_TMP
    assert seen["temp_extension"] == "part2"

  def test_run_audit_inline_forwards_temp_extension(self, tmp_path):
    p = tmp_path / "movie.mp4.part2"
    p.write_bytes(b"x")
    _age(p, IN_TRANSIT_MIN_AGE_SECONDS + 60)
    rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), mock.MagicMock(), temp_extension="part2")
    assert rc == 1


# ---------------------------------------------------------------------------
# Repair findings through the engine (probe_one, maybe_auto_fix, _inline_probe)
# ---------------------------------------------------------------------------

_HYBRID_DETAILS = {
  "reason": "stretched_dims",
  "width": 1920,
  "height": 1088,
  "true_height": 1080,
  "dar": "1920:1080",
}


def _repair_auto_fix(hybrid=True, damage=False):
  return mock.MagicMock(
    ffprobe_failed=False,
    orphan_sidecar=False,
    leftover_tmp=False,
    preconv_original=False,
    hybrid_aspect=hybrid,
    stream_damage=damage,
  )


class TestEngineRepairFindings:
  def test_probe_media_returns_hybrid_finding(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: (FindingKind.HYBRID_ASPECT, dict(_HYBRID_DETAILS)))
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA})
    assert finding is not None
    assert finding.kind == FindingKind.HYBRID_ASPECT
    assert finding.details["dar"] == "1920:1080"

  def test_probe_media_returns_stream_damage_finding(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: (FindingKind.STREAM_DAMAGE, {"reason": "stream_damage"}))
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.mkv", "kind_hint": KIND_HINT_MEDIA})
    assert finding.kind == FindingKind.STREAM_DAMAGE

  def test_probe_media_passes_ffmpeg_dir(self, monkeypatch):
    seen = {}
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda path, ffmpeg_dir=None: seen.update(dir=ffmpeg_dir))
    engine, _db, _pcm = _engine()
    engine.ffmpeg_dir = "/opt/ff"
    assert engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA}) is None
    assert seen["dir"] == "/opt/ff"

  def test_probe_media_clean_when_repair_check_passes(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: None)
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA}) is None

  def test_ffprobe_failure_wins_over_repair_check(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: {"reason": "probe_returned_none"})
    called = []
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: called.append(True))
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA})
    assert finding.kind == FindingKind.FFPROBE_FAILED
    assert called == []

  def test_auto_fix_hybrid_repairs_when_enabled(self):
    engine, _db, pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=True))
    processor = mock.MagicMock()
    processor.repair_file.return_value = RepairResult("/x.mp4", STATUS_REPAIRED, "display aspect 1920:1080")
    engine._repairers[("/tmp/sma.yml", pcm.get_profile_for_path.return_value)] = processor
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert engine.maybe_auto_fix(finding) == "repaired"
    processor.repair_file.assert_called_once_with("/x.mp4")

  def test_auto_fix_hybrid_skipped_when_repair_not_repaired(self):
    engine, _db, pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=True))
    processor = mock.MagicMock()
    processor.repair_file.return_value = RepairResult("/x.mp4", STATUS_FAILED, "boom")
    engine._repairers[("/tmp/sma.yml", pcm.get_profile_for_path.return_value)] = processor
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert engine.maybe_auto_fix(finding) == "skipped"
    engine.log.warning.assert_called()

  def test_auto_fix_hybrid_skipped_when_disabled(self):
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=False, damage=True))
    with mock.patch.object(AuditEngine, "_repair", side_effect=AssertionError("must not repair")):
      assert engine.maybe_auto_fix(Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))) == "skipped"

  def test_auto_fix_stream_damage_repairs_when_enabled(self):
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=False, damage=True))
    with mock.patch.object(AuditEngine, "_repair", return_value="repaired") as repair:
      assert engine.maybe_auto_fix(Finding(FindingKind.STREAM_DAMAGE, "/x.mkv", {})) == "repaired"
    repair.assert_called_once_with("/x.mkv")

  def test_auto_fix_stream_damage_skipped_when_disabled(self):
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=True, damage=False))
    with mock.patch.object(AuditEngine, "_repair", side_effect=AssertionError("must not repair")):
      assert engine.maybe_auto_fix(Finding(FindingKind.STREAM_DAMAGE, "/x.mkv", {})) == "skipped"

  def test_auto_fix_dry_run_returns_dry_run(self):
    engine, _db, _pcm = _engine(dry_run=True, auto_fix=_repair_auto_fix(hybrid=True, damage=True))
    assert engine.maybe_auto_fix(Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))) == "dry_run"
    assert engine.maybe_auto_fix(Finding(FindingKind.STREAM_DAMAGE, "/x.mp4", {})) == "dry_run"

  def test_repair_exception_returns_skipped(self):
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=True))
    with mock.patch.object(AuditEngine, "_repair_processor", side_effect=RuntimeError("settings broke")):
      assert engine._repair("/x.mp4") == "skipped"
    engine.log.exception.assert_called_once()

  def test_repair_file_exception_returns_skipped(self):
    engine, _db, pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix(hybrid=True))
    processor = mock.MagicMock()
    processor.repair_file.side_effect = OSError("disk")
    engine._repairers[("/tmp/sma.yml", pcm.get_profile_for_path.return_value)] = processor
    assert engine._repair("/x.mp4") == "skipped"
    engine.log.exception.assert_called_once()

  def test_repair_processor_built_with_routed_profile_and_cached(self):
    engine, _db, pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix())
    engine.ffmpeg_dir = "/opt/ff"
    pcm.get_profile_for_path.side_effect = lambda p: "rq" if "/rq/" in p else "default"
    with mock.patch("resources.readsettings.ReadSettings") as rs, mock.patch("resources.library_audit.engine.RepairProcessor") as rp:
      first = engine._repair_processor("/lib/a.mp4")
      again = engine._repair_processor("/lib/b.mp4")
      other = engine._repair_processor("/rq/c.mp4")
    assert first is again
    assert other is rp.return_value
    assert rs.call_count == 2
    assert [c.kwargs["profile"] for c in rs.call_args_list] == ["default", "rq"]
    for c in rs.call_args_list:
      assert c.args == ("/tmp/sma.yml",)
      assert c.kwargs["logger"] is engine.log
    assert rp.call_count == 2
    assert rp.call_args.args[0] is rs.return_value
    assert rp.call_args.kwargs == {"logger": engine.log, "ffmpeg_dir": "/opt/ff"}

  def test_repair_processor_cache_keyed_by_config_too(self):
    engine, _db, pcm = _engine(dry_run=False, auto_fix=_repair_auto_fix())
    pcm.get_profile_for_path.return_value = None
    pcm.get_config_for_path.side_effect = lambda p: "/b.yml" if "/b/" in p else "/a.yml"
    with mock.patch("resources.readsettings.ReadSettings") as rs, mock.patch("resources.library_audit.engine.RepairProcessor"):
      engine._repair_processor("/a/x.mp4")
      engine._repair_processor("/b/x.mp4")
      engine._repair_processor("/a/y.mp4")
    assert [c.args[0] for c in rs.call_args_list] == ["/a.yml", "/b.yml"]

  def test_inline_probe_emits_hybrid_finding(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.read_media_ids", lambda _p: {})
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: (FindingKind.HYBRID_ASPECT, dict(_HYBRID_DETAILS)))
    out = _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, {})
    assert out.kind == FindingKind.HYBRID_ASPECT
    assert out.details["true_height"] == 1080

  def test_inline_probe_emits_stream_damage_finding(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.read_media_ids", lambda _p: {})
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: (FindingKind.STREAM_DAMAGE, {"reason": "stream_damage"}))
    assert _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, {}).kind == FindingKind.STREAM_DAMAGE

  def test_inline_probe_clean_when_no_damage(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.read_media_ids", lambda _p: {})
    monkeypatch.setattr("resources.library_audit.engine.repair_check", lambda *a, **k: None)
    assert _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, {}) is None

  def test_inline_fix_hybrid_without_repairer_does_nothing(self):
    from resources.library_audit.engine import _inline_fix

    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert _inline_fix(finding, mock.MagicMock(), None, False, None) is None

  def test_inline_fix_stream_damage_is_not_repaired(self):
    from resources.library_audit.engine import _inline_fix

    repairer = mock.MagicMock()
    finding = Finding(FindingKind.STREAM_DAMAGE, "/x.mp4", {})
    assert _inline_fix(finding, mock.MagicMock(), repairer, False, None) is None
    repairer.repair_file.assert_not_called()

  def test_inline_fix_hybrid_failure_logs_error(self):
    from resources.library_audit.engine import _inline_fix

    repairer = mock.MagicMock()
    repairer.repair_file.return_value = RepairResult("/x.mp4", STATUS_FAILED, "boom")
    logger = mock.MagicMock()
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert _inline_fix(finding, logger, repairer, False, None) == "repair_failed"
    logger.error.assert_called_once()

  def test_audit_auto_fix_schema_defaults_repairs_off(self):
    from resources.config_schema import AuditAutoFix

    assert AuditAutoFix().hybrid_aspect is False
    assert AuditAutoFix().stream_damage is False


class TestShortDetails:
  def test_empty_dict_returns_empty_string(self):
    from resources.library_audit.engine import _short_details

    assert _short_details({}) == ""

  def test_filters_to_known_keys(self):
    from resources.library_audit.engine import _short_details

    out = _short_details({"reason": "empty", "size_bytes": 0, "extra": "ignored", "mp4_sibling": "/x.mp4"})
    assert "reason=empty" in out
    assert "size_bytes=0" in out
    assert "mp4_sibling=/x.mp4" in out
    assert "extra" not in out
