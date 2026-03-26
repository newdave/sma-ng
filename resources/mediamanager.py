"""Shared API helpers for Sonarr/Radarr post-processing scripts."""
import time
import requests


def build_api(settings_section, user_agent):
    """Build base URL and headers from a Sonarr/Radarr settings dict."""
    protocol = "https://" if settings_section.get('ssl') else "http://"
    base_url = protocol + settings_section['host'] + ":" + str(settings_section['port']) + settings_section.get('webroot', '')
    headers = {'X-Api-Key': settings_section['apikey'], 'User-Agent': user_agent}
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
    log.info("API response: ID %s %s." % (rstate.get('id', '?'), rstate.get('status', '?')))
    return rstate


def wait_for_command(base_url, headers, command_id, log, retries=6, delay=10):
    """Poll /api/v3/command/{id} until completed or retries exhausted."""
    url = base_url + "/api/v3/command/" + str(command_id)
    r = requests.get(url, headers=headers)
    command = r.json()
    attempts = 0
    while command['status'].lower() not in ['complete', 'completed'] and attempts < retries:
        time.sleep(delay)
        r = requests.get(url, headers=headers)
        command = r.json()
        attempts += 1
    return command['status'].lower() in ['complete', 'completed']


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
    cmd = api_command(base_url, headers, {'name': command_name, id_field: media_id}, log)
    return wait_for_command(base_url, headers, cmd['id'], log)


def rename(base_url, headers, file_id, command_name_files, command_name_all, id_field, media_id, log):
    """Trigger a rename command."""
    if file_id:
        payload = {'name': command_name_files, 'files': [file_id], id_field: media_id}
    else:
        payload = {'name': command_name_all, id_field + 's': [media_id]}
    cmd = api_command(base_url, headers, payload, log)
    wait_for_command(base_url, headers, cmd['id'], log)
