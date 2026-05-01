#!/usr/bin/env python3
"""Jellyfin Media Server library refresh integration.

After a successful conversion :func:`refreshJellyfin` is called from
:py:meth:`resources.mediaprocessor.MediaProcessor.post`. For each configured
``services.jellyfin.<name>`` instance whose routing-derived ``path`` prefix
matches the converted file's directory, POSTs::

    /Library/Media/Updated

with an ``Updates`` payload so Jellyfin refreshes only the converted
file's parent directory rather than the entire library.

Authentication uses the per-instance ``apikey`` (Jellyfin Dashboard → API
Keys), passed via the ``X-Emby-Token`` header — Jellyfin retained the Emby
header name after the fork.
"""

from __future__ import annotations

import logging

from autoprocess._media_server import trigger_refresh
from resources.log import getLogger


def refreshJellyfin(settings, path: str, logger: logging.Logger | None = None) -> None:
  """Trigger a targeted Jellyfin refresh for the directory containing *path*."""
  log = logger or getLogger(__name__)
  instances = [i for i in (getattr(settings, "jellyfin_instances", []) or []) if i.get("refresh", False)]
  if not instances:
    return
  trigger_refresh(instances, path, product_label="Jellyfin", logger=log)


__all__ = ["refreshJellyfin"]
