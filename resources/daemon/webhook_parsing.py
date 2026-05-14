import json
import re as _re
import shlex

_ARR_TVDB_RE = _re.compile(r"\{tvdb-(\d+)\}", _re.IGNORECASE)
_ARR_TMDB_RE = _re.compile(r"\{tmdb-(\d+)\}", _re.IGNORECASE)
_ARR_PROFILE_TAG_PREFIX = "sma-profile-"


def _extract_arr_tags(tags):
  """Return ``(tag_ids, tag_labels)`` from ARR payload tag values.

  Sonarr/Radarr webhook payloads may expose tags as integer IDs, label
  strings, or objects depending on version/plugin shape.
  """
  tag_ids = []
  tag_labels = []
  for tag in tags or []:
    if isinstance(tag, int):
      tag_ids.append(tag)
      continue
    if isinstance(tag, str):
      value = tag.strip()
      if not value:
        continue
      if value.isdigit():
        tag_ids.append(int(value))
      else:
        tag_labels.append(value)
      continue
    if isinstance(tag, dict):
      tag_id = tag.get("id")
      if isinstance(tag_id, int):
        tag_ids.append(tag_id)
      label = tag.get("label")
      if isinstance(label, str) and label.strip():
        tag_labels.append(label.strip())
  return tag_ids, tag_labels


def _extract_profile_from_tag_labels(tag_labels):
  """Return profile override from an ARR tag label, or ``None``.

  Matches labels like ``sma-profile-lq`` and returns ``lq``.
  """
  for label in tag_labels or []:
    normalized = str(label).strip()
    lowered = normalized.lower()
    if lowered.startswith(_ARR_PROFILE_TAG_PREFIX) and len(lowered) > len(_ARR_PROFILE_TAG_PREFIX):
      return normalized[len(_ARR_PROFILE_TAG_PREFIX) :].strip()
  return None


def parse_generic_webhook_body(body_text, content_type, send_json_response):
  """Parse generic webhook input into (path, extra_args, config_override, max_retries)."""
  if not body_text:
    send_json_response(400, {"error": "Empty request body"})
    return None, [], None, 0

  path = None
  extra_args = []
  config_override = None
  max_retries = 0

  if "application/json" in content_type:
    try:
      data = json.loads(body_text)
      if isinstance(data, dict):
        path = data.get("path") or data.get("file") or data.get("input")
        extra_args = data.get("args", [])
        config_override = data.get("config")
        max_retries = int(data.get("max_retries", 0))
        if isinstance(extra_args, str):
          extra_args = shlex.split(extra_args)
      elif isinstance(data, str):
        path = data
    except json.JSONDecodeError:
      path = body_text
    except (ValueError, TypeError) as exc:
      send_json_response(400, {"error": "Invalid webhook body", "message": str(exc)})
      return None, [], None, 0
  else:
    path = body_text

  if not path:
    send_json_response(400, {"error": "No path provided"})
    return None, [], None, 0

  return path, extra_args, config_override, max_retries


def parse_sonarr_body(body_text, send_json_response):
  """Parse Sonarr webhook input into (path, extra_args, profile_override, tag_ids)."""
  if not body_text:
    send_json_response(400, {"error": "Empty request body"})
    return None, [], None, []

  try:
    data = json.loads(body_text)
  except (json.JSONDecodeError, ValueError):
    send_json_response(400, {"error": "Invalid JSON"})
    return None, [], None, []

  event_type = data.get("eventType", "")
  if event_type == "Test":
    send_json_response(200, {"status": "ok", "message": "SMA-NG Sonarr webhook test successful"})
    return None, [], None, []
  if event_type != "Download":
    send_json_response(400, {"error": "Unsupported eventType '%s'; only 'Download' is handled" % event_type})
    return None, [], None, []

  episode_file = data.get("episodeFile") or {}
  path = episode_file.get("path", "").strip()
  if not path:
    send_json_response(400, {"error": "episodeFile.path is missing or empty"})
    return None, [], None, []

  series = data.get("series") or {}
  episodes = data.get("episodes") or []
  tag_ids, tag_labels = _extract_arr_tags(series.get("tags") or [])
  profile_override = _extract_profile_from_tag_labels(tag_labels)
  args = ["--tv"]

  tvdb_id = series.get("tvdbId")
  if not tvdb_id:
    match = _ARR_TVDB_RE.search(path)
    if match:
      tvdb_id = int(match.group(1))
  if tvdb_id:
    args += ["-tvdb", str(tvdb_id)]
    if episodes:
      first = episodes[0]
      season = first.get("seasonNumber")
      if season is not None:
        args += ["-s", str(season)]
      for episode in episodes:
        episode_num = episode.get("episodeNumber")
        if episode_num is not None:
          args += ["-e", str(episode_num)]
  else:
    imdb_id = series.get("imdbId")
    if imdb_id:
      args += ["-imdb", str(imdb_id)]

  return path, args, profile_override, tag_ids


def parse_radarr_body(body_text, send_json_response):
  """Parse Radarr webhook input into (path, extra_args, profile_override, tag_ids)."""
  if not body_text:
    send_json_response(400, {"error": "Empty request body"})
    return None, [], None, []

  try:
    data = json.loads(body_text)
  except (json.JSONDecodeError, ValueError):
    send_json_response(400, {"error": "Invalid JSON"})
    return None, [], None, []

  event_type = data.get("eventType", "")
  if event_type == "Test":
    send_json_response(200, {"status": "ok", "message": "SMA-NG Radarr webhook test successful"})
    return None, [], None, []
  if event_type != "Download":
    send_json_response(400, {"error": "Unsupported eventType '%s'; only 'Download' is handled" % event_type})
    return None, [], None, []

  movie_file = data.get("movieFile") or {}
  path = movie_file.get("path", "").strip()
  if not path:
    send_json_response(400, {"error": "movieFile.path is missing or empty"})
    return None, [], None, []

  movie = data.get("movie") or {}
  tag_ids, tag_labels = _extract_arr_tags(movie.get("tags") or [])
  profile_override = _extract_profile_from_tag_labels(tag_labels)
  args = ["--movie"]
  tmdb_id = movie.get("tmdbId")
  if not tmdb_id:
    match = _ARR_TMDB_RE.search(path)
    if match:
      tmdb_id = int(match.group(1))
  if tmdb_id:
    args += ["-tmdb", str(tmdb_id)]
  else:
    imdb_id = movie.get("imdbId")
    if imdb_id:
      args += ["-imdb", str(imdb_id)]

  return path, args, profile_override, tag_ids
