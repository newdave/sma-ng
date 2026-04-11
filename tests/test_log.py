"""Tests for resources/log.py - logging configuration and helpers."""

import io
import json
import logging
import os
from configparser import RawConfigParser

import pytest

from resources.daemon.context import JobContextFilter, clear_job_id, set_job_id
from resources.log import JSONFormatter, checkLoggingConfig, defaults


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


class TestJobContextFilter:
    def test_default_job_id_is_dash(self):
        filt = JobContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record)
        assert record.job_id == "-"  # type: ignore[attr-defined]

    def test_set_job_id_injects_into_record(self):
        filt = JobContextFilter()
        token = set_job_id(42)
        try:
            record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
            filt.filter(record)
            assert record.job_id == "42"  # type: ignore[attr-defined]
        finally:
            clear_job_id(token)

    def test_clear_job_id_restores_default(self):
        filt = JobContextFilter()
        token = set_job_id(99)
        clear_job_id(token)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record)
        assert record.job_id == "-"  # type: ignore[attr-defined]

    def test_filter_always_returns_true(self):
        filt = JobContextFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        assert filt.filter(record) is True

    def test_context_isolation_between_set_calls(self):
        filt = JobContextFilter()
        token1 = set_job_id(1)
        token2 = set_job_id(2)
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record)
        assert record.job_id == "2"  # type: ignore[attr-defined]
        clear_job_id(token2)
        record2 = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        filt.filter(record2)
        assert record2.job_id == "1"  # type: ignore[attr-defined]
        clear_job_id(token1)


class TestDaemonLogFixture:
    def test_daemon_log_captures_daemon_logger(self, daemon_log):
        logger = logging.getLogger("DAEMON")
        logger.info("hello from daemon")
        assert "hello from daemon" in daemon_log.text

    def test_daemon_log_captures_child_logger(self, daemon_log):
        logger = logging.getLogger("DAEMON.myconfig")
        logger.warning("child logger message")
        assert "child logger message" in daemon_log.text

    def test_daemon_log_records_have_correct_level(self, daemon_log):
        logger = logging.getLogger("DAEMON")
        logger.error("something went wrong")
        errors = [r for r in daemon_log.records if r.levelno == logging.ERROR]
        assert any("something went wrong" in r.getMessage() for r in errors)


class TestJSONFormatter:
    @pytest.mark.skipif(JSONFormatter is None, reason="python-json-logger not installed")
    def test_emits_valid_json(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        logger = logging.getLogger("test.json_formatter")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("hello json")
        finally:
            logger.removeHandler(handler)
        output = stream.getvalue().strip()
        record = json.loads(output)
        assert record["message"] == "hello json"
        assert record["level"] == "INFO"

    @pytest.mark.skipif(JSONFormatter is None, reason="python-json-logger not installed")
    def test_includes_job_id_from_extra(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        logger = logging.getLogger("test.json_job_id")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("job started", extra={"job_id": 42, "path": "/media/foo.mkv"})
        finally:
            logger.removeHandler(handler)
        record = json.loads(stream.getvalue().strip())
        assert record["job_id"] == 42
        assert record["path"] == "/media/foo.mkv"

    @pytest.mark.skipif(JSONFormatter is None, reason="python-json-logger not installed")
    def test_each_line_is_valid_json(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        logger = logging.getLogger("test.json_ndjson")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("line one")
            logger.warning("line two")
            logger.error("line three")
        finally:
            logger.removeHandler(handler)
        lines = [l for l in stream.getvalue().splitlines() if l.strip()]
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert "message" in record
            assert "level" in record


class TestCheckLoggingConfigMigratesFormat:
    def test_migrates_daemon_formatter_to_include_job_id(self, tmp_path):
        configfile = str(tmp_path / "logging.ini")
        config = RawConfigParser()
        for s in defaults:
            config.add_section(s)
            for k in defaults[s]:
                config.set(s, k, str(defaults[s][k]))
        # Simulate an old config without %(job_id)s in the format
        config.set("formatter_daemonFormatter", "format", "%(asctime)s [%(levelname)s] %(message)s")
        with open(configfile, "w") as f:
            config.write(f)
        checkLoggingConfig(configfile)
        config2 = RawConfigParser()
        config2.read(configfile)
        assert "%(job_id)s" in config2.get("formatter_daemonFormatter", "format")
