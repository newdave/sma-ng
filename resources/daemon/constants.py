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


def resolve_node_id():
  """Return the stable cluster node identifier for this daemon instance."""
  return os.environ.get("SMA_NODE_NAME", "").strip() or socket.gethostname()
