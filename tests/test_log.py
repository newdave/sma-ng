"""Tests for resources/log.py - logging configuration and helpers."""

import os
from configparser import RawConfigParser

from resources.log import checkLoggingConfig, defaults


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
        config = RawConfigParser()
        for s in defaults:
            config.add_section(s)
            for k in defaults[s]:
                config.set(s, k, str(defaults[s][k]))
        config.set("handlers", "keys", "consoleHandler, sysLogHandler")
        with open(configfile, "w") as f:
            config.write(f)
        checkLoggingConfig(configfile)
        config2 = RawConfigParser()
        config2.read(configfile)
        assert "sysLogHandler" not in config2.get("handlers", "keys")

    def test_removes_legacy_file_handler(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        config = RawConfigParser()
        for s in defaults:
            config.add_section(s)
            for k in defaults[s]:
                config.set(s, k, str(defaults[s][k]))
        # Simulate a legacy config with fileHandler wired in
        config.set("handlers", "keys", "consoleHandler, fileHandler")
        config.add_section("handler_fileHandler")
        config.set("handler_fileHandler", "class", "handlers.RotatingFileHandler")
        config.set("handler_fileHandler", "args", "('sma.log', 'a', 100000, 3)")
        config.set("logger_root", "handlers", "consoleHandler, fileHandler")
        with open(configfile, "w") as f:
            config.write(f)
        checkLoggingConfig(configfile)
        config2 = RawConfigParser()
        config2.read(configfile)
        assert "fileHandler" not in config2.get("handlers", "keys")
        assert not config2.has_section("handler_fileHandler")
        assert "fileHandler" not in config2.get("logger_root", "handlers")

    def test_no_file_handler_in_fresh_config(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        checkLoggingConfig(configfile)
        config = RawConfigParser()
        config.read(configfile)
        assert "fileHandler" not in config.get("handlers", "keys")
        assert not config.has_section("handler_fileHandler")

    def test_strips_trailing_commas(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        config = RawConfigParser()
        for s in defaults:
            config.add_section(s)
            for k in defaults[s]:
                config.set(s, k, str(defaults[s][k]))
        config.set("handlers", "keys", "consoleHandler, ")
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
        checkLoggingConfig(configfile)


class TestGetLogger:
    def test_returns_logger_with_custom_path(self, tmp_path):
        from resources.log import getLogger

        logger = getLogger("test", custompath=str(tmp_path))
        assert logger is not None
        assert logger.name == "test"
        configpath = os.path.join(str(tmp_path), "config")
        assert os.path.isdir(configpath)

    def test_returns_logger_with_file_custom_path(self, tmp_path):
        from resources.log import getLogger

        fakefile = str(tmp_path / "somefile.txt")
        logger = getLogger("test2", custompath=fakefile)
        assert logger is not None
