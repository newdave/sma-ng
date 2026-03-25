#!/opt/sma/venv/bin/python3
"""
SMA-NG SABnzbd Post-Processing Script

Submits conversion job to daemon via webhook.
The daemon handles config selection via path matching and triggers media manager rescans.
"""
import os
import sys
from resources.log import getLogger
from resources.readsettings import ReadSettings
from resources.webhook_client import submit_job

log = getLogger("SABPostProcess")
log.info("SABnzbd post-processing started.")

try:
    settings = ReadSettings()

    if len(sys.argv) < 8:
        log.error("Not enough arguments from SABnzbd.")
        sys.exit(1)

    path = str(sys.argv[1])
    nzb = str(sys.argv[2])
    status = int(sys.argv[7])
    category = str(sys.argv[5]).lower().strip()

    log.info("Path: %s" % path)
    log.info("Category: %s" % category)
    log.info("Status: %d" % status)

    if status != 0:
        log.error("Download failed with status %d, skipping." % status)
        sys.exit(1)

    # Check for bypass category
    bypass = settings.SAB.get('bypass', '').lower()
    if bypass and category.startswith(bypass):
        log.info("Bypass category matched, skipping conversion.")
        sys.exit(0)

    # Submit all valid files in directory to daemon
    if os.path.isdir(path):
        for r, _, files in os.walk(path):
            for f in files:
                filepath = os.path.join(r, f)
                submit_job(filepath, logger=log)
    elif os.path.isfile(path):
        submit_job(path, logger=log)
    else:
        log.error("Path does not exist: %s" % path)
        sys.exit(1)

except:
    log.exception("Error in SABnzbd post-processing.")
    sys.exit(1)
