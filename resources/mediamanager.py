"""Shared API helpers for Sonarr/Radarr post-processing scripts."""

import time

import requests


def build_api(settings_section, user_agent):
  """Build base URL and headers from a Sonarr/Radarr settings dict."""
  protocol = "https://" if settings_section.get("ssl") else "http://"
  base_url = protocol + settings_section["host"] + ":" + str(settings_section["port"]) + settings_section.get("webroot", "")
  headers = {"X-Api-Key": settings_section["apikey"], "User-Agent": user_agent}
  return base_url, headers


def api_command(base_url, headers, payload, log):
  """POST a command to /api/v3/command and return the response."""
  url = base_url + "/api/v3/command"
  log.debug("API command: %s" % str(payload))
  r = requests.post(url, json=payload, headers=headers)
  rstate = r.json()
  try:
    rstate = rstate[0]
  except (KeyError, IndexError, TypeError):
    pass
  log.info("API response: ID %s %s." % (rstate.get("id", "?"), rstate.get("status", "?")))
  return rstate


def wait_for_command(base_url, headers, command_id, log, retries=6, delay=10):
  """Poll /api/v3/command/{id} until completed or retries exhausted."""
  url = base_url + "/api/v3/command/" + str(command_id)
  r = requests.get(url, headers=headers)
  command = r.json()
  attempts = 0
  while command["status"].lower() not in ["complete", "completed"] and attempts < retries:
    time.sleep(delay)
    r = requests.get(url, headers=headers)
    command = r.json()
    attempts += 1
  return command["status"].lower() in ["complete", "completed"]


def api_get(base_url, headers, endpoint, log):
  """GET /api/v3/{endpoint}."""
  url = base_url + "/api/v3/" + endpoint
  r = requests.get(url, headers=headers)
  return r.json()


def api_put(base_url, headers, endpoint, data, log):
  """PUT /api/v3/{endpoint}."""
  url = base_url + "/api/v3/" + endpoint
  r = requests.put(url, json=data, headers=headers)
  return r.json()


def rescan(base_url, headers, command_name, id_field, media_id, log):
  """Trigger a rescan command and wait for completion. Returns True on success."""
  cmd = api_command(base_url, headers, {"name": command_name, id_field: media_id}, log)
  return wait_for_command(base_url, headers, cmd["id"], log)


def rescan_via_arr(base_url, headers, arr_type, file_path, log):
  """Trigger Sonarr/Radarr to re-read an existing library file after in-place conversion.

  Looks up the series/movie ID via ``/api/v3/parse`` (matching on the file's
  basename), then issues ``RescanSeries`` (Sonarr) or ``RescanMovie`` (Radarr)
  for that ID. This is the correct command for files already imported into the
  library — ``DownloadedEpisodesScan`` / ``DownloadedMoviesScan`` are scoped to
  download-client folders and silently no-op on library paths.

  Returns the command ID on success, or None on lookup failure / API error.
  The caller decides whether to wait for completion (e.g. before issuing a
  follow-up RenameFiles command).
  """
  import os

  try:
    parse_resp = requests.get(
      base_url + "/api/v3/parse",
      headers=headers,
      params={"title": os.path.basename(file_path)},
      timeout=10,
    )
    parse_data = parse_resp.json()
    if arr_type == "sonarr":
      media = parse_data.get("series") or {}
      media_id = media.get("id")
      if not media_id:
        log.warning("rescan_via_arr: Sonarr could not parse series for %s" % file_path)
        return None
      payload = {"name": "RescanSeries", "seriesId": media_id}
    else:
      media = parse_data.get("movie") or {}
      media_id = media.get("id")
      if not media_id:
        log.warning("rescan_via_arr: Radarr could not parse movie for %s" % file_path)
        return None
      payload = {"name": "RescanMovie", "movieIds": [media_id]}

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


def rename_via_arr(base_url, headers, arr_type, file_path, log):
  """Trigger Sonarr/Radarr's built-in RenameFiles command for a specific file.

  Looks up the file record by matching *file_path* against the path field in
  the episodefile (Sonarr) or moviefile (Radarr) list, then issues a
  RenameFiles command for that file ID.  Polls until the command completes.

  Returns the new file path string on success, or None if the file record
  could not be found or the command failed.
  """
  import os

  try:
    if arr_type == "sonarr":
      parse_resp = requests.get(
        base_url + "/api/v3/parse",
        headers=headers,
        params={"title": os.path.basename(file_path)},
        timeout=10,
      )
      parse_data = parse_resp.json()
      series = parse_data.get("series") or {}
      media_id = series.get("id")
      if not media_id:
        log.warning("rename_via_arr: Sonarr could not parse series for %s" % file_path)
        return None

      files_resp = requests.get(
        base_url + "/api/v3/episodefile",
        headers=headers,
        params={"seriesId": media_id},
        timeout=10,
      )
      file_records = files_resp.json()
      file_id = None
      for rec in file_records:
        if rec.get("path") == file_path:
          file_id = rec["id"]
          break

      if not file_id:
        log.warning("rename_via_arr: no Sonarr episodefile record matched path %s" % file_path)
        return None

      log.info("rename_via_arr: triggering Sonarr RenameFiles for seriesId=%s fileId=%s" % (media_id, file_id))
      cmd = api_command(
        base_url,
        headers,
        {
          "name": "RenameFiles",
          "seriesId": media_id,
          "files": [file_id],
        },
        log,
      )
      if not wait_for_command(base_url, headers, cmd["id"], log):
        log.warning("rename_via_arr: Sonarr RenameFiles command did not complete successfully")
        return None

      updated = requests.get(
        base_url + "/api/v3/episodefile/" + str(file_id),
        headers=headers,
        timeout=10,
      )
      return updated.json().get("path")

    else:  # radarr
      parse_resp = requests.get(
        base_url + "/api/v3/parse",
        headers=headers,
        params={"title": os.path.basename(file_path)},
        timeout=10,
      )
      parse_data = parse_resp.json()
      movie = parse_data.get("movie") or {}
      media_id = movie.get("id")
      if not media_id:
        log.warning("rename_via_arr: Radarr could not parse movie for %s" % file_path)
        return None

      files_resp = requests.get(
        base_url + "/api/v3/moviefile",
        headers=headers,
        params={"movieId": media_id},
        timeout=10,
      )
      file_records = files_resp.json()
      if isinstance(file_records, dict):
        file_records = [file_records]
      file_id = None
      for rec in file_records:
        if rec.get("path") == file_path:
          file_id = rec["id"]
          break

      if not file_id:
        log.warning("rename_via_arr: no Radarr moviefile record matched path %s" % file_path)
        return None

      log.info("rename_via_arr: triggering Radarr RenameFiles for movieId=%s fileId=%s" % (media_id, file_id))
      cmd = api_command(
        base_url,
        headers,
        {
          "name": "RenameFiles",
          "movieIds": [media_id],
        },
        log,
      )
      if not wait_for_command(base_url, headers, cmd["id"], log):
        log.warning("rename_via_arr: Radarr RenameFiles command did not complete successfully")
        return None

      updated = requests.get(
        base_url + "/api/v3/moviefile/" + str(file_id),
        headers=headers,
        timeout=10,
      )
      return updated.json().get("path")

  except Exception:
    log.exception("rename_via_arr: unexpected error for %s" % file_path)
    return None
