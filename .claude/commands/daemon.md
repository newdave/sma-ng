# Start Daemon Server

Start the HTTP webhook server for triggering conversions via API.

Usage: /project:daemon [options]

Options can include: --host, --port, --workers, --config

```bash
python daemon.py $ARGUMENTS
```

Default: listens on 127.0.0.1:8585 with 2 worker threads.

Examples:
- `/project:daemon` - Start with defaults
- `/project:daemon --host 0.0.0.0` - Listen on all interfaces
- `/project:daemon --port 9000 --workers 4` - Custom port and workers
