"""Shared API helpers for Sonarr/Radarr post-processing scripts."""

import os
import time

import requests

from autoprocess._media_server import apply_path_mapping, path_under

# Seconds before any Sonarr/Radarr HTTP call is abandoned.
REQUEST_TIMEOUT = 30

ARR_KINDS = ("sonarr", "radarr")


def matching_arr_instances(filepath, settings, kinds=ARR_KINDS):
  """Return every ``(instance, kind)`` whose routing path holds *filepath*, best match first.

  An instance is usable only when it has both a ``path`` and an ``apikey``.
  Both paths are normalised and matched on directory boundaries (``/TV`` does not
  claim ``/TV-Anime``). The longest instance path wins across all *kinds*; ties keep
  the order of *kinds* and then the settings order.
  """
  dirpath = os.path.normpath(os.path.dirname(filepath))
  found = []
  for kind in kinds:
    for instance in getattr(settings, kind + "_instances", None) or []:
      ipath = instance.get("path") or ""
      if not ipath or not instance.get("apikey"):
        continue
      ipath = os.path.normpath(ipath)
      if path_under(dirpath, ipath):
        found.append((len(ipath), instance, kind))
  found.sort(key=lambda item: item[0], reverse=True)
  return [(instance, kind) for _, instance, kind in found]


def find_arr_instance(filepath, settings, kinds=ARR_KINDS):
  """Return ``(instance, kind)`` for the best Sonarr/Radarr match of *filepath*, or ``(None, None)``."""
  matches = matching_arr_instances(filepath, settings, kinds)
  return matches[0] if matches else (None, None)


def build_api(settings_section, user_agent):
  """Build base URL and headers from a Sonarr/Radarr settings dict."""
  protocol = "https://" if settings_section.get("ssl") else "http://"
  base_url = protocol + settings_section["host"] + ":" + str(settings_section["port"]) + (settings_section.get("webroot") or "")
  headers = {"X-Api-Key": settings_section["apikey"], "User-Agent": user_agent}
  return base_url, headers


def api_command(base_url, headers, payload, log):
  """POST a command to /api/v3/command and return the response."""
  url = base_url + "/api/v3/command"
  log.debug("API command: %s" % str(payload))
  r = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
  rstate = r.json()
  try:
    rstate = rstate[0]
  except (KeyError, IndexError, TypeError):
    pass
  log.info("API response: ID %s %s." % (rstate.get("id", "?"), rstate.get("status", "?")))
  return rstate


# Command states after which Sonarr/Radarr will never report "completed".
TERMINAL_FAILURE_STATES = ("failed", "aborted", "cancelled", "canceled", "orphaned")


def wait_for_command(base_url, headers, command_id, log, timeout=300, delay=2, max_delay=15):
  """Poll /api/v3/command/{id} until completed, a terminal failure state, or the time budget runs out.

  The poll interval starts at ``delay`` seconds and doubles up to ``max_delay``. A transient
  HTTP/JSON error on one poll is logged and retried rather than aborting the wait. Returns True
  only when the command completed; the last seen status is logged on any other outcome.
  """
  url = base_url + "/api/v3/command/" + str(command_id)
  deadline = time.monotonic() + timeout
  status = "unknown"
  while True:
    try:
      r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
      status = str(r.json().get("status", "unknown")).lower()
    except Exception as e:
      log.warning("Command %s poll failed (%s); will retry." % (command_id, type(e).__name__))
    else:
      if status in ("complete", "completed"):
        return True
      if status in TERMINAL_FAILURE_STATES:
        log.warning("Command %s ended with status '%s'." % (command_id, status))
        return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      log.warning("Command %s did not complete within %ss; last status '%s'." % (command_id, timeout, status))
      return False
    time.sleep(min(delay, remaining))
    delay = min(delay * 2, max_delay)


def api_get(base_url, headers, endpoint, log):
  """GET /api/v3/{endpoint}."""
  url = base_url + "/api/v3/" + endpoint
  r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
  return r.json()


def api_put(base_url, headers, endpoint, data, log):
  """PUT /api/v3/{endpoint}."""
  url = base_url + "/api/v3/" + endpoint
  r = requests.put(url, json=data, headers=headers, timeout=REQUEST_TIMEOUT)
  return r.json()


def rescan(base_url, headers, command_name, id_field, media_id, log):
  """Trigger a rescan command and wait for completion. Returns True on success."""
  cmd = api_command(base_url, headers, {"name": command_name, id_field: media_id}, log)
  return wait_for_command(base_url, headers, cmd["id"], log)


def _arr_media_by_external_id(base_url, headers, endpoint, params, log):
  """Return the Sonarr series / Radarr movie id matching an external id query, or None."""
  resp = requests.get(base_url + "/api/v3/" + endpoint, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
  data = resp.json()
  if isinstance(data, dict):
    data = [data]
  for item in data or []:
    if isinstance(item, dict) and item.get("id"):
      return item["id"]
  log.debug("rescan_via_arr: no %s matched %s" % (endpoint, params))
  return None


def _arr_media_by_parse(base_url, headers, key, file_path):
  """Return the id Sonarr/Radarr's title parser maps the file's basename to, or None."""
  resp = requests.get(base_url + "/api/v3/parse", headers=headers, params={"title": os.path.basename(file_path)}, timeout=REQUEST_TIMEOUT)
  media = resp.json().get(key) or {}
  return media.get("id")


def _arr_media_by_path(base_url, headers, endpoint, file_path):
  """Return the id of the series/movie whose library ``path`` contains *file_path*, or None.

  The longest matching path wins so ``/tv/Blade Runner 2099`` beats ``/tv/Blade Runner``.
  """
  resp = requests.get(base_url + "/api/v3/" + endpoint, headers=headers, timeout=REQUEST_TIMEOUT)
  dirpath = os.path.normpath(os.path.dirname(file_path))
  best_id, best_len = None, -1
  for item in resp.json() or []:
    ipath = item.get("path") if isinstance(item, dict) else None
    if not ipath or not item.get("id"):
      continue
    ipath = os.path.normpath(ipath)
    if path_under(dirpath, ipath) and len(ipath) > best_len:
      best_id, best_len = item["id"], len(ipath)
  return best_id


def resolve_arr_media_id(base_url, headers, arr_type, file_path, log, tvdbid=None, tmdbid=None):
  """Resolve the Sonarr series id / Radarr movie id that owns *file_path*.

  Tries, in order: the external id the trigger passed (Sonarr ``tvdbId``, Radarr
  ``tmdbId``), the title parser on the basename, and finally the series/movie whose
  library ``path`` contains the file. The parser alone is not enough: Sonarr reads a
  numeric title such as ``Blade Runner 2099 (2026) - S01E01`` as "Blade Runner" with
  year 2099 and returns no series, which left converted files unlinked.
  """
  if arr_type == "sonarr":
    endpoint, key, ext_params = "series", "series", ({"tvdbId": tvdbid} if tvdbid else None)
  else:
    endpoint, key, ext_params = "movie", "movie", ({"tmdbId": tmdbid} if tmdbid else None)
  if ext_params:
    media_id = _arr_media_by_external_id(base_url, headers, endpoint, ext_params, log)
    if media_id:
      return media_id
  media_id = _arr_media_by_parse(base_url, headers, key, file_path)
  if media_id:
    return media_id
  return _arr_media_by_path(base_url, headers, endpoint, file_path)


def rescan_via_arr(base_url, headers, arr_type, file_path, log, tvdbid=None, tmdbid=None):
  """Trigger Sonarr/Radarr to re-read an existing library file after in-place conversion.

  Resolves the series/movie id with :func:`resolve_arr_media_id` (external id,
  then title parse, then library path), then issues ``RescanSeries`` (Sonarr) or
  ``RescanMovie`` (Radarr) for that id. This is the correct command for files
  already imported into the library — ``DownloadedEpisodesScan`` /
  ``DownloadedMoviesScan`` are scoped to download-client folders and silently
  no-op on library paths.

  Returns the command ID on success, or None on lookup failure / API error.
  The caller decides whether to wait for completion (e.g. before issuing a
  follow-up RenameFiles command).
  """
  try:
    media_id = resolve_arr_media_id(base_url, headers, arr_type, file_path, log, tvdbid=tvdbid, tmdbid=tmdbid)
    if arr_type == "sonarr":
      if not media_id:
        log.warning("rescan_via_arr: Sonarr could not resolve series for %s (tvdbId=%s)" % (file_path, tvdbid))
        return None
      payload = {"name": "RescanSeries", "seriesId": media_id}
    else:
      if not media_id:
        log.warning("rescan_via_arr: Radarr could not resolve movie for %s (tmdbId=%s)" % (file_path, tmdbid))
        return None
      payload = {"name": "RescanMovie", "movieId": media_id, "movieIds": [media_id]}

    log.info("rescan_via_arr: triggering %s for id=%s" % (payload["name"], media_id))
    cmd = api_command(base_url, headers, payload, log)
    return cmd.get("id")
  except Exception:
    log.exception("rescan_via_arr: unexpected error for %s" % file_path)
    return None


def downloaded_scan_via_arr(base_url, headers, arr_type, file_path, log):
  """Trigger Sonarr/Radarr to import a specific file already on disk.

  After ``RescanSeries`` / ``RescanMovie`` runs, any episodefile/moviefile
  whose recorded path no longer exists is unlinked from the database. When
  SMA converts in place (``Show.S01E01.mkv`` → ``Show.S01E01.mp4``) the old
  path is gone, so the unlink fires and the new file is left orphaned —
  Rescan* re-evaluates known records but does not import new files.

  ``DownloadedEpisodesScan`` / ``DownloadedMoviesScan`` with the *file*
  path (not its directory) and ``importMode: Move`` is the import flow:
  Sonarr/Radarr identifies the file, matches it to the existing
  series/movie, and links it as the new episodefile/moviefile. This works
  on library paths in Sonarr v3+/Radarr v4+ when the path argument is a
  single file rather than a download-client root.

  Returns the command ID on success, or None on API error.
  """
  try:
    if arr_type == "sonarr":
      payload = {"name": "DownloadedEpisodesScan", "path": file_path, "importMode": "Move"}
    else:
      payload = {"name": "DownloadedMoviesScan", "path": file_path, "importMode": "Move"}
    log.info("downloaded_scan_via_arr: triggering %s for %s" % (payload["name"], file_path))
    cmd = api_command(base_url, headers, payload, log)
    return cmd.get("id")
  except Exception:
    log.exception("downloaded_scan_via_arr: unexpected error for %s" % file_path)
    return None


def rename(base_url, headers, file_id, command_name_files, command_name_all, id_field, media_id, log):
  """Trigger a rename command."""
  if file_id:
    payload = {"name": command_name_files, "files": [file_id], id_field: media_id}
  else:
    payload = {"name": command_name_all, id_field + "s": [media_id]}
  cmd = api_command(base_url, headers, payload, log)
  wait_for_command(base_url, headers, cmd["id"], log)


def _find_file_id(records, file_path, path_mapping=None):
  """Return the id of the Arr file record that corresponds to *file_path*.

  Tries an exact match, then the path rewritten through the instance's
  ``path-mapping`` (local path -> path as seen by Sonarr/Radarr), then a
  unique basename match as a last resort for undeclared container mounts.
  """
  candidates = [file_path]
  if path_mapping:
    mapped = os.path.join(apply_path_mapping(os.path.dirname(file_path), path_mapping), os.path.basename(file_path))
    if mapped not in candidates:
      candidates.append(mapped)
  for cand in candidates:
    for rec in records:
      if rec.get("path") == cand:
        return rec["id"]
  base = os.path.basename(file_path)
  matches = [rec for rec in records if os.path.basename(rec.get("path") or "") == base]
  if len(matches) == 1:
    return matches[0]["id"]
  return None


# Per-arr API shape for rename_via_arr: parse-response key, file-record
# endpoint and the media id field shared by the file list and RenameFiles.
_ARR_RENAME_SPECS = {
  "sonarr": {"label": "Sonarr", "parse_key": "series", "files_endpoint": "episodefile", "id_field": "seriesId"},
  "radarr": {"label": "Radarr", "parse_key": "movie", "files_endpoint": "moviefile", "id_field": "movieId"},
}


def rename_via_arr(base_url, headers, arr_type, file_path, log, path_mapping=None):
  """Trigger Sonarr/Radarr's built-in RenameFiles command for a specific file.

  Looks up the file record by matching *file_path* against the path field in
  the episodefile (Sonarr) or moviefile (Radarr) list, then issues a
  RenameFiles command for that file ID.  Polls until the command completes.

  Returns the new file path string on success, or None if the file record
  could not be found or the command failed.
  """
  spec = _ARR_RENAME_SPECS["sonarr" if arr_type == "sonarr" else "radarr"]
  label, files_endpoint, id_field = spec["label"], spec["files_endpoint"], spec["id_field"]
  try:
    parse_resp = requests.get(
      base_url + "/api/v3/parse",
      headers=headers,
      params={"title": os.path.basename(file_path)},
      timeout=REQUEST_TIMEOUT,
    )
    parse_data = parse_resp.json()
    media = parse_data.get(spec["parse_key"]) or {}
    media_id = media.get("id")
    if not media_id:
      log.warning("rename_via_arr: %s could not parse %s for %s" % (label, spec["parse_key"], file_path))
      return None

    files_resp = requests.get(
      base_url + "/api/v3/" + files_endpoint,
      headers=headers,
      params={id_field: media_id},
      timeout=REQUEST_TIMEOUT,
    )
    file_records = files_resp.json()
    if isinstance(file_records, dict):
      file_records = [file_records]
    file_id = _find_file_id(file_records, file_path, path_mapping)

    if not file_id:
      log.warning("rename_via_arr: no %s %s record matched path %s" % (label, files_endpoint, file_path))
      return None

    log.info("rename_via_arr: triggering %s RenameFiles for %s=%s fileId=%s" % (label, id_field, media_id, file_id))
    cmd = api_command(base_url, headers, {"name": "RenameFiles", id_field: media_id, "files": [file_id]}, log)
    if not wait_for_command(base_url, headers, cmd["id"], log):
      log.warning("rename_via_arr: %s RenameFiles command did not complete successfully" % label)
      return None

    updated = requests.get(
      base_url + "/api/v3/" + files_endpoint + "/" + str(file_id),
      headers=headers,
      timeout=REQUEST_TIMEOUT,
    )
    return updated.json().get("path")
  except Exception:
    log.exception("rename_via_arr: unexpected error for %s" % file_path)
    return None
