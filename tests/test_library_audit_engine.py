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
  HYBRID_DIMS_TABLE,
  ffprobe_check,
  hybrid_aspect_check,
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
    "resources.library_audit.engine.hybrid_aspect_check",
    lambda *a, **k: {"width": 1920, "height": 1088, "true_height": 1080, "dar": "16:9"},
  )
  monkeypatch.setattr("resources.library_audit.engine.repair_hybrid_aspect", lambda *a, **k: True)
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), mock.MagicMock(), repair_hybrid=True)
  assert rc == 0
  assert "action=repaired" in capsys.readouterr().out


def test_run_audit_inline_repair_hybrid_failure_exits_nonzero(tmp_path, monkeypatch, capsys):
  (tmp_path / "show.mp4").write_bytes(b"x")
  monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
  monkeypatch.setattr(
    "resources.library_audit.engine.hybrid_aspect_check",
    lambda *a, **k: {"width": 1920, "height": 1088, "true_height": 1080, "dar": "16:9"},
  )
  monkeypatch.setattr("resources.library_audit.engine.repair_hybrid_aspect", lambda *a, **k: False)
  rc = run_audit_inline([str(tmp_path)], mock.MagicMock(skip_dirs=[]), mock.MagicMock(), repair_hybrid=True)
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
      lambda _p: None,
    )
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x.tmp", "kind_hint": KIND_HINT_TMP}) is None

  def test_tmp_hint_returns_leftover_finding(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.tmp_artifact_check",
      lambda _p: {"reason": "stale", "size_bytes": 0},
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
      lambda _p: {"reason": "stale", "size_bytes": 0},
    )
    out = _inline_probe("/x.tmp", KIND_HINT_TMP, None, {})
    assert out is not None
    assert out.kind == FindingKind.LEFTOVER_TMP

  def test_inline_tmp_clean(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr(
      "resources.library_audit.engine.tmp_artifact_check",
      lambda _p: None,
    )
    assert _inline_probe("/x.tmp", KIND_HINT_TMP, None, {}) is None

  def test_inline_unknown_hint(self):
    from resources.library_audit.engine import _inline_probe

    assert _inline_probe("/x", "unknown_hint", None, {}) is None


# ---------------------------------------------------------------------------
# Hybrid-aspect detection (probe + dims table)
# ---------------------------------------------------------------------------


def _hybrid_probe_patch(monkeypatch, width=1920, height=1088, sar=None, info="video"):
  """Patch probes.FFMpeg with a probe returning a video stream of the given
  dims/SAR. ``info=None`` → probe returns None; ``info="novideo"`` → probed
  info has no video stream."""
  fake_ffmpeg = mock.MagicMock()
  if info is None:
    fake_ffmpeg.probe.return_value = None
  elif info == "novideo":
    fake_ffmpeg.probe.return_value = mock.MagicMock(video=None)
  else:
    video = mock.MagicMock()
    video.video_width = width
    video.video_height = height
    video.sample_aspect_ratio = sar
    fake_ffmpeg.probe.return_value = mock.MagicMock(video=video)
  monkeypatch.setattr("resources.library_audit.probes.FFMpeg", lambda **_: fake_ffmpeg)
  return fake_ffmpeg


class TestHybridAspectCheck:
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
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, width=1920, height=1088, sar=None)
    details = hybrid_aspect_check(str(p))
    assert details == {
      "reason": "stretched_dims",
      "width": 1920,
      "height": 1088,
      "true_height": 1080,
      "dar": "1920:1080",
    }

  def test_detects_with_explicit_square_par(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, width=3840, height=2176, sar="1:1")
    details = hybrid_aspect_check(str(p))
    assert details is not None
    assert details["true_height"] == 2160
    assert details["dar"] == "3840:2160"

  def test_non_square_par_means_already_repaired(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, width=1920, height=1088, sar="135:136")
    assert hybrid_aspect_check(str(p)) is None

  def test_standard_dims_not_flagged(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, width=1920, height=1080, sar=None)
    assert hybrid_aspect_check(str(p)) is None

  def test_non_mp4_extension_skipped_without_probe(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mkv"
    p.write_bytes(b"\x00" * 64)
    fake = _hybrid_probe_patch(monkeypatch, width=1920, height=1088, sar=None)
    assert hybrid_aspect_check(str(p)) is None
    fake.probe.assert_not_called()

  def test_missing_file_returns_none(self, tmp_path, monkeypatch):
    fake = _hybrid_probe_patch(monkeypatch)
    assert hybrid_aspect_check(str(tmp_path / "nope.mp4")) is None
    fake.probe.assert_not_called()

  def test_probe_returns_none(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, info=None)
    assert hybrid_aspect_check(str(p)) is None

  def test_probe_without_video_stream(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, info="novideo")
    assert hybrid_aspect_check(str(p)) is None

  def test_probe_exception_swallowed(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    fake_ffmpeg = mock.MagicMock()
    fake_ffmpeg.probe.side_effect = RuntimeError("probe blew up")
    monkeypatch.setattr("resources.library_audit.probes.FFMpeg", lambda **_: fake_ffmpeg)
    assert hybrid_aspect_check(str(p)) is None

  def test_non_int_dims_returns_none(self, tmp_path, monkeypatch):
    p = tmp_path / "movie.mp4"
    p.write_bytes(b"\x00" * 64)
    _hybrid_probe_patch(monkeypatch, width=None, height=1088, sar=None)
    assert hybrid_aspect_check(str(p)) is None


# ---------------------------------------------------------------------------
# Hybrid-aspect engine integration (probe_one, maybe_auto_fix, _inline_probe)
# ---------------------------------------------------------------------------

_HYBRID_DETAILS = {
  "reason": "stretched_dims",
  "width": 1920,
  "height": 1088,
  "true_height": 1080,
  "dar": "1920:1080",
}


def _hybrid_auto_fix(enabled=True):
  return mock.MagicMock(
    ffprobe_failed=False,
    orphan_sidecar=False,
    leftover_tmp=False,
    preconv_original=False,
    hybrid_aspect=enabled,
  )


class TestEngineHybridAspect:
  def test_probe_media_returns_hybrid_finding(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.hybrid_aspect_check", lambda *a, **k: dict(_HYBRID_DETAILS))
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA})
    assert finding is not None
    assert finding.kind == FindingKind.HYBRID_ASPECT
    assert finding.details["dar"] == "1920:1080"

  def test_probe_media_clean_when_hybrid_check_passes(self, monkeypatch):
    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.hybrid_aspect_check", lambda *a, **k: None)
    engine, _db, _pcm = _engine()
    assert engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA}) is None

  def test_ffprobe_failure_wins_over_hybrid_check(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.ffprobe_check",
      lambda *a, **k: {"reason": "probe_returned_none"},
    )
    called = []
    monkeypatch.setattr(
      "resources.library_audit.engine.hybrid_aspect_check",
      lambda *a, **k: called.append(True) or dict(_HYBRID_DETAILS),
    )
    engine, _db, _pcm = _engine()
    finding = engine.probe_one({"path": "/x.mp4", "kind_hint": KIND_HINT_MEDIA})
    assert finding is not None
    assert finding.kind == FindingKind.FFPROBE_FAILED
    assert called == []

  def test_auto_fix_repairs_when_enabled(self, monkeypatch):
    calls = []

    def fake_repair(path, dar, ffmpeg_dir=None, logger=None):
      calls.append((path, dar, ffmpeg_dir))
      return True

    monkeypatch.setattr("resources.library_audit.engine.repair_hybrid_aspect", fake_repair)
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_hybrid_auto_fix(enabled=True))
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert engine.maybe_auto_fix(finding) == "repaired"
    assert calls == [("/x.mp4", "1920:1080", None)]

  def test_auto_fix_skipped_when_repair_fails(self, monkeypatch):
    monkeypatch.setattr(
      "resources.library_audit.engine.repair_hybrid_aspect",
      lambda *a, **k: False,
    )
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_hybrid_auto_fix(enabled=True))
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert engine.maybe_auto_fix(finding) == "skipped"

  def test_auto_fix_skipped_when_disabled(self, monkeypatch):
    def boom(*_a, **_k):
      raise AssertionError("repair must not run when disabled")

    monkeypatch.setattr("resources.library_audit.engine.repair_hybrid_aspect", boom)
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_hybrid_auto_fix(enabled=False))
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert engine.maybe_auto_fix(finding) == "skipped"

  def test_auto_fix_dry_run_returns_dry_run(self):
    engine, _db, _pcm = _engine(dry_run=True, auto_fix=_hybrid_auto_fix(enabled=True))
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", dict(_HYBRID_DETAILS))
    assert engine.maybe_auto_fix(finding) == "dry_run"

  def test_auto_fix_skipped_when_dar_missing(self, monkeypatch):
    def boom(*_a, **_k):
      raise AssertionError("repair must not run without a dar")

    monkeypatch.setattr("resources.library_audit.engine.repair_hybrid_aspect", boom)
    engine, _db, _pcm = _engine(dry_run=False, auto_fix=_hybrid_auto_fix(enabled=True))
    finding = Finding(FindingKind.HYBRID_ASPECT, "/x.mp4", {"reason": "stretched_dims"})
    assert engine.maybe_auto_fix(finding) == "skipped"

  def test_inline_probe_emits_hybrid_finding(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.read_media_ids", lambda _p: {})
    monkeypatch.setattr("resources.library_audit.engine.hybrid_aspect_check", lambda *a, **k: dict(_HYBRID_DETAILS))
    out = _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, {})
    assert out is not None
    assert out.kind == FindingKind.HYBRID_ASPECT
    assert out.details["true_height"] == 1080

  def test_inline_probe_clean_when_no_hybrid(self, monkeypatch):
    from resources.library_audit.engine import _inline_probe

    monkeypatch.setattr("resources.library_audit.engine.ffprobe_check", lambda *a, **k: None)
    monkeypatch.setattr("resources.library_audit.engine.read_media_ids", lambda _p: {})
    monkeypatch.setattr("resources.library_audit.engine.hybrid_aspect_check", lambda *a, **k: None)
    assert _inline_probe("/x.mp4", KIND_HINT_MEDIA, None, {}) is None

  def test_audit_auto_fix_schema_defaults_hybrid_off(self):
    from resources.config_schema import AuditAutoFix

    assert AuditAutoFix().hybrid_aspect is False


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
