"""Logging setup with optional INI-based configuration."""

import copy
import json
import logging
import logging.handlers
import os
import re
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

# ── Single-line formatter, redaction, and width capping ──────────────────────
# Goals (see docs/brainstorming/2026-04-27-logging-refactor.md):
#   - Every application log record fits on exactly one line on disk.
#   - JSON-shaped substrings render compactly (no indent=).
#   - Records exceeding SMA_LOG_MAX_WIDTH are truncated with a "…+N" suffix.
#   - Tracebacks (exc_info) are emitted on subsequent lines, each prefixed
#     with two spaces + "| " so they're greppable as a group while leaving
#     the application-level message a single line.
#   - Secrets (api_key, db_url, username, password, node_id, apikey, token)
#     are redacted at the layer regardless of how the caller dressed them up.

_DEFAULT_MAX_WIDTH = 1024
_NEWLINE_MARKER = " ⏎ "
_TRACEBACK_PREFIX = "  | "
_REDACTED = "***"


def _redact_keys():
  """Return the union of secret-bearing field names. Imported lazily so the
  log module can be loaded without the daemon package on PYTHONPATH (e.g.
  during early CLI startup)."""
  try:
    from resources.daemon.constants import SECRET_KEYS, SERVICE_SECRET_FIELDS

    return frozenset(SECRET_KEYS) | frozenset(SERVICE_SECRET_FIELDS)
  except Exception:
    return frozenset({"api_key", "api-key", "db_url", "db-url", "username", "password", "node_id", "node-id", "apikey", "token"})


def _redact_value(value, secrets):
  """Recursively replace values whose dict key is in *secrets* with `***`.

  Walks dicts and lists; scalars pass through. Empty values are left alone
  (no point masking ``None`` / ``""`` since the user clearly didn't set
  them).
  """
  if isinstance(value, dict):
    out = {}
    for k, v in value.items():
      if isinstance(k, str) and k in secrets and v not in (None, "", 0, False):
        out[k] = _REDACTED
      else:
        out[k] = _redact_value(v, secrets)
    return out
  if isinstance(value, list):
    return [_redact_value(v, secrets) for v in value]
  if isinstance(value, tuple):
    return tuple(_redact_value(v, secrets) for v in value)
  return value


def _build_text_redact_pattern(secrets):
  """Compile a regex matching ``key<sep>value`` pairs where key is a secret."""
  if not secrets:
    return None
  keys = "|".join(re.escape(k) for k in sorted(secrets, key=len, reverse=True))
  # Match: optional opening quote, key, optional closing quote, separator
  # (`:` or `=`), optional whitespace + opening quote, then the value up
  # to the next delimiter.
  return re.compile(r"(?P<key>['\"]?(?:" + keys + r")['\"]?)(?P<sep>\s*[:=]\s*['\"]?)(?P<val>[^,\s'\"}\]]+)")


def _redact_text(message, pattern):
  if not pattern or not message:
    return message
  return pattern.sub(lambda m: m.group("key") + m.group("sep") + _REDACTED, message)


def _compact_json_substrings(s):
  """Identify balanced JSON-shaped substrings in *s* and re-dump them compactly.

  Heuristic: scan for ``{``/``[``, then walk forward tracking depth (with
  string-aware brace counting) to find the matching close. Pass the slice
  through ``json.loads``; on failure leave the original substring intact.
  Embedded secrets in the parsed object are redacted at the same time.
  """
  if not s or ("{" not in s and "[" not in s):
    return s
  secrets = _redact_keys()
  out = []
  i = 0
  n = len(s)
  while i < n:
    # Find next opening brace/bracket.
    next_brace = -1
    for ch in ("{", "["):
      idx = s.find(ch, i)
      if idx != -1 and (next_brace == -1 or idx < next_brace):
        next_brace = idx
    if next_brace == -1:
      out.append(s[i:])
      break
    out.append(s[i:next_brace])
    open_ch = s[next_brace]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    end = -1
    in_str = False
    esc = False
    for j in range(next_brace, n):
      c = s[j]
      if in_str:
        if esc:
          esc = False
        elif c == "\\":
          esc = True
        elif c == '"':
          in_str = False
      else:
        if c == '"':
          in_str = True
        elif c == open_ch:
          depth += 1
        elif c == close_ch:
          depth -= 1
          if depth == 0:
            end = j + 1
            break
    if end == -1:
      out.append(s[next_brace:])
      break
    candidate = s[next_brace:end]
    try:
      parsed = json.loads(candidate)
      parsed = _redact_value(parsed, secrets)
      out.append(json.dumps(parsed, separators=(",", ":"), default=str, ensure_ascii=False))
    except (json.JSONDecodeError, ValueError):
      out.append(candidate)
    i = end
  return "".join(out)


class RedactingFilter(logging.Filter):
  """Filter that walks ``record.args`` and any ``extra=`` fields and replaces
  values whose key is a known secret with ``***``. Final message-level
  text masking happens in :class:`SingleLineFormatter` so it covers
  pre-formatted strings too.
  """

  _STANDARD_RECORD_KEYS = frozenset(
    {
      "name",
      "msg",
      "args",
      "levelname",
      "levelno",
      "pathname",
      "filename",
      "module",
      "exc_info",
      "exc_text",
      "stack_info",
      "lineno",
      "funcName",
      "created",
      "msecs",
      "relativeCreated",
      "thread",
      "threadName",
      "processName",
      "process",
      "message",
      "asctime",
      "taskName",
    }
  )

  def filter(self, record):
    secrets = _redact_keys()
    if record.args:
      try:
        if isinstance(record.args, dict):
          record.args = _redact_value(copy.deepcopy(record.args), secrets)
        elif isinstance(record.args, (tuple, list)):
          record.args = type(record.args)(_redact_value(copy.deepcopy(a), secrets) if isinstance(a, (dict, list, tuple)) else a for a in record.args)
      except Exception:
        pass
    for key in list(record.__dict__):
      if key in self._STANDARD_RECORD_KEYS:
        continue
      val = record.__dict__[key]
      if isinstance(val, (dict, list, tuple)):
        try:
          record.__dict__[key] = _redact_value(copy.deepcopy(val), secrets)
        except Exception:
          pass
    return True


class SingleLineFormatter(logging.Formatter):
  """Formatter enforcing the single-line invariant.

  - Newlines in the rendered message become a visible marker ``⏎``.
  - JSON-looking substrings are compacted (no whitespace).
  - Output is truncated to ``max_width`` chars with a ``…+N`` tail marker.
  - ``record.exc_info`` tracebacks are appended on subsequent lines, each
    prefixed with two spaces + ``| `` so the application message itself
    remains exactly one line.
  """

  def __init__(self, fmt=None, datefmt=None, style="%", max_width=None):
    super().__init__(fmt, datefmt, style)
    if max_width is None:
      try:
        max_width = int(os.environ.get("SMA_LOG_MAX_WIDTH", _DEFAULT_MAX_WIDTH))
      except (TypeError, ValueError):
        max_width = _DEFAULT_MAX_WIDTH
    self._max_width = max_width
    self._redact_pattern = _build_text_redact_pattern(_redact_keys())

  def format(self, record):
    # Format message without the parent appending exc_info; we'll do that
    # ourselves with our own per-frame prefix.
    saved_exc_info = record.exc_info
    saved_exc_text = record.exc_text
    record.exc_info = None
    record.exc_text = None
    try:
      base = super().format(record)
    finally:
      record.exc_info = saved_exc_info
      record.exc_text = saved_exc_text

    base = base.replace("\r\n", "\n").replace("\r", "\n")
    base = _compact_json_substrings(base)
    base = _redact_text(base, self._redact_pattern)
    if "\n" in base:
      base = base.replace("\n", _NEWLINE_MARKER)

    if self._max_width and len(base) > self._max_width:
      trimmed = len(base) - self._max_width
      # Reserve room for the marker so the final string is at most max_width.
      marker = "…+%d" % trimmed
      base = base[: max(0, self._max_width - len(marker))] + marker

    if saved_exc_info:
      tb = self.formatException(saved_exc_info)
      tb_lines = [ln for ln in tb.split("\n") if ln]
      base = base + "\n" + "\n".join(_TRACEBACK_PREFIX + ln for ln in tb_lines)
    return base


class ColorFormatter(SingleLineFormatter):
  """Formatter that colorizes ERROR and WARNING lines on TTYs.

  Inherits :class:`SingleLineFormatter`'s collapse/compact/truncate
  behaviour so TTY output stays one record per line just like the file
  handlers.
  """

  def format(self, record):
    msg = super().format(record)
    if record.levelno >= logging.ERROR:
      return _ANSI_RED + msg + _ANSI_RESET
    if record.levelno >= logging.WARNING:
      return _ANSI_YELLOW + msg + _ANSI_RESET
    return msg


def _iter_all_handlers():
  """Yield every handler attached to root and any named logger."""
  for handler in logging.root.handlers:
    yield handler
  for logger in logging.Logger.manager.loggerDict.values():
    if not isinstance(logger, logging.Logger):
      continue
    for handler in logger.handlers:
      yield handler


def _is_structured_handler(handler):
  """Return True for handlers that should NOT receive SingleLineFormatter.

  - JSONFormatter writes its own line-bounded NDJSON; double-formatting
    would corrupt the JSON.
  - PostgreSQLLogHandler stores structured fields in the cluster ``logs``
    table and renders them server-side; the formatter is irrelevant there.
  """
  fmt = handler.formatter
  if JSONFormatter is not None and isinstance(fmt, JSONFormatter):
    return True
  cls_name = type(handler).__name__
  return cls_name == "PostgreSQLLogHandler"


def _apply_single_line_formatter():
  """Wrap formatters on every handler with :class:`SingleLineFormatter`.

  Called immediately after ``fileConfig()`` so the single-line invariant
  holds regardless of what ``logging.ini`` specifies. Skips handlers that
  intentionally use structured formats (JSONFormatter, PostgreSQLLogHandler).
  TTY StreamHandlers are still wrapped here; ``_apply_color_formatters``
  later replaces those formatters with :class:`ColorFormatter`, which
  inherits the same collapse/compact/truncate behaviour.
  """
  for handler in _iter_all_handlers():
    if _is_structured_handler(handler):
      continue
    fmt = handler.formatter
    if isinstance(fmt, SingleLineFormatter):
      continue
    handler.setFormatter(SingleLineFormatter(fmt._fmt if fmt else None, fmt.datefmt if fmt else None))


def _apply_color_formatters():
  """Replace formatters on TTY StreamHandlers with ColorFormatter equivalents.

  Called after ``_apply_single_line_formatter`` so the parent formatter is
  already SingleLineFormatter; ColorFormatter inherits from it. Only applies
  to handlers writing to a TTY so log files stay clean.
  """
  for handler in _iter_all_handlers():
    if _is_structured_handler(handler):
      continue
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
      if hasattr(handler.stream, "isatty") and handler.stream.isatty():
        fmt = handler.formatter
        handler.setFormatter(ColorFormatter(fmt._fmt if fmt else None, fmt.datefmt if fmt else None))


def _apply_redacting_filter():
  """Attach :class:`RedactingFilter` to the root logger so every record is
  scrubbed before reaching any handler. Idempotent."""
  if not any(isinstance(f, RedactingFilter) for f in logging.root.filters):
    logging.root.addFilter(RedactingFilter())


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
        # Always sync the daemon log path so stale production paths
        # (e.g. /opt/sma/logs/daemon.log) don't break dev/test runs.
        if config.has_option(s, k) and config.get(s, k) != value:
          config.set(s, k, value)
          write = True
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

  fileConfig(configfile, disable_existing_loggers=False)
  _apply_single_line_formatter()
  _apply_color_formatters()
  _apply_job_context_filter()
  _apply_json_formatter()
  _apply_redacting_filter()

  return logging.getLogger(name)
