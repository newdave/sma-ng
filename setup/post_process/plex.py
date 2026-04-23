#!/usr/bin/env python3
import os

from plexapi.server import PlexServer

HOST = "localhost"
PORT = 32400
TOKEN = "ztwEQ-7tKZs9uAmszfzd"
SSL = False


def main():
  print("Plex Post-Processing Refresh Script")
  protocol = "https" if SSL else "http"
  plex: PlexServer = PlexServer(f"{protocol}://{HOST}:{PORT}", TOKEN)
  sectionType = "show" if os.environ.get("SMA_SEASON") or os.environ.get("SMA_EPISODE") else "movie"
  for section in plex.library.sections():
    if section.type == sectionType:
      print("Updating section %s on server %s:%s" % (section.title, HOST, PORT))
      section.update()


if __name__ == "__main__":
  main()
