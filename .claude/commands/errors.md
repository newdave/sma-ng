# Fetch Recent Errors from Live Daemon

Fetch recent failed jobs and their error logs from the deployed SMA-NG daemon.

## Steps

1. Read `setup/local.yml` and parse:
   - Host: take the first label from `deploy.hosts`, then look up `hosts.<label>.address` for the bare IP/hostname
   - Port: 8585 (default; not configurable in `local.yml`)
   - API key: `daemon.api_key`
   Construct base URL as `http://<host>:8585`.

2. Fetch `<base_url>/jobs?status=failed&limit=10` with header `X-API-Key: <api_key>`.
   - If unreachable, say so and stop.

3. For each failed job (most recent first), fetch its error log:
   `<base_url>/logs/<log_name>?job_id=<job-id>&level=ERROR&lines=100`
   with the same `X-API-Key` header.

4. Present each job as:

   **Job `<id>`** — `<path>`

   Error: `<error field>`

   Args: `<args field>`

   ```text
   <error log lines>
   ```

5. Summarise the distinct failure patterns and offer to investigate or fix.

If $ARGUMENTS is a job ID, fetch only that job (`<base_url>/jobs/<id>`) and its full log.
Use `level=INFO`; no `job_id` filter is needed if `log_name` is known.
