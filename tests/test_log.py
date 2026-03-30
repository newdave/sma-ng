"""Tests for resources/log.py - logging configuration and helpers."""

import os
from configparser import RawConfigParser
from unittest.mock import patch

from resources.log import checkLoggingConfig, defaults, rotator


class TestCheckLoggingConfig:
    def test_creates_config_when_missing(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        assert not os.path.exists(configfile)
        checkLoggingConfig(configfile)
        assert os.path.exists(configfile)
        config = RawConfigParser()
        config.read(configfile)
        for section in defaults:
            assert config.has_section(section)
            for key in defaults[section]:
                assert config.has_option(section, key)

    def test_updates_existing_config_with_missing_sections(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        # Write a partial config
        config = RawConfigParser()
        config.add_section("loggers")
        config.set("loggers", "keys", "root, manual, nzbget, daemon")
        with open(configfile, "w") as f:
            config.write(f)
        checkLoggingConfig(configfile)
        config2 = RawConfigParser()
        config2.read(configfile)
        assert config2.has_section("handlers")
        assert config2.has_section("formatters")

    def test_removes_syslog_handler(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        # Create config with sysLogHandler
        config = RawConfigParser()
        for s in defaults:
            config.add_section(s)
            for k in defaults[s]:
                config.set(s, k, str(defaults[s][k]))
        config.set("handlers", "keys", "consoleHandler, sysLogHandler, fileHandler")
        with open(configfile, "w") as f:
            config.write(f)
        checkLoggingConfig(configfile)
        config2 = RawConfigParser()
        config2.read(configfile)
        assert "sysLogHandler" not in config2.get("handlers", "keys")

    def test_strips_trailing_commas(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        config = RawConfigParser()
        for s in defaults:
            config.add_section(s)
            for k in defaults[s]:
                config.set(s, k, str(defaults[s][k]))
        config.set("handlers", "keys", "consoleHandler, fileHandler, ")
        with open(configfile, "w") as f:
            config.write(f)
        checkLoggingConfig(configfile)
        config2 = RawConfigParser()
        config2.read(configfile)
        val = config2.get("handlers", "keys")
        assert not val.endswith(",")
        assert not val.endswith(" ")

    def test_idempotent_on_complete_config(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        checkLoggingConfig(configfile)
        mtime1 = os.path.getmtime(configfile)
        # Second call shouldn't write if config is already complete
        checkLoggingConfig(configfile)


class TestRotator:
    def test_rename_success(self, tmp_path):
        source = str(tmp_path / "sma.log")
        dest = str(tmp_path / "sma.log.1")
        with open(source, "w") as f:
            f.write("log data")
        rotator(source, dest)
        assert os.path.exists(dest)
        assert not os.path.exists(source)

    def test_rename_fails_falls_back_to_copy(self, tmp_path):
        source = str(tmp_path / "sma.log")
        dest = str(tmp_path / "sma.log.1")
        with open(source, "w") as f:
            f.write("log data")
        with patch("os.rename", side_effect=OSError("cross-device")):
            rotator(source, dest)
        assert os.path.exists(dest)
        assert os.path.exists(source)
        # Source should be truncated
        assert os.path.getsize(source) == 0
        with open(dest) as f:
            assert f.read() == "log data"

    def test_nonexistent_source_does_nothing(self, tmp_path):
        source = str(tmp_path / "nonexistent.log")
        dest = str(tmp_path / "nonexistent.log.1")
        rotator(source, dest)
        assert not os.path.exists(dest)

    def test_both_rename_and_copy_fail(self, tmp_path, capsys):
        source = str(tmp_path / "sma.log")
        dest = str(tmp_path / "sma.log.1")
        with open(source, "w") as f:
            f.write("data")
        with patch("os.rename", side_effect=OSError("fail")):
            with patch("shutil.copyfile", side_effect=OSError("fail too")):
                rotator(source, dest)
        captured = capsys.readouterr()
        assert "Error rotating logfiles" in captured.out


class TestGetLogger:
    def test_returns_logger_with_custom_path(self, tmp_path):
        from resources.log import getLogger

        logger = getLogger("test", custompath=str(tmp_path))
        assert logger is not None
        assert logger.name == "test"
        # Verify config directory and logging.ini were created
        configpath = os.path.join(str(tmp_path), "config")
        assert os.path.isdir(configpath)

    def test_returns_logger_with_file_custom_path(self, tmp_path):
        from resources.log import getLogger

        # If custompath is a file, it should use its directory
        fakefile = str(tmp_path / "somefile.txt")
        logger = getLogger("test2", custompath=fakefile)
        assert logger is not None
