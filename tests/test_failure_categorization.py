"""Drift-guard: every documented failure value maps to an operator category.

If a new enum value lands in ``resources/processor/failures.py`` without
an accompanying entry in ``_FAILURE_CATEGORY_MAP``, this test fails CI
with a message naming the missing value. This is the only safeguard
against silent ``UNKNOWN`` mis-categorisation when new causes are added.
"""

from __future__ import annotations

import pytest

from resources.processor.failures import (
  WORKER_SENTINEL_EXCEPTION,
  WORKER_SENTINEL_INVALID_ARGS,
  WORKER_SENTINEL_PATH_MISSING,
  WORKER_SENTINEL_PROCESS_FAILED,
  FailureCategory,
  FfmpegFailureCause,
  FfmpegFailureClass,
  categorize_failure,
)


@pytest.mark.parametrize("value", [cls.value for cls in FfmpegFailureClass])
def test_every_failure_class_maps_to_non_unknown_category(value):
  cat = categorize_failure(value)
  assert cat is not FailureCategory.UNKNOWN, "FfmpegFailureClass.%s maps to UNKNOWN — add it to _FAILURE_CATEGORY_MAP." % value


@pytest.mark.parametrize("value", [cause.value for cause in FfmpegFailureCause])
def test_every_failure_cause_maps_to_non_unknown_category(value):
  cat = categorize_failure(value)
  assert cat is not FailureCategory.UNKNOWN, "FfmpegFailureCause.%s maps to UNKNOWN — add it to _FAILURE_CATEGORY_MAP." % value


@pytest.mark.parametrize(
  ("sentinel", "expected"),
  [
    (WORKER_SENTINEL_PATH_MISSING, FailureCategory.SOURCE_MEDIA),
    (WORKER_SENTINEL_INVALID_ARGS, FailureCategory.CONFIG),
    (WORKER_SENTINEL_PROCESS_FAILED, FailureCategory.SYSTEM),
    (WORKER_SENTINEL_EXCEPTION, FailureCategory.SYSTEM),
  ],
)
def test_worker_sentinels_map_to_documented_categories(sentinel, expected):
  assert categorize_failure(sentinel) is expected


def test_none_resolves_to_unknown():
  assert categorize_failure(None) is FailureCategory.UNKNOWN


def test_unmapped_string_resolves_to_unknown_without_raising():
  # Unrecognised strings are drift signals — return UNKNOWN, never raise.
  assert categorize_failure("a-cause-that-does-not-exist") is FailureCategory.UNKNOWN


def test_category_set_is_bounded_to_six():
  """FailureCategory is a Prometheus label — assert its size is what we documented."""
  assert {c.value for c in FailureCategory} == {
    "config",
    "source_media",
    "hardware",
    "disk",
    "system",
    "unknown",
  }
