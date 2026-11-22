"""File helpers shared by MediaProcessor and RepairProcessor.

Both write a finished file into the library: apply the configured output
permissions, then move it over the destination without leaving a partial file
behind. The mixin needs ``self.settings.permissions`` and ``self.log``.
"""

import os
import shutil


class FileOpsMixin:
  # Generic permission setter
  def setPermissions(self, path):
    """
    Apply chmod and chown to a file using values from settings.

    Uses the chmod, uid, and gid values in settings.permissions. On Windows
    (os.name == 'nt') the chown call is skipped. Logs an exception if
    permissions cannot be applied but does not raise.
    """
    try:
      if os.path.exists(path):
        os.chmod(path, self.settings.permissions.get("chmod", int("0664", 8)))
        if os.name != "nt":
          os.chown(path, self.settings.permissions.get("uid", -1), self.settings.permissions.get("gid", -1))
      else:
        self.log.debug("File %s does not exist, unable to set permissions." % path)
    except Exception:
      self.log.exception("Unable to set new file permissions.")

  # Copy src to dst atomically: write to a temp file then rename into place
  def _atomic_copy(self, src, dst):
    """
    Copy src to dst atomically by writing to a temp file then renaming it into place.

    Uses shutil.copy2 to preserve metadata, applies permissions to the temp
    file, then calls os.replace for an atomic rename. Cleans up the temp file
    if any step fails before re-raising the exception.
    """
    dst_tmp = dst + ".smatmp"
    try:
      shutil.copy2(src, dst_tmp)
      self.setPermissions(dst_tmp)
      os.replace(dst_tmp, dst)
    except Exception:
      try:
        if os.path.exists(dst_tmp):
          os.remove(dst_tmp)
      except Exception:
        pass
      raise

  # Move src to dst atomically: rename if same filesystem, else atomic copy + delete
  def _atomic_move(self, src, dst):
    """
    Move src to dst atomically.

    Attempts os.replace first (instant on the same filesystem, and replaces an
    existing dst on every platform). Falls back to _atomic_copy followed by
    os.remove when a cross-device OSError is raised.
    """
    try:
      os.replace(src, dst)
    except OSError:
      self._atomic_copy(src, dst)
      os.remove(src)
