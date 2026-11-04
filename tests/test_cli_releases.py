"""Tests for resources/cli/releases.py (latest GitHub release check)."""

import io
import json
import urllib.error

import pytest

from resources.cli import releases


class _Resp(io.BytesIO):
  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False


def _opener(payload=None, exc=None):
  def opener(req, timeout):
    assert req.full_url == "https://api.github.com/repos/newdave/sma-ng/releases/latest"
    assert timeout == releases.TIMEOUT
    if exc:
      raise exc
    return _Resp(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

  return opener


@pytest.mark.parametrize("text,expected", [("sma-ng-v3.2.35", (3, 2, 35)), ("v1.0.0", (1, 0, 0)), ("3.10.2", (3, 10, 2)), ("dev", None), (None, None)])
def test_parse_version(text, expected):
  assert releases.parse_version(text) == expected


def test_latest_release():
  data = {"tag_name": "sma-ng-v3.2.35", "html_url": "https://github.com/x", "published_at": "2026-10-28T19:32:14Z"}
  assert releases.latest_release(opener=_opener(data)) == {"version": "3.2.35", "tag": "sma-ng-v3.2.35", "url": "https://github.com/x", "published": "2026-10-28"}


@pytest.mark.parametrize(
  "payload,exc,message",
  [
    (None, urllib.error.URLError("offline"), "offline"),
    (None, TimeoutError("timed out"), "timed out"),
    (b"not json", None, "Expecting value"),
    ({"tag_name": "nightly"}, None, "no version"),
    ([], None, "no version"),
  ],
)
def test_latest_release_failures(payload, exc, message):
  with pytest.raises(releases.ReleaseCheckError, match=message):
    releases.latest_release(opener=_opener(payload, exc))


def test_custom_repo():
  def opener(req, timeout):
    assert req.full_url == "https://api.github.com/repos/me/fork/releases/latest"
    return _Resp(json.dumps({"tag_name": "v9.9.9"}).encode())

  assert releases.latest_release("me/fork", opener=opener)["version"] == "9.9.9"


@pytest.mark.parametrize(
  "current,latest,expected", [("3.2.34", "3.2.35", True), ("3.2.35", "3.2.35", False), ("3.3.0", "3.2.35", False), ("3.10.0", "3.9.9", False), ("dev", "3.2.35", None), ("3.2.35", None, None)]
)
def test_is_outdated(current, latest, expected):
  assert releases.is_outdated(current, latest) is expected
