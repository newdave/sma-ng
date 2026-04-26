"""Tests for SingleLineFormatter and RedactingFilter in resources/log.py.

These pin the contract spelled out in
docs/brainstorming/2026-04-27-logging-refactor.md so the formatter can
be reasoned about as the single point of enforcement for log shape.
"""

import logging

import pytest

from resources.log import RedactingFilter, SingleLineFormatter

NEWLINE_MARKER = " ⏎ "


def _record(msg, *, level=logging.INFO, args=None, exc_info=None, extra=None):
  rec = logging.LogRecord(
    name="test",
    level=level,
    pathname=__file__,
    lineno=1,
    msg=msg,
    args=args,
    exc_info=exc_info,
  )
  if extra:
    for k, v in extra.items():
      setattr(rec, k, v)
  return rec


class TestSingleLineCollapse:
  def test_newline_in_message_is_collapsed(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("line one\nline two\nline three"))
    assert "\n" not in out
    assert NEWLINE_MARKER in out
    assert "line one" in out and "line three" in out

  def test_carriage_return_is_collapsed(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("a\rb\r\nc"))
    assert "\n" not in out and "\r" not in out
    assert NEWLINE_MARKER in out

  def test_unicode_marker_is_present(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("a\nb"))
    assert "⏎" in out

  def test_single_line_message_passes_through_unchanged(self):
    fmt = SingleLineFormatter("%(message)s")
    assert fmt.format(_record("plain message")) == "plain message"


class TestJsonCompaction:
  def test_indented_json_is_compacted(self):
    fmt = SingleLineFormatter("%(message)s")
    pretty = '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ]\n}'
    out = fmt.format(_record("payload " + pretty))
    assert '"a":1' in out
    assert "\n" not in out

  def test_non_json_braces_left_intact(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("set [a, b, c] is {curly text}"))
    assert "set [a, b, c] is {curly text}" in out

  def test_nested_json_compacts_recursively(self):
    fmt = SingleLineFormatter("%(message)s")
    msg = '{ "outer": { "inner": [1, 2, 3] } }'
    out = fmt.format(_record(msg))
    assert '{"outer":{"inner":[1,2,3]}}' in out


class TestWidthCap:
  def test_truncates_to_max_width(self):
    fmt = SingleLineFormatter("%(message)s", max_width=40)
    out = fmt.format(_record("x" * 200))
    assert len(out) <= 40
    assert "…+" in out  # tail marker

  def test_short_message_unchanged(self):
    fmt = SingleLineFormatter("%(message)s", max_width=100)
    assert fmt.format(_record("short")) == "short"

  def test_env_var_default(self, monkeypatch):
    monkeypatch.setenv("SMA_LOG_MAX_WIDTH", "50")
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("y" * 200))
    assert len(out) <= 50


class TestTraceback:
  def test_exception_traceback_emitted_with_prefix(self):
    fmt = SingleLineFormatter("%(message)s")
    try:
      raise ValueError("boom")
    except ValueError:
      import sys

      out = fmt.format(_record("error happened", level=logging.ERROR, exc_info=sys.exc_info()))
    lines = out.split("\n")
    # First line is the application message; remaining lines are traceback.
    assert lines[0] == "error happened"
    assert all(ln.startswith("  | ") for ln in lines[1:] if ln)
    assert any("ValueError: boom" in ln for ln in lines)

  def test_no_traceback_when_no_exc_info(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("hello"))
    assert "\n" not in out


class TestRedactionInMessage:
  def test_text_pattern_masks_kv_pair(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record("connecting with api_key=abc123 to host"))
    assert "abc123" not in out
    assert "***" in out

  def test_json_substring_masks_secret_field(self):
    fmt = SingleLineFormatter("%(message)s")
    out = fmt.format(_record('config: {"api_key": "topsecret", "host": "x"}'))
    assert "topsecret" not in out
    assert '"api_key":"***"' in out
    assert '"host":"x"' in out


class TestRedactingFilter:
  def test_filter_redacts_extra_dict(self):
    f = RedactingFilter()
    rec = _record("loaded config", extra={"config": {"daemon": {"api_key": "secret", "host": "h"}}})
    f.filter(rec)
    assert rec.config["daemon"]["api_key"] == "***"
    assert rec.config["daemon"]["host"] == "h"

  def test_filter_redacts_dict_in_args(self):
    """Python's logging unwraps a 1-tuple-of-dict into a bare dict for
    %-formatting; we exercise both the tuple and unwrapped paths."""
    f = RedactingFilter()
    # Tuple of two dicts — stays a tuple.
    rec = _record(
      "dump %s and %s",
      args=({"token": "xxxxxx", "url": "http://x"}, {"apikey": "yyy"}),
    )
    f.filter(rec)
    assert rec.args[0]["token"] == "***"
    assert rec.args[0]["url"] == "http://x"
    assert rec.args[1]["apikey"] == "***"

    # Single-dict args (LogRecord unwraps to bare mapping).
    rec2 = _record("dump %(token)s", args={"token": "zzz", "host": "h"})
    f.filter(rec2)
    assert rec2.args["token"] == "***"
    assert rec2.args["host"] == "h"

  def test_filter_leaves_empty_secret_alone(self):
    """Don't bother masking None/'' — the user clearly didn't set it."""
    f = RedactingFilter()
    rec = _record("dump", extra={"creds": {"api_key": "", "password": None}})
    f.filter(rec)
    assert rec.creds["api_key"] == ""
    assert rec.creds["password"] is None

  def test_filter_preserves_non_secret_keys(self):
    f = RedactingFilter()
    rec = _record("dump", extra={"data": {"hostname": "node1", "workers": 4}})
    f.filter(rec)
    assert rec.data == {"hostname": "node1", "workers": 4}


class TestEndToEndViaLogger:
  """Sanity check that wiring through getLogger() preserves behaviour.

  Doesn't reload the global logging config — just attaches a handler with
  SingleLineFormatter to a fresh logger, sends a record through it, and
  asserts the formatted output passes through both the filter and the
  formatter.
  """

  def test_filter_plus_formatter_compose(self):
    import io

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(SingleLineFormatter("%(message)s"))
    logger = logging.getLogger("sma.test.formatter.compose")
    logger.handlers.clear()
    logger.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
      logger.info("processing %s", {"daemon": {"api_key": "shhh"}})
      logger.info("ml1\nml2")
    finally:
      logger.removeHandler(handler)
      logger.filters.clear()
    output = buf.getvalue()
    assert "shhh" not in output
    assert "***" in output
    assert NEWLINE_MARKER in output
    # Each input call produced exactly one line of output.
    assert output.count("\n") == 2  # two records, each terminated by handler


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
  """Default the width cap so tests with their own caps remain isolated."""
  monkeypatch.delenv("SMA_LOG_MAX_WIDTH", raising=False)
