import os

# Go up two levels: resources/daemon/ → resources/ → project root
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DAEMON_CONFIG = os.path.join(SCRIPT_DIR, "config", "daemon.json")
DEFAULT_PROCESS_CONFIG = os.path.join(SCRIPT_DIR, "config", "autoProcess.ini")
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
