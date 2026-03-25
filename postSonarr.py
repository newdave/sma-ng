#!/opt/sma/venv/bin/python3
"""
SMA-NG Sonarr Post-Processing Script

Submits conversion job to daemon via webhook, waits for completion,
then performs Sonarr-specific API operations (rescan, rename, scene info restore).
"""
import os
import sys
import requests
import time
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_and_wait


# Sonarr API functions
def rescanRequest(baseURL, headers, seriesid, log):
    url = baseURL + "/api/v3/command"
    payload = {'name': 'RescanSeries', 'seriesId': seriesid}
    log.debug("Sonarr RescanSeries: %s" % str(payload))
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.info("Sonarr RescanSeries response: ID %d %s." % (rstate['id'], rstate['status']))
    return rstate


def waitForCommand(baseURL, headers, commandID, log, retries=6, delay=10):
    url = baseURL + "/api/v3/command/" + str(commandID)
    r = requests.get(url, headers=headers)
    command = r.json()
    attempts = 0
    while command['status'].lower() not in ['complete', 'completed'] and attempts < retries:
        time.sleep(delay)
        r = requests.get(url, headers=headers)
        command = r.json()
        attempts += 1
    return command['status'].lower() in ['complete', 'completed']


def renameRequest(baseURL, headers, fileid, seriesid, log):
    url = baseURL + "/api/v3/command"
    if fileid:
        payload = {'name': 'RenameFiles', 'files': [fileid], 'seriesId': seriesid}
    else:
        payload = {'name': 'RenameSeries', 'seriesIds': [seriesid]}
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.info("Sonarr Rename response: ID %d %s." % (rstate['id'], rstate['status']))
    return rstate


def getEpisode(baseURL, headers, episodeid, log):
    url = baseURL + "/api/v3/episode/" + str(episodeid)
    r = requests.get(url, headers=headers)
    return r.json()


def updateEpisode(baseURL, headers, new, episodeid, log):
    url = baseURL + "/api/v3/episode/" + str(episodeid)
    r = requests.put(url, json=new, headers=headers)
    return r.json()


def getEpisodeFile(baseURL, headers, episodefileid, log):
    url = baseURL + "/api/v3/episodefile/" + str(episodefileid)
    r = requests.get(url, headers=headers)
    return r.json()


def updateEpisodeFile(baseURL, headers, new, episodefileid, log):
    url = baseURL + "/api/v3/episodefile/" + str(episodefileid)
    r = requests.put(url, json=new, headers=headers)
    return r.json()


log = getLogger("SonarrPostProcess")
log.info("Sonarr post-processing started.")

if os.environ.get('sonarr_eventtype') == "Test":
    log.info("Successful postSonarr.py SMA-NG test, exiting.")
    sys.exit(0)

if os.environ.get('sonarr_eventtype') != "Download":
    log.error("Invalid event type %s, script only works for On Download/On Import and On Upgrade." % os.environ.get('sonarr_eventtype'))
    sys.exit(1)

try:
    settings = ReadSettings()

    inputfile = os.environ.get('sonarr_episodefile_path')
    original = os.environ.get('sonarr_episodefile_scenename')
    tvdb_id = int(os.environ.get('sonarr_series_tvdbid'))
    imdb_id = os.environ.get('sonarr_series_imdbid')
    season = int(os.environ.get('sonarr_episodefile_seasonnumber'))
    seriesid = int(os.environ.get('sonarr_series_id'))
    scenename = os.environ.get('sonarr_episodefile_scenename')
    releasegroup = os.environ.get('sonarr_episodefile_releasegroup')
    episodefile_id = os.environ.get('sonarr_episodefile_id')
    episode = int(os.environ.get('sonarr_episodefile_episodenumbers').split(",")[0])
    episodeid = int(os.environ.get('sonarr_episodefile_episodeids').split(",")[0])

    log.info("Input file: %s" % inputfile)
    log.info("TVDB ID: %s, S%02dE%02d" % (tvdb_id, season, episode))

    # Build extra args for the daemon
    extra_args = ['-tvdb', str(tvdb_id), '-s', str(season), '-e', str(episode)]
    if imdb_id:
        extra_args.extend(['-imdb', str(imdb_id)])

    # Submit to daemon and wait for completion
    job = submit_and_wait(inputfile, args=extra_args, logger=log)

    if not job or job.get('status') != 'completed':
        log.error("Conversion job failed or timed out.")
        sys.exit(1)

    log.info("Conversion completed successfully.")

    if not settings.Sonarr.get('rescan', True):
        log.info("Rescan disabled, exiting.")
        sys.exit(0)

    # Sonarr API operations
    try:
        host = settings.Sonarr['host']
        port = settings.Sonarr['port']
        webroot = settings.Sonarr['webroot']
        apikey = settings.Sonarr['apikey']
        ssl = settings.Sonarr['ssl']
        protocol = "https://" if ssl else "http://"
        baseURL = protocol + host + ":" + str(port) + webroot

        if not apikey:
            log.error("Sonarr API key is blank, cannot update Sonarr.")
            sys.exit(1)

        headers = {'X-Api-Key': apikey, 'User-Agent': "SMA-NG - postSonarr"}

        # Trigger rescan
        rescanCommand = rescanRequest(baseURL, headers, seriesid, log)
        if not waitForCommand(baseURL, headers, rescanCommand['id'], log):
            log.error("Rescan command timed out.")
            sys.exit(1)

        log.info("Rescan completed.")

        # Verify file exists
        sonarrepinfo = getEpisode(baseURL, headers, episodeid, log)
        if not sonarrepinfo:
            log.error("No valid episode information found, aborting.")
            sys.exit(1)

        if not sonarrepinfo.get('hasFile'):
            log.warning("Episode has no file after rescan, triggering second rescan.")
            rescanAgain = rescanRequest(baseURL, headers, seriesid, log)
            if waitForCommand(baseURL, headers, rescanAgain['id'], log):
                sonarrepinfo = getEpisode(baseURL, headers, episodeid, log)
                if not sonarrepinfo or not sonarrepinfo.get('hasFile'):
                    log.warning("Still no file after second rescan.")
                    sys.exit(1)

        # Set monitored
        try:
            sonarrepinfo['monitored'] = True
            sonarrepinfo = updateEpisode(baseURL, headers, sonarrepinfo, episodeid, log)
            log.info("Sonarr monitoring updated for %s." % sonarrepinfo.get('title', ''))
        except:
            log.exception("Failed to restore monitored status.")

        # Restore scene info
        if scenename or releasegroup:
            try:
                mf = getEpisodeFile(baseURL, headers, sonarrepinfo['episodeFileId'], log)
                mf['sceneName'] = scenename
                mf['releaseGroup'] = releasegroup
                mf = updateEpisodeFile(baseURL, headers, mf, sonarrepinfo['episodeFileId'], log)
                log.debug("Restored scene info: %s / %s" % (scenename, releasegroup))
            except:
                log.exception("Unable to restore scene information.")

        # Trigger rename
        if settings.Sonarr.get('rename'):
            try:
                renameCmd = renameRequest(baseURL, headers, sonarrepinfo.get('episodeFileId'), seriesid, log)
                waitForCommand(baseURL, headers, renameCmd['id'], log)
            except:
                log.exception("Failed to trigger rename.")

    except:
        log.exception("Sonarr API operations failed.")

except:
    log.exception("Error in Sonarr post-processing.")
    sys.exit(1)
