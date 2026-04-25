import os
import socket

# Go up two levels: resources/daemon/ → resources/ → project root
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_PROCESS_CONFIG = os.path.join(SCRIPT_DIR, "config", "sma-ng.yml")
DAEMON_SECTION = "daemon"
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

_node_id_cache: str | None = None

SECRET_KEYS: frozenset = frozenset({"api_key", "db_url", "username", "password", "node_id"})


def set_node_id_cache(value: str) -> None:
  """Store the resolved node identity so resolve_node_id() returns it without re-deriving."""
  global _node_id_cache
  _node_id_cache = value


def resolve_node_id() -> str:
  """Return the stable cluster node identifier for this daemon instance."""
  if _node_id_cache:
    return _node_id_cache
  return os.environ.get("SMA_NODE_NAME", "").strip() or socket.gethostname()
