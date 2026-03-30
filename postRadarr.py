#!/opt/sma/venv/bin/python3
"""
SMA-NG Radarr Post-Processing Script

Submits conversion job to daemon via webhook, waits for completion,
then performs Radarr-specific API operations (rescan, rename, scene info restore).
"""

import os
import sys

from resources.log import getLogger
from resources.mediamanager import api_get, api_put, build_api, rename, rescan
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_and_wait

log = getLogger("RadarrPostProcess")
log.info("Radarr post-processing started.")

if os.environ.get("radarr_eventtype") == "Test":
    log.info("Successful postRadarr.py SMA-NG test, exiting.")
    sys.exit(0)

if os.environ.get("radarr_eventtype") != "Download":
    log.error("Invalid event type %s, script only works for On Download/On Import and On Upgrade." % os.environ.get("radarr_eventtype"))
    sys.exit(1)

try:
    settings = ReadSettings()

    inputfile = os.environ.get("radarr_moviefile_path")
    original = os.environ.get("radarr_moviefile_scenename")
    imdbid = os.environ.get("radarr_movie_imdbid")
    tmdbid = os.environ.get("radarr_movie_tmdbid")
    movieid = int(os.environ.get("radarr_movie_id"))
    moviefileid = int(os.environ.get("radarr_moviefile_id"))
    scenename = os.environ.get("radarr_moviefile_scenename")
    releasegroup = os.environ.get("radarr_moviefile_releasegroup")

    log.info("Input file: %s" % inputfile)
    log.info("TMDB ID: %s, IMDB ID: %s" % (tmdbid, imdbid))

    extra_args = []
    if tmdbid:
        extra_args.extend(["-tmdb", str(tmdbid)])
    if imdbid:
        extra_args.extend(["-imdb", str(imdbid)])

    job = submit_and_wait(inputfile, args=extra_args if extra_args else None, logger=log)

    if not job or job.get("status") != "completed":
        log.error("Conversion job failed or timed out.")
        sys.exit(1)

    log.info("Conversion completed successfully.")

    if not settings.Radarr.get("rescan", True):
        log.info("Rescan disabled, exiting.")
        sys.exit(0)

    try:
        base_url, headers = build_api(settings.Radarr, "SMA-NG - postRadarr")

        if not settings.Radarr["apikey"]:
            log.error("Radarr API key is blank, cannot update Radarr.")
            sys.exit(1)

        if not rescan(base_url, headers, "RescanMovie", "movieId", movieid, log):
            log.error("Rescan command timed out.")
            sys.exit(1)
        log.info("Rescan completed.")

        # Verify file exists
        movieinfo = api_get(base_url, headers, "movie/" + str(movieid), log)
        if not movieinfo.get("hasFile"):
            log.warning("Movie has no file after rescan, triggering second rescan.")
            if rescan(base_url, headers, "RescanMovie", "movieId", movieid, log):
                movieinfo = api_get(base_url, headers, "movie/" + str(movieid), log)
                if not movieinfo.get("hasFile"):
                    log.warning("Still no file after second rescan.")
                    sys.exit(1)

        # Set monitored
        try:
            movieinfo["monitored"] = True
            movieinfo = api_put(base_url, headers, "movie/" + str(movieid), movieinfo, log)
            log.info("Radarr monitoring updated for %s." % movieinfo.get("title", ""))
        except:
            log.exception("Failed to restore monitored status.")

        # Restore scene info
        if scenename or releasegroup:
            try:
                file_id = movieinfo["movieFile"]["id"]
                mf = api_get(base_url, headers, "moviefile/" + str(file_id), log)
                mf["sceneName"] = scenename
                mf["releaseGroup"] = releasegroup
                api_put(base_url, headers, "moviefile/" + str(file_id), mf, log)
                log.debug("Restored scene info: %s / %s" % (scenename, releasegroup))
            except:
                log.exception("Unable to restore scene information.")

        # Trigger rename
        if settings.Radarr.get("rename"):
            try:
                rename(base_url, headers, movieinfo["movieFile"]["id"], "RenameFiles", "RenameMovies", "movieId", movieid, log)
            except:
                log.exception("Failed to trigger rename.")

    except:
        log.exception("Radarr API operations failed.")

except:
    log.exception("Error in Radarr post-processing.")
    sys.exit(1)
