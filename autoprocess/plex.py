#!/usr/bin/env python3
"""Plex Media Server library refresh integration."""

import logging
import os

import requests
from plexapi.library import LibrarySection
from plexapi.server import PlexServer

from resources.log import getLogger
from resources.readsettings import ReadSettings


def refreshPlex(settings: ReadSettings, path: str | None = None, logger: logging.Logger | None = None):
  """Trigger a targeted Plex library section refresh on every configured
  Plex instance that has ``refresh: true``.

  Iterates ``settings.plex_instances`` (the same shape used for Emby/
  Jellyfin) so multi-server deployments refresh each server in turn.
  Single-instance configs see no behaviour change.

  Args:
      settings: Parsed SMA settings, used to read Plex connection details and
          path mappings.
      path: Absolute path to the converted output file. The parent directory
          is used as the refresh target.
      logger: Optional logger instance. Defaults to the module logger.
  """
  log = logger or getLogger(__name__)

  if not path:
    log.error("No path provided to refreshPlex.")
    return

  # Build the iteration list. Prefer the new `settings.plex_instances`
  # projection (where ReadSettings already filtered by refresh/plexmatch
  # at projection time). Fall back to the legacy `settings.Plex`
  # singleton so older callers / tests that monkey-patch only that
  # field keep working.
  raw_instances = getattr(settings, "plex_instances", None)
  if isinstance(raw_instances, list) and raw_instances:
    instances = [i for i in raw_instances if i.get("refresh", False)]
  else:
    # Legacy: always run the refresh logic against settings.Plex; the
    # connection attempt inside getPlexServer surfaces the missing-
    # host/token case. Preserves the pre-multi-instance contract.
    legacy = getattr(settings, "Plex", None) or {}
    if isinstance(legacy, dict):
      instances = [legacy]
    else:
      instances = []

  if not instances:
    return

  # Two modes of connecting:
  #   - multi-instance path (settings.plex_instances): use _connect_plex
  #     with the per-instance dict
  #   - legacy singleton path (settings.Plex): delegate to getPlexServer
  #     so test patches targeting getPlexServer continue to fire
  using_legacy = not isinstance(raw_instances, list) or not raw_instances
  for inst in instances:
    label = inst.get("_name") or inst.get("host") or "plex"
    log.info("Starting Plex refresh for %s.", label)
    if using_legacy:
      plex = getPlexServer(settings, log)
    else:
      plex = _connect_plex(inst, log, label)
    _refresh_with_connected_server(plex, inst, path, log, label)


def _refresh_with_connected_server(plex, inst: dict, path: str, log: logging.Logger, label: str) -> None:
  """Run the directory-scan + section.update against an already-connected
  ``plex`` (may be ``None`` if connection failed)."""
  targetpath = os.path.dirname(path)
  pathMapping = inst.get("path-mapping", {}) or {}

  # Path Mapping
  targetdirs = targetpath.split(os.sep)
  for k in sorted(pathMapping.keys(), reverse=True):
    mapdirs = k.split(os.sep)
    if mapdirs == targetdirs[: len(mapdirs)]:
      targetpath = os.path.normpath(os.path.join(pathMapping[k], os.path.relpath(targetpath, k)))
      log.debug("PathMapping match found, replacing %s with %s, final directory is %s." % (k, pathMapping[k], targetpath))
      break

  log.info("Checking if any sections on %s contain the path %s.", label, targetpath)

  if plex:
    sections: list[LibrarySection] = plex.library.sections()

    section: LibrarySection
    for section in sections:
      location: str
      for location in section.locations:
        log.debug("Checking section %s path %s." % (section.title, location))
        if os.path.commonprefix([targetpath, location]) == location:
          section.update(path=targetpath)
          log.info("Refreshing %s with path %s" % (section.title, targetpath))
  else:
    log.error("Unable to establish Plex server connection for %s.", label)


def _connect_plex(inst: dict, log: logging.Logger, label: str) -> PlexServer | None:
  """Build a PlexServer for one instance dict."""
  if not inst.get("host") or not inst.get("token"):
    log.error("No Plex host/token configured for %s — check setup/local.yml services.plex.<name>.", label)
    return None
  session: requests.Session | None = None
  if inst.get("ignore-certs"):
    session = requests.Session()
    session.verify = False
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
  protocol = "https://" if inst.get("ssl") else "http://"
  try:
    plex = PlexServer(protocol + str(inst.get("host")) + ":" + str(inst.get("port")), inst.get("token"), session=session)
    log.info("Connected to Plex server %s (%s).", plex.friendlyName, label)
    return plex
  except Exception:
    log.exception("Error connecting to Plex server %s.", label)
    return None


def getPlexServer(settings: ReadSettings, logger: logging.Logger | None = None) -> PlexServer | None:
  """Establish a connection to the first configured Plex Media Server.

  Backward-compat shim around the per-instance ``_connect_plex``. Reads
  ``settings.Plex`` (the singleton) and delegates. New code paths should
  iterate ``settings.plex_instances`` instead.
  """
  log = logger or getLogger(__name__)
  inst = getattr(settings, "Plex", None) or {}
  if not inst:
    log.error("No Plex host/token configured, please update your configuration file.")
    return None
  return _connect_plex(inst, log, label=str(inst.get("host") or "plex"))
