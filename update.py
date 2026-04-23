#!/opt/sma/venv/bin/python3
"""Updates ``autoProcess.ini`` with FFmpeg paths and media manager settings read from ``config.xml``."""

import configparser
import logging
import os
import sys
import xml.etree.ElementTree as ET

from resources.readsettings import ReadSettings

xml = "/config/config.xml"
autoProcess = None


def main():
  """Read ``config.xml`` and patch ``autoProcess.ini`` with derived settings.

  Sets FFmpeg/FFprobe paths from ``SMA_FFMPEG_PATH``/``SMA_FFPROBE_PATH``
  environment variables (falling back to bare ``ffmpeg``/``ffprobe``). When
  ``SMA_RS`` names a config section and ``config.xml`` is present, also
  writes the media manager API key, SSL flag, port, webroot, and host.
  """
  _autoProcess = autoProcess if autoProcess is not None else os.path.join(os.environ.get("SMA_PATH", "/usr/local/sma"), "config/autoProcess.ini")
  _xml = xml

  # Ensure a valid config file
  ReadSettings()

  if not os.path.isfile(_autoProcess):
    logging.error("autoProcess.ini does not exist")
    sys.exit(1)

  safeConfigParser = configparser.ConfigParser()
  safeConfigParser.read(_autoProcess)

  # Set FFMPEG/FFProbe Paths
  ffmpegpath = os.environ.get("SMA_FFMPEG_PATH") or "ffmpeg"
  ffprobepath = os.environ.get("SMA_FFPROBE_PATH") or "ffprobe"
  safeConfigParser.set("Converter", "ffmpeg", ffmpegpath)
  safeConfigParser.set("Converter", "ffprobe", ffprobepath)

  section = os.environ.get("SMA_RS")
  if section and os.path.isfile(_xml):
    tree = ET.parse(_xml)
    root = tree.getroot()
    port = root.find("Port").text
    try:
      sslport = root.find("SslPort").text
    except:
      sslport = port
    webroot = root.find("UrlBase").text
    webroot = webroot if webroot else ""
    ssl = root.find("EnableSsl").text
    ssl = ssl.lower() in ["true", "yes", "t", "1", "y"] if ssl else False
    apikey = root.find("ApiKey").text

    # Set values from config.xml
    safeConfigParser.set(section, "apikey", apikey)
    safeConfigParser.set(section, "ssl", str(ssl).lower())
    safeConfigParser.set(section, "port", sslport if ssl else port)
    safeConfigParser.set(section, "webroot", webroot)

    # Set IP from environment variable
    ip = os.environ.get("HOST")
    if ip:
      safeConfigParser.set(section, "host", ip)
    else:
      safeConfigParser.set(section, "host", "127.0.0.1")

  with open(_autoProcess, "w") as fp:
    safeConfigParser.write(fp)


if __name__ == "__main__":
  main()
