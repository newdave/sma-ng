"""Argv-level tests for hardware-acceleration option generation in converter/avcodecs.py."""

import pytest

from converter import avcodecs
from converter.avcodecs import (
  H264QSVCodec,
  H264VAAPICodec,
  H265QSVCodec,
  H265VAAPICodec,
  NVEncH264Codec,
  NVEncH265Codec,
)


def _vf(opts):
  assert opts.count("-vf") == 1, opts
  return opts[opts.index("-vf") + 1]


class TestVAAPISurfaceFormat:
  def test_h265_10bit_uses_p010le(self):
    opts = H265VAAPICodec().parse_options({"codec": "h265vaapi", "pix_fmt": "yuv420p10le"})
    assert _vf(opts) == "format=p010le|vaapi,hwupload,scale_vaapi=format=p010le"
    assert "-pix_fmt" not in opts

  def test_h265_8bit_uses_nv12(self):
    opts = H265VAAPICodec().parse_options({"codec": "h265vaapi", "pix_fmt": "yuv420p"})
    assert _vf(opts) == "format=nv12|vaapi,hwupload,scale_vaapi=format=nv12"

  def test_h264_10bit_with_scale(self):
    opts = H264VAAPICodec().parse_options({"codec": "h264vaapi", "pix_fmt": "yuv420p10le", "width": 1280})
    assert _vf(opts) == "format=p010le|vaapi,hwupload,scale_vaapi=w=1280:h=trunc(ow/a/2)*2:format=p010le"

  def test_no_pix_fmt_defaults_nv12(self):
    opts = H265VAAPICodec().parse_options({"codec": "h265vaapi"})
    assert _vf(opts) == "format=nv12|vaapi,hwupload"


class TestNVEncH264:
  def test_scale_and_pix_fmt_single_filter(self):
    opts = NVEncH264Codec().parse_options({"codec": "h264_nvenc", "pix_fmt": "yuv420p", "width": 1280, "height": 720})
    assert "-pix_fmt" not in opts
    assert _vf(opts) == "scale_npp=w=1280:h=720:format=yuv420p"

  def test_pix_fmt_only(self):
    opts = NVEncH264Codec().parse_options({"codec": "h264_nvenc", "pix_fmt": "yuv420p"})
    assert "-pix_fmt" not in opts
    assert _vf(opts) == "scale_npp=format=yuv420p"

  def test_matches_h265_behaviour(self):
    src = {"codec": "CODEC", "pix_fmt": "yuv420p", "width": 1280, "height": 720}
    assert _vf(NVEncH264Codec().parse_options({**src, "codec": "h264_nvenc"})) == _vf(NVEncH265Codec().parse_options({**src, "codec": "h265_nvenc"}))


class TestQSVSingleScaleFilter:
  @pytest.mark.parametrize("cls", [H264QSVCodec, H265QSVCodec])
  def test_width_and_10bit_fold_into_one_filter(self, cls):
    opts = cls().parse_options({"codec": cls.codec_name, "pix_fmt": "yuv420p10le", "width": 1280})
    vf = _vf(opts)
    assert vf == "scale_qsv=w=1280:h=trunc(ow/a/2)*2:format=p010le"
    assert vf.count("scale_qsv") == 1
    assert "-pix_fmt" not in opts

  @pytest.mark.parametrize("cls", [H264QSVCodec, H265QSVCodec])
  def test_width_height_8bit(self, cls):
    opts = cls().parse_options({"codec": cls.codec_name, "pix_fmt": "yuv420p", "width": 1280, "height": 720})
    assert _vf(opts) == "scale_qsv=w=1280:h=720:format=nv12"

  @pytest.mark.parametrize("cls", [H264QSVCodec, H265QSVCodec])
  def test_format_only(self, cls):
    opts = cls().parse_options({"codec": cls.codec_name, "pix_fmt": "yuv420p10le"})
    assert _vf(opts) == "scale_qsv=format=p010le"


class TestVAAPIDefaultRenderNode:
  def test_prefers_renderd128_when_present(self, monkeypatch):
    monkeypatch.setattr(avcodecs.os.path, "exists", lambda p: True)
    monkeypatch.setattr(avcodecs.glob, "glob", lambda p: ["/dev/dri/renderD129"])
    assert avcodecs._default_vaapi_render_node() == "/dev/dri/renderD128"

  def test_first_existing_node_when_128_missing(self, monkeypatch):
    monkeypatch.setattr(avcodecs.os.path, "exists", lambda p: False)
    monkeypatch.setattr(avcodecs.glob, "glob", lambda p: ["/dev/dri/renderD130", "/dev/dri/renderD129"])
    assert avcodecs._default_vaapi_render_node() == "/dev/dri/renderD129"

  def test_falls_back_to_128_when_none(self, monkeypatch):
    monkeypatch.setattr(avcodecs.os.path, "exists", lambda p: False)
    monkeypatch.setattr(avcodecs.glob, "glob", lambda p: [])
    assert avcodecs._default_vaapi_render_node() == "/dev/dri/renderD128"

  def test_fallback_node_in_argv(self, monkeypatch):
    monkeypatch.setattr(avcodecs, "_default_vaapi_render_node", lambda: "/dev/dri/renderD129")
    opts = H265VAAPICodec().parse_options({"codec": "h265vaapi"})
    assert "vaapi=vaapi0:/dev/dri/renderD129" in opts

  def test_configured_device_wins(self):
    opts = H265VAAPICodec().parse_options({"codec": "h265vaapi", "device": "sma"})
    assert "-init_hw_device" not in opts
    assert opts[opts.index("-filter_hw_device") + 1] == "sma"
