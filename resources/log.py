"""Logging setup with optional INI-based configuration."""

import logging
import os
from configparser import RawConfigParser
from logging.config import fileConfig

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
        "class": "StreamHandler",
        "level": "INFO",
        "formatter": "daemonFormatter",
        "args": "(sys.stdout,)",
    },
    "formatter_simpleFormatter": {
        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    },
    "formatter_minimalFormatter": {"format": "%(message)s", "datefmt": ""},
    "formatter_nzbgetFormatter": {"format": "[%(levelname)s] %(message)s", "datefmt": ""},
    "formatter_daemonFormatter": {"format": "%(asctime)s [%(levelname)s] %(message)s", "datefmt": "%Y-%m-%d %H:%M:%S"},
}

CONFIG_DEFAULT = "logging.ini"
CONFIG_DIRECTORY = "./config"
RESOURCE_DIRECTORY = "./resources"
RELATIVE_TO_ROOT = "../"


def checkLoggingConfig(configfile):
    """Ensure a logging INI file exists with all required sections and keys.

    Creates the file if it does not exist, and adds any missing sections or
    options from ``defaults``. Removes the legacy ``fileHandler`` and
    ``sysLogHandler`` entries if present. Strips the ``sysLogHandler`` entry
    on Windows.

    Args:
        configfile: Path to the logging configuration file to create or update.
    """
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
            if not config.has_option(s, k):
                config.set(s, k, str(defaults[s][k]))

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

    configfile = os.path.abspath(os.path.join(configpath, CONFIG_DEFAULT)).replace("\\", "\\\\")
    checkLoggingConfig(configfile)

    fileConfig(configfile)

    return logging.getLogger(name)
