#!/opt/sma/venv/bin/python3
"""
SMA-NG Sonarr Post-Processing Script

Submits conversion job to daemon via webhook, waits for completion,
then performs Sonarr-specific API operations (rescan, rename, scene info restore).
"""
import os
import sys
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_and_wait
from resources.mediamanager import build_api, rescan, rename, api_get, api_put

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

    extra_args = ['-tvdb', str(tvdb_id), '-s', str(season), '-e', str(episode)]
    if imdb_id:
        extra_args.extend(['-imdb', str(imdb_id)])

    job = submit_and_wait(inputfile, args=extra_args, logger=log)

    if not job or job.get('status') != 'completed':
        log.error("Conversion job failed or timed out.")
        sys.exit(1)

    log.info("Conversion completed successfully.")

    if not settings.Sonarr.get('rescan', True):
        log.info("Rescan disabled, exiting.")
        sys.exit(0)

    try:
        base_url, headers = build_api(settings.Sonarr, "SMA-NG - postSonarr")

        if not settings.Sonarr['apikey']:
            log.error("Sonarr API key is blank, cannot update Sonarr.")
            sys.exit(1)

        if not rescan(base_url, headers, 'RescanSeries', 'seriesId', seriesid, log):
            log.error("Rescan command timed out.")
            sys.exit(1)
        log.info("Rescan completed.")

        # Verify file exists
        epinfo = api_get(base_url, headers, 'episode/' + str(episodeid), log)
        if not epinfo:
            log.error("No valid episode information found, aborting.")
            sys.exit(1)

        if not epinfo.get('hasFile'):
            log.warning("Episode has no file after rescan, triggering second rescan.")
            if rescan(base_url, headers, 'RescanSeries', 'seriesId', seriesid, log):
                epinfo = api_get(base_url, headers, 'episode/' + str(episodeid), log)
                if not epinfo or not epinfo.get('hasFile'):
                    log.warning("Still no file after second rescan.")
                    sys.exit(1)

        # Set monitored
        try:
            epinfo['monitored'] = True
            epinfo = api_put(base_url, headers, 'episode/' + str(episodeid), epinfo, log)
            log.info("Sonarr monitoring updated for %s." % epinfo.get('title', ''))
        except:
            log.exception("Failed to restore monitored status.")

        # Restore scene info
        if scenename or releasegroup:
            try:
                file_id = epinfo['episodeFileId']
                mf = api_get(base_url, headers, 'episodefile/' + str(file_id), log)
                mf['sceneName'] = scenename
                mf['releaseGroup'] = releasegroup
                api_put(base_url, headers, 'episodefile/' + str(file_id), mf, log)
                log.debug("Restored scene info: %s / %s" % (scenename, releasegroup))
            except:
                log.exception("Unable to restore scene information.")

        # Trigger rename
        if settings.Sonarr.get('rename'):
            try:
                rename(base_url, headers, epinfo.get('episodeFileId'), 'RenameFiles', 'RenameSeries', 'seriesId', seriesid, log)
            except:
                log.exception("Failed to trigger rename.")

    except:
        log.exception("Sonarr API operations failed.")

except:
    log.exception("Error in Sonarr post-processing.")
    sys.exit(1)
