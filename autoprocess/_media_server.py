"""Shared targeted-refresh helper for Emby / Jellyfin.

Both servers expose ``POST /Library/Media/Updated`` (Jellyfin retained the
endpoint after forking from Emby). Posting an ``Updates`` payload with a
single entry triggers a metadata refresh scoped to the converted file's
parent directory rather than a whole-library rescan.
"""

from __future__ import annotations

import logging
import os

import requests


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


def _base_url(instance: dict) -> str:
  protocol = "https://" if instance.get("ssl") else "http://"
  return protocol + instance["host"] + ":" + str(instance["port"]) + (instance.get("webroot") or "")


def trigger_refresh(
  instances: list[dict],
  path: str,
  *,
  product_label: str,
  logger: logging.Logger | None = None,
) -> None:
  """Notify each matching Emby/Jellyfin instance that *path*'s directory changed.

  Iterates *instances* (already filtered to the right server kind by the
  caller). For each instance whose routing-derived ``path`` is a prefix of
  the file's directory, POSTs ``/Library/Media/Updated`` with the mapped
  target directory. Per-instance failures are logged and swallowed so a
  transient outage never fails the conversion job.
  """
  log = logger or logging.getLogger(__name__)
  if not instances:
    return

  targetpath = os.path.dirname(path)

  for instance in instances:
    instance_path = instance.get("path") or ""
    if not instance_path or not targetpath.startswith(instance_path):
      continue
    if not instance.get("apikey"):
      log.warning("%s [%s] skipped: no apikey configured" % (product_label, instance.get("section", "main")))
      continue

    section = instance.get("section", "main")
    mapped = _apply_path_mapping(targetpath, instance.get("path-mapping") or {})
    if mapped != targetpath:
      log.debug("%s [%s] path-mapping rewrote %s -> %s" % (product_label, section, targetpath, mapped))

    url = _base_url(instance) + "/Library/Media/Updated"
    headers = {
      "Content-Type": "application/json",
      "X-Emby-Token": instance["apikey"],
    }
    payload = {"Updates": [{"Path": mapped, "UpdateType": "Modified"}]}
    verify = not instance.get("ignore-certs", False)

    try:
      log.info("Triggering %s [%s] refresh for %s" % (product_label, section, mapped))
      r = requests.post(url, json=payload, headers=headers, verify=verify, timeout=10)
      if 200 <= r.status_code < 300:
        log.info("%s [%s] accepted (HTTP %s)" % (product_label, section, r.status_code))
      else:
        log.warning("%s [%s] returned HTTP %s: %s" % (product_label, section, r.status_code, r.text[:200]))
    except Exception:
      log.exception("%s [%s] request failed" % (product_label, section))


__all__ = ["trigger_refresh"]
