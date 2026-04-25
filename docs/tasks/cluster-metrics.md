# Cluster-Wide Transcoding Metrics — Task Breakdown

**Source PRP**: [docs/prps/cluster-metrics.md](../prps/cluster-metrics.md)

**Feature**: Cluster-wide transcoding job statistics and interactive metrics dashboard

---

## Task Dependency Order

```
T-001 (DB migration + complete_job)
  └── T-002 (worker size capture)         depends on T-001
  └── T-003 (get_metrics() query method)  depends on T-001
        └── T-004 (handler + routes)      depends on T-003
              └── T-005 (metrics.html)    depends on T-004
              └── T-006 (dashboard KPI)   depends on T-004
T-007 (tests)                             depends on T-004
T-008 (docs)                              depends on T-005, T-006
```

---

## T-001 — DB Schema Migration & complete_job() Extension

**Priority**: Critical | **Effort**: S | **Prereqs**: none

### Task Purpose

**As a** daemon operator
**I need** the jobs table to store input/output file sizes
**So that** compression ratio and throughput metrics can be computed

### Functional Requirements

- **REQ-1**: When the daemon starts, the `jobs` table shall have `input_size_bytes BIGINT` and
  `output_size_bytes BIGINT` columns, added idempotently via `ADD COLUMN IF NOT EXISTS`.
- **REQ-2**: A composite index `idx_jobs_status_completed ON jobs(status, completed_at)` shall
  exist after startup, added idempotently.
- **REQ-3**: `complete_job(job_id, ...)` shall accept optional `input_size: int | None = None`
  and `output_size: int | None = None` keyword arguments and persist them when provided.
- **REQ-4**: Existing callers of `complete_job()` with no size arguments must continue to work
  without modification (backward-compatible signature change).

### Implementation Steps

**File**: `resources/daemon/db.py`

1. In `_init_db()`, after the last existing `ADD COLUMN IF NOT EXISTS` statement (line ~155),
   add three `cur.execute()` calls:

   ```python
   cur.execute("""
       ALTER TABLE jobs
       ADD COLUMN IF NOT EXISTS input_size_bytes BIGINT
   """)
   cur.execute("""
       ALTER TABLE jobs
       ADD COLUMN IF NOT EXISTS output_size_bytes BIGINT
   """)
   cur.execute("""
       CREATE INDEX IF NOT EXISTS idx_jobs_status_completed
       ON jobs(status, completed_at)
   """)
   ```

2. Find `complete_job(self, job_id, ...)` and extend its signature:

   ```python
   def complete_job(self, job_id: int, input_size: int | None = None, output_size: int | None = None):
   ```

3. Inside `complete_job()`, extend the `UPDATE jobs SET ...` to include the size columns
   only when values are provided. Use a conditional approach:

   ```python
   size_updates = ""
   size_params = []
   if input_size is not None:
       size_updates += ", input_size_bytes = %s"
       size_params.append(input_size)
   if output_size is not None:
       size_updates += ", output_size_bytes = %s"
       size_params.append(output_size)
   # Inject size_updates into the SET clause before WHERE job_id = %s
   ```

### Acceptance Criteria

- **Given** the daemon starts against an existing PostgreSQL database without the new columns,
  **When** `_init_db()` runs,
  **Then** `input_size_bytes` and `output_size_bytes` columns exist and the index is present.

- **Given** a call to `complete_job(job_id)` with no size args,
  **When** it executes,
  **Then** the job row has `status='completed'` and size columns remain NULL (no error).

- **Given** a call to `complete_job(job_id, input_size=1000, output_size=600)`,
  **When** it executes,
  **Then** `input_size_bytes=1000` and `output_size_bytes=600` are persisted.

### Validation

```bash
source venv/bin/activate && ruff check resources/daemon/db.py --fix
source venv/bin/activate && python -m pytest tests/ -q --tb=short
```

---

## T-002 — Worker File Size Capture at Job Completion

**Priority**: Critical | **Effort**: S | **Prereqs**: T-001

### Task Purpose

**As a** metrics system
**I need** actual file sizes captured at conversion time
**So that** compression ratio can be computed from real data

### Functional Requirements

- **REQ-1**: When a conversion job completes successfully, `input_size_bytes` shall be set to
  the size of the source file before conversion and `output_size_bytes` to the output file size.
- **REQ-2**: If `os.path.getsize()` raises `OSError`, the error shall be logged as a warning
  and `None` passed for that size — the job must not fail due to a size-capture error.
- **REQ-3**: Size capture shall happen in `_run_conversion_inner()` in `worker.py`.

### Implementation Steps

**File**: `resources/daemon/worker.py`

1. At the beginning of `_run_conversion_inner()`, after the input path is established, capture
   the input size:

   ```python
   input_size = None
   try:
       input_size = os.path.getsize(path)
   except OSError:
       logger.warning("Could not stat input file for job %s: %s", job_id, path)
   ```

2. After conversion completes (find the variable holding the final output path — it may be
   `output_path`, `dest`, or similar — check the local variable at the `complete_job` call site):

   ```python
   output_size = None
   try:
       output_size = os.path.getsize(output_path)  # use actual variable name
   except OSError:
       logger.warning("Could not stat output file for job %s", job_id)
   ```

3. Extend the `complete_job()` call:

   ```python
   self.job_db.complete_job(job_id, input_size=input_size, output_size=output_size)
   ```

### Acceptance Criteria

- **Given** a job completes successfully with readable input and output files,
  **When** the job record is checked in the DB,
  **Then** `input_size_bytes` and `output_size_bytes` are non-NULL positive integers.

- **Given** `os.path.getsize()` raises `OSError` on either file,
  **When** the job completes,
  **Then** the job status is `completed` (not `failed`) and a warning is logged.

### Validation

```bash
source venv/bin/activate && ruff check resources/daemon/worker.py --fix
source venv/bin/activate && python -m pytest tests/test_worker.py -q --tb=short
```

---

## T-003 — get_metrics() Database Method

**Priority**: Critical | **Effort**: M | **Prereqs**: T-001

### Task Purpose

**As a** metrics API endpoint
**I need** a single database method returning all KPIs and time-series data
**So that** the handler can serve a single comprehensive JSON response

### Functional Requirements

- **REQ-1**: `get_metrics(window: str = "24h")` shall accept `"24h"`, `"7d"`, `"30d"`, or
  `"all"` and return a dict matching the shape defined in the PRP data model.
- **REQ-2**: KPIs shall include: completed, failed, pending, running, cancelled, total,
  failure_rate_pct, avg_duration_seconds, p95_duration_seconds, avg_compression_pct.
- **REQ-3**: Time-series shall be zero-filled (no missing hours/days) using `generate_series`
  + `LEFT JOIN` + `COALESCE`.
- **REQ-4**: Per-node breakdown shall join `cluster_nodes` for `node_name`.
- **REQ-5**: For window `"all"`, `timeseries` shall be an empty list (no bucketing).
- **REQ-6**: Duration shall be computed as `EXTRACT(EPOCH FROM (completed_at - started_at))`
  — no new `duration_seconds` column.

### Implementation Steps

**File**: `resources/daemon/db.py`

Add `get_metrics(self, window: str = "24h") -> dict` method. Follow the pseudocode in the PRP
exactly, including:

```python
WINDOW_MAP = {
    "24h":  ("hour", 24),
    "7d":   ("day",  7),
    "30d":  ("day",  30),
    "all":  None,
}
```

Three queries within a single `with self._conn() as conn` block:
1. KPI aggregate query (see PRP pseudocode Task 3)
2. Time-series query with `generate_series` (skip for `"all"`)
3. Per-node breakdown query with `LEFT JOIN cluster_nodes`

Serialize `bucket` timestamps with `.isoformat()` for JSON compatibility.

### Acceptance Criteria

- **Given** a PostgreSQL database with 100 completed jobs over the last 48 hours,
  **When** `get_metrics("24h")` is called,
  **Then** `timeseries` has exactly 24 entries (one per hour), all with integer counts.

- **Given** jobs with `input_size_bytes=1000` and `output_size_bytes=600`,
  **When** `get_metrics("all")` is called,
  **Then** `kpis.avg_compression_pct` is approximately 40.0.

- **Given** a window of `"all"`,
  **When** `get_metrics("all")` is called,
  **Then** `timeseries` is `[]`.

### Validation

```bash
source venv/bin/activate && ruff check resources/daemon/db.py --fix
source venv/bin/activate && python -m pytest tests/ -q --tb=short
# With live DB:
# TEST_DB_URL=... python -m pytest tests/test_cluster.py -v -k metrics
```

---

## T-004 — Handler Methods, Routes, and Page Loader

**Priority**: High | **Effort**: S | **Prereqs**: T-003

### Task Purpose

**As an** HTTP client
**I need** `/api/metrics` and `/metrics` routes registered and responding
**So that** the frontend can fetch data and load the metrics page

### Functional Requirements

- **REQ-1**: `GET /api/metrics?window=<w>` shall return JSON from `get_metrics(window)`.
- **REQ-2**: When `is_distributed` is `False`, `/api/metrics` shall return HTTP 503 with
  `{"available": false, "reason": "...", "docs_url": "/docs#cluster-metrics"}`.
- **REQ-3**: The `window` param shall be validated against `{"24h", "7d", "30d", "all"}`;
  invalid values fall back to `"24h"`.
- **REQ-4**: `GET /metrics` shall serve `metrics.html` with `window.SMA_API_KEY` injected,
  mirroring the `_get_dashboard()` pattern.
- **REQ-5**: `/metrics` shall be in `PUBLIC_ENDPOINTS` (no auth required for the page itself).

### Implementation Steps

**File**: `resources/daemon/handler.py`

1. Add `"/metrics"` to the `PUBLIC_ENDPOINTS` list (line ~37).

2. Add handler method:

   ```python
   def _get_metrics_api(self, _path, query):
       if not self.server.job_db.is_distributed:
           self.send_json_response(503, {
               "available": False,
               "reason": "PostgreSQL is not configured. Set SMA_DAEMON_DB_URL to enable cluster metrics.",
               "docs_url": "/docs#cluster-metrics",
           })
           return
       window = query.get("window", ["24h"])[0]
       if window not in {"24h", "7d", "30d", "all"}:
           window = "24h"
       self.send_json_response(200, self.server.job_db.get_metrics(window=window))

   def _get_metrics_page(self, _path, _query):
       html = _load_metrics_html()
       html = html.replace("__SMA_API_KEY__", self.server.api_key or "")
       self.send_html_response(200, html)
   ```

**File**: `resources/daemon/docs_ui.py`

3. Add `_load_metrics_html()` mirroring `_load_dashboard_html()` — reads
   `resources/metrics.html` from disk.

**File**: `resources/daemon/routes.py`

4. In `_get_routes()`, add:

   ```python
   "/metrics":     lambda handler, path, query: handler._get_metrics_page(path, query),
   "/api/metrics": lambda handler, path, query: handler._get_metrics_api(path, query),
   ```

### Acceptance Criteria

- **Given** a running daemon in standalone mode,
  **When** `GET /api/metrics` is called,
  **Then** the response is HTTP 503 with `{"available": false, ...}`.

- **Given** a running daemon with PostgreSQL,
  **When** `GET /api/metrics?window=7d` is called,
  **Then** the response is HTTP 200 with `{"available": true, "window": "7d", ...}`.

- **Given** an invalid window param `?window=999d`,
  **When** the request is processed,
  **Then** the response uses `window: "24h"` (fallback).

- **Given** `GET /metrics`,
  **When** the page is requested,
  **Then** HTTP 200 is returned with HTML content.

### Validation

```bash
source venv/bin/activate && ruff check resources/daemon/handler.py resources/daemon/routes.py \
    resources/daemon/docs_ui.py --fix
source venv/bin/activate && python -m pytest tests/test_handler.py -q --tb=short
```

---

## T-005 — metrics.html Detail Page

**Priority**: High | **Effort**: L | **Prereqs**: T-004

### Task Purpose

**As an** operator
**I need** a dedicated metrics page with interactive charts
**So that** I can analyze transcoding throughput, duration, and compression trends over time

### Functional Requirements

- **REQ-1**: Page loads Chart.js 4.5.1 from jsDelivr CDN.
- **REQ-2**: Three charts: line (jobs/time), horizontal bar (per-node), doughnut (status split).
- **REQ-3**: Four KPI cards: P95 duration (formatted), avg compression %, failure rate %,
  total jobs.
- **REQ-4**: Time-window selector (24h / 7d / 30d) updates all charts using
  `chart.update('none')` without page reload.
- **REQ-5**: In standalone mode (`available: false`), a styled warning panel is shown instead
  of charts; the panel includes the reason text and a link to docs.
- **REQ-6**: Chart instances are declared outside Alpine's reactive `return {}` (module-closure
  pattern) to avoid Proxy conflicts.
- **REQ-7**: `this.$nextTick()` is called before `new Chart()` to ensure canvas has dimensions.
- **REQ-8**: Duration values in tooltips and KPI cards are formatted as `Xh Ym Zs`.
- **REQ-9**: Dark theme: `bg-gray-900` page background, `bg-gray-800` cards, consistent with
  `dashboard.html`.
- **REQ-10**: Nav link back to `/dashboard` in the page header.

### Implementation Steps

**File**: `resources/metrics.html` (new file)

Structure:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <!-- Same CDN links as dashboard.html: Alpine.js, Tailwind -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"></script>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen">

  <!-- Header with nav: Dashboard link, page title -->
  <!-- Window selector: 24h / 7d / 30d buttons -->
  <!-- KPI cards grid: 4 cards -->
  <!-- Charts section: jobsCanvas (line), nodesCanvas (bar), statusCanvas (doughnut) -->
  <!-- Unavailable panel: x-show="!loading && !available" -->

  <script>
    function authHeaders() { /* same as dashboard.html */ }
    function metrics() {
      let chart_jobs = null, chart_nodes = null, chart_status = null;
      // ... full implementation per PRP pseudocode ...
    }
  </script>
</body>
</html>
```

Follow the full Alpine.js pseudocode from the PRP (Task 8 section) exactly, including:
- `formatDuration()` helper
- `chartColors()` resolving Tailwind CSS variables via `getComputedStyle`
- `renderOrUpdate()` with create-or-update logic
- `destroy()` lifecycle cleanup

### Acceptance Criteria

- **Given** a browser opens `/metrics` with PostgreSQL connected,
  **When** the page loads,
  **Then** all three charts render with data and KPI cards show numeric values.

- **Given** the user clicks the `7d` window button,
  **When** the fetch completes,
  **Then** chart data updates (no page reload, no chart flicker) and the active button
  has the highlighted style.

- **Given** the daemon is in standalone mode,
  **When** `/metrics` loads,
  **Then** charts are not rendered and a yellow warning panel is shown with the reason text.

- **Given** a job duration of 3723 seconds,
  **When** it appears in a chart tooltip or KPI card,
  **Then** it displays as `1h 2m 3s`.

### Validation

```bash
# Open in browser after daemon starts
open http://localhost:8585/metrics
# Check browser console for JS errors
# Test window selector clicks (24h / 7d / 30d)
# Test standalone mode: restart daemon without SMA_DAEMON_DB_URL
```

---

## T-006 — Dashboard KPI Strip and Nav Link

**Priority**: Medium | **Effort**: S | **Prereqs**: T-004

### Task Purpose

**As an** operator on the main dashboard
**I need** a compact metrics summary and a link to the full metrics page
**So that** I get at-a-glance insight without navigating away

### Functional Requirements

- **REQ-1**: Dashboard fetches `/api/metrics?window=24h` as part of its existing refresh cycle.
- **REQ-2**: When `available` is true, a KPI strip shows 4 cards: Jobs/hr (throughput),
  Avg Duration, Failure Rate %, Avg Compression %.
- **REQ-3**: KPI strip is hidden (`x-show`) when metrics are unavailable.
- **REQ-4**: A "Metrics" nav link is added to the dashboard header alongside Admin/Docs.

### Implementation Steps

**File**: `resources/dashboard.html`

1. In the `dashboard()` Alpine function, add state:

   ```javascript
   metricsAvailable: false,
   metrics: null,
   ```

2. In `refresh()`, add a parallel fetch:

   ```javascript
   fetch("/api/metrics?window=24h", { headers: this.authHeaders() })
     .then(r => r.json())
     .then(d => { this.metricsAvailable = d.available; this.metrics = d; })
     .catch(() => {});
   ```

3. After the existing status card grid, add the KPI strip:

   ```html
   <div x-show="metricsAvailable" class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
     <!-- Jobs/hr, Avg Duration, Failure Rate, Avg Compression cards -->
   </div>
   ```

4. In the header nav, add alongside existing Admin/Docs links:

   ```html
   <a href="/metrics" class="text-sm text-gray-400 hover:text-white">Metrics</a>
   ```

### Acceptance Criteria

- **Given** the dashboard is open with PostgreSQL connected,
  **When** the page refreshes,
  **Then** 4 KPI cards appear below the status grid with live values.

- **Given** the daemon is in standalone mode,
  **When** the dashboard loads,
  **Then** the KPI strip is not visible (no error, no empty cards).

- **Given** the header nav,
  **When** viewed,
  **Then** a "Metrics" link is present and navigates to `/metrics`.

### Validation

```bash
open http://localhost:8585/dashboard
# Verify KPI strip appears
# Verify Metrics nav link present
```

---

## T-007 — Tests

**Priority**: High | **Effort**: M | **Prereqs**: T-004

### Task Purpose

**As a** developer
**I need** handler tests for the metrics endpoint
**So that** regressions are caught in CI without requiring a live database

### Functional Requirements

- **REQ-1**: Tests use the mock-based pattern from `test_handler.py` (`_make_server()`,
  `_make_handler()`, no real DB required).
- **REQ-2**: Tests cover: standalone mode (503), valid window, invalid window fallback,
  and the metrics page route (200 HTML).
- **REQ-3**: All existing tests continue to pass.

### Implementation Steps

**File**: `tests/test_metrics.py` (new file)

Mirror the pattern from `tests/test_handler.py`:

```python
from tests.conftest import _make_server, _make_handler  # or however it's imported

class TestGetMetricsApi:
    def test_returns_503_when_not_distributed(self):
        server = _make_server(is_distributed=False)
        handler = _make_handler(server)
        handler._get_metrics_api("/api/metrics", {})
        # assert send_json_response called with 503

    def test_returns_metrics_when_distributed(self):
        server = _make_server(is_distributed=True)
        server.job_db.get_metrics.return_value = {"available": True, "kpis": {}, ...}
        handler = _make_handler(server)
        handler._get_metrics_api("/api/metrics", {"window": ["24h"]})
        # assert send_json_response called with 200

    def test_invalid_window_falls_back_to_24h(self):
        ...  # assert get_metrics called with window="24h"

    def test_metrics_page_returns_html(self):
        ...  # assert send_html_response called with 200
```

### Acceptance Criteria

- **Given** `is_distributed=False` on the mock server,
  **When** `_get_metrics_api` is called,
  **Then** `send_json_response` is called with status 503 and `available=False`.

- **Given** an invalid `window` param,
  **When** `_get_metrics_api` processes it,
  **Then** `job_db.get_metrics` is called with `window="24h"`.

- **Given** the full test suite runs,
  **When** `pytest tests/` completes,
  **Then** all existing tests still pass.

### Validation

```bash
source venv/bin/activate && python -m pytest tests/test_metrics.py -v --tb=short
source venv/bin/activate && python -m pytest tests/ -q --tb=short
```

---

## T-008 — Documentation

**Priority**: Medium | **Effort**: S | **Prereqs**: T-005, T-006

### Task Purpose

**As a** user or operator
**I need** documentation for the metrics feature
**So that** I know how to enable it and what it shows

### Implementation Steps

Per CLAUDE.md, all documentation changes must be applied in three places:

1. **`docs/metrics.md`** (new file): Document the `/api/metrics` endpoint (params, response
   shape), the `/metrics` UI page, the PostgreSQL requirement, and the two new DB columns.

2. **`docs/daemon.md`**: Add `/api/metrics` to the API endpoint reference table; add a mention
   of `/metrics` in the web UI section.

3. **`resources/docs.html`**: Add a Metrics section consistent with other daemon docs sections.

4. **GitHub wiki** (`/tmp/sma-wiki/`): Create or update the corresponding wiki page and push:

   ```bash
   cd /tmp/sma-wiki && git add -A && git commit -m "docs: add cluster metrics page" && git push origin HEAD:master
   ```

### Acceptance Criteria

- **Given** a user visits `/docs` in the daemon UI,
  **When** they look for metrics documentation,
  **Then** they find a section explaining the feature and its requirements.

- **Given** the `docs/` directory,
  **When** `docs/metrics.md` is read,
  **Then** it describes the endpoint, UI, PostgreSQL requirement, and new DB columns.

### Validation

```bash
# markdownlint check
source venv/bin/activate && markdownlint docs/metrics.md docs/daemon.md 2>/dev/null || true
open http://localhost:8585/docs  # verify Metrics section appears
```

---

## Summary

| Task | Description | Effort | Prereqs |
|------|-------------|--------|---------|
| T-001 | DB schema migration + complete_job() extension | S | — |
| T-002 | Worker file size capture | S | T-001 |
| T-003 | get_metrics() database method | M | T-001 |
| T-004 | Handler methods + route registration | S | T-003 |
| T-005 | metrics.html detail page with Chart.js | L | T-004 |
| T-006 | Dashboard KPI strip + nav link | S | T-004 |
| T-007 | Tests for metrics endpoint | M | T-004 |
| T-008 | Documentation (3 places) | S | T-005, T-006 |

**Critical path**: T-001 → T-003 → T-004 → T-005
