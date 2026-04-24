import configparser
import os

from ruamel.yaml import YAML


def load(path: str) -> dict:
  yaml = YAML(typ="rt")
  try:
    with open(path, "r") as f:
      data = yaml.load(f)
    return dict(data) if data else {}
  except OSError:
    return {}


def write(path: str, data: dict) -> None:
  yaml = YAML(typ="rt")
  yaml.default_flow_style = False
  yaml.width = 120
  os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
  with open(path, "w") as f:
    yaml.dump(data, f)


def migrate_ini_to_yaml(ini_path: str, yaml_path: str, bak_path: str, defaults: dict) -> None:
  config = configparser.ConfigParser(strict=False)
  config.read(ini_path)

  separator = config.get("Converter", "opts-separator", fallback=",")

  data = {}
  for section in config.sections():
    section_data = {}
    if section in defaults:
      for key in config.options(section):
        default_val = defaults[section].get(key)
        raw = config.get(section, key, fallback=None)
        if raw is None:
          section_data[key] = default_val
          continue
        if isinstance(default_val, bool):
          section_data[key] = config.getboolean(section, key, fallback=default_val)
        elif isinstance(default_val, int):
          section_data[key] = config.getint(section, key, fallback=default_val)
        elif isinstance(default_val, float):
          section_data[key] = config.getfloat(section, key, fallback=default_val)
        elif isinstance(default_val, list):
          if key in ("preopts", "postopts"):
            parts = raw.split(separator)
          else:
            parts = raw.split(",")
          section_data[key] = [p.strip() for p in parts if p.strip()]
        elif isinstance(default_val, dict):
          result = {}
          for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
              k, v = pair.split(":", 1)
              k, v = k.strip(), v.strip()
              if key == "bitrate-ratio":
                try:
                  result[k] = float(v)
                except ValueError:
                  result[k] = v
              else:
                result[k] = v
          section_data[key] = result
        else:
          section_data[key] = config.get(section, key, fallback=str(default_val) if default_val is not None else "")
    else:
      for key in config.options(section):
        section_data[key] = config.get(section, key)
    data[section] = section_data

  write(yaml_path, data)
  os.rename(ini_path, bak_path)


def cfg_getpath(val) -> str | None:
  if not val:
    return None
  return os.path.normpath(os.path.expandvars(str(val)))


def cfg_getdirectory(val) -> str | None:
  path = cfg_getpath(val)
  if path:
    try:
      os.makedirs(path, exist_ok=True)
    except (OSError, TypeError):
      pass
  return path


def cfg_getdirectories(val) -> list:
  if not val:
    return []
  result = []
  for v in val:
    p = cfg_getpath(v)
    if p:
      try:
        os.makedirs(p, exist_ok=True)
      except (OSError, TypeError):
        pass
      result.append(p)
  return result


def cfg_getextension(val) -> str | None:
  if not val:
    return None
  ext = str(val).lower().replace(" ", "").replace(".", "")
  return ext if ext else None


def cfg_getextensions(val) -> list:
  if not val:
    return []
  return [x for x in (cfg_getextension(v) for v in val) if x]
