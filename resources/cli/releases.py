"""Latest SMA-NG release on GitHub, for `sma version` (stdlib only).

Release tags look like ``sma-ng-v3.2.35`` (release-please component tags).
The check is best effort: any network or parse failure is reported as
"unknown" and never fails the command.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

DEFAULT_REPO = "newdave/sma-ng"
API_URL = "https://api.github.com/repos/%s/releases/latest"
TIMEOUT = 3.0

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class ReleaseCheckError(Exception):
  """The latest release could not be determined."""


def parse_version(text):
  """(major, minor, patch) from a version or tag such as '3.2.35' or 'sma-ng-v3.2.35'; None when absent."""
  match = _VERSION_RE.search(text or "")
  return tuple(int(x) for x in match.groups()) if match else None


def latest_release(repo=None, opener=urllib.request.urlopen, timeout=TIMEOUT):
  """Return {"version": "3.2.35", "tag": ..., "url": ..., "published": ...} for the latest release."""
  req = urllib.request.Request(API_URL % (repo or DEFAULT_REPO), headers={"Accept": "application/vnd.github+json", "User-Agent": "sma-ng-cli"})
  try:
    with opener(req, timeout=timeout) as resp:
      data = json.loads(resp.read().decode("utf-8"))
  except (urllib.error.URLError, OSError, ValueError) as exc:
    raise ReleaseCheckError(str(getattr(exc, "reason", exc))) from None
  tag = data.get("tag_name") if isinstance(data, dict) else None
  version = parse_version(tag)
  if not version:
    raise ReleaseCheckError("no version in the latest release tag %r" % tag)
  return {"version": "%d.%d.%d" % version, "tag": tag, "url": data.get("html_url"), "published": (data.get("published_at") or "")[:10] or None}


def is_outdated(current, latest):
  """True when *current* is an older release than *latest*; None when either is not a version (e.g. 'dev')."""
  a, b = parse_version(current), parse_version(latest)
  if a is None or b is None:
    return None
  return a < b
