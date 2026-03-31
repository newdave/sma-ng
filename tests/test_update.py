"""Tests for update.py - config patching for Docker/environment-based deployments."""

import configparser
import os
import xml.etree.ElementTree as ET
from unittest.mock import patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_update(env, ini_path, xml_path=None):
    """Execute update.py main() in an isolated environment."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("update", os.path.join(PROJECT_ROOT, "update.py"))
    module = importlib.util.module_from_spec(spec)

    with (
        patch.dict(os.environ, env, clear=True),
        patch("resources.readsettings.ReadSettings._validate_binaries"),
    ):
        try:
            spec.loader.exec_module(module)
            # Override module-level paths so main() uses the test-provided values
            module.autoProcess = ini_path
            module.xml = xml_path or "/nonexistent/config.xml"
            module.main()
        except SystemExit as e:
            return e.code
    return 0


class TestUpdateBasic:
    """Test update.py basic config patching."""

    def test_sets_ffmpeg_from_env(self, tmp_ini):
        ini = tmp_ini()
        env = {
            "SMA_FFMPEG_PATH": "/custom/ffmpeg",
            "SMA_FFPROBE_PATH": "/custom/ffprobe",
            "SMA_PATH": PROJECT_ROOT,
        }
        _run_update(env, ini)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Converter", "ffmpeg") == "/custom/ffmpeg"
        assert cfg.get("Converter", "ffprobe") == "/custom/ffprobe"

    def test_defaults_ffmpeg_when_env_absent(self, tmp_ini):
        ini = tmp_ini()
        _run_update({"SMA_PATH": PROJECT_ROOT}, ini)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Converter", "ffmpeg") == "ffmpeg"
        assert cfg.get("Converter", "ffprobe") == "ffprobe"

    def test_exits_if_ini_missing(self):
        env = {"SMA_PATH": PROJECT_ROOT}
        code = _run_update(env, "/nonexistent/autoProcess.ini")
        assert code == 1


class TestUpdateXMLParsing:
    """Test update.py XML config parsing (SMA_RS / media manager integration)."""

    def _write_xml(self, tmp_path, port="8989", ssl_port="9898", url_base="", enable_ssl="False", api_key="TESTKEY"):
        xml_path = str(tmp_path / "config.xml")
        root = ET.Element("Config")
        ET.SubElement(root, "Port").text = port
        ET.SubElement(root, "SslPort").text = ssl_port
        ET.SubElement(root, "UrlBase").text = url_base
        ET.SubElement(root, "EnableSsl").text = enable_ssl
        ET.SubElement(root, "ApiKey").text = api_key
        tree = ET.ElementTree(root)
        tree.write(xml_path)
        return xml_path

    def test_sets_sonarr_settings_from_xml(self, tmp_ini, tmp_path):
        ini = tmp_ini()
        xml = self._write_xml(tmp_path, port="8989", api_key="abc123")
        env = {
            "SMA_RS": "Sonarr",
            "SMA_PATH": PROJECT_ROOT,
        }
        _run_update(env, ini, xml_path=xml)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Sonarr", "apikey") == "abc123"
        assert cfg.get("Sonarr", "port") == "8989"
        assert cfg.get("Sonarr", "ssl") == "false"

    def test_uses_ssl_port_when_ssl_enabled(self, tmp_ini, tmp_path):
        ini = tmp_ini()
        xml = self._write_xml(tmp_path, port="8989", ssl_port="9898", enable_ssl="True")
        env = {"SMA_RS": "Sonarr", "SMA_PATH": PROJECT_ROOT}
        _run_update(env, ini, xml_path=xml)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Sonarr", "port") == "9898"
        assert cfg.get("Sonarr", "ssl") == "true"

    def test_sets_host_from_env(self, tmp_ini, tmp_path):
        ini = tmp_ini()
        xml = self._write_xml(tmp_path)
        env = {
            "SMA_RS": "Sonarr",
            "SMA_PATH": PROJECT_ROOT,
            "HOST": "10.0.0.5",
        }
        _run_update(env, ini, xml_path=xml)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Sonarr", "host") == "10.0.0.5"

    def test_defaults_host_to_localhost_when_no_env(self, tmp_ini, tmp_path):
        ini = tmp_ini()
        xml = self._write_xml(tmp_path)
        env = {"SMA_RS": "Sonarr", "SMA_PATH": PROJECT_ROOT}
        _run_update(env, ini, xml_path=xml)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Sonarr", "host") == "127.0.0.1"

    def test_skips_xml_when_sma_rs_not_set(self, tmp_ini, tmp_path):
        ini = tmp_ini()
        xml = self._write_xml(tmp_path, api_key="SHOULD_NOT_APPEAR")
        env = {"SMA_PATH": PROJECT_ROOT}
        _run_update(env, ini, xml_path=xml)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        # apikey should remain empty (from default ini)
        assert cfg.get("Sonarr", "apikey") == ""

    def test_webroot_set_from_xml(self, tmp_ini, tmp_path):
        ini = tmp_ini()
        xml = self._write_xml(tmp_path, url_base="/sonarr")
        env = {"SMA_RS": "Sonarr", "SMA_PATH": PROJECT_ROOT}
        _run_update(env, ini, xml_path=xml)
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        assert cfg.get("Sonarr", "webroot") == "/sonarr"
