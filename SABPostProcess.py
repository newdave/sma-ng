#!/opt/sma/venv/bin/python3
"""
SMA-NG SABnzbd Post-Processing Script

Submits conversion job to daemon via webhook.
The daemon handles config selection via path matching and triggers media manager rescans.
"""

import sys

import resources.webhook_client as webhook
from resources.log import getLogger
from resources.readsettings import ReadSettings

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

    if webhook.check_bypass(settings.SAB.get("bypass", []), category):
        log.info("Bypass category matched, skipping conversion.")
        sys.exit(0)

    webhook.submit_path(path, logger=log)

except:
    log.exception("Error in SABnzbd post-processing.")
    sys.exit(1)
