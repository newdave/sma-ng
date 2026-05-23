# Cluster Metrics

SMA-NG provides a built-in metrics dashboard for monitoring transcoding job throughput,
duration, and compression efficiency across all cluster nodes.

## Requirements

Cluster metrics require PostgreSQL mode. Set `daemon.db_url` in `sma-ng.yml`
to enable. In standalone (in-memory) mode the metrics page shows an
"unavailable" notice.

## Accessing Metrics

- **Dashboard widget**: The main dashboard (`/dashboard`) shows a 4-card KPI strip
  (throughput, avg duration, failure rate, compression) pulled from the last 24 hours
  when PostgreSQL is connected.
- **Full metrics page**: Navigate to `/dashboard/metrics` for interactive charts with a time-window
  selector (24h / 7d / 30d / all-time). *(Previously served at `/metrics`; that path now serves
  Prometheus text exposition — see "Prometheus" below.)*
- **Prometheus**: `GET /metrics` returns Prometheus text exposition for scraping by Prometheus /
  Grafana / Alertmanager. See the [Prometheus Exposition](daemon.md#prometheus-exposition) section in
  `docs/daemon.md` for the metric catalogue and a sample scrape config.

## Metrics API

### `GET /api/metrics`

Returns aggregated job metrics as JSON.

**Query parameters**

| Parameter | Values | Default | Description |
| --------- | ------ | ------- | ----------- |
| `window`  | `24h` \| `7d` \| `30d` \| `all` | `24h` | Time window for completed/failed counts |

**Response (PostgreSQL available)**

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
      "node_id": "abc123",
      "node_name": "worker-01",
      "completed": 42,
      "failed": 2,
      "avg_duration_seconds": 138.2
    }
  ],
  "encoders": {
    "qsv":      { "count": 38, "bytes_saved": 120000000, "minutes": 1100.0 },
    "vaapi":    { "count":  3, "bytes_saved":   3000000, "minutes":   90.0 },
    "software": { "count":  1, "bytes_saved":    456789, "minutes":   44.5 }
  },
  "failures": {
    "hardware":     { "count": 1 },
    "source_media": { "count": 1 }
  }
}
```

**Response (standalone mode)**

```json
{
  "available": false,
  "reason": "Cluster metrics are only available in distributed (PostgreSQL) mode. ...",
  "docs_url": "/docs/daemon"
}
```

**KPI field notes**

| Field | Description |
| ----- | ----------- |
| `completed`, `failed`, `cancelled` | Count within the selected `window` |
| `pending`, `running`, `total` | Real-time snapshot; not time-windowed |
| `failure_rate_pct` | `failed / (completed + failed) * 100` for the window |
| `avg_duration_seconds` | Mean wall-clock time (started\_at → completed\_at) for completed jobs |
| `p95_duration_seconds` | 95th-percentile duration for completed jobs |
| `avg_compression_pct` | Mean `(1 − output_bytes / input_bytes) × 100` — only populated when file sizes are recorded |
| `bytes_saved_total` | Sum of `(input − output)` over completed jobs in the window, clamped to ≥ 0 per job. Operator-quotable "disk space reclaimed" number |
| `bytes_grown_total` | Sum of `(output − input)` over completed jobs whose output exceeded the source. Surfaces HDR / quality-bumping configs that grow the file |
| `minutes_transcoded_total` | Sum of source container duration in minutes (rounded to 0.01) across completed jobs in the window |
| `throughput_per_hour` | `completed / window_hours`; `null` for the `all` window |

**Breakdowns**

| Key | Shape | Description |
| --- | ----- | ----------- |
| `encoders` | `{backend: {count, bytes_saved, minutes}}` | Per-encoder-backend rollup. Backend is one of `qsv` / `vaapi` / `nvenc` / `videotoolbox` / `amf` / `software` / `copy` / `unknown` |
| `failures` | `{category: {count}}` | Per-failure-category rollup of failed jobs. Category is one of `config` / `source_media` / `hardware` / `disk` / `system` / `unknown` |

**Timeseries**

Zero-filled buckets (no gaps):

- `24h` window → 24 hourly buckets
- `7d` window → 7 daily buckets
- `30d` window → 30 daily buckets
- `all` window → empty array (no bucketing)

## Database Schema Changes

Two columns are added to the `jobs` table on daemon startup (idempotent migrations):

| Column | Type | Description |
| ------ | ---- | ----------- |
| `input_size_bytes` | `BIGINT` | Source file size captured before conversion |
| `output_size_bytes` | `BIGINT` | Output file size (currently NULL — reserved for future worker integration) |

A composite index `idx_jobs_status_completed ON jobs(status, completed_at)` is also
created to support efficient windowed aggregation queries.

## Charts

The `/dashboard/metrics` page uses Chart.js 4.5.1 (loaded from jsDelivr CDN) to render:

- **Line chart** — jobs completed/failed per hour (24h) or per day (7d/30d)
- **Doughnut chart** — job status distribution across all statuses
- **Horizontal bar chart** — per-node completed/failed job counts

All charts update in place when switching time windows (no page reload).

## Storage instruments

The daemon exposes four additional Prometheus instruments covering
output-directory capacity and the orphan-sweep janitor (see
[`docs/daemon.md`](daemon.md#storage-management--janitor) for the
janitor itself).

| Instrument                              | Type    | Labels             | Meaning                                                        |
| --------------------------------------- | ------- | ------------------ | -------------------------------------------------------------- |
| `sma_output_dir_total_bytes`            | Gauge   | `node_id`          | Total bytes on the filesystem hosting `output-directory`.      |
| `sma_output_dir_used_bytes`             | Gauge   | `node_id`          | Used bytes on that filesystem (sampled at scrape).             |
| `sma_output_dir_free_bytes`             | Gauge   | `node_id`          | Free bytes on that filesystem (sampled at scrape).             |
| `sma_output_orphan_files_swept_total`   | Counter | `node_id`, `kind`  | Files removed by the janitor, by kind (`sma`/`smatmp`/`empty_mp4`). |

Capacity gauges are populated via `Gauge.set_function()` — the scrape
calls `shutil.disk_usage()` lazily, so there is no background poller.
The gauges are only registered when `base.converter.output-directory` is
non-empty; missing-directory and permission errors collapse to zero so
the scrape loop never crashes.

Sample PromQL for alerts:

```promql
# Alert when less than 50 GB free for 10 minutes on any node.
sma_output_dir_free_bytes < 50e9 for 10m

# Alert when the janitor is sweeping more than 5 files per hour
# (sustained, across all kinds) — a sign that workers are crashing
# or being killed mid-transcode.
sum by (node_id) (rate(sma_output_orphan_files_swept_total[1h])) > 5
```

The full janitor cadence and the structured log line it emits per
cycle are documented in
[`docs/daemon.md`](daemon.md#storage-management--janitor).
