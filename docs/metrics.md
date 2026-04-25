# Cluster Metrics

SMA-NG provides a built-in metrics dashboard for monitoring transcoding job throughput,
duration, and compression efficiency across all cluster nodes.

## Requirements

Cluster metrics require PostgreSQL mode. Set `SMA_DAEMON_DB_URL` (or `daemon.db_url` in
`sma-ng.yml`) to enable. In standalone (in-memory) mode the metrics page shows an
"unavailable" notice.

## Accessing Metrics

- **Dashboard widget**: The main dashboard (`/dashboard`) shows a 4-card KPI strip
  (throughput, avg duration, failure rate, compression) pulled from the last 24 hours
  when PostgreSQL is connected.
- **Full metrics page**: Navigate to `/metrics` for interactive charts with a time-window
  selector (24h / 7d / 30d / all-time).

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
  ]
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
| `throughput_per_hour` | `completed / window_hours`; `null` for the `all` window |

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

The `/metrics` page uses Chart.js 4.5.1 (loaded from jsDelivr CDN) to render:

- **Line chart** — jobs completed/failed per hour (24h) or per day (7d/30d)
- **Doughnut chart** — job status distribution across all statuses
- **Horizontal bar chart** — per-node completed/failed job counts

All charts update in place when switching time windows (no page reload).
