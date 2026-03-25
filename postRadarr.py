#!/opt/sma/venv/bin/python3
"""
SMA-NG Radarr Post-Processing Script

Submits conversion job to daemon via webhook, waits for completion,
then performs Radarr-specific API operations (rescan, rename, scene info restore).
"""
import os
import sys
import requests
import time
import shutil
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_and_wait


# Radarr API functions
def rescanRequest(baseURL, headers, movieid, log):
    url = baseURL + "/api/v3/command"
    payload = {'name': 'RescanMovie', 'movieId': movieid}
    log.debug("Radarr RescanMovie: %s" % str(payload))
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.info("Radarr RescanMovie response: ID %d %s." % (rstate['id'], rstate['status']))
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


def renameRequest(baseURL, headers, fileid, movieid, log):
    url = baseURL + "/api/v3/command"
    if fileid:
        payload = {'name': 'RenameFiles', 'files': [fileid], 'movieId': movieid}
    else:
        payload = {'name': 'RenameMovies', 'movieIds': [movieid]}
    r = requests.post(url, json=payload, headers=headers)
    rstate = r.json()
    try:
        rstate = rstate[0]
    except:
        pass
    log.info("Radarr Rename response: ID %d %s." % (rstate['id'], rstate['status']))
    return rstate


def getMovie(baseURL, headers, movieid, log):
    url = baseURL + "/api/v3/movie/" + str(movieid)
    r = requests.get(url, headers=headers)
    return r.json()


def updateMovie(baseURL, headers, new, movieid, log):
    url = baseURL + "/api/v3/movie/" + str(movieid)
    r = requests.put(url, json=new, headers=headers)
    return r.json()


def getMovieFile(baseURL, headers, moviefileid, log):
    url = baseURL + "/api/v3/moviefile/" + str(moviefileid)
    r = requests.get(url, headers=headers)
    return r.json()


def updateMovieFile(baseURL, headers, new, moviefileid, log):
    url = baseURL + "/api/v3/moviefile/" + str(moviefileid)
    r = requests.put(url, json=new, headers=headers)
    return r.json()


log = getLogger("RadarrPostProcess")
log.info("Radarr post-processing started.")

if os.environ.get('radarr_eventtype') == "Test":
    log.info("Successful postRadarr.py SMA-NG test, exiting.")
    sys.exit(0)

if os.environ.get('radarr_eventtype') != "Download":
    log.error("Invalid event type %s, script only works for On Download/On Import and On Upgrade." % os.environ.get('radarr_eventtype'))
    sys.exit(1)

try:
    settings = ReadSettings()

    inputfile = os.environ.get('radarr_moviefile_path')
    original = os.environ.get('radarr_moviefile_scenename')
    imdbid = os.environ.get('radarr_movie_imdbid')
    tmdbid = os.environ.get('radarr_movie_tmdbid')
    movieid = int(os.environ.get('radarr_movie_id'))
    moviefileid = int(os.environ.get('radarr_moviefile_id'))
    scenename = os.environ.get('radarr_moviefile_scenename')
    releasegroup = os.environ.get('radarr_moviefile_releasegroup')

    log.info("Input file: %s" % inputfile)
    log.info("TMDB ID: %s, IMDB ID: %s" % (tmdbid, imdbid))

    # Build extra args for the daemon
    extra_args = []
    if tmdbid:
        extra_args.extend(['-tmdb', str(tmdbid)])
    if imdbid:
        extra_args.extend(['-imdb', str(imdbid)])

    # Submit to daemon and wait for completion
    job = submit_and_wait(inputfile, args=extra_args if extra_args else None, logger=log)

    if not job or job.get('status') != 'completed':
        log.error("Conversion job failed or timed out.")
        sys.exit(1)

    log.info("Conversion completed successfully.")

    if not settings.Radarr.get('rescan', True):
        log.info("Rescan disabled, exiting.")
        sys.exit(0)

    # Radarr API operations
    try:
        host = settings.Radarr['host']
        port = settings.Radarr['port']
        webroot = settings.Radarr['webroot']
        apikey = settings.Radarr['apikey']
        ssl = settings.Radarr['ssl']
        protocol = "https://" if ssl else "http://"
        baseURL = protocol + host + ":" + str(port) + webroot

        if not apikey:
            log.error("Radarr API key is blank, cannot update Radarr.")
            sys.exit(1)

        headers = {'X-Api-Key': apikey, 'User-Agent': "SMA-NG - postRadarr"}

        # Trigger rescan
        rescanCommand = rescanRequest(baseURL, headers, movieid, log)
        if not waitForCommand(baseURL, headers, rescanCommand['id'], log):
            log.error("Rescan command timed out.")
            sys.exit(1)

        log.info("Rescan completed.")

        # Verify file exists
        movieinfo = getMovie(baseURL, headers, movieid, log)
        if not movieinfo.get('hasFile'):
            log.warning("Movie has no file after rescan, triggering second rescan.")
            rescanAgain = rescanRequest(baseURL, headers, movieid, log)
            if waitForCommand(baseURL, headers, rescanAgain['id'], log):
                movieinfo = getMovie(baseURL, headers, movieid, log)
                if not movieinfo.get('hasFile'):
                    log.warning("Still no file after second rescan.")
                    sys.exit(1)

        # Set monitored
        try:
            movieinfo['monitored'] = True
            movieinfo = updateMovie(baseURL, headers, movieinfo, movieid, log)
            log.info("Radarr monitoring updated for %s." % movieinfo['title'])
        except:
            log.exception("Failed to restore monitored status.")

        # Restore scene info
        if scenename or releasegroup:
            try:
                mf = getMovieFile(baseURL, headers, movieinfo['movieFile']['id'], log)
                mf['sceneName'] = scenename
                mf['releaseGroup'] = releasegroup
                mf = updateMovieFile(baseURL, headers, mf, movieinfo['movieFile']['id'], log)
                log.debug("Restored scene info: %s / %s" % (scenename, releasegroup))
            except:
                log.exception("Unable to restore scene information.")

        # Trigger rename
        if settings.Radarr.get('rename'):
            try:
                renameCommand = renameRequest(baseURL, headers, movieinfo['movieFile']['id'], movieid, log)
                waitForCommand(baseURL, headers, renameCommand['id'], log)
            except:
                log.exception("Failed to trigger rename.")

    except:
        log.exception("Radarr API operations failed.")

except:
    log.exception("Error in Radarr post-processing.")
    sys.exit(1)
