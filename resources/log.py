"""Logging setup with rotating file handlers and optional INI-based configuration."""

import logging
import os
import shutil
from configparser import RawConfigParser
from logging.config import fileConfig
from logging.handlers import BaseRotatingHandler

defaults = {
    "loggers": {
        "keys": "root, manual, nzbget, daemon",
    },
    "handlers": {
        "keys": "consoleHandler, nzbgetHandler, fileHandler, manualHandler, daemonHandler",
    },
    "formatters": {
        "keys": "simpleFormatter, minimalFormatter, nzbgetFormatter, daemonFormatter",
    },
    "logger_root": {
        "level": "DEBUG",
        "handlers": "consoleHandler, fileHandler",
    },
    "logger_nzbget": {
        "level": "DEBUG",
        "handlers": "nzbgetHandler, fileHandler",
        "propagate": 0,
        "qualname": "NZBGetPostProcess",
    },
    "logger_manual": {
        "level": "DEBUG",
        "handlers": "manualHandler, fileHandler",
        "propagate": 0,
        "qualname": "MANUAL",
    },
    "logger_daemon": {
        "level": "DEBUG",
        "handlers": "daemonHandler, fileHandler",
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
    "handler_fileHandler": {
        "class": "handlers.RotatingFileHandler",
        "level": "INFO",
        "formatter": "simpleFormatter",
        "args": "('%(logfilename)s', 'a', 100000, 3, 'utf-8')",
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
LOG_NAME = "sma.log"


def checkLoggingConfig(configfile):
    """Ensure a logging INI file exists with all required sections and keys.

    Creates the file if it does not exist, and adds any missing sections or
    options from ``defaults``. Strips the ``sysLogHandler`` entry on Windows.

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
    """Return a configured logger, initialising rotating-file logging if needed.

    Locates the config and log directories relative to the SMA root (or
    ``custompath`` when provided), ensures ``logging.ini`` is present and
    up-to-date, applies it, and attaches the custom ``rotator`` to any
    ``BaseRotatingHandler`` instances on the returned logger.

    Args:
        name: Logger name passed to ``logging.getLogger()``. Defaults to the
            root logger when ``None``.
        custompath: Optional override for the SMA root directory. Useful when
            calling from a non-standard working directory.

    Returns:
        A ``logging.Logger`` instance configured with file and console handlers.
    """
    if custompath:
        custompath = os.path.realpath(custompath)
        if not os.path.isdir(custompath):
            custompath = os.path.dirname(custompath)
        rootpath = os.path.abspath(custompath)
        resourcepath = os.path.normpath(os.path.join(rootpath, RESOURCE_DIRECTORY))
        configpath = os.path.normpath(os.path.join(rootpath, CONFIG_DIRECTORY))
    else:
        rootpath = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), RELATIVE_TO_ROOT))
        resourcepath = os.path.normpath(os.path.join(rootpath, RESOURCE_DIRECTORY))
        configpath = os.path.normpath(os.path.join(rootpath, CONFIG_DIRECTORY))

    logpath = configpath
    if not os.path.isdir(logpath):
        os.makedirs(logpath)

    if not os.path.isdir(configpath):
        os.makedirs(configpath)

    configfile = os.path.abspath(os.path.join(configpath, CONFIG_DEFAULT)).replace("\\", "\\\\")
    checkLoggingConfig(configfile)

    logfile = os.path.abspath(os.path.join(logpath, LOG_NAME)).replace("\\", "\\\\")
    fileConfig(configfile, defaults={"logfilename": logfile})

    logger = logging.getLogger(name)
    rotatingFileHandlers = [x for x in logger.handlers if isinstance(x, BaseRotatingHandler)]
    for rh in rotatingFileHandlers:
        rh.rotator = rotator

    return logging.getLogger(name)


def rotator(source, dest):
    """Rotate a log file by renaming it, falling back to copy-and-truncate.

    Assigned as the ``rotator`` callable on ``BaseRotatingHandler`` instances
    so that log rotation works correctly across filesystems (e.g. when the log
    file and its destination are on different mount points).

    Args:
        source: Path to the current log file that should be rotated out.
        dest: Destination path for the rotated log file.
    """
    if os.path.exists(source):
        try:
            os.rename(source, dest)
        except:
            try:
                shutil.copyfile(source, dest)
                open(source, "w").close()
            except Exception as e:
                print("Error rotating logfiles: %s." % (e))
