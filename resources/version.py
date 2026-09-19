"""Single source of truth for the SMA-NG version and build commit."""

import importlib.metadata
import os
import tomllib

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_version():
  """Return the SMA-NG semver.

  Prefers pyproject.toml from the source tree (always accurate in a
  checkout, where installed package metadata can be stale from an old
  editable install), falling back to installed package metadata, then
  "unknown".
  """
  try:
    with open(os.path.join(_ROOT_DIR, "pyproject.toml"), "rb") as f:
      version = tomllib.load(f)["project"]["version"]
    if version:
      return version
  except Exception:
    pass
  try:
    return importlib.metadata.version("sma-ng")
  except Exception:
    return "unknown"


def get_commit():
  """Git commit the running code was built from.

  Baked into the image at build time via the ``SMA_GIT_SHA`` build-arg
  (see docker/Dockerfile and the docker.yml workflow). Empty/absent on
  bare-metal or source checkouts, where the semver alone can't
  distinguish commits within a release, so report "unknown" rather than
  an empty string.
  """
  return (os.environ.get("SMA_GIT_SHA") or "unknown").strip() or "unknown"


def version_string():
  """Human-readable version line for --version output."""
  commit = get_commit()
  return "sma-ng %s (commit %s)" % (get_version(), commit[:12] if commit != "unknown" else commit)
