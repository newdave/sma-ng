"""Tests for the OpenVINO analyzer backend stub."""

from types import SimpleNamespace

import pytest

from resources.openvino_analyzer import (
  OpenVINOAnalyzerBackend,
  OpenVINODependencyError,
  OpenVINODeviceError,
  ensure_requested_devices_available,
  normalize_openvino_device,
)


class FakeCore:
  def __init__(self, available_devices):
    self.available_devices = available_devices
    self.properties = []

  def set_property(self, value):
    self.properties.append(value)


class TestNormalizeOpenVINODevice:
  def test_normalizes_npu_device(self):
    assert normalize_openvino_device("npu") == "NPU"

  def test_normalizes_auto_with_npu_targets(self):
    assert normalize_openvino_device("auto:npu,cpu") == "AUTO:NPU,CPU"

  def test_deduplicates_targets(self):
    assert normalize_openvino_device("multi:npu,npu,gpu") == "MULTI:NPU,GPU"

  def test_rejects_unknown_target(self):
    with pytest.raises(OpenVINODeviceError):
      normalize_openvino_device("auto:fpga")


class TestEnsureRequestedDevicesAvailable:
  def test_accepts_available_npu(self):
    assert ensure_requested_devices_available("NPU", ["CPU", "NPU"]) == "NPU"

  def test_accepts_device_families_with_indexes(self):
    assert ensure_requested_devices_available("AUTO:NPU,GPU", ["GPU.0", "NPU"]) == "AUTO:NPU,GPU"

  def test_rejects_missing_npu(self):
    with pytest.raises(OpenVINODeviceError, match="NPU"):
      ensure_requested_devices_available("AUTO:NPU,CPU", ["CPU", "GPU"])


class TestOpenVINOAnalyzerBackend:
  def test_raises_dependency_error_when_openvino_missing(self, monkeypatch):
    backend = OpenVINOAnalyzerBackend({"device": "NPU"})

    def raise_import_error(name):
      raise ImportError(name)

    monkeypatch.setattr("resources.openvino_analyzer.importlib.import_module", raise_import_error)

    with pytest.raises(OpenVINODependencyError):
      backend.create_core()

  def test_validate_device_supports_npu(self):
    backend = OpenVINOAnalyzerBackend({"device": "NPU"})
    core = FakeCore(["CPU", "NPU"])

    assert backend.validate_device(core=core) == "NPU"

  def test_create_core_uses_runtime_api_and_sets_cache(self, monkeypatch, tmp_path):
    fake_core = FakeCore(["CPU", "NPU"])
    fake_openvino = SimpleNamespace(runtime=SimpleNamespace(Core=lambda: fake_core))
    backend = OpenVINOAnalyzerBackend({"device": "AUTO:NPU,CPU", "cache_dir": str(tmp_path / "ov-cache")})

    monkeypatch.setattr("resources.openvino_analyzer.importlib.import_module", lambda name: fake_openvino)

    created_core = backend.create_core()

    assert created_core is fake_core
    assert fake_core.properties == [{"CACHE_DIR": str(tmp_path / "ov-cache")}]

  def test_analyze_returns_placeholder_observations_after_validation(self):
    backend = OpenVINOAnalyzerBackend({"device": "NPU"})
    core = FakeCore(["NPU"])

    observations = backend.analyze(core=core)

    assert observations.content_type == "general_live_action"
