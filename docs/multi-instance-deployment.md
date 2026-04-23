# Multi-Instance Deployment

This guide covers running multiple `sma-ng` daemon instances on one host or across multiple hosts.

The key requirement is a shared PostgreSQL database. When `db_url` points at the same PostgreSQL instance, all SMA-NG nodes coordinate through the job table and heartbeat system:

- pending jobs are visible to every node
- only one node can claim a given job
- stale running jobs are requeued if a node disappears
- `/status` shows the full cluster, not just the local process

## Deployment Models

### 1. One host, one instance

Use this when you just want a single daemon with one worker pool.

```text
Host A
└─ sma-ng daemon
   └─ PostgreSQL
```

### 2. One host, multiple instances

Use this when one machine needs separate SMA daemons, usually because:

- different media trees need different path mappings
- different ports or API keys are required
- you want process isolation between workloads
- you want to dedicate different instances to different GPUs or configs

```text
Host A
├─ sma-ng-tv       -> port 8585
├─ sma-ng-movies   -> port 8586
└─ PostgreSQL
```

### 3. Multiple hosts, shared cluster

Use this when you want to scale out conversion work across multiple machines.

```text
Host A               Host B               Host C
├─ sma-ng daemon     ├─ sma-ng daemon     ├─ sma-ng daemon
└─ shared mounts?    └─ shared mounts?    └─ shared mounts?
         \               |               /
          \              |              /
           └──── shared PostgreSQL ────┘
```

## Non-Negotiable Requirements

For multi-instance or multi-host setups:

- every instance must use the same PostgreSQL database via `db_url`
- every instance must have access to the same effective media paths for any jobs it can claim
- path rewriting must normalize host-specific mount differences
- API keys and daemon ports must not conflict on the same host
- each instance should have its own config directory and logs directory unless intentional sharing is required

If two nodes see the same file under different paths, fix that first with `path_rewrites` and consistent mount layout.

## Shared PostgreSQL

Set the same database URL on every node:

```json
{
  "db_url": "postgresql://sma:password@db-host:5432/sma"
}
```

Or with environment variables:

```bash
SMA_DAEMON_DB_URL=postgresql://sma:password@db-host:5432/sma
```

### Recommended PostgreSQL Placement

- small single-host setup: PostgreSQL on the same machine
- multi-host cluster: PostgreSQL on a stable host or managed service
- avoid SQLite for clustered deployments; it is not the distributed backend

## Path Strategy

The safest clustered layout is for all nodes to see the same media roots under the same paths:

```text
/mnt/media/TV
/mnt/media/Movies
/downloads
```

If that is not possible, normalize incoming paths with `path_rewrites`:

```json
{
  "path_rewrites": [
    {"from": "/srv/media", "to": "/mnt/media"},
    {"from": "/volume1/media", "to": "/mnt/media"}
  ]
}
```

This is especially important when:

- Sonarr/Radarr webhooks come from a different host than SMA-NG
- one machine uses local disks and another uses NFS/SMB mounts
- Docker containers see different inside-container paths than the host

## One Host, Multiple Instances

On a single machine, run each instance with its own:

- `daemon.json`
- `daemon.env`
- `autoProcess.ini` set
- port
- logs directory
- systemd unit name or container name

Example layout:

```text
/opt/sma-tv/
├─ config/
│  ├─ autoProcess.ini
│  ├─ daemon.json
│  └─ daemon.env
└─ logs/

/opt/sma-movies/
├─ config/
│  ├─ autoProcess.ini
│  ├─ daemon.json
│  └─ daemon.env
└─ logs/
```

Example `daemon.json` values:

```json
{
  "default_config": "/opt/sma-tv/config/autoProcess.ini",
  "api_key": "tv-secret",
  "db_url": "postgresql://sma:password@127.0.0.1:5432/sma",
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "/opt/sma-tv/config/autoProcess.ini"}
  ]
}
```

```json
{
  "default_config": "/opt/sma-movies/config/autoProcess.ini",
  "api_key": "movies-secret",
  "db_url": "postgresql://sma:password@127.0.0.1:5432/sma",
  "path_configs": [
    {"path": "/mnt/media/Movies", "config": "/opt/sma-movies/config/autoProcess.ini"}
  ]
}
```

Start them on different ports:

```bash
python daemon.py --port 8585 --daemon-config /opt/sma-tv/config/daemon.json --logs-dir /opt/sma-tv/logs
python daemon.py --port 8586 --daemon-config /opt/sma-movies/config/daemon.json --logs-dir /opt/sma-movies/logs
```

## Multiple Hosts

For multiple hosts, keep these values aligned:

- shared `db_url`
- compatible `path_configs`
- identical API behavior and webhook expectations

Typical pattern:

1. mount media on every node
2. deploy the same code version everywhere
3. give each node its own `daemon.json` and `daemon.env`
4. point all nodes at the same PostgreSQL
5. set per-node `ffmpeg_dir`, GPU config, and worker count as needed

Host-specific differences usually belong in:

- mount paths plus `path_rewrites`
- `ffmpeg_dir`
- hardware-specific `autoProcess.ini`
- worker count

## Worker Count and Capacity Planning

Each daemon process has its own `--workers` pool.

Examples:

- 3 hosts × 2 workers each = up to 6 concurrent conversions cluster-wide
- 1 host with 2 daemon instances × 3 workers each = up to 6 concurrent conversions on that host

Be realistic about hardware contention:

- software encodes compete for CPU
- QSV instances compete for the same iGPU
- NVENC sessions compete for the same NVIDIA card
- too many workers will usually reduce throughput, not improve it

Start conservative:

- software: `1-2` workers per host
- Intel QSV: `1-2` workers per iGPU
- NVIDIA: depends on model and session limits; start with `1-2`

## Logging

Each instance should have its own `logs_dir`.

On one host, do not point multiple independent daemons at the same logs directory unless you explicitly want mixed daemon-level logs. Per-config logs are named from config stems, so separate directories keep the log API and dashboard cleaner.

Recommended:

```text
/opt/sma-tv/logs
/opt/sma-movies/logs
/opt/sma-node-a/logs
/opt/sma-node-b/logs
```

## Systemd Pattern

For multiple local instances, create one service unit per instance or a templated unit.

Simple pattern:

```ini
[Service]
EnvironmentFile=/opt/sma-tv/config/daemon.env
ExecStart=/opt/sma/venv/bin/python /opt/sma/daemon.py \
  --port 8585 \
  --daemon-config /opt/sma-tv/config/daemon.json \
  --logs-dir /opt/sma-tv/logs
```

Repeat with different paths and ports for each instance.

## Docker / Compose Pattern

For Docker-based clusters:

- run one compose project per host
- point every `sma-ng` container at the same PostgreSQL if clustering across hosts
- publish a distinct port per instance on the same host
- mount the same media paths into the container on every node
- set a distinct `SMA_NODE_NAME` for every daemon instance; the daemon uses
  that value from `daemon.env` as its cluster node ID

If you want a quick starting point, see [Docker Compose Quick Start](docker-compose-quickstart.md).

## Verification Checklist

After bringing nodes up:

1. `GET /health` on each node should succeed
2. `GET /status` should show all nodes when PostgreSQL is enabled
3. submit one job and verify exactly one node claims it
4. stop one node mid-job and confirm stale recovery works after `stale_seconds`
5. check `/logs` and `/jobs/<id>` on each instance

## Common Failure Modes

### Jobs never get picked up

- `db_url` not set or not shared
- PostgreSQL connectivity broken
- all nodes excluded by locked configs or no workers available

### Jobs fail only on some nodes

- mismatched mount paths
- missing FFmpeg binaries or GPU access on one host
- different config files or stale deployment version

### Same webhook path works on one node but not another

- inconsistent `path_rewrites`
- different container bind mounts
- different `path_configs`

### Dashboard only shows one node

- instance is not using PostgreSQL mode
- heartbeat not reaching the database
- multiple Docker daemon containers are sharing the same hostname / node ID
- nodes are using different `db_url` values

## Minimal Multi-Host Example

Node A and Node B both run:

```json
{
  "default_config": "/config/autoProcess.ini",
  "db_url": "postgresql://sma:password@db.example.com:5432/sma",
  "path_rewrites": [
    {"from": "/downloads", "to": "/mnt/downloads"}
  ],
  "path_configs": [
    {"path": "/mnt/media/TV", "config": "/config/autoProcess.tv.ini"},
    {"path": "/mnt/media/Movies", "config": "/config/autoProcess.movies.ini"}
  ]
}
```

Node A:

```bash
python daemon.py --host 0.0.0.0 --port 8585 --workers 2
```

Node B:

```bash
python daemon.py --host 0.0.0.0 --port 8585 --workers 2
```

Both nodes will participate in the same queue as long as the media paths resolve correctly on both machines.
