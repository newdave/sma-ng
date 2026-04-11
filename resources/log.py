"""Logging setup with optional INI-based configuration."""

import logging
import logging.handlers
import os
from configparser import RawConfigParser
from logging.config import fileConfig

try:
    from pythonjsonlogger.json import JsonFormatter as _JsonFormatter

    class JSONFormatter(_JsonFormatter):
        """JSON log formatter for daemon file handlers.

        Emits one JSON object per line (NDJSON) with the fields:
        ``timestamp``, ``level``, ``logger``, ``job_id``, ``message``.
        Any additional ``extra=`` fields passed to the log call are
        included automatically by the base class.
        """

        def add_fields(self, log_record, record, message_dict):
            super().add_fields(log_record, record, message_dict)
            log_record["timestamp"] = self.formatTime(record, self.datefmt)
            log_record["level"] = record.levelname
            log_record["logger"] = record.name
            log_record["job_id"] = getattr(record, "job_id", "-")
            log_record.setdefault("message", record.getMessage())

except ImportError:
    JSONFormatter = None  # type: ignore[assignment,misc]

defaults = {
    "loggers": {
        "keys": "root, manual, nzbget, daemon",
    },
    "handlers": {
        "keys": "consoleHandler, nzbgetHandler, manualHandler, daemonHandler",
    },
    "formatters": {
        "keys": "simpleFormatter, minimalFormatter, nzbgetFormatter, daemonFormatter",
    },
    "logger_root": {
        "level": "DEBUG",
        "handlers": "consoleHandler",
    },
    "logger_nzbget": {
        "level": "DEBUG",
        "handlers": "nzbgetHandler",
        "propagate": 0,
        "qualname": "NZBGetPostProcess",
    },
    "logger_manual": {
        "level": "DEBUG",
        "handlers": "manualHandler",
        "propagate": 0,
        "qualname": "MANUAL",
    },
    "logger_daemon": {
        "level": "DEBUG",
        "handlers": "daemonHandler",
        "propagate": 0,
        "qualname": "DAEMON",
    },
    "handler_consoleHandler": {
        "class": "StreamHandler",
        "level": "INFO",
        "formatter": "simpleFormatter",
        "args": "(sys.stdout,)",
    },
    "handler_nzbgetHandler": {
        "class": "StreamHandler",
        "level": "INFO",
        "formatter": "nzbgetFormatter",
        "args": "(sys.stdout,)",
    },
    "handler_manualHandler": {
        "class": "StreamHandler",
        "level": "INFO",
        "formatter": "minimalFormatter",
        "args": "(sys.stdout,)",
    },
    "handler_daemonHandler": {
        "class": "handlers.RotatingFileHandler",
        "level": "INFO",
        "formatter": "daemonFormatter",
        "args": None,  # filled in by checkLoggingConfig with the absolute logs path
    },
    "formatter_simpleFormatter": {
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    },
    "formatter_minimalFormatter": {"format": "%(message)s", "datefmt": ""},
    "formatter_nzbgetFormatter": {"format": "[%(levelname)s] %(message)s", "datefmt": ""},
    "formatter_daemonFormatter": {"format": "%(asctime)s [%(levelname)s] [job:%(job_id)s] %(message)s", "datefmt": "%Y-%m-%d %H:%M:%S"},
}

CONFIG_DEFAULT = "logging.ini"
CONFIG_DIRECTORY = "./config"
RESOURCE_DIRECTORY = "./resources"
RELATIVE_TO_ROOT = "../"

# Shared RotatingFileHandler parameters — imported by config.py to avoid duplication
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

# ANSI color codes
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    """Formatter that colorizes ERROR and WARNING lines on TTYs."""

    def format(self, record):
        msg = super().format(record)
        if record.levelno >= logging.ERROR:
            return _ANSI_RED + msg + _ANSI_RESET
        if record.levelno >= logging.WARNING:
            return _ANSI_YELLOW + msg + _ANSI_RESET
        return msg


def _apply_color_formatters():
    """Replace formatters on StreamHandlers with ColorFormatter equivalents.

    Called after fileConfig() so colors are applied regardless of what logging.ini
    specifies. Only applies to handlers writing to a TTY so log files stay clean.
    """
    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            if hasattr(handler.stream, "isatty") and handler.stream.isatty():
                fmt = handler.formatter
                handler.setFormatter(ColorFormatter(fmt._fmt if fmt else None, fmt.datefmt if fmt else None))
    for logger in logging.Logger.manager.loggerDict.values():
        if not isinstance(logger, logging.Logger):
            continue
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                if hasattr(handler.stream, "isatty") and handler.stream.isatty():
                    fmt = handler.formatter
                    handler.setFormatter(ColorFormatter(fmt._fmt if fmt else None, fmt.datefmt if fmt else None))


def _apply_job_context_filter():
    """Attach JobContextFilter to the DAEMON logger if not already present.

    The filter injects ``job_id`` into every LogRecord so that
    ``%(job_id)s`` works in daemonFormatter format strings.  Called after
    ``fileConfig()`` so the DAEMON logger is guaranteed to exist.
    """
    from resources.daemon.context import JobContextFilter

    daemon_logger = logging.getLogger("DAEMON")
    if not any(isinstance(f, JobContextFilter) for f in daemon_logger.filters):
        daemon_logger.addFilter(JobContextFilter())


def _apply_json_formatter():
    """Replace the formatter on DAEMON's RotatingFileHandler with JSONFormatter.

    Only applied when JSONFormatter is available (i.e. python-json-logger is
    installed).  The stdout/TTY StreamHandlers are left unchanged so human-
    readable output on the console is preserved.
    """
    if JSONFormatter is None:
        return

    daemon_logger = logging.getLogger("DAEMON")
    for handler in daemon_logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            if not isinstance(handler.formatter, JSONFormatter):
                handler.setFormatter(JSONFormatter())


def checkLoggingConfig(configfile, logs_dir=None):
    """Ensure a logging INI file exists with all required sections and keys.

    Creates the file if it does not exist, and adds any missing sections or
    options from ``defaults``. Removes the legacy ``fileHandler`` and
    ``sysLogHandler`` entries if present. Strips the ``sysLogHandler`` entry
    on Windows.

    Args:
        configfile: Path to the logging configuration file to create or update.
        logs_dir: Directory for daemon.log. Defaults to a ``logs/`` sibling of
            the config directory when not provided.
    """
    if logs_dir is None:
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(configfile)), "logs")
    daemon_log = os.path.join(logs_dir, "daemon.log").replace("\\", "\\\\")
    daemon_handler_args = "('%s', 'a', %d, %d, 'utf-8')" % (daemon_log, LOG_MAX_BYTES, LOG_BACKUP_COUNT)

    write = True
    config = RawConfigParser()
    if os.path.exists(configfile):
        config.read(configfile)
        write = False
    for s in defaults:
        if not config.has_section(s):
            config.add_section(s)
            write = True
        for k in defaults[s]:
            value = defaults[s][k]
            if s == "handler_daemonHandler" and k == "args":
                value = daemon_handler_args
            if not config.has_option(s, k):
                config.set(s, k, str(value))
                write = True

    # Ensure the keys lists in the index sections include all expected entries.
    # Old logging.ini files may have these options but with shorter values that
    # predate the daemon logger/handler/formatter additions.
    for index_section, expected_keys in (
        ("loggers", [e[len("logger_") :] for e in defaults if e.startswith("logger_")]),
        ("handlers", [e[len("handler_") :] for e in defaults if e.startswith("handler_")]),
        ("formatters", [e[len("formatter_") :] for e in defaults if e.startswith("formatter_")]),
    ):
        current = [x.strip() for x in config.get(index_section, "keys", fallback="").split(",") if x.strip()]
        missing = [k for k in expected_keys if k not in current]
        if missing:
            config.set(index_section, "keys", ", ".join(current + missing))
            write = True

    # Migrate existing configs: replace StreamHandler daemonHandler with RotatingFileHandler
    if config.get("handler_daemonHandler", "class", fallback="") == "StreamHandler":
        config.set("handler_daemonHandler", "class", "handlers.RotatingFileHandler")
        config.set("handler_daemonHandler", "args", daemon_handler_args)
        write = True

    # Migrate daemonFormatter to include job_id field if not already present
    _new_daemon_fmt = defaults["formatter_daemonFormatter"]["format"]
    if "%(job_id)s" not in config.get("formatter_daemonFormatter", "format", fallback=""):
        config.set("formatter_daemonFormatter", "format", _new_daemon_fmt)
        write = True

    # Remove legacy fileHandler from handlers list and delete its section
    if config.has_option("handlers", "keys") and "fileHandler" in config.get("handlers", "keys"):
        keys = [k.strip() for k in config.get("handlers", "keys").split(",") if k.strip() != "fileHandler"]
        config.set("handlers", "keys", ", ".join(keys))
        write = True
    if config.has_section("handler_fileHandler"):
        config.remove_section("handler_fileHandler")
        write = True
    # Remove fileHandler from any logger handler lists
    for section in config.sections():
        if section.startswith("logger_") and config.has_option(section, "handlers"):
            handlers = [h.strip() for h in config.get(section, "handlers").split(",") if h.strip() != "fileHandler"]
            new_val = ", ".join(handlers)
            if new_val != config.get(section, "handlers"):
                config.set(section, "handlers", new_val)
                write = True

    # Remove sysLogHandler if you're on Windows
    if "sysLogHandler" in config.get("handlers", "keys"):
        config.set("handlers", "keys", config.get("handlers", "keys").replace("sysLogHandler", ""))
        write = True
    while config.get("handlers", "keys").endswith(",") or config.get("handlers", "keys").endswith(" "):
        config.set("handlers", "keys", config.get("handlers", "keys")[:-1])
        write = True
    if write:
        if not os.path.isdir(logs_dir):
            os.makedirs(logs_dir)
        fp = open(configfile, "w")
        config.write(fp)
        fp.close()


def getLogger(name=None, custompath=None):
    """Return a configured logger, initialising logging if needed.

    Locates the config directory relative to the SMA root (or ``custompath``
    when provided), ensures ``logging.ini`` is present and up-to-date, and
    applies it.

    Args:
        name: Logger name passed to ``logging.getLogger()``. Defaults to the
            root logger when ``None``.
        custompath: Optional override for the SMA root directory. Useful when
            calling from a non-standard working directory.

    Returns:
        A ``logging.Logger`` instance configured with console handlers.
    """
    if custompath:
        custompath = os.path.realpath(custompath)
        if not os.path.isdir(custompath):
            custompath = os.path.dirname(custompath)
        rootpath = os.path.abspath(custompath)
        configpath = os.path.normpath(os.path.join(rootpath, CONFIG_DIRECTORY))
    else:
        rootpath = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), RELATIVE_TO_ROOT))
        configpath = os.path.normpath(os.path.join(rootpath, CONFIG_DIRECTORY))

    if not os.path.isdir(configpath):
        os.makedirs(configpath)

    logs_dir = os.path.normpath(os.path.join(rootpath, "logs"))
    configfile = os.path.abspath(os.path.join(configpath, CONFIG_DEFAULT)).replace("\\", "\\\\")
    checkLoggingConfig(configfile, logs_dir=logs_dir)

    fileConfig(configfile)
    _apply_color_formatters()
    _apply_job_context_filter()
    _apply_json_formatter()

    return logging.getLogger(name)
