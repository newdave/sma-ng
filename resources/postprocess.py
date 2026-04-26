"""Post-processing script runner that executes scripts in the ``post_process/`` directory."""

import json
import logging
import os
from subprocess import PIPE, Popen

from resources.extensions import bad_post_extensions, bad_post_files
from resources.metadata import MediaType


class PostProcessor:
  """Discovers and runs scripts from the ``post_process/`` directory after a conversion.

  Scripts are executed in sorted order with conversion output paths exposed
  via the ``SMA_FILES`` environment variable (JSON-encoded).
  """

  def __init__(self, files, logger=None, wait=False):
    """Initialise the post-processor and gather runnable scripts.

    Args:
        files: List of output file paths from the conversion, serialised
            into the ``SMA_FILES`` environment variable for scripts.
        logger: Optional logger instance. Defaults to the module logger.
        wait: If ``True``, wait for each script process to exit before
            starting the next one.
    """
    self.log = logger or logging.getLogger(__name__)

    self.log.debug("Output: %s." % files)

    self.set_script_environment(files)
    self.scripts = self.gather_scripts()
    self.wait = wait

  def set_script_environment(self, files):
    """Copy the current OS environment and inject ``SMA_FILES``.

    Args:
        files: List of output file paths to serialise into the environment.
    """
    self.log.debug("Setting script environment.")
    self.post_process_environment = os.environ.copy()
    self.post_process_environment["SMA_FILES"] = json.dumps(files)

  def gather_scripts(self):
    """Collect executable scripts from the ``post_process/`` directory.

    Skips files with extensions in ``bad_post_extensions``, subdirectories,
    and filenames listed in ``bad_post_files``.

    Returns:
        Sorted list of absolute paths to runnable scripts.
    """
    self.log.debug("Gathering scripts.")
    current_directory = os.path.dirname(os.path.realpath(__file__))
    post_process_directory = os.path.join(current_directory, "../post_process")
    scripts = []
    for script in sorted(os.listdir(post_process_directory)):
      if os.path.splitext(script)[1] in bad_post_extensions or os.path.isdir(os.path.join(post_process_directory, script)) or script in bad_post_files:
        self.log.debug("Skipping %s." % script)
        continue
      else:
        self.log.debug("Script added: %s." % script)
        scripts.append(os.path.join(post_process_directory, script))
    return scripts

  def setEnv(self, mediatype, tmdbid, season=None, episode=None):
    """Set media-type-specific metadata environment variables.

    Dispatches to :meth:`setTV` or :meth:`setMovie` based on ``mediatype``.

    Args:
        mediatype: A ``MediaType`` enum value indicating TV or Movie.
        tmdbid: TMDB identifier for the title.
        season: Season number (TV only).
        episode: Episode number or list of episode numbers (TV only).
    """
    if mediatype == MediaType.TV:
      self.setTV(tmdbid, season, episode)
    elif mediatype == MediaType.Movie:
      self.setMovie(tmdbid)

  def setTV(self, tmdbid, season, episode):
    """Set TV-specific environment variables for post-process scripts.

    Args:
        tmdbid: TMDB series identifier.
        season: Season number.
        episode: Single episode number or list of episode numbers for
            multi-episode files. When a list, ``SMA_EPISODE`` is the first
            element and ``SMA_EPISODES`` is a comma-separated string.
    """
    self.log.debug("Setting TV metadata.")
    self.post_process_environment["SMA_TMDBID"] = str(tmdbid)
    self.post_process_environment["SMA_SEASON"] = str(season)
    if isinstance(episode, list):
      self.post_process_environment["SMA_EPISODE"] = str(episode[0])
      self.post_process_environment["SMA_EPISODES"] = ",".join(str(e) for e in episode)
    else:
      self.post_process_environment["SMA_EPISODE"] = str(episode)
      self.post_process_environment["SMA_EPISODES"] = str(episode)

  def setMovie(self, tmdbid):
    """Set movie-specific environment variables for post-process scripts.

    Args:
        tmdbid: TMDB movie identifier.
    """
    self.log.debug("Setting movie metadata.")
    self.post_process_environment["SMA_TMDBID"] = str(tmdbid)

  def run_scripts(self):
    """Execute all gathered scripts in sorted order.

    Logs stdout/stderr for each script. If ``self.wait`` is ``True``, waits
    for the process to exit before continuing. Exceptions are caught and
    logged so that one failing script does not block the rest.
    """
    # Compact JSON on a single line; SingleLineFormatter handles redaction
    # of any secrets that might end up in the post-process environment.
    self.log.debug("Running scripts. environment=%s", json.dumps(self.post_process_environment, default=str))
    for script in self.scripts:
      try:
        command = self.run_script_command(script)
        self.log.info("Running script '%s'." % (script))
        stdout, stderr = command.communicate()
        self.log.debug("Stdout: %s." % stdout)
        self.log.debug("Stderr: %s." % stderr)
        if self.wait:
          status = command.wait()
      except:
        self.log.exception("Failed to execute script %s." % script)

  def run_script_command(self, script):
    """Spawn a script as a subprocess with the current post-process environment.

    Args:
        script: Absolute path to the script to execute.

    Returns:
        A ``subprocess.Popen`` instance for the running script.
    """
    return Popen([str(script)], shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=self.post_process_environment, close_fds=(os.name != "nt"))
