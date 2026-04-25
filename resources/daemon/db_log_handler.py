import logging
import threading


class PostgreSQLLogHandler(logging.Handler):
  """Buffered log handler that writes to the cluster logs table."""

  def __init__(self, db, node_id: str, batch_size: int = 50):
    super().__init__()
    self._db = db
    self._node_id = node_id
    self._batch_size = batch_size
    self._batch: list[dict] = []
    self._lock = threading.Lock()

  def emit(self, record: logging.LogRecord) -> None:
    try:
      entry = {
        "node_id": self._node_id,
        "level": record.levelname,
        "logger": record.name,
        "message": self.format(record),
      }
      with self._lock:
        self._batch.append(entry)
        if len(self._batch) >= self._batch_size:
          self._flush_locked()
    except Exception:
      pass  # NEVER raise from emit()

  def flush(self) -> None:
    try:
      with self._lock:
        self._flush_locked()
    except Exception:
      pass

  def _flush_locked(self) -> None:
    if not self._batch:
      return
    batch, self._batch = self._batch, []
    try:
      self._db.insert_logs(batch)
    except Exception:
      pass

  def close(self) -> None:
    self.flush()
    super().close()
