#!/opt/sma/venv/bin/python3
"""Updates ``sma-ng.yml`` with FFmpeg paths and media manager settings read from ``config.xml``."""

import configparser
import logging
import os
import sys
import xml.etree.ElementTree as ET

from resources.readsettings import ReadSettings
from resources.yamlconfig import load as yaml_load
from resources.yamlconfig import write as yaml_write

xml = "/config/config.xml"
autoProcess = None


def main():
  """Read ``config.xml`` and patch ``sma-ng.yml`` with derived settings.

  Sets FFmpeg/FFprobe paths from ``SMA_FFMPEG_PATH``/``SMA_FFPROBE_PATH``
  environment variables (falling back to bare ``ffmpeg``/``ffprobe``). When
  ``SMA_RS`` names a config section and ``config.xml`` is present, also
  writes the media manager API key, SSL flag, port, webroot, and host.
  """
  _autoProcess = autoProcess if autoProcess is not None else os.path.join(os.environ.get("SMA_PATH", "/usr/local/sma"), "config/sma-ng.yml")
  _xml = xml

  # Ensure a valid config file
  ReadSettings()

  if not os.path.isfile(_autoProcess):
    legacy_ini = os.path.splitext(_autoProcess)[0] + ".ini"
    if os.path.isfile(legacy_ini):
      _autoProcess = legacy_ini
    else:
      logging.error("autoProcess config does not exist")
      sys.exit(1)

  if _autoProcess.endswith((".yaml", ".yml")):
    config = yaml_load(_autoProcess)
  elif _autoProcess.endswith(".ini"):
    config = configparser.ConfigParser()
    config.read(_autoProcess)
  else:
    logging.error("Unsupported config format: %s" % _autoProcess)
    sys.exit(1)

  # Set FFMPEG/FFProbe Paths
  ffmpegpath = os.environ.get("SMA_FFMPEG_PATH") or "ffmpeg"
  ffprobepath = os.environ.get("SMA_FFPROBE_PATH") or "ffprobe"
  if isinstance(config, configparser.ConfigParser):
    config.set("Converter", "ffmpeg", ffmpegpath)
    config.set("Converter", "ffprobe", ffprobepath)
  else:
    config.setdefault("Converter", {})["ffmpeg"] = ffmpegpath
    config.setdefault("Converter", {})["ffprobe"] = ffprobepath

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
    if isinstance(config, configparser.ConfigParser):
      if not config.has_section(section):
        config.add_section(section)
      config.set(section, "apikey", apikey)
      config.set(section, "ssl", str(ssl).lower())
      config.set(section, "port", sslport if ssl else port)
      config.set(section, "webroot", webroot)
    else:
      config.setdefault(section, {})["apikey"] = apikey
      config[section]["ssl"] = ssl
      config[section]["port"] = int(sslport if ssl else port)
      config[section]["webroot"] = webroot

    # Set IP from environment variable
    ip = os.environ.get("HOST")
    host = ip if ip else "127.0.0.1"
    if isinstance(config, configparser.ConfigParser):
      config.set(section, "host", host)
    else:
      config[section]["host"] = host

  if isinstance(config, configparser.ConfigParser):
    with open(_autoProcess, "w") as fp:
      config.write(fp)
  else:
    yaml_write(_autoProcess, config)


if __name__ == "__main__":
  main()
