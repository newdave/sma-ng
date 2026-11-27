# Cluster Metrics

SMA-NG reports job throughput, duration, compression and failure data through three surfaces: a dashboard, a JSON API
and a Prometheus endpoint.

| Surface | Path | Source | Needs PostgreSQL |
| ------- | ---- | ------ | ---------------- |
| Dashboard KPI strip | `/dashboard` | `GET /api/metrics?window=24h` | Yes |
| Metrics page | `/dashboard/metrics` | `GET /api/metrics` | Yes |
| JSON API | `/api/metrics` | The `jobs` table | Yes |
| Prometheus | `/metrics` | In-process counters, histograms and gauges | No |

The JSON metrics need PostgreSQL because they aggregate the shared `jobs` table across every node. With the default
SQLite database (no `daemon.db-url`), `/api/metrics` answers `503` with `"available": false`, and the dashboard shows
an "unavailable" notice. The Prometheus endpoint works in both modes and describes only the node that serves it.

## Accessing metrics

- **Dashboard widget.** `/dashboard` shows four cards: throughput per hour, average duration, failure rate and average
  compression, taken from the last 24 hours.
- **Metrics page.** `/dashboard/metrics` shows interactive charts with a time-window selector (24h, 7d, 30d, all
  time). The page itself is public like the other dashboard pages, but it loads its data from the authenticated
  `/api/metrics`.
- **Prometheus.** `GET /metrics` returns the Prometheus text exposition format. When an API key or basic auth is
  configured, the endpoint requires credentials, because its series carry the node id. Without any auth configured it
  is open. A daemon with only an API key accepts a Bearer token or `X-API-Key`; use `basic_auth` in the scrape
  configuration only when both `daemon.username` and `daemon.password` are set, because Basic authentication is
  enabled only by that pair. See [Daemon Mode](daemon.md) for the authentication rules.

```yaml
scrape_configs:
  - job_name: sma-ng
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: YOUR_API_KEY
    static_configs:
      - targets: ["sma-host:8585"]
```

## JSON API: `GET /api/metrics`

The endpoint returns aggregated job metrics. The optional `window` query parameter selects the period for the
completed, failed and cancelled counts: `24h` (default), `7d`, `30d` or `all`. An unrecognized value falls back to
`24h`.

```bash
curl -s -H "X-API-Key: $SMA_API_KEY" "http://sma-host:8585/api/metrics?window=7d"
```

```json
{
  "available": true,
  "window": "24h",
  "kpis": {
    "completed": 42,
    "failed": 2,
    "cancelled": 0,
    "pending": 5,
    "running": 1,
    "total": 50,
    "failure_rate_pct": 4.55,
    "avg_duration_seconds": 138.2,
    "p95_duration_seconds": 412.0,
    "avg_compression_pct": 34.1,
    "bytes_saved_total": 123456789,
    "bytes_grown_total": 45000,
    "minutes_transcoded_total": 1234.5,
    "throughput_per_hour": 1.75
  },
  "timeseries": [
    { "bucket": "2026-04-25T00:00:00+00:00", "completed": 12, "failed": 0 }
  ],
  "nodes": [
    {
      "node_id": "sma-master",
      "node_name": "sma-master",
      "completed": 42,
      "failed": 2,
      "avg_duration_seconds": 138.2
    }
  ],
  "encoders": {
    "qsv": { "count": 38, "bytes_saved": 120000000, "minutes": 1100.0 },
    "software": { "count": 1, "bytes_saved": 456789, "minutes": 44.5 }
  },
  "failures": {
    "hardware": { "count": 1 },
    "source_media": { "count": 1 }
  }
}
```

Without PostgreSQL the response is `503` with `{"available": false, "reason": "...", "docs_url": "/docs/daemon"}`.

### KPI fields

| Field | Description |
| ----- | ----------- |
| `completed`, `failed`, `cancelled` | Job counts within the selected window. |
| `pending`, `running`, `total` | A live snapshot of the `jobs` table; the window does not apply. |
| `failure_rate_pct` | `failed / (completed + failed) * 100` for the window; `null` when no job finished. |
| `avg_duration_seconds` | Mean wall-clock time from `started_at` to `completed_at` for completed jobs. |
| `p95_duration_seconds` | The 95th-percentile duration for completed jobs. |
| `avg_compression_pct` | Mean of `(1 - output_bytes / input_bytes) * 100` over completed jobs that recorded both sizes. |
| `bytes_saved_total` | Sum of `input - output` over completed jobs that shrank. This is the disk space reclaimed. |
| `bytes_grown_total` | Sum of `output - input` over completed jobs whose output is larger than the source. |
| `minutes_transcoded_total` | Total source duration in minutes across completed jobs, rounded to 0.01. |
| `throughput_per_hour` | `completed` divided by the window length in hours; `null` for the `all` window. |

For the `all` window, the counts also include jobs that were deleted by retention. Their totals live in the
`job_stats_archive` table, so lifetime figures survive pruning. Windowed figures count only jobs that still exist.

### Breakdowns and time series

| Key | Shape | Description |
| --- | ----- | ----------- |
| `encoders` | `{backend: {count, bytes_saved, minutes}}` | Completed jobs per encoder backend: `qsv`, `vaapi`, `nvenc`, `videotoolbox`, `amf`, `software`, `copy` or `unknown`. |
| `failures` | `{category: {count}}` | Failed jobs per category: `config`, `source_media`, `hardware`, `disk`, `system` or `unknown`. |
| `nodes` | list | Completed and failed counts and average duration per node. |
| `timeseries` | list | Completed and failed counts per bucket, zero-filled so there are no gaps. |

The `timeseries` array has 24 hourly buckets for `24h`, 7 daily buckets for `7d`, 30 daily buckets for `30d`, and is
empty for `all`.

## Per-job columns

Workers record these values on each `jobs` row when a job finishes, and the metrics above are computed from them:

| Column | Meaning |
| ------ | ------- |
| `input_size_bytes`, `output_size_bytes` | Source and output file sizes. Failed jobs keep the source size. |
| `source_duration_seconds` | Source container duration. |
| `encoder_backend`, `encoder_name` | The backend and encoder that produced the output. |
| `failure_category`, `failure_cause` | The operator-facing category and the raw cause of a failure. |
| `request_source`, `request_profile` | Who queued the job (for example `sonarr`) and the profile it resolved to. |
| `source_*`, `dest_*` | Video codec, width, height, HDR type, audio codec and channels before and after conversion. |

The daemon adds missing columns on startup, so no manual migration is needed.

## Prometheus instruments

`GET /metrics` exposes the following series. All counters and histograms start at zero when the daemon starts, so use
`rate()` or `increase()` in queries.

| Instrument | Type | Labels | Meaning |
| ---------- | ---- | ------ | ------- |
| `sma_build_info` | Gauge | `version`, `node_id` | Always 1; the labels identify the running release. |
| `sma_jobs_total` | Counter | `status` | Jobs that reached a terminal status (`completed`, `failed`, `cancelled`). |
| `sma_jobs_enqueued_total` | Counter | `request_source`, `request_profile` | Jobs accepted into the queue. |
| `sma_job_duration_seconds` | Histogram | `status` | Wall-clock time from claim to terminal status. |
| `sma_jobs_in_flight` | Gauge | `node_id` | Jobs currently running on this daemon. |
| `sma_queue_depth` | Gauge | `node_id` | Pending jobs, sampled at scrape time. |
| `sma_failures_total` | Counter | `failure_category`, `failure_cause` | Failed jobs by category and raw cause. |
| `sma_fallback_transitions_total` | Counter | `from_tier`, `to_tier`, `failure_class` | Steps of the hardware-to-software fallback ladder. |
| `sma_bytes_saved_bytes_total` | Counter | `encoder_backend` | Bytes reclaimed by transcodes. |
| `sma_bytes_grown_bytes_total` | Counter | `encoder_backend` | Bytes added by transcodes whose output grew. |
| `sma_source_seconds_transcoded_total` | Counter | `encoder_backend` | Source seconds transcoded. |

`request_source` is one of `sonarr`, `radarr`, `webhook`, `scan`, `audit`, `cli` or `unknown`. The
`encoder_backend` label takes the same values as the `encoders` breakdown above.

### Storage instruments

Four more instruments cover output-directory capacity and the orphan-sweep janitor. The janitor is tuned with the
`daemon.storage-janitor-interval-seconds` and `daemon.storage-janitor-max-age-seconds` keys; see
[Daemon Mode](daemon.md) for both.

| Instrument | Type | Labels | Meaning |
| ---------- | ---- | ------ | ------- |
| `sma_output_dir_total_bytes` | Gauge | `node_id` | Total bytes on the filesystem that holds `output-directory`. |
| `sma_output_dir_used_bytes` | Gauge | `node_id` | Used bytes on that filesystem, sampled at scrape time. |
| `sma_output_dir_free_bytes` | Gauge | `node_id` | Free bytes on that filesystem, sampled at scrape time. |
| `sma_output_orphan_files_swept_total` | Counter | `node_id`, `kind` | Files the janitor removed, by kind (`sma`, `smatmp`, `empty_mp4`). |

The capacity gauges read the filesystem when Prometheus scrapes, so there is no background poller. They exist only when
`base.converter.output-directory` is set. A missing directory or a permission error reports zero instead of failing the
scrape.

Example alert expressions:

```promql
# Less than 50 GB free on any node (use "for: 10m" in the alert rule).
sma_output_dir_free_bytes < 50e9

# The janitor sweeps more than 5 files per hour: workers are crashing or being killed mid-transcode.
sum by (node_id) (rate(sma_output_orphan_files_swept_total[1h])) > 5
```

## Charts

The metrics page uses Chart.js 4.5.1, loaded from the jsDelivr CDN, so the browser needs internet access to draw them:

- a line chart of jobs completed and failed per hour (24h) or per day (7d, 30d);
- a doughnut chart of the job status distribution;
- a bar chart of completed and failed jobs per node.

The charts update in place when you change the time window.
