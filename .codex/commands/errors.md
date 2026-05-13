---
name: errors
description: Fetch recent failed jobs and error logs from the deployed SMA-NG daemon
---

# Fetch Recent Errors

Mirror of `.claude/commands/errors.md`.
If these diverge, follow the Claude command and sync this file.

## Steps

1. Read `setup/local.yml`:
   host is the first `deploy.hosts` label resolved through `hosts.<label>.address`;
   API key is `daemon.api_key`;
   port is `8585`.
2. If `$ARGUMENTS` is a job ID, fetch `/jobs/<id>` and its log with `level=INFO&lines=200`.
3. Otherwise fetch `/jobs?status=failed&limit=10`, then each job log with
   `/logs/<log_name>?job_id=<id>&level=ERROR&lines=100`.
4. Use `X-API-Key: <api_key>` for daemon requests.
5. Present job ID, path, error field, args field, relevant log lines, and distinct failure patterns.
