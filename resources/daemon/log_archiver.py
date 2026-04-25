import gzip
import json
import os
import time
from collections import defaultdict


class LogArchiver:
  """Archives aged cluster log rows from PostgreSQL to gzipped JSONL files."""

  def __init__(self, archive_dir, archive_after_days, delete_after_days, logger):
    self._archive_dir = archive_dir
    self._archive_after_days = archive_after_days
    self._delete_after_days = delete_after_days
    self._log = logger

  def run(self, job_db):
    """Archive old DB log rows to filesystem, then prune old archive files."""
    try:
      archived = self._archive_from_db(job_db)
      if archived:
        self._log.info("LogArchiver: archived %d log rows to %s" % (archived, self._archive_dir))
    except Exception:
      self._log.exception("LogArchiver: error during DB archival")
    if self._delete_after_days > 0:
      try:
        pruned = self._prune_old_files()
        if pruned:
          self._log.info("LogArchiver: pruned %d old archive file(s)" % pruned)
      except Exception:
        self._log.exception("LogArchiver: error during archive file pruning")

  def _archive_from_db(self, job_db):
    """Fetch aged DB rows, write .gz files, delete DB rows on success."""
    records = job_db.get_logs_for_archival(self._archive_after_days)
    if not records:
      return 0

    groups = defaultdict(list)
    for r in records:
      ts = r["timestamp"]
      date = ts.date() if hasattr(ts, "date") else ts
      groups[(r["node_id"], date)].append(r)

    all_written = True
    for (node_id, date), recs in groups.items():
      if not self._write_archive(node_id, date, recs):
        all_written = False
        self._log.warning("LogArchiver: write failed for %s/%s — skipping DB deletion" % (node_id, date))

    if all_written:
      return job_db.delete_logs_before(self._archive_after_days)
    return 0

  def _write_archive(self, node_id, date, records):
    """Write records to <archive_dir>/<node_id>/<YYYY-MM-DD>.jsonl.gz atomically."""
    node_dir = os.path.join(self._archive_dir, node_id)
    os.makedirs(node_dir, exist_ok=True)
    filename = "%s.jsonl.gz" % date.isoformat()
    final_path = os.path.join(node_dir, filename)
    tmp_path = final_path + ".tmp"
    try:
      with gzip.open(tmp_path, "wt", encoding="utf-8") as f:
        for r in records:
          row = dict(r)
          ts = row.get("timestamp")
          if ts is not None and hasattr(ts, "isoformat"):
            row["timestamp"] = ts.isoformat()
          f.write(json.dumps(row) + "\n")
      os.replace(tmp_path, final_path)
      return True
    except Exception as e:
      self._log.warning("LogArchiver: failed to write %s: %s" % (final_path, e))
      try:
        os.unlink(tmp_path)
      except OSError:
        pass
      return False

  def _prune_old_files(self):
    """Delete .gz archive files older than delete_after_days. Returns count deleted."""
    if not os.path.isdir(self._archive_dir):
      return 0
    cutoff = time.time() - self._delete_after_days * 86400
    deleted = 0
    for node_entry in os.scandir(self._archive_dir):
      if not node_entry.is_dir():
        continue
      for file_entry in os.scandir(node_entry.path):
        if not file_entry.name.endswith(".jsonl.gz"):
          continue
        try:
          if os.path.getmtime(file_entry.path) < cutoff:
            os.unlink(file_entry.path)
            deleted += 1
        except OSError:
          pass
    return deleted
