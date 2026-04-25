# Cluster-Wide Transcoding Job Metrics

## Discovery Summary

### Initial Task Analysis

User requested tracking and displaying statistics/metrics for transcoding jobs cluster-wide.
Preflight analysis found solid foundations (PostgreSQL job table with timestamps and node attribution)
but identified a large scope gap between "richer stats cards" and "full time-series charting with
schema changes." Clarifications were collected before research began.

### User Clarifications Received

- **Question**: Which metrics are required?
  **Answer**: All of the following — job counts by status, throughput rate, avg/P95 duration,
  per-node breakdown, file size before/after + compression ratio, failure rate over time, queue depth.
  **Impact**: Requires two new DB columns (`input_size_bytes`, `output_size_bytes`) and worker-side
  capture at job completion.

- **Question**: What time windows?
  **Answer**: All-time aggregate (A) and configurable window with a UI selector (B: 24h/7d/30d).
  **Impact**: Time-series queries use PostgreSQL `date_trunc` + `generate_series` for zero-filled
  buckets; no indefinite retention burden.

- **Question**: Where in the UI?
  **Answer**: Summary widget on the existing dashboard + a separate `/metrics` detail page.
  **Impact**: Two HTML surfaces — `dashboard.html` gets a compact KPI strip, new `metrics.html` gets
  full charts.

- **Question**: Charts or tables?
  **Answer**: Full interactive charts (Chart.js via CDN).
  **Impact**: Chart.js 4.5.1 added as a CDN dependency; requires careful Alpine.js integration
  (module-closure pattern).

- **Question**: PostgreSQL-only or standalone too?
  **Answer**: PostgreSQL/cluster mode only; show "not available" message in standalone.
  **Impact**: Reuse existing `is_distributed` guard pattern from `handler.py:421-423`.

---

## Goal

Build a cluster-wide metrics system for SMA-NG that:

1. Captures file sizes and derives duration at job completion in the PostgreSQL job table
2. Exposes a `/api/metrics` endpoint returning aggregated KPIs and time-series data for a
   configurable time window (24h / 7d / 30d)
3. Adds a compact KPI strip to the existing dashboard (`/dashboard`) with totals, throughput,
   average duration, and failure rate
4. Creates a new `/metrics` detail page with interactive Chart.js charts:
   - Line chart: jobs completed per hour/day over the selected window
   - Bar chart: per-node job throughput comparison
   - Doughnut chart: job status distribution
   - KPI cards: P95 duration, average compression ratio, failure rate
5. Gracefully degrades to a "not available" state in standalone (non-PostgreSQL) mode

---

## Why

- Operators running SMA-NG in cluster mode have no visibility into throughput trends,
  conversion efficiency, or per-node load distribution.
- Compression ratio and file size data are needed to justify and tune hardware acceleration
  and codec selection decisions.
- Failure rate trending enables early detection of systemic issues (bad source files, codec
  errors, storage problems) before they accumulate.

---

## What

### Success Criteria

- [ ] Two new columns (`input_size_bytes BIGINT`, `output_size_bytes BIGINT`) added to `jobs`
      table via idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migration
- [ ] Worker captures actual file sizes at job completion and persists them
- [ ] `GET /api/metrics?window=24h|7d|30d|all` returns structured JSON with KPIs + time-series
- [ ] Dashboard shows 4 new KPI cards (throughput, avg duration, failure rate, compression ratio)
      that auto-refresh with the existing 5-second poll cycle
- [ ] `/metrics` page renders with Chart.js — line, bar, and doughnut charts load correctly
- [ ] Time-window selector (24h / 7d / 30d) updates all charts without page reload
- [ ] In standalone (non-PostgreSQL) mode, `/api/metrics` returns `{"available": false, ...}` and
      the UI shows a styled "not available" message
- [ ] All existing tests pass; new handler tests added for the metrics endpoint
- [ ] `ruff check` passes on all modified Python files

---

## All Needed Context

### Research Phase Summary

- **Codebase patterns found**: Existing `get_stats()` in `db.py:407`, `is_distributed` guard in
  `handler.py:421-423`, `cleanup_old_jobs` time-window query pattern in `db.py:429-434`, Alpine.js
  `dashboard()` function in `dashboard.html:598`, auth header helper, tab pattern, CDN-loaded
  third-party JS (`js-yaml` in `admin.html:11`).
- **External research needed**: Yes — Chart.js + Alpine.js integration, PostgreSQL `generate_series`
  gap-filling, `PERCENTILE_CONT` syntax.
- **Knowledge gaps filled**: Module-closure Chart.js initialization pattern to avoid Alpine reactivity
  conflicts; `generate_series` LEFT JOIN for zero-filled time buckets; `FILTER` clause for
  single-scan conditional aggregation; `$nextTick` requirement before canvas initialization.

### Documentation & References

```yaml
- url: https://www.chartjs.org/docs/latest/developers/api.html
  section: "update(mode) and destroy()"
  critical: >
    Use chart.update('none') for data-only refreshes (fast, no animation).
    Call chart.destroy() before reusing a canvas. Never destroy + recreate
    when only data changes — mutate chart.data then update().

- url: https://janostlund.com/2024-02-11/integrating-chartjs-with-alpine
  section: "Module-closure chart instance pattern"
  critical: >
    Declare `let chart = null` OUTSIDE the Alpine `return {}` object.
    Storing the Chart instance inside x-data causes Alpine's Proxy wrapper
    to break Chart.js instanceof checks and produce render failures.

- url: https://www.chartjs.org/docs/latest/configuration/tooltip.html
  section: "callbacks.label, context.parsed.y"
  critical: >
    context.parsed.y is the raw numeric value. Return a formatted string
    from label() for custom tooltip content (e.g. duration formatting).

- url: https://popsql.com/learn-sql/postgresql/how-to-use-generate-series-to-avoid-gaps-in-data-in-postgresql
  section: "Hourly interval with LEFT JOIN"
  critical: >
    Without generate_series + LEFT JOIN, hours/days with zero completions
    are omitted — Chart.js then misaligns labels with data points. Always
    COALESCE(COUNT(j.id), 0) in the SELECT.

- url: https://popsql.com/learn-sql/postgresql/how-to-calculate-percentiles-in-postgresql
  section: "PERCENTILE_CONT with GROUP BY"
  critical: >
    Syntax: PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY column).
    Cannot use WHERE inside the aggregate — filter in the outer query.

- url: https://www.tigerdata.com/learn/understanding-filter-in-postgresql-with-examples
  section: "FILTER clause with COUNT"
  critical: >
    COUNT(*) FILTER (WHERE status = 'failed') performs conditional aggregation
    in a single table scan. Cast to NUMERIC before dividing for failure rate
    to avoid PostgreSQL integer-division truncation.

- url: https://alpinejs.dev/magics/nexttick
  critical: >
    Call `this.$nextTick(() => { chart = new Chart(...) })` when the canvas
    is inside an x-show block. Alpine DOM updates are async; Chart.js needs
    non-zero canvas dimensions at construction time.

- file: resources/daemon/db.py
  lines: 407-415
  why: Existing get_stats() — extend this method or add a parallel get_metrics()

- file: resources/daemon/db.py
  lines: 103-170
  why: Migration pattern — ALTER TABLE ... ADD COLUMN IF NOT EXISTS inside _init_db()

- file: resources/daemon/db.py
  lines: 424-438
  why: cleanup_old_jobs — shows NOW() - make_interval(days => %s) time-window pattern

- file: resources/daemon/handler.py
  lines: 421-427
  why: is_distributed guard pattern — copy verbatim for /api/metrics endpoint

- file: resources/daemon/handler.py
  lines: 507-508
  why: _get_stats() — minimal handler method pattern to mirror

- file: resources/daemon/routes.py
  lines: 56-72
  why: Route registration pattern — add /api/metrics and /metrics here

- file: resources/daemon/docs_ui.py
  lines: 103-109
  why: _load_dashboard_html() / _load_admin_html() — mirror for _load_metrics_html()

- file: resources/dashboard.html
  lines: 598-650
  why: Alpine.js dashboard() function structure, authHeaders(), refresh pattern

- file: resources/dashboard.html
  lines: 147-174
  why: Stat card grid + tab pattern to mirror in dashboard KPI strip

- file: resources/admin.html
  lines: 9-11
  why: CDN third-party JS loading precedent (js-yaml) — same pattern for Chart.js

- file: resources/daemon/worker.py
  lines: 156-175
  why: complete_job() call site — extend to pass input_size and output_size
```

### Current Codebase Tree (relevant paths)

```text
resources/
├── daemon/
│   ├── db.py           # PostgreSQLJobDatabase — get_stats(), _init_db(), migrations
│   ├── handler.py      # WebhookHandler — _get_stats(), _get_cluster_*, route dispatch
│   ├── routes.py       # _get_routes() / _post_routes() dicts
│   ├── docs_ui.py      # _load_dashboard_html(), _load_admin_html() page loaders
│   ├── worker.py       # ConversionWorker — _run_conversion_inner(), complete_job() call
│   └── server.py       # DaemonServer
├── dashboard.html      # Main dashboard UI (Alpine.js + Tailwind)
├── admin.html          # Admin UI
└── docs.html           # Inline docs served at /docs
tests/
├── test_handler.py     # Mock-based handler tests
├── test_cluster.py     # Live PostgreSQL tests (require TEST_DB_URL)
└── conftest.py         # job_db fixture (skips without TEST_DB_URL)
```

### Desired Codebase Tree (additions)

```text
resources/
├── daemon/
│   └── db.py           # MODIFIED: add get_metrics(), two new migration ALTER TABLEs,
│   │                   #           modify complete_job() to accept size params
│   └── handler.py      # MODIFIED: add _get_metrics_api(), _get_metrics_page()
│   └── routes.py       # MODIFIED: register /api/metrics, /metrics
│   └── docs_ui.py      # MODIFIED: add _load_metrics_html()
│   └── worker.py       # MODIFIED: capture os.path.getsize before/after, pass to complete_job()
├── metrics.html         # NEW: full metrics detail page (Alpine.js + Chart.js)
└── dashboard.html       # MODIFIED: add KPI strip widget, link to /metrics
tests/
└── test_metrics.py      # NEW: mock-based tests for _get_metrics_api()
docs/
└── metrics.md           # NEW: metrics feature documentation
```

### Known Gotchas

```python
# CRITICAL: Chart.js instance MUST live outside Alpine's reactive return {}
# Putting it inside causes Proxy wrapper to break Chart.js instanceof checks
let chart = None  # conceptually — in JS: let chart = null (module-closure scope)

# CRITICAL: Always use generate_series + LEFT JOIN for time-series queries
# Missing hours/days silently omit from results — misaligns Chart.js labels
# Use: COALESCE(COUNT(j.id), 0) after the LEFT JOIN

# CRITICAL: Cast to NUMERIC before division for failure rate
# COUNT(*) / COUNT(*) in PostgreSQL is integer division — always truncates to 0 or 1
# Use: COUNT(*) FILTER (WHERE status='failed')::NUMERIC / NULLIF(total, 0)

# CRITICAL: NULLIF(input_size_bytes, 0) in compression ratio — guards divide-by-zero

# CRITICAL: $nextTick() before new Chart() when canvas is inside x-show block
# Alpine DOM updates are async; Chart.js measures canvas dimensions at construction

# CRITICAL: Validate window param against allowlist before using in SQL
# Never interpolate user input into make_interval — use {'24h', '7d', '30d', 'all'}

# GOTCHA: complete_job() is called from worker.py — signature change must be
# backward-compatible (use keyword args with defaults: input_size=None, output_size=None)

# GOTCHA: is_distributed guard returns early with send_json_response(503, {...})
# Match this exact pattern from handler.py:421-423 for /api/metrics

# PERFORMANCE: Add composite index (status, completed_at) — PERCENTILE_CONT
# requires a sort; without an index this is a full table scan on large jobs tables

# GOTCHA: duration_seconds does NOT need a new column — compute from timestamps:
# EXTRACT(EPOCH FROM (completed_at - started_at)) AS duration_seconds
```

---

## Implementation Blueprint

### Data Models

```python
# New columns in jobs table (added via migration in db.py _init_db())
# input_size_bytes BIGINT  — os.path.getsize(input_path) before conversion
# output_size_bytes BIGINT — os.path.getsize(output_path) after conversion
# duration_seconds computed on-the-fly: EXTRACT(EPOCH FROM (completed_at - started_at))

# /api/metrics JSON response shape
{
    "available": True,           # False when not in distributed mode
    "window": "24h",             # requested window: "24h" | "7d" | "30d" | "all"
    "kpis": {
        "total_jobs": 1234,
        "completed": 1100,
        "failed": 45,
        "pending": 89,
        "running": 0,
        "failure_rate_pct": 3.93,
        "avg_duration_seconds": 142.5,
        "p95_duration_seconds": 412.0,
        "avg_compression_pct": 34.2,    # % reduction: (1 - out/in) * 100
        "throughput_per_hour": 5.2,
    },
    "timeseries": [
        {"bucket": "2026-04-24T00:00:00Z", "completed": 12, "failed": 1},
        ...
    ],
    "nodes": [
        {
            "node_id": "abc123",
            "node_name": "worker-01",
            "completed": 550,
            "failed": 20,
            "avg_duration_seconds": 138.0,
        },
        ...
    ],
}

# Unavailable response (HTTP 200)
{
    "available": False,
    "reason": "PostgreSQL is not configured. Set SMA_DAEMON_DB_URL ...",
    "docs_url": "/docs#cluster-metrics",
}
```

### Tasks

```yaml
Task 1 — DB Schema Migration (db.py _init_db):
  MODIFY resources/daemon/db.py:
    - FIND: last cur.execute(...ADD COLUMN...) in _init_db migration block (line ~155)
    - INJECT after it:
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
    - PRESERVE: all existing migration statements above

Task 2 — Extend complete_job() signature (db.py):
  MODIFY resources/daemon/db.py complete_job():
    - ADD parameters: input_size: int | None = None, output_size: int | None = None
    - EXTEND UPDATE SET clause to include input_size_bytes and output_size_bytes
      when not None (use conditional SQL building or always UPDATE with NULL-safe values)
    - PRESERVE: existing status='completed', completed_at=NOW() logic

Task 3 — Add get_metrics() to PostgreSQLJobDatabase (db.py):
  MODIFY resources/daemon/db.py:
    - ADD method get_metrics(window: str = "24h") -> dict
    - Validate window: map to SQL interval:
        WINDOW_MAP = {"24h": ("hour", 24), "7d": ("day", 7), "30d": ("day", 30), "all": None}
    - Run queries within a single with self._conn() as conn block:
        a) KPI query: COUNT by status, failure rate, AVG/P95 duration, avg compression
        b) Time-series query: generate_series + LEFT JOIN + COALESCE (zero-filled)
        c) Per-node query: GROUP BY node_id JOIN cluster_nodes for node_name
    - Return the dict structure defined in Data Models above
    - Return {"available": False, ...} when not self.is_distributed (though handler
      guards this — keep db method PostgreSQL-only for simplicity)

Task 4 — Capture file sizes in worker (worker.py):
  MODIFY resources/daemon/worker.py _run_conversion_inner():
    - FIND: the call to self.job_db.complete_job(job_id) (line ~166)
    - BEFORE the call: capture input_size using os.path.getsize(original_input_path)
      (the input path before conversion — store it at the start of the method)
    - AFTER conversion completes: capture output_size using os.path.getsize(output_path)
    - PASS both to complete_job(job_id, input_size=input_size, output_size=output_size)
    - HANDLE OSError from getsize gracefully — log warning, pass None (not a fatal error)
    - PRESERVE: all existing error handling, retry logic, and status updates

Task 5 — Add /api/metrics handler method (handler.py):
  MODIFY resources/daemon/handler.py:
    - ADD _get_metrics_api(self, _path, query) method:
        1. Guard: if not self.server.job_db.is_distributed → send_json_response(503, unavailable_dict)
        2. Parse window: query.get("window", ["24h"])[0], validate against allowlist
        3. Call self.server.job_db.get_metrics(window=window)
        4. send_json_response(200, result)
    - ADD _get_metrics_page(self, _path, _query) method:
        - Mirror _get_dashboard(): inject API key, serve _load_metrics_html()
    - ADD "/metrics" to PUBLIC_ENDPOINTS list at handler.py:37
      (metrics page itself is public; /api/metrics data checks is_distributed)

Task 6 — Register routes (routes.py):
  MODIFY resources/daemon/routes.py _get_routes():
    - ADD: "/metrics": lambda handler, path, query: handler._get_metrics_page(path, query)
    - ADD: "/api/metrics": lambda handler, path, query: handler._get_metrics_api(path, query)
    - PRESERVE: all existing route registrations

Task 7 — Add page loader (docs_ui.py):
  MODIFY resources/daemon/docs_ui.py:
    - ADD _load_metrics_html() function mirroring _load_dashboard_html()
    - IMPORT in handler.py alongside existing _load_dashboard_html import

Task 8 — Create metrics.html (new page):
  CREATE resources/metrics.html:
    - MIRROR structure from dashboard.html: same CDN links (Alpine.js, Tailwind)
    - ADD Chart.js CDN script tag:
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"></script>
    - IMPLEMENT Alpine.js function metrics() with:
        - State: window = '24h', loading = true, available = false, data = null
        - let chart_jobs = null, let chart_nodes = null, let chart_status = null (module-closure)
        - init(): fetch /api/metrics?window=24h, set available, call $nextTick → renderCharts()
        - changeWindow(w): mutate chart data, call chart.update('none') for all charts
        - renderCharts(data): initialize Chart instances on x-ref canvases
        - destroy(): chart_*.destroy() for all instances
    - IMPLEMENT three chart canvases:
        - x-ref="jobsCanvas" — line chart (jobs per hour/day time-series)
        - x-ref="nodesCanvas" — horizontal bar chart (per-node completed count)
        - x-ref="statusCanvas" — doughnut chart (pending/running/completed/failed)
    - IMPLEMENT KPI cards section (no charts — styled divs with x-text bindings):
        - P95 Duration (formatted HHh MMm SSs)
        - Average Compression Ratio (percentage)
        - Failure Rate (percentage)
        - Total Jobs (all-time count)
    - IMPLEMENT time window selector buttons (24h / 7d / 30d) with active styling
    - IMPLEMENT unavailable state panel (x-show="!loading && !available")
    - IMPLEMENT nav link back to dashboard (mirroring admin.html back-link pattern)
    - USE same Tailwind dark theme: bg-gray-900, text-gray-100, bg-gray-800 cards

Task 9 — Add KPI strip to dashboard.html:
  MODIFY resources/dashboard.html:
    - ADD metrics state to dashboard() Alpine function: metrics: null, metricsAvailable: false
    - EXTEND refresh() to also fetch /api/metrics?window=24h and store in this.metrics
    - ADD KPI strip section after the existing status cards (line ~147-158 area):
        Four cards: Jobs/hr (throughput), Avg Duration, Failure Rate, Avg Compression
        Only show when metricsAvailable is true (x-show)
    - ADD nav link to /metrics page in the header nav (alongside existing Admin/Docs links)
    - PRESERVE: all existing dashboard functionality and refresh cycle

Task 10 — Add tests (test_metrics.py):
  CREATE tests/test_metrics.py:
    - MIRROR test pattern from test_handler.py: use _make_server() and _make_handler()
    - TEST _get_metrics_api() with is_distributed=False → assert 503 response
    - TEST _get_metrics_api() with is_distributed=True → assert 200 with available=True
    - TEST invalid window param → assert it falls back to default or returns 400
    - TEST _get_metrics_page() → assert 200 HTML response
    - Mock job_db.get_metrics() to return fixture data

Task 11 — Documentation:
  CREATE docs/metrics.md:
    - Document the /api/metrics endpoint (params, response shape)
    - Document the /metrics UI page
    - Note: PostgreSQL-only requirement
    - Note: new columns added to jobs table
  UPDATE docs/daemon.md:
    - Add /api/metrics to the API endpoint reference table
    - Add mention of /metrics page in the UI section
  UPDATE resources/docs.html:
    - Add metrics section consistent with other daemon docs sections
```

### Per-Task Pseudocode

```python
# Task 3 — get_metrics() core SQL patterns

WINDOW_MAP = {
    "24h":  ("hour", 24),
    "7d":   ("day",  7),
    "30d":  ("day",  30),
    "all":  None,
}

def get_metrics(self, window: str = "24h") -> dict:
    config = WINDOW_MAP.get(window, WINDOW_MAP["24h"])

    with self._conn() as conn:
        with conn.cursor() as cur:

            # --- KPI query ---
            if config:
                unit, count = config
                interval_clause = f"AND completed_at >= NOW() - make_interval({unit}s => %s)"
                params = [count]
            else:
                interval_clause = ""
                params = []

            cur.execute(f"""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'completed')   AS completed,
                    COUNT(*) FILTER (WHERE status = 'failed')      AS failed,
                    COUNT(*) FILTER (WHERE status = 'pending')     AS pending,
                    COUNT(*) FILTER (WHERE status = 'running')     AS running,
                    COUNT(*) FILTER (WHERE status = 'cancelled')   AS cancelled,
                    COUNT(*)                                        AS total,
                    ROUND(
                        COUNT(*) FILTER (WHERE status = 'failed')::NUMERIC
                        / NULLIF(COUNT(*) FILTER (WHERE status IN ('completed','failed')), 0) * 100,
                    2)                                              AS failure_rate_pct,
                    AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))
                        FILTER (WHERE status = 'completed')        AS avg_duration_seconds,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (
                        ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
                    )                                               AS p95_duration_seconds,
                    AVG(1.0 - output_size_bytes::FLOAT / NULLIF(input_size_bytes, 0)) * 100
                        FILTER (WHERE status = 'completed'
                            AND input_size_bytes > 0
                            AND output_size_bytes IS NOT NULL)      AS avg_compression_pct
                FROM jobs
                WHERE 1=1 {interval_clause}
            """, params)
            kpi_row = cur.fetchone()

            # --- Time-series query (zero-filled) ---
            if config:
                unit, count = config
                # generate_series + LEFT JOIN pattern (see docs above)
                cur.execute(f"""
                    WITH buckets AS (
                        SELECT generate_series(
                            date_trunc(%s, NOW()) - make_interval({unit}s => %s - 1),
                            date_trunc(%s, NOW()),
                            make_interval({unit}s => 1)
                        ) AS bucket
                    )
                    SELECT
                        b.bucket,
                        COALESCE(COUNT(j.id) FILTER (WHERE j.status = 'completed'), 0) AS completed,
                        COALESCE(COUNT(j.id) FILTER (WHERE j.status = 'failed'), 0)    AS failed
                    FROM buckets b
                    LEFT JOIN jobs j
                        ON date_trunc(%s, j.completed_at) = b.bucket
                       AND j.status IN ('completed', 'failed')
                    GROUP BY b.bucket
                    ORDER BY b.bucket
                """, [unit, count, unit, unit])
                timeseries = [
                    {"bucket": row["bucket"].isoformat(), "completed": row["completed"], "failed": row["failed"]}
                    for row in cur.fetchall()
                ]
            else:
                timeseries = []  # all-time: no time-series buckets

            # --- Per-node query ---
            cur.execute("""
                SELECT
                    j.node_id,
                    COALESCE(n.node_name, j.node_id)               AS node_name,
                    COUNT(*) FILTER (WHERE j.status = 'completed') AS completed,
                    COUNT(*) FILTER (WHERE j.status = 'failed')    AS failed,
                    AVG(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)))
                        FILTER (WHERE j.status = 'completed')      AS avg_duration_seconds
                FROM jobs j
                LEFT JOIN cluster_nodes n ON n.node_id = j.node_id
                GROUP BY j.node_id, n.node_name
                ORDER BY completed DESC
            """)
            nodes = list(cur.fetchall())

    return {
        "available": True,
        "window": window,
        "kpis": dict(kpi_row),
        "timeseries": timeseries,
        "nodes": [dict(n) for n in nodes],
    }
```

```python
# Task 4 — worker.py: file size capture at completion

def _run_conversion_inner(self, job_id, path, ...):
    input_size = None
    try:
        input_size = os.path.getsize(path)  # capture BEFORE conversion
    except OSError:
        logger.warning("Could not get input file size for job %s", job_id)

    # ... existing conversion logic ...

    output_size = None
    try:
        output_size = os.path.getsize(output_path)  # capture AFTER conversion
    except OSError:
        logger.warning("Could not get output file size for job %s", job_id)

    # EXISTING call — extend signature only
    self.job_db.complete_job(job_id, input_size=input_size, output_size=output_size)
```

```javascript
// Task 8 — metrics.html: Chart.js + Alpine.js integration (module-closure pattern)
// CRITICAL: let chart_* declared OUTSIDE return {} to avoid Alpine Proxy conflicts

function metrics() {
    let chart_jobs   = null;
    let chart_nodes  = null;
    let chart_status = null;

    function formatDuration(s) {
        if (!s) return "—";
        const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
        if (h > 0) return `${h}h ${m}m ${sec}s`;
        if (m > 0) return `${m}m ${sec}s`;
        return `${sec}s`;
    }

    function chartColors() {
        // resolve at call time — picks up active Tailwind theme
        const st = getComputedStyle(document.documentElement);
        return {
            text:   st.getPropertyValue("--color-gray-200").trim()  || "#e5e7eb",
            grid:   st.getPropertyValue("--color-gray-700").trim()  || "#374151",
            border: st.getPropertyValue("--color-gray-600").trim()  || "#4b5563",
        };
    }

    return {
        window: "24h",
        loading: true,
        available: false,
        unavailableReason: "",
        data: null,

        async init() {
            await this.load();
        },

        async load() {
            this.loading = true;
            const res  = await fetch(`/api/metrics?window=${this.window}`, { headers: authHeaders() });
            const json = await res.json();
            this.loading   = false;
            this.available = json.available;
            if (!json.available) {
                this.unavailableReason = json.reason;
                return;
            }
            this.data = json;
            // $nextTick ensures x-show has made canvas visible before Chart.js measures it
            this.$nextTick(() => this.renderOrUpdate(json));
        },

        async changeWindow(w) {
            this.window = w;
            await this.load();
        },

        renderOrUpdate(json) {
            const c = chartColors();
            const baseOpts = {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { labels: { color: c.text } },
                    tooltip: { backgroundColor: "#1f2937", titleColor: c.text, bodyColor: c.text },
                },
                scales: {
                    x: { ticks: { color: c.text }, grid: { color: c.grid } },
                    y: { ticks: { color: c.text }, grid: { color: c.grid }, beginAtZero: true },
                },
            };

            // --- Jobs line chart ---
            const jobsData = {
                labels:   json.timeseries.map(r => r.bucket),
                datasets: [
                    { label: "Completed", data: json.timeseries.map(r => r.completed),
                      borderColor: "#6366f1", backgroundColor: "#6366f133", fill: true, tension: 0.3 },
                    { label: "Failed",    data: json.timeseries.map(r => r.failed),
                      borderColor: "#ef4444", backgroundColor: "#ef444433", fill: true, tension: 0.3 },
                ],
            };
            if (!chart_jobs) {
                chart_jobs = new Chart(this.$refs.jobsCanvas, { type: "line", data: jobsData, options: { ...baseOpts, plugins: { ...baseOpts.plugins, tooltip: { ...baseOpts.plugins.tooltip, callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}` } } } } });
            } else {
                chart_jobs.data = jobsData;
                chart_jobs.update("none");
            }

            // --- Per-node bar chart ---
            const nodeData = {
                labels:   json.nodes.map(n => n.node_name),
                datasets: [{ label: "Completed", data: json.nodes.map(n => n.completed), backgroundColor: "#6366f1" }],
            };
            const barOpts = { ...baseOpts, indexAxis: "y" };
            if (!chart_nodes) {
                chart_nodes = new Chart(this.$refs.nodesCanvas, { type: "bar", data: nodeData, options: barOpts });
            } else {
                chart_nodes.data = nodeData;
                chart_nodes.update("none");
            }

            // --- Status doughnut ---
            const kpi = json.kpis;
            const statusData = {
                labels:   ["Completed", "Failed", "Pending", "Running", "Cancelled"],
                datasets: [{ data: [kpi.completed, kpi.failed, kpi.pending, kpi.running, kpi.cancelled],
                             backgroundColor: ["#22c55e", "#ef4444", "#f59e0b", "#6366f1", "#6b7280"] }],
            };
            const doughnutOpts = { responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: "right", labels: { color: c.text } },
                           tooltip: { backgroundColor: "#1f2937", titleColor: c.text, bodyColor: c.text } } };
            if (!chart_status) {
                chart_status = new Chart(this.$refs.statusCanvas, { type: "doughnut", data: statusData, options: doughnutOpts });
            } else {
                chart_status.data = statusData;
                chart_status.update("none");
            }
        },

        destroy() {
            [chart_jobs, chart_nodes, chart_status].forEach(c => { if (c) c.destroy(); });
            chart_jobs = chart_nodes = chart_status = null;
        },

        fmt: formatDuration,
    };
}
```

### Integration Points

```yaml
DATABASE:
  - migration: "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS input_size_bytes BIGINT"
  - migration: "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS output_size_bytes BIGINT"
  - index: "CREATE INDEX IF NOT EXISTS idx_jobs_status_completed ON jobs(status, completed_at)"
  - location: resources/daemon/db.py _init_db() after line 155

API/ROUTES:
  - add to: resources/daemon/routes.py _get_routes()
  - new routes: "/metrics" (HTML page), "/api/metrics" (JSON data)
  - auth: "/metrics" added to PUBLIC_ENDPOINTS (handler.py:37); /api/metrics guards
    internally via is_distributed check, consistent with /cluster/logs pattern
  - window param allowlist: {"24h", "7d", "30d", "all"} — validate in handler

FRONTEND:
  - new page: resources/metrics.html
  - CDN: Chart.js 4.5.1 (chart.umd.min.js) via jsDelivr
  - modify: resources/dashboard.html — add metrics KPI strip + nav link to /metrics
  - Alpine.js pattern: module-closure chart instances, x-ref canvases, $nextTick before render
  - Tailwind dark theme: consistent with existing dashboard (bg-gray-900, gray-800 cards)
```

---

## Validation Loop

### Level 1: Syntax & Style

```bash
source venv/bin/activate && ruff check resources/daemon/db.py resources/daemon/handler.py \
    resources/daemon/worker.py resources/daemon/routes.py resources/daemon/docs_ui.py --fix

source venv/bin/activate && ruff check tests/test_metrics.py --fix
```

### Level 2: Tests

```bash
# Run all existing tests first — must stay green
source venv/bin/activate && python -m pytest tests/ -q --tb=short

# Run new metrics tests specifically
source venv/bin/activate && python -m pytest tests/test_metrics.py -v --tb=short

# Run with live PostgreSQL (optional — requires TEST_DB_URL env var)
TEST_DB_URL="postgresql://user:pass@localhost/sma_test" \
    source venv/bin/activate && python -m pytest tests/test_metrics.py tests/test_cluster.py -v --tb=short
```

### Level 3: Manual Verification

```bash
# Start daemon in cluster mode
source venv/bin/activate && python daemon.py --host 0.0.0.0 --port 8585

# Verify /api/metrics returns structured response (PostgreSQL connected)
curl -s http://localhost:8585/api/metrics?window=24h | python -m json.tool

# Verify unavailable response (no PostgreSQL)
curl -s http://localhost:8585/api/metrics | python -m json.tool  # expect available: false

# Open browser: verify /metrics page loads and charts render
open http://localhost:8585/metrics

# Verify dashboard KPI strip appears at /dashboard
open http://localhost:8585/dashboard
```

---

## Final Validation Checklist

- [ ] All existing tests pass: `source venv/bin/activate && python -m pytest tests/ -q`
- [ ] No ruff errors: `ruff check resources/daemon/ tests/test_metrics.py`
- [ ] `/api/metrics?window=24h` returns `{"available": true, "kpis": {...}, "timeseries": [...], "nodes": [...]}`
- [ ] `/api/metrics` returns `{"available": false}` in standalone mode
- [ ] `/metrics` page loads, all three charts render, window selector updates charts
- [ ] Dashboard `/dashboard` shows KPI strip (only when PostgreSQL connected)
- [ ] Dashboard `/dashboard` shows nav link to `/metrics`
- [ ] `input_size_bytes` and `output_size_bytes` populated on completed jobs in DB
- [ ] `OSError` from `os.path.getsize` is caught and logged — does not fail the job
- [ ] Invalid `window` param (e.g. `?window=999d`) falls back to default `24h`
- [ ] Documentation updated: `docs/metrics.md`, `docs/daemon.md`, `resources/docs.html`

---

## Anti-Patterns to Avoid

- Do not store the Chart.js instance inside Alpine's `x-data` reactive object
- Do not interpolate user-supplied `window` values directly into SQL `INTERVAL` expressions
- Do not use integer division for failure rate or compression ratio calculations
- Do not omit `generate_series` + LEFT JOIN from time-series queries
- Do not call `new Chart()` without `$nextTick` when canvas is inside an `x-show` block
- Do not change `complete_job()` in a backward-incompatible way — use keyword args with defaults
- Do not add a new `duration_seconds` column — compute it from existing timestamps
- Do not skip the `is_distributed` guard on `/api/metrics`

---

## Task Breakdown Reference

See [docs/tasks/cluster-metrics.md](../tasks/cluster-metrics.md) for the detailed implementation task breakdown.

**Critical path**: T-001 (DB migration) → T-003 (get_metrics) → T-004 (handler/routes) → T-005 (metrics.html)
**Parallel after T-004**: T-006 (dashboard KPI strip) and T-007 (tests) can run simultaneously.

---

## Confidence Score: 9/10

High confidence for one-pass implementation. All patterns exist in the codebase and are
well-documented. The only uncertainty is the exact location of `output_path` in the worker's
conversion pipeline — the implementer must verify the variable name holding the final output
file path at the point `complete_job()` is called, as it may differ from the initial `path`
argument depending on the conversion flow.
