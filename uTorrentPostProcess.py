#!/opt/sma/venv/bin/python3
"""
SMA-NG uTorrent Post-Processing Script

Submits conversion job to daemon via webhook on torrent completion.
Optionally performs pre/post actions via uTorrent WebUI.

Args: %L %T %D %K %F %I %N
      Label, Tracker, Directory, single|multi, Filename, InfoHash, Name
"""
import os
import sys
from resources.log import getLogger
from resources.readsettings import ReadSettings
import resources.webhook_client as webhook

log = getLogger("uTorrentPostProcess")
log.info("uTorrent post-processing started.")

try:
    settings = ReadSettings()

    if len(sys.argv) < 7:
        log.error("Not enough arguments. Expected: label tracker directory kind filename info_hash [name]")
        sys.exit(1)

    label = sys.argv[1].lower().strip()
    path = sys.argv[3]
    kind = sys.argv[4].lower()
    filename = sys.argv[5]
    info_hash = sys.argv[6]
    torrent_name = sys.argv[7] if len(sys.argv) > 7 else info_hash

    log.info("Label: %s" % label)
    log.info("Path: %s" % path)
    log.info("Kind: %s" % kind)
    log.info("Torrent: %s (%s)" % (torrent_name, info_hash))

    if webhook.check_bypass(settings.uTorrent.get('bypass', []), label):
        log.info("Bypass label matched, skipping.")
        sys.exit(0)

    # WebUI pre-action
    webui = settings.uTorrent.get('webui', False)
    actionbefore = settings.uTorrent.get('actionbefore', '')
    actionafter = settings.uTorrent.get('actionafter', '')
    if isinstance(actionbefore, str):
        actionbefore = actionbefore.lower()
    if isinstance(actionafter, str):
        actionafter = actionafter.lower()

    if webui and actionbefore:
        try:
            import requests as _req
            _host = settings.uTorrent.get('host', 'localhost')
            _port = settings.uTorrent.get('port', 8080)
            _ssl = settings.uTorrent.get('ssl', False)
            _proto = "https://" if _ssl else "http://"
            _user = settings.uTorrent.get('user', '')
            _passwd = settings.uTorrent.get('pass', '')
            _base = "%s%s:%s/gui/" % (_proto, _host, _port)
            _r = _req.get(_base + "token.html", auth=(_user, _passwd))
            _token = _r.text.split("'")[1] if "'" in _r.text else ''
            _req.get("%s?action=%s&hash=%s&token=%s" % (_base, actionbefore, info_hash, _token), auth=(_user, _passwd))
            log.info("uTorrent action '%s' sent for %s." % (actionbefore, info_hash))
        except:
            log.exception("Failed to send uTorrent pre-action.")

    # Submit files to daemon
    if kind == 'single' and filename:
        filepath = os.path.join(path, filename)
        webhook.submit_path(filepath, logger=log)
    else:
        webhook.submit_path(path, logger=log)

    # WebUI post-action
    if webui and actionafter:
        try:
            import requests as _req
            _host = settings.uTorrent.get('host', 'localhost')
            _port = settings.uTorrent.get('port', 8080)
            _ssl = settings.uTorrent.get('ssl', False)
            _proto = "https://" if _ssl else "http://"
            _user = settings.uTorrent.get('user', '')
            _passwd = settings.uTorrent.get('pass', '')
            _base = "%s%s:%s/gui/" % (_proto, _host, _port)
            _r = _req.get(_base + "token.html", auth=(_user, _passwd))
            _token = _r.text.split("'")[1] if "'" in _r.text else ''
            _req.get("%s?action=%s&hash=%s&token=%s" % (_base, actionafter, info_hash, _token), auth=(_user, _passwd))
            log.info("uTorrent action '%s' sent for %s." % (actionafter, info_hash))
        except:
            log.exception("Failed to send uTorrent post-action.")

except:
    log.exception("Error in uTorrent post-processing.")
    sys.exit(1)
