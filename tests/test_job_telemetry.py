"""SMANG-12 review fixes: HDR classes, copy-encoder telemetry, robust event parsing."""

import json
from unittest import mock
from unittest.mock import MagicMock, patch

from converter.ffmpeg import MediaStreamInfo
from manual import emit_job_summary, media_facts, processFile
from resources.naming import HDR_DISPLAY, classify_hdr
from tests.test_manual import TestJobSummaryEvent, TestProcessFile
from tests.test_mediaprocessor import TestProcess
from tests.test_worker import _SUMMARY_SOURCE, _make_worker

_HDR10PLUS = "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"


def _video(transfer="bt709", side_data=(), dv_profile=None):
  v = MediaStreamInfo()
  v.type = "video"
  v.color = {"transfer": transfer}
  v.framedata = {"side_data_list": [{"side_data_type": t} for t in side_data]}
  v.dv_profile = dv_profile
  return v


class TestClassifyHdr:
  def test_classes(self):
    assert classify_hdr(_video()) == "sdr"
    assert classify_hdr(_video("smpte2084")) == "hdr10"
    assert classify_hdr(_video("arib-std-b67")) == "hlg"
    assert classify_hdr(_video("smpte2084", [_HDR10PLUS])) == "hdr10plus"
    assert classify_hdr(_video("smpte2084", ["Dolby Vision Metadata"])) == "dolby_vision"
    assert classify_hdr(_video("smpte2084", dv_profile=8)) == "dolby_vision"
    assert classify_hdr(_video("bt709", dv_profile=5)) == "dolby_vision"

  def test_dolby_vision_beats_hdr10plus(self):
    assert classify_hdr(_video("smpte2084", [_HDR10PLUS, "Dolby Vision Metadata"])) == "dolby_vision"

  def test_framedata_transfer_and_none(self):
    v = _video("")
    v.framedata["color_transfer"] = "smpte2084"
    assert classify_hdr(v) == "hdr10"
    assert classify_hdr(None) is None

  def test_display_output_unchanged(self):
    assert HDR_DISPLAY == {"smpte2084": "HDR", "arib-std-b67": "HLG", "bt2020": "HDR10"}

  def test_media_facts_uses_shared_classes(self):
    dv = TestJobSummaryEvent._info()
    dv.video.dv_profile = 8
    assert media_facts(dv)["hdr"] == "dolby_vision"
    plus = TestJobSummaryEvent._info()
    plus.video.framedata = {"side_data_list": [{"side_data_type": _HDR10PLUS}]}
    assert media_facts(plus)["hdr"] == "hdr10plus"


class TestCopyEncoder:
  def test_process_marks_bypassed_result(self):
    mp = TestProcess()._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=({"video": {}, "audio": []}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=True)
    mp.getDimensions = MagicMock(return_value={"x": 1, "y": 1})
    assert mp.process("/fake/movie.mp4", info=info)["bypassed"] is True

  def test_process_marks_converted_result(self):
    mp = TestProcess()._make_mp()
    info = MagicMock()
    mp.isValidSource = MagicMock(return_value=info)
    mp.outputDirHasFreeSpace = MagicMock(return_value=True)
    mp.generateOptions = MagicMock(return_value=({"video": {}, "audio": [{"map": 1, "codec": "aac"}]}, [], [], [], []))
    mp.canBypassConvert = MagicMock(return_value=False)
    mp._run_ffmpeg = MagicMock(return_value=("/fake/out.mp4", "/fake/input.mkv", []))
    mp._verify_output = MagicMock(return_value=None)
    mp.getDimensions = MagicMock(return_value={"x": 1, "y": 1})
    assert mp.process("/fake/input.mkv", info=info)["bypassed"] is False

  def test_emit_job_summary_carries_encoder(self):
    with patch("manual.log") as log:
      emit_job_summary(dest_info=TestJobSummaryEvent._info(codec="h264", transfer="bt709"), encoder=("copy", "copy"))
    payload = json.loads(log.info.call_args[0][0])
    assert payload["encoder_name"] == "copy"
    assert payload["encoder_backend"] == "copy"

  def test_process_file_reports_copy_encoder_for_bypassed_output(self):
    mp = TestProcessFile()._make_mp()
    mp.converter.probe.return_value = TestJobSummaryEvent._info(codec="h264", transfer="bt709")
    mp.process.return_value = {
      "output": "/out/file.mp4",
      "input": "/file.mp4",
      "input_deleted": False,
      "delete": False,
      "external_subs": [],
      "options": {},
      "x": 1,
      "y": 1,
      "cues_to_front": False,
      "bypassed": True,
    }
    mp.replicate.return_value = ["/out/file.mp4"]
    mp.getDefaultAudioLanguage.return_value = None
    mp.restoreFromOutput.return_value = "/out/file.mp4"
    with patch("manual.getInfo", return_value=None), patch("manual.find_arr_instance", return_value=(None, None)), patch("manual.triggerRescan", return_value=None):
      with patch("manual.emit_job_summary") as emit:
        processFile("/file.mp4", mp, info=TestJobSummaryEvent._info())
    assert emit.call_args_list[1].kwargs["encoder"] == ("copy", "copy")

  def test_process_file_leaves_encoder_to_ladder_when_converted(self):
    mp = TestProcessFile()._make_mp()
    mp.converter.probe.return_value = TestJobSummaryEvent._info(codec="h264", transfer="bt709")
    mp.process.return_value = {
      "output": "/out/file.mp4",
      "input": "/file.mp4",
      "input_deleted": False,
      "delete": False,
      "external_subs": [],
      "options": {},
      "x": 1,
      "y": 1,
      "cues_to_front": False,
      "bypassed": False,
    }
    mp.replicate.return_value = ["/out/file.mp4"]
    mp.getDefaultAudioLanguage.return_value = None
    mp.restoreFromOutput.return_value = "/out/file.mp4"
    with patch("manual.getInfo", return_value=None), patch("manual.find_arr_instance", return_value=(None, None)), patch("manual.triggerRescan", return_value=None):
      with patch("manual.emit_job_summary") as emit:
        processFile("/file.mp4", mp, info=TestJobSummaryEvent._info())
    assert emit.call_args_list[1].kwargs.get("encoder") is None

  def test_worker_sets_encoder_from_summary_when_ladder_did_not_run(self):
    worker = _make_worker()
    worker._record_job_summary('{"encoder_backend":"copy","encoder_name":"copy","event":"sma.job.summary"}')
    assert worker._last_encoder_name == "copy"
    assert worker._last_encoder_backend == "copy"

  def test_worker_keeps_ladder_encoder(self):
    worker = _make_worker()
    worker._last_encoder_name = "h265qsv"
    worker._last_encoder_backend = "qsv"
    worker._record_job_summary('{"encoder_backend":"copy","encoder_name":"copy","event":"sma.job.summary"}')
    assert worker._last_encoder_name == "h265qsv"
    assert worker._last_encoder_backend == "qsv"


class TestEventParsing:
  def test_summary_parsed_after_prefix_containing_braces(self):
    worker = _make_worker()
    worker._record_job_summary("INFO [{node} job:1] " + _SUMMARY_SOURCE)
    assert worker._job_telemetry["source_video_codec"] == "hevc"

  def test_attempts_parsed_after_prefix_containing_braces(self):
    worker = _make_worker()
    line = 'INFO [{x}] {"attempts":[{"failure_class":null,"tier":"hw"}],"encoder_backend":"qsv","encoder_name":"h265qsv","event":"ffmpeg.attempts","result":"ok"}'
    worker._record_fallback_event(line)
    assert worker._last_encoder_name == "h265qsv"


class TestTimeoutTelemetry:
  def test_timeout_fail_job_carries_telemetry_and_duration(self, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"")
    db = mock.MagicMock()
    worker = _make_worker(job_db=db)

    def fake_run(*_a, **_k):
      worker._job_telemetry = {"source_video_codec": "hevc"}
      worker._abort_reason = "timeout"
      return (False, None, 90.0)

    worker._run_conversion = mock.MagicMock(side_effect=fake_run)
    worker.process_job({"id": 6, "path": str(media), "config": "/cfg.ini", "args": None})
    kwargs = db.fail_job.call_args.kwargs
    assert kwargs["telemetry"] == {"source_video_codec": "hevc"}
    assert kwargs["source_duration_seconds"] == 90.0
