import os
import socket

# Go up two levels: resources/daemon/ → resources/ → project root
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_PROCESS_CONFIG = os.path.join(SCRIPT_DIR, "config", "sma-ng.yml")
DAEMON_SECTION = "daemon"
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
# Canonical location for per-failure ffmpeg stderr sidecars. Overridable
# via daemon.ffmpeg-stderr-dir in sma-ng.yml. Kept under LOGS_DIR so it
# rotates/archives alongside the daemon log instead of contaminating
# /transcodes with diagnostic files.
FFMPEG_STDERR_DIR = os.path.join(LOGS_DIR, "ffmpeg-stderr")
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# Library-audit finding lifecycle (see resources/library_audit/).
STATUS_OPEN = "open"
STATUS_ACKED = "acked"
STATUS_DISMISSED = "dismissed"
STATUS_RESOLVED = "resolved"

# Library-audit queue-row lifecycle.
AUDIT_UNIT_PENDING = "pending"
AUDIT_UNIT_CLAIMED = "claimed"
AUDIT_UNIT_DONE = "done"
AUDIT_UNIT_ERROR = "error"

# Library-audit run lifecycle.
AUDIT_RUN_QUEUED = "queued"
AUDIT_RUN_ENUMERATING = "enumerating"
AUDIT_RUN_PROBING = "probing"
AUDIT_RUN_COMPLETED = "completed"
AUDIT_RUN_FAILED = "failed"

_node_id_cache: str | None = None

SECRET_KEYS: frozenset = frozenset({"api_key", "db_url", "username", "password", "node_id"})

# Per-instance service secret fields. _strip_secrets walks
# data["services"][<type>][<instance>] and redacts each of these.
SERVICE_SECRET_FIELDS: frozenset = frozenset({"apikey", "token", "password"})


def set_node_id_cache(value: str | None) -> None:
  """Store the resolved node identity so resolve_node_id() returns it without re-deriving."""
  global _node_id_cache
  _node_id_cache = value


def resolve_node_id() -> str:
  """Return the stable cluster node identifier for this daemon instance."""
  if _node_id_cache:
    return _node_id_cache
  return socket.gethostname()
