"""Tests for .mise/tasks/deploy/lib/ini_merge.py."""

import os
import shutil
import sys
import tempfile

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, ".mise", "tasks", "deploy", "lib"))
import ini_merge  # noqa: E402
from ini_merge import parse_keys  # noqa: E402

# ── Fixtures ─────────────────────────────────────────────────────────────────

SAMPLE_INI = """\
[Alpha]
a = 1
b = 2
c = 3

[Beta]
x = 10
y = 20
"""

LIVE_INI_SUBSET = """\
[Alpha]
c = 99
a = original

[Beta]
y = 20
"""

LIVE_INI_WITH_EXTRAS = """\
[Alpha]
a = 1
b = 2
c = 3
obsolete = old

[Beta]
x = 10
y = 20
legacy_key = leftover
"""

LIVE_INI_BLANK_VALUES = """\
[Alpha]
a = 1
b =
c = 3
"""


def _write(path, content):
    with open(path, "w") as f:
        f.write(content)


def _read(path):
    with open(path) as f:
        return f.read()


# ── parse_keys ────────────────────────────────────────────────────────────────


class TestParseKeys:
    def test_parses_sections_and_keys(self, tmp_path):
        p = tmp_path / "sample.ini"
        _write(p, SAMPLE_INI)
        result = parse_keys(str(p))
        assert result == {
            "Alpha": {"a": "1", "b": "2", "c": "3"},
            "Beta": {"x": "10", "y": "20"},
        }

    def test_ignores_comment_lines(self, tmp_path):
        p = tmp_path / "test.ini"
        _write(p, "[Sec]\n# ignored = yes\nreal = val\n")
        result = parse_keys(str(p))
        assert result["Sec"] == {"real": "val"}
        assert "ignored" not in result["Sec"]


# ── Regression: existing add-only behaviour unchanged ─────────────────────────


class TestAddsMissingKeysUnchanged:
    def test_adds_missing_keys_to_existing_section(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, SAMPLE_INI)
        _write(live, LIVE_INI_SUBSET)

        sys.argv = ["ini_merge.py", str(sample), str(live)]
        ini_merge.main()

        result = parse_keys(str(live))
        # b was missing from live — must be added
        assert result["Alpha"]["b"] == "2"
        # x was missing from Beta — must be added
        assert result["Beta"]["x"] == "10"

    def test_does_not_overwrite_existing_keys(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, SAMPLE_INI)
        _write(live, LIVE_INI_SUBSET)

        sys.argv = ["ini_merge.py", str(sample), str(live)]
        ini_merge.main()

        result = parse_keys(str(live))
        # a was already in live with value "original" — must not be changed
        assert result["Alpha"]["a"] == "original"
        # c was already in live with value "99"
        assert result["Alpha"]["c"] == "99"


# ── --sort ────────────────────────────────────────────────────────────────────


class TestSort:
    def test_sort_reorders_to_match_sample(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, SAMPLE_INI)
        # Live has keys in reverse order
        _write(live, "[Alpha]\nc = 3\nb = 2\na = 1\n\n[Beta]\ny = 20\nx = 10\n")

        sys.argv = ["ini_merge.py", str(sample), str(live), "--sort"]
        ini_merge.main()

        lines = [ln.rstrip() for ln in open(live) if "=" in ln and not ln.startswith("#")]
        alpha_lines = [ln for ln in lines if ln[0] in "abc"]
        assert alpha_lines == ["a = 1", "b = 2", "c = 3"]

    def test_sort_extra_keys_placed_after_sample_keys(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, "[Alpha]\na = 1\nb = 2\n")
        # Live has an extra key not in sample
        _write(live, "[Alpha]\nextra = z\na = 1\nb = 2\n")

        sys.argv = ["ini_merge.py", str(sample), str(live), "--sort"]
        ini_merge.main()

        lines = [ln.rstrip() for ln in open(live) if "=" in ln and not ln.startswith("#")]
        # a and b should come before extra
        assert lines.index("a = 1") < lines.index("extra = z")
        assert lines.index("b = 2") < lines.index("extra = z")

    def test_sort_extra_keys_sorted_alphabetically_among_themselves(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, "[Sec]\nbase = 0\n")
        _write(live, "[Sec]\nbase = 0\nzz = last\nmm = mid\naa = first\n")

        sys.argv = ["ini_merge.py", str(sample), str(live), "--sort"]
        ini_merge.main()

        lines = [ln.rstrip() for ln in open(live) if "=" in ln and not ln.startswith("#")]
        extra = [l for l in lines if not l.startswith("base")]
        assert extra == ["aa = first", "mm = mid", "zz = last"]


# ── --deprecate ───────────────────────────────────────────────────────────────


class TestDeprecate:
    def test_deprecate_comments_out_live_only_keys(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, SAMPLE_INI)
        _write(live, LIVE_INI_WITH_EXTRAS)

        sys.argv = ["ini_merge.py", str(sample), str(live), "--deprecate"]
        ini_merge.main()

        content = _read(live)
        assert "# deprecated: obsolete = old" in content
        assert "# deprecated: legacy_key = leftover" in content

    def test_deprecate_does_not_touch_sample_keys(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, SAMPLE_INI)
        _write(live, LIVE_INI_WITH_EXTRAS)

        sys.argv = ["ini_merge.py", str(sample), str(live), "--deprecate"]
        ini_merge.main()

        content = _read(live)
        # a, b, c, x, y are in sample — must NOT be deprecated
        assert "# deprecated: a" not in content
        assert "# deprecated: x" not in content

    def test_deprecate_skips_already_commented_lines(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, "[Sec]\nkeep = 1\n")
        _write(live, "[Sec]\nkeep = 1\n# already commented = val\n")

        sys.argv = ["ini_merge.py", str(sample), str(live), "--deprecate"]
        ini_merge.main()

        content = _read(live)
        # The already-commented line must not be double-deprecated
        assert "# deprecated: # already commented" not in content


# ── --backup ──────────────────────────────────────────────────────────────────


class TestBackup:
    def test_backup_creates_copy_before_modification(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        backup = tmp_path / "live.bak"
        _write(sample, SAMPLE_INI)
        _write(live, LIVE_INI_SUBSET)
        original_content = _read(live)

        sys.argv = ["ini_merge.py", str(sample), str(live), "--backup", str(backup)]
        ini_merge.main()

        assert backup.exists(), "backup file should be created"
        assert _read(backup) == original_content

    def test_backup_content_matches_pre_modification_live(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        backup = tmp_path / "backup.ini"
        _write(sample, SAMPLE_INI)
        _write(live, LIVE_INI_SUBSET)
        original = _read(live)

        sys.argv = ["ini_merge.py", str(sample), str(live), "--backup", str(backup)]
        ini_merge.main()

        # Live should be modified; backup should be the original
        live_content = _read(live)
        assert live_content != original
        assert _read(backup) == original


# ── --remove-blank ────────────────────────────────────────────────────────────


class TestRemoveBlank:
    def test_remove_blank_strips_empty_value_lines(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, LIVE_INI_BLANK_VALUES)
        _write(live, LIVE_INI_BLANK_VALUES)

        sys.argv = ["ini_merge.py", str(sample), str(live), "--remove-blank"]
        ini_merge.main()

        content = _read(live)
        assert "b =" not in content
        # Non-blank keys must still be present
        assert "a = 1" in content
        assert "c = 3" in content

    def test_remove_blank_does_not_affect_non_blank_keys(self, tmp_path):
        sample = tmp_path / "sample.ini"
        live = tmp_path / "live.ini"
        _write(sample, SAMPLE_INI)
        _write(live, SAMPLE_INI)

        sys.argv = ["ini_merge.py", str(sample), str(live), "--remove-blank"]
        ini_merge.main()

        result = parse_keys(str(live))
        assert result["Alpha"] == {"a": "1", "b": "2", "c": "3"}
