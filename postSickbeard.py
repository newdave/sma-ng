#!/opt/sma/venv/bin/python3
"""
SMA-NG Sickbeard/SickRage Post-Processing Script

Submits conversion job to daemon via webhook, waits for completion,
then triggers a show refresh on Sickbeard/SickRage.
"""
import os
import sys
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_and_wait

log = getLogger("SickbeardPostProcess")
log.info("Sickbeard post-processing started.")

try:
    settings = ReadSettings()

    if len(sys.argv) < 6:
        log.error("Not enough arguments. Usage: postSickbeard.py <inputfile> <original> <tvdb_id> <season> <episode>")
        sys.exit(1)

    inputfile = sys.argv[1]
    original = sys.argv[2]
    tvdb_id = sys.argv[3]
    season = sys.argv[4]
    episode = sys.argv[5]

    log.info("Input file: %s" % inputfile)
    log.info("TVDB ID: %s, S%sE%s" % (tvdb_id, season, episode))

    extra_args = ['-tvdb', str(tvdb_id), '-s', str(season), '-e', str(episode)]

    # Submit to daemon and wait
    job = submit_and_wait(inputfile, args=extra_args, logger=log)

    if not job or job.get('status') != 'completed':
        log.error("Conversion failed or timed out.")
        sys.exit(1)

    log.info("Conversion completed.")

    # Trigger show refresh on Sickbeard/SickRage
    try:
        import requests
        for section_name, section in [('Sickbeard', settings.Sickbeard), ('Sickrage', settings.Sickrage)]:
            host = section.get('host', '')
            port = section.get('port', '')
            apikey = section.get('apikey', '')
            if not host or not apikey:
                continue
            ssl = section.get('ssl', False)
            protocol = "https://" if ssl else "http://"
            webroot = section.get('webroot', '')
            url = "%s%s:%s%s/api/%s/?cmd=show.refresh&tvdbid=%s" % (protocol, host, port, webroot, apikey, tvdb_id)
            log.info("Requesting %s refresh: %s" % (section_name, url))
            r = requests.get(url, timeout=30)
            log.info("%s response: %s" % (section_name, r.text.strip()))
            break
    except:
        log.exception("Failed to trigger show refresh.")

except:
    log.exception("Error in Sickbeard post-processing.")
    sys.exit(1)
