"""Tests for scripts/lint-logging.py.

The lint rules themselves are documented in
docs/brainstorming/2026-04-27-logging-refactor.md and the script's
module docstring; these tests are the executable contract.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from textwrap import dedent

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LINTER_PATH = REPO_ROOT / "scripts" / "lint-logging.py"


def _load_linter():
  """Import the standalone lint-logging.py as a module."""
  spec = importlib.util.spec_from_file_location("lint_logging", LINTER_PATH)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules.setdefault("lint_logging", mod)
  spec.loader.exec_module(mod)
  return mod


@pytest.fixture(scope="module")
def linter():
  return _load_linter()


def _write(tmp_path: Path, body: str, name: str = "sample.py") -> Path:
  p = tmp_path / name
  p.write_text(dedent(body))
  return p


class TestIndentInLogCall:
  def test_rejects_log_info_with_json_dumps_indent(self, linter, tmp_path):
    f = _write(
      tmp_path,
      """
      import json
      log = None  # placeholder
      log.info("payload %s", json.dumps({}, indent=4))
      """,
    )
    errs = linter.lint_file(f)
    assert errs and any("indent" in e for e in errs)

  def test_rejects_self_log_debug_with_indent(self, linter, tmp_path):
    f = _write(
      tmp_path,
      """
      import json
      class C:
        def m(self):
          self.log.debug(json.dumps({}, indent=4))
      """,
    )
    errs = linter.lint_file(f)
    assert errs and any("indent" in e for e in errs)

  def test_allows_compact_json_dumps_in_log_call(self, linter, tmp_path):
    f = _write(
      tmp_path,
      """
      import json
      log = None
      log.info("payload %s", json.dumps({}))
      log.debug("payload %s", json.dumps({}, indent=None))
      """,
    )
    assert linter.lint_file(f) == []


class TestNewlineInMessage:
  def test_rejects_literal_newline_in_log_message(self, linter, tmp_path):
    f = _write(
      tmp_path,
      """
      log = None
      log.info("first line\\nsecond line")
      """,
    )
    errs = linter.lint_file(f)
    assert errs and any("newline" in e for e in errs)

  def test_rejects_concatenated_string_with_newline(self, linter, tmp_path):
    f = _write(
      tmp_path,
      """
      log = None
      log.info("prefix" + "\\nsuffix")
      """,
    )
    errs = linter.lint_file(f)
    assert errs and any("newline" in e for e in errs)

  def test_allows_runtime_value_via_format_arg(self, linter, tmp_path):
    """Linter only inspects literal strings; runtime values pass through."""
    f = _write(
      tmp_path,
      """
      log = None
      msg = "first\\nsecond"  # not detectable, fine
      log.info("payload %s", msg)
      """,
    )
    assert linter.lint_file(f) == []


class TestPrintWithIndentJson:
  def test_rejects_print_json_dumps_indent(self, linter, tmp_path):
    f = _write(
      tmp_path,
      """
      import json
      print(json.dumps({}, indent=4))
      """,
    )
    errs = linter.lint_file(f)
    assert errs and any("multi-line" in e for e in errs)

  def test_allows_plain_print(self, linter, tmp_path):
    """A bare print() outside the restricted prefix is fine."""
    f = _write(tmp_path, "print('hello')\n")
    assert linter.lint_file(f) == []


class TestPrintInDaemonPackage:
  """The print-in-restricted-area rule keys off the file path under
  resources/daemon/, so we have to write the fixture into the actual
  repo tree rather than tmp_path.
  """

  def _make(self, name: str, body: str) -> Path:
    target_dir = REPO_ROOT / "resources" / "daemon"
    p = target_dir / f"_lint_test_{name}.py"
    p.write_text(dedent(body))
    return p

  def test_rejects_bare_print_in_resources_daemon(self, linter):
    p = self._make("bare", "print('hi')\n")
    try:
      errs = linter.lint_file(p)
      assert errs and any("print()" in e for e in errs)
    finally:
      p.unlink()

  def test_allows_print_with_noqa_marker(self, linter):
    p = self._make(
      "noqa",
      """
      def show():
        print('hi')  # noqa: log-print
      """,
    )
    try:
      assert linter.lint_file(p) == []
    finally:
      p.unlink()
