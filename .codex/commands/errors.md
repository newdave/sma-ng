---
name: errors
description: Fetch recent failed jobs and their error logs from the deployed SMA-NG daemon
---

# Fetch Recent Errors from Live Daemon

This command mirrors the authoritative Claude workflow in `.claude/commands/errors.md`.
If the two files diverge, follow the Claude version and sync this Codex copy.

## Usage

`/errors [count|job:<id>]`

- No argument: fetch the 10 most recent failed jobs
- Numeric argument: fetch that many recent failed jobs
- `job:<id>`: fetch only that job and its full log

## Steps

1. Read `setup/.local.ini` and parse:
   - Host: take the first entry in `DEPLOY_HOSTS` under `[deploy]`, strip any `user@` prefix to get the bare IP/hostname
   - Port: `8585`
   - API key: `api_key` under `[daemon]`
   Construct the base URL as `http://<host>:8585`.

2. Resolve `$ARGUMENTS`:
   - Empty -> `limit=10`
   - Integer `N` -> `limit=N`
   - `job:<id>` -> fetch only that job

3. If fetching recent failures, call:
   - `<base_url>/jobs?status=failed&limit=<N>`
   with header `X-API-Key: <api_key>`.
   - If unreachable, say so and stop.

4. For each failed job, fetch its error log:
   - `<base_url>/logs/<log_name>?job_id=<id>&level=ERROR&lines=100`
   with the same `X-API-Key` header.

5. If fetching a single job, call:
   - `<base_url>/jobs/<id>`
   - `<base_url>/logs/<log_name>?level=INFO&lines=200`

6. Present each job as:

   **Job <id>** — `<path>`
   Error: `<error field>`
   Args: `<args field>`
   ```
   <error log lines>
   ```

7. Summarise the distinct failure patterns and offer to investigate or fix.
