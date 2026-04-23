#!/usr/bin/env python3
"""Plex Media Server library refresh integration."""

import logging
import os
from typing import List, Optional

import requests
from plexapi.library import LibrarySection
from plexapi.server import PlexServer

from resources.log import getLogger
from resources.readsettings import ReadSettings


def refreshPlex(settings: ReadSettings, path: str = None, logger: logging.Logger = None):
  """Trigger a targeted Plex library section refresh for a converted file's directory.

  Applies any configured path mappings before looking up which library
  section contains the file, then calls ``section.update(path=...)`` to
  refresh only that directory.

  Args:
      settings: Parsed SMA settings, used to read Plex connection details and
          path mappings.
      path: Absolute path to the converted output file. The parent directory
          is used as the refresh target.
      logger: Optional logger instance. Defaults to the module logger.
  """
  log = logger or getLogger(__name__)

  log.info("Starting Plex refresh.")

  targetpath = os.path.dirname(path)
  pathMapping = settings.Plex.get("path-mapping", {})

  # Path Mapping
  targetdirs = targetpath.split(os.sep)
  for k in sorted(pathMapping.keys(), reverse=True):
    mapdirs = k.split(os.sep)
    if mapdirs == targetdirs[: len(mapdirs)]:
      targetpath = os.path.normpath(os.path.join(pathMapping[k], os.path.relpath(targetpath, k)))
      log.debug("PathMapping match found, replacing %s with %s, final directory is %s." % (k, pathMapping[k], targetpath))
      break

  plex = getPlexServer(settings, log)

  log.info("Checking if any sections contain the path %s." % (targetpath))

  if plex:
    sections: List[LibrarySection] = plex.library.sections()

    section: LibrarySection
    for section in sections:
      location: str
      for location in section.locations:
        log.debug("Checking section %s path %s." % (section.title, location))
        if os.path.commonprefix([targetpath, location]) == location:
          section.update(path=targetpath)
          log.info("Refreshing %s with path %s" % (section.title, targetpath))
  else:
    log.error("Unable to establish Plex server connection.")


def getPlexServer(settings: ReadSettings, logger: logging.Logger = None) -> Optional[PlexServer]:
  """Establish a connection to a Plex Media Server.

  Connects directly to a local or reachable Plex Media Server using the
  configured host, port, and Plex token.

  Args:
      settings: Parsed SMA settings containing Plex connection details
          (``host``, ``port``, ``token``, ``ssl``, ``ignore_certs``).
      logger: Optional logger instance. Defaults to the module logger.

  Returns:
      A connected ``PlexServer`` instance, or ``None`` if connection fails.
  """
  log = logger or getLogger(__name__)

  if not settings.Plex.get("host") or not settings.Plex.get("token"):
    log.error("No Plex host/token configured, please update your configuration file.")
    return None

  plex: PlexServer = None
  session: requests.Session = None

  if settings.Plex.get("ignore-certs"):
    session = requests.Session()
    session.verify = False
    requests.packages.urllib3.disable_warnings()

  log.info("Connecting to Plex server...")
  protocol = "https://" if settings.Plex.get("ssl") else "http://"
  try:
    plex = PlexServer(protocol + settings.Plex.get("host") + ":" + str(settings.Plex.get("port")), settings.Plex.get("token"), session=session)
    log.info("Connected to Plex server %s using direct server settings." % (plex.friendlyName))
  except:
    log.exception("Error connecting to Plex server.")

  return plex
