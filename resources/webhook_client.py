"""
SMA-NG Webhook Client

Shared module for submitting conversion jobs to the SMA-NG daemon
and polling for completion. Used by all integration scripts.
"""

import json
import logging
import os
import time

try:
    import requests
except ImportError:
    requests = None

# Daemon connection defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8585
ENV_DAEMON_HOST = "SMA_DAEMON_HOST"
ENV_DAEMON_PORT = "SMA_DAEMON_PORT"
ENV_DAEMON_API_KEY = "SMA_DAEMON_API_KEY"


def get_daemon_url():
    """Get daemon base URL from environment or defaults."""
    host = os.environ.get(ENV_DAEMON_HOST, DEFAULT_HOST)
    port = os.environ.get(ENV_DAEMON_PORT, DEFAULT_PORT)
    return "http://%s:%s" % (host, port)


def get_api_key():
    """Get API key from environment."""
    return os.environ.get(ENV_DAEMON_API_KEY, "")


def _headers():
    """Build request headers with optional API key."""
    headers = {"Content-Type": "application/json", "User-Agent": "SMA-NG webhook-client"}
    api_key = get_api_key()
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def submit_job(path, config=None, args=None, logger=None):
    """
    Submit a conversion job to the SMA-NG daemon.

    Args:
        path: Absolute path to file or directory to convert
        config: Optional config file override (absolute path)
        args: Optional list of extra manual.py arguments
        logger: Optional logger instance

    Returns:
        dict with job info (job_id, status, config, etc.) on success
        None on failure
    """
    log = logger or logging.getLogger(__name__)

    if not requests:
        log.error("Python 'requests' module required for webhook client. Install with: pip install requests")
        return None

    url = get_daemon_url() + "/webhook"
    payload = {"path": path}
    if config:
        payload["config"] = config
    if args:
        payload["args"] = args

    try:
        log.info("Submitting job to daemon: %s" % path)
        log.debug("Webhook URL: %s" % url)
        log.debug("Payload: %s" % json.dumps(payload))
        r = requests.post(url, json=payload, headers=_headers(), timeout=30)
        result = r.json()
        if r.status_code in (200, 201, 202):
            log.info("Job %d queued: %s (config: %s)" % (result.get("job_id", 0), path, os.path.basename(result.get("config", ""))))
            return result
        else:
            log.error("Daemon returned %d: %s" % (r.status_code, result.get("error", "Unknown error")))
            return None
    except requests.ConnectionError:
        log.error("Cannot connect to SMA-NG daemon at %s. Is the daemon running?" % get_daemon_url())
        return None
    except Exception:
        log.exception("Failed to submit job to daemon")
        return None


def get_job_status(job_id, logger=None):
    """Get current status of a job."""
    log = logger or logging.getLogger(__name__)
    url = "%s/jobs/%d" % (get_daemon_url(), job_id)
    try:
        r = requests.get(url, headers=_headers(), timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        log.exception("Failed to get job status")
        return None


def wait_for_completion(job_id, logger=None, poll_interval=5, timeout=0):
    """
    Poll daemon until a job completes or fails.

    Args:
        job_id: Job ID to monitor
        logger: Optional logger
        poll_interval: Seconds between status checks (default 5)
        timeout: Max seconds to wait (0 = unlimited)

    Returns:
        Final job dict on completion/failure, None on timeout or error
    """
    log = logger or logging.getLogger(__name__)
    start = time.time()

    log.info("Waiting for job %d to complete (poll every %ds)..." % (job_id, poll_interval))
    while True:
        job = get_job_status(job_id, logger=log)
        if job is None:
            log.warning("Lost contact with daemon while polling job %d" % job_id)
            return None

        status = job.get("status", "")
        if status == "completed":
            elapsed = int(time.time() - start)
            log.info("Job %d completed in %ds" % (job_id, elapsed))
            return job
        elif status == "failed":
            log.error("Job %d failed: %s" % (job_id, job.get("error", "Unknown")))
            return job
        elif status in ("pending", "running"):
            if timeout > 0 and (time.time() - start) > timeout:
                log.warning("Timed out waiting for job %d after %ds" % (job_id, timeout))
                return None
            time.sleep(poll_interval)
        else:
            log.warning("Unknown job status '%s' for job %d" % (status, job_id))
            return None


def submit_and_wait(path, config=None, args=None, logger=None, poll_interval=5, timeout=0):
    """
    Submit a job and wait for it to complete.

    Returns:
        Final job dict on completion, None on failure
    """
    result = submit_job(path, config=config, args=args, logger=logger)
    if not result:
        return None

    job_id = result.get("job_id")
    if not job_id:
        return None

    return wait_for_completion(job_id, logger=logger, poll_interval=poll_interval, timeout=timeout)


def check_daemon_health(logger=None):
    """Check if the daemon is running and healthy."""
    log = logger or logging.getLogger(__name__)
    url = get_daemon_url() + "/health"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        return data.get("status") == "ok"
    except Exception:
        log.debug("Daemon health check failed at %s" % url)
        return False


def check_bypass(bypass_list, value):
    """Check if value matches any bypass prefix. Returns True if bypassed."""
    for b in bypass_list:
        if b and value.startswith(b):
            return True
    return False


def submit_path(path, logger=None):
    """Submit all files at path (file or directory) to daemon. Returns count of submitted jobs."""
    count = 0
    if os.path.isfile(path):
        if submit_job(path, logger=logger):
            count += 1
    elif os.path.isdir(path):
        for root, _, files in os.walk(path):
            for f in files:
                if submit_job(os.path.join(root, f), logger=logger):
                    count += 1
    else:
        if logger:
            logger.error("Path does not exist: %s" % path)
    return count
