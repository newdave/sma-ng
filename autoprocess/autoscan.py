#!/usr/bin/env python3
"""Autoscan (Cloudbox) webhook integration.

After a successful conversion the file is back at its final source-dir
location (`MediaProcessor.post()` is the call site). For each configured
``services.autoscan.<name>`` instance whose routing-derived ``path``
prefix matches the file's directory, POST::

    /triggers/manual?dir=<directory>

so Autoscan can fan the targeted scan out to whatever target it manages
(Plex, Emby, Jellyfin, Sonarr, …). HTTP Basic Auth is applied when both
``username`` and ``password`` are configured.
"""

from __future__ import annotations

import logging
import os

import requests
from requests.auth import HTTPBasicAuth

from resources.log import getLogger


def _apply_path_mapping(targetpath: str, mapping: dict) -> str:
  """Replace the longest matching mapping prefix in *targetpath*.

  Same algorithm as ``autoprocess.plex.refreshPlex`` so YAML semantics
  match what users already know from the Plex integration.
  """
  if not mapping:
    return targetpath
  targetdirs = targetpath.split(os.sep)
  for k in sorted(mapping.keys(), reverse=True):
    mapdirs = k.split(os.sep)
    if mapdirs == targetdirs[: len(mapdirs)]:
      return os.path.normpath(os.path.join(mapping[k], os.path.relpath(targetpath, k)))
  return targetpath


def triggerAutoscan(settings, path: str, logger: logging.Logger | None = None) -> None:
  """Notify each matching Autoscan instance that *path*'s directory changed.

  Iterates ``settings.autoscan_instances``; for each instance whose
  routing-derived ``path`` is a prefix of the file's directory, POSTs
  ``/triggers/manual?dir=<dir>`` (with optional HTTP Basic Auth and
  optional path-mapping). Per-instance failures are logged and swallowed
  so a transient Autoscan outage never fails the conversion job.
  """
  log = logger or getLogger(__name__)

  instances = getattr(settings, "autoscan_instances", []) or []
  if not instances:
    return

  targetpath = os.path.dirname(path)

  for instance in instances:
    instance_path = instance.get("path") or ""
    if not instance_path or not targetpath.startswith(instance_path):
      continue

    section = instance.get("section", "main")
    mapped = _apply_path_mapping(targetpath, instance.get("path-mapping") or {})
    if mapped != targetpath:
      log.debug("Autoscan [%s] path-mapping rewrote %s -> %s" % (section, targetpath, mapped))

    protocol = "https://" if instance.get("ssl") else "http://"
    base_url = protocol + instance["host"] + ":" + str(instance["port"]) + (instance.get("webroot") or "")
    url = base_url + "/triggers/manual"

    auth = None
    username = instance.get("username") or ""
    password = instance.get("password") or ""
    if username and password:
      auth = HTTPBasicAuth(username, password)

    verify = not instance.get("ignore-certs", False)
    try:
      log.info("Triggering Autoscan [%s] for %s" % (section, mapped))
      r = requests.post(url, params={"dir": mapped}, auth=auth, verify=verify, timeout=10)
      if 200 <= r.status_code < 300:
        log.info("Autoscan [%s] accepted (HTTP %s)" % (section, r.status_code))
      else:
        log.warning("Autoscan [%s] returned HTTP %s: %s" % (section, r.status_code, r.text[:200]))
    except Exception:
      log.exception("Autoscan [%s] request failed" % section)
