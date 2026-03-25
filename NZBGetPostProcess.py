#!/opt/sma/venv/bin/python3
#
##############################################################################
### NZBGET POST-PROCESSING SCRIPT                                          ###
### SMA-NG webhook integration for NZBGet                                  ###
##############################################################################
#
# Submits conversion jobs to the SMA-NG daemon via webhook.
#
# NOTE: This script requires NZBGet v11.0+.
#
# NOTE: Configure the daemon connection via environment variables:
#   SMA_DAEMON_HOST, SMA_DAEMON_PORT, SMA_DAEMON_API_KEY
#
##############################################################################
### OPTIONS                                                                ###
#
# SMA-NG installation path.
#MP4_FOLDER=~/sma-ng/
#
# Convert file before passing to destination (true, false).
#SHOULDCONVERT=true
#
# Sonarr category name.
#SONARR_CAT=sonarr
#
# Radarr category name.
#RADARR_CAT=radarr
#
# Sickbeard category name.
#SICKBEARD_CAT=sickbeard
#
# Sickrage category name.
#SICKRAGE_CAT=sickrage
#
# Bypass category name.
#BYPASS_CAT=bypass
#
### NZBGET POST-PROCESSING SCRIPT                                          ###
##############################################################################
import os
import sys
import logging

log = logging.getLogger("NZBGetPostProcess")
log.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
log.addHandler(handler)

# NZBGet exit codes
POSTPROCESS_SUCCESS = 93
POSTPROCESS_ERROR = 94
POSTPROCESS_NONE = 95

# Validate NZBGet environment
if not os.environ.get('NZBOP_VERSION'):
    log.error("This script requires NZBGet v11.0+.")
    sys.exit(POSTPROCESS_ERROR)

mp4_folder = os.environ.get('NZBPO_MP4_FOLDER', '').strip()
if mp4_folder:
    sys.path.insert(0, mp4_folder)
    os.chdir(mp4_folder)

try:
    import resources.webhook_client as webhook
except ImportError:
    log.error("Cannot import webhook_client. Check MP4_FOLDER setting: %s" % mp4_folder)
    sys.exit(POSTPROCESS_ERROR)

# Read NZBGet settings
shouldConvert = os.environ.get('NZBPO_SHOULDCONVERT', 'true').lower() == 'true'
sonarrcat = os.environ.get('NZBPO_SONARR_CAT', 'sonarr').lower()
radarrcat = os.environ.get('NZBPO_RADARR_CAT', 'radarr').lower()
sickbeardcat = os.environ.get('NZBPO_SICKBEARD_CAT', 'sickbeard').lower()
sickragecat = os.environ.get('NZBPO_SICKRAGE_CAT', 'sickrage').lower()
bypasscat = os.environ.get('NZBPO_BYPASS_CAT', 'bypass').lower()

# Validate download status
if os.environ.get('NZBPP_TOTALSTATUS') != 'SUCCESS':
    log.warning("Download not successful, skipping.")
    sys.exit(POSTPROCESS_NONE)

directory = os.environ.get('NZBPP_DIRECTORY', '')
category = os.environ.get('NZBPP_CATEGORY', '').lower()

log.info("Directory: %s" % directory)
log.info("Category: %s" % category)

if not directory or not os.path.isdir(directory):
    log.error("Invalid directory: %s" % directory)
    sys.exit(POSTPROCESS_ERROR)

# Check bypass
if category.startswith(bypasscat):
    log.info("Bypass category matched, skipping.")
    sys.exit(POSTPROCESS_NONE)

if not shouldConvert:
    log.info("Conversion disabled, skipping.")
    sys.exit(POSTPROCESS_NONE)

# Submit all files in directory to daemon
submitted = 0
for root, _, files in os.walk(directory):
    for f in files:
        filepath = os.path.join(root, f)
        result = webhook.submit_job(filepath, logger=log)
        if result:
            submitted += 1

if submitted > 0:
    log.info("Submitted %d job(s) to daemon." % submitted)
    sys.exit(POSTPROCESS_SUCCESS)
else:
    log.warning("No jobs submitted.")
    sys.exit(POSTPROCESS_NONE)
