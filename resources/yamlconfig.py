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
