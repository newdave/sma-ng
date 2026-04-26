import io
import os
import re

from ruamel.yaml import YAML

# Hyphen and underscore are alias-equivalent in our schema (pydantic
# populate_by_name + alias_generator=_to_kebab). Treat them as the same key
# during dedup so users with mixed kebab/snake configs don't end up with
# two separate dict entries pointing at the same logical field.
_TOPLEVEL_KEY_RE = re.compile(r"(?m)^([A-Za-z_][\w-]*):")


def _canonicalize_keys(obj):
  """Recursively rewrite mapping keys to use underscores (snake_case) so
  ``foo-bar`` and ``foo_bar`` collapse onto a single key during dedup.
  Lists are walked element-wise; scalars pass through."""
  if isinstance(obj, dict):
    out = {}
    for k, v in obj.items():
      key = k.replace("-", "_") if isinstance(k, str) else k
      out[key] = _canonicalize_keys(v)
    return out
  if isinstance(obj, list):
    return [_canonicalize_keys(v) for v in obj]
  return obj


def _deep_merge(a, b):
  """Recursively merge ``b`` into ``a`` with later-wins semantics for
  scalars and lists; dicts merge key-by-key. Returns the merged result
  without mutating either input."""
  if isinstance(a, dict) and isinstance(b, dict):
    out = dict(a)
    for k, v in b.items():
      out[k] = _deep_merge(out[k], v) if k in out else v
    return out
  return b


def _load_with_dedup(path: str):
  """Load a YAML file, transparently deep-merging duplicate top-level keys
  (later-wins for leaves) so hand-edited or stamped-twice configs still
  resolve. Returns a ruamel CommentedMap so callers can round-trip writes."""
  yaml = YAML(typ="rt")
  yaml.allow_duplicate_keys = True
  with open(path, "r") as f:
    raw = f.read()

  keys = _TOPLEVEL_KEY_RE.findall(raw)
  if len(keys) == len(set(keys)):
    return yaml.load(raw)

  # Duplicates found — fall back to PyYAML with a deep-merging custom
  # constructor and round-trip the result back through ruamel so callers
  # still get a CommentedMap. Comments inside merged sections are lost,
  # which is the cost of recovery; surrounding sections retain theirs on
  # subsequent reads since the dump produces a deduped file.
  import yaml as _pyyaml

  class _MergeLoader(_pyyaml.SafeLoader):
    pass

  def _construct_merging_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
      key = loader.construct_object(key_node, deep=deep)
      value = loader.construct_object(value_node, deep=deep)
      if key in result:
        result[key] = _deep_merge(result[key], value)
      else:
        result[key] = value
    return result

  _MergeLoader.add_constructor(
    _pyyaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_merging_mapping,
  )

  plain = _pyyaml.load(raw, _MergeLoader) or {}
  plain = _canonicalize_keys(plain)
  buf = io.StringIO()
  yaml.dump(plain, buf)
  return yaml.load(buf.getvalue())


def load(path: str) -> dict:
  try:
    data = _load_with_dedup(path)
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
