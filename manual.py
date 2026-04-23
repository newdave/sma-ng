#!/opt/sma/venv/bin/python3
"""Command-line tool for manually converting and tagging media files with SMA-NG.

Wraps ``MediaProcessor`` and ``Metadata`` to provide an interactive or fully
automated (``-a``) workflow for single files and directories. Metadata can be
supplied via TMDB/TVDB/IMDB IDs, inferred from filenames with guessit, or
entered interactively at the prompt. Run ``python manual.py --help`` for the
full list of options.
"""

import argparse
import enum
import glob
import json
import logging
import os
import struct
import sys

import guessit
import tmdbsimple as tmdb

from converter.avcodecs import attachment_codec_list, audio_codec_list, subtitle_codec_list, video_codec_list
from resources.extensions import tmdb_api_key
from resources.log import getLogger
from resources.mediaprocessor import MediaProcessor
from resources.metadata import MediaType, Metadata
from resources.readsettings import ReadSettings

os.environ["REGEX_DISABLED"] = "1"  # Fixes Toilal/rebulk#20

log = getLogger("MANUAL")

logging.getLogger("subliminal").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("enzyme").setLevel(logging.WARNING)
logging.getLogger("qtfaststart").setLevel(logging.CRITICAL)
logging.getLogger("rebulk").setLevel(logging.WARNING)

log.debug("Manual processor started.")


class MediaTypes(enum.Enum):
  """Interactive media-type selection options presented to the user at the CLI prompt."""

  @classmethod
  def descriptors(cls):
    return {
      cls.MOVIE_TMDB: "Movie (via TMDB)",
      cls.MOVIE_IMDB: "Movie (via IMDB)",
      cls.TV_TMDB: "TV (via TMDB)",
      cls.TV_TVDB: "TV (via TVDB)",
      cls.TV_IMDB: "TV (via IMDB)",
      cls.CONVERT: "Convert without tagging",
      cls.SKIP: "Skip file",
    }

  def __str__(self):
    return "{0}. {1}".format(self.value, MediaTypes.descriptors().get(self, ""))

  MOVIE_TMDB = 1
  MOVIE_IMDB = 2
  TV_TMDB = 3
  TV_TVDB = 4
  TV_IMDB = 5
  CONVERT = 6
  SKIP = 7


def mediatype():
  """Interactively prompt the user to select a media type from ``MediaTypes``.

  Returns:
      The selected ``MediaTypes`` enum value.
  """
  while True:
    try:
      print("Select media type:")
      for mt in MediaTypes:
        print(str(mt))
      result = input("#: ")
      return MediaTypes(int(result))
    except KeyboardInterrupt:
      raise
    except (EOFError, ValueError):
      print("Invalid selection")


def getValue(prompt, num=False):
  """Prompt the user for a string (or numeric) value at the CLI.

  Args:
      prompt: Text to display before the input field.
      num: If ``True``, only accept strings that are all digits.

  Returns:
      The entered string value (stripped of surrounding quotes/spaces).
  """
  while True:
    try:
      print(prompt + ":")
      value = input("#: ").strip(' "')
      # Remove escape characters in non-windows environments
      if os.name != "nt":
        value = value.replace("\\", "")
      if num is True and not value.isdigit():
        print("Must be a numerical value")
        continue
      return value
    except EOFError:
      print("Must be a numerical value")


def getYesNo():
  """Interactively prompt the user for a yes/no answer.

  Returns:
      ``True`` for yes, ``False`` for no.
  """
  yes = ["y", "yes", "true", "1"]
  no = ["n", "no", "false", "0"]
  while True:
    try:
      data = input("# [y/n]: ")
      if data.lower() in yes:
        return True
      elif data.lower() in no:
        return False
      else:
        print("Invalid selection")
    except EOFError:
      print("Invalid selection")


class SkipFileException(Exception):
  """Raised when the user interactively selects "Skip file" for a given input."""


def getInfo(fileName, settings, silent=False, tag=True, tvdbid=None, tmdbid=None, imdbid=None, season=None, episode=None, language=None, original=None, type_hint=None):
  """Collect or guess TMDB metadata for an input file.

  In silent mode, attempts to guess metadata from the filename and returns
  it without prompting. In interactive mode, shows the guess (if any) and
  lets the user confirm, override, or enter IDs manually.

  Args:
      fileName: Path to the input media file used for guessit inference.
      settings: Parsed ``ReadSettings`` instance.
      silent: If ``True``, skip all interactive prompts.
      tag: If ``False``, skip metadata lookup and return ``None``.
      tvdbid: Optional TVDB ID hint.
      tmdbid: Optional TMDB ID hint.
      imdbid: Optional IMDB ID hint.
      season: Optional season number hint.
      episode: Optional episode number hint.
      language: Optional ISO 639 language code for tagging.
      original: Optional original release filename used for guessing.
      type_hint: ``"tv"`` or ``"movie"`` to bias guessit.

  Returns:
      A :class:`~resources.metadata.Metadata` instance, or ``None`` if
      tagging is disabled or the user chose to convert without tagging.

  Raises:
      SkipFileException: If the user interactively selects "Skip file".
  """
  if not tag:
    return None

  tagdata = None
  # Try to guess the file is guessing is enabled
  if fileName is not None:
    tagdata = guessInfo(fileName, settings, tvdbid=tvdbid, tmdbid=tmdbid, imdbid=imdbid, season=season, episode=episode, language=language, original=original, type_hint=type_hint)

  if not silent:
    if tagdata:
      print("Proceed using guessed identification from filename?")
      if getYesNo():
        return tagdata
    else:
      print("Unable to determine identity based on filename, must enter manually")
    m_type = mediatype()
    if m_type is MediaTypes.TV_TMDB:
      tmdbid = getValue("Enter TMDB ID (TV)", True)
      season = getValue("Enter Season Number", True)
      episode = getValue("Enter Episode Number", True)
      return Metadata(MediaType.TV, tmdbid=tmdbid, season=season, episode=episode, language=language, logger=log, original=original)
    if m_type is MediaTypes.TV_TVDB:
      tvdbid = getValue("Enter TVDB ID (TV)", True)
      season = getValue("Enter Season Number", True)
      episode = getValue("Enter Episode Number", True)
      return Metadata(MediaType.TV, tvdbid=tvdbid, season=season, episode=episode, language=language, logger=log, original=original)
    if m_type is MediaTypes.TV_IMDB:
      imdbid = getValue("Enter IMDB ID (TV)", True)
      season = getValue("Enter Season Number", True)
      episode = getValue("Enter Episode Number", True)
      return Metadata(MediaType.TV, imdbid=imdbid, season=season, episode=episode, language=language, logger=log, original=original)
    elif m_type is MediaTypes.MOVIE_IMDB:
      imdbid = getValue("Enter IMDB ID (Movie)")
      return Metadata(MediaType.Movie, imdbid=imdbid, language=language, logger=log, original=original)
    elif m_type is MediaTypes.MOVIE_TMDB:
      tmdbid = getValue("Enter TMDB ID (Movie)", True)
      return Metadata(MediaType.Movie, tmdbid=tmdbid, language=language, logger=log, original=original)
    elif m_type is MediaTypes.CONVERT:
      return None
    elif m_type is MediaTypes.SKIP:
      raise SkipFileException
  else:
    if tagdata and tag:
      return tagdata
    else:
      return None


def guessInfo(fileName, settings, tmdbid=None, tvdbid=None, imdbid=None, season=None, episode=None, language=None, original=None, type_hint=None):
  """Use guessit to infer metadata from a filename and look it up on TMDB.

  Args:
      fileName: Path (or basename) to guess from.
      settings: Parsed ``ReadSettings`` instance (controls ``fullpathguess``).
      tmdbid: Optional TMDB ID override.
      tvdbid: Optional TVDB ID override.
      imdbid: Optional IMDB ID override.
      season: Optional season number override.
      episode: Optional episode number override.
      language: Optional ISO 639 language code.
      original: Optional original release filename preferred over ``fileName``.
      type_hint: ``"tv"`` or ``"movie"`` to bias guessit.

  Returns:
      A :class:`~resources.metadata.Metadata` instance, or ``None`` on failure.
  """
  if not settings.fullpathguess:
    fileName = os.path.basename(fileName)
  guessit_opts = {}
  if type_hint == "tv":
    guessit_opts["type"] = "episode"
  elif type_hint == "movie":
    guessit_opts["type"] = "movie"
  guess = guessit.guessit(original or fileName, guessit_opts)
  try:
    if guess["type"] == "movie":
      return movieInfo(guess, tmdbid=tmdbid, imdbid=imdbid, language=language, original=original)
    elif guess["type"] == "episode":
      return tvInfo(guess, tmdbid=tmdbid, tvdbid=tvdbid, imdbid=imdbid, season=season, episode=episode, language=language, original=original)
    else:
      return None
  except KeyboardInterrupt:
    raise
  except Exception:
    log.exception("Unable to guess movie information")
    return None


def _tmdb_search(media_type, title, year):
  """Search TMDB for *title*, falling back to a year-less query when needed.

  Args:
      media_type: ``"movie"`` or ``"tv"``.
      title: Title string to search for.
      year: Optional release/air year; ``None`` means no year filter.

  Returns:
      The first result dict from the TMDB API, or ``None`` if nothing matched.
  """
  tmdb.API_KEY = tmdb_api_key
  search = tmdb.Search()
  if media_type == "movie":
    if year:
      search.movie(query=title, year=year)
      if not search.results:
        search.movie(query=title)
    else:
      search.movie(query=title)
  else:
    if year:
      search.tv(query=title, first_air_date_year=year)
      if not search.results:
        search.tv(query=title)
    else:
      search.tv(query=title)
  return search.results[0] if search.results else None


def movieInfo(guessData, tmdbid=None, imdbid=None, language=None, original=None):
  """Look up a movie on TMDB from guessit data or a known identifier.

  Args:
      guessData: dict returned by ``guessit.guessit()`` with at least ``title``
          and optionally ``year``.
      tmdbid: Optional TMDB movie ID (skips search when provided).
      imdbid: Optional IMDB movie ID (skips search when provided).
      language: Optional ISO 639 language code.
      original: Optional original release filename for the ``Metadata`` object.

  Returns:
      A :class:`~resources.metadata.Metadata` instance, or ``None`` if no
      match was found on TMDB.
  """
  if not tmdbid and not imdbid:
    result = _tmdb_search("movie", guessData["title"], guessData.get("year"))
    if result is None:
      return None
    tmdbid = result["id"]
    log.debug("Guessed filename resulted in TMDB ID %s" % tmdbid)

  metadata = Metadata(MediaType.Movie, tmdbid=tmdbid, imdbid=imdbid, language=language, logger=log, original=original)
  log.info("Matched movie title as: %s %s (TMDB ID: %s)" % (metadata.title, metadata.date, metadata.tmdbid))
  return metadata


def tvInfo(guessData, tmdbid=None, tvdbid=None, imdbid=None, season=None, episode=None, language=None, original=None):
  """Look up a TV episode on TMDB from guessit data or known identifiers.

  Args:
      guessData: dict returned by ``guessit.guessit()`` with at least ``title``
          and optionally ``year``, ``season``, and ``episode``.
      tmdbid: Optional TMDB series ID (skips search when provided).
      tvdbid: Optional TVDB series ID.
      imdbid: Optional IMDB series ID.
      season: Season number override (falls back to guessData).
      episode: Episode number override (falls back to guessData).
      language: Optional ISO 639 language code.
      original: Optional original release filename for the ``Metadata`` object.

  Returns:
      A :class:`~resources.metadata.Metadata` instance, or ``None`` if no
      match was found on TMDB.
  """
  season = season or guessData.get("season", 0)
  episode = episode or guessData.get("episode", 0)

  if not tmdbid and not tvdbid and not imdbid:
    result = _tmdb_search("tv", guessData["title"], guessData.get("year"))
    if result is None:
      return None
    tmdbid = result["id"]

  metadata = Metadata(MediaType.TV, tmdbid=tmdbid, imdbid=imdbid, tvdbid=tvdbid, season=season, episode=episode, language=language, logger=log, original=original)
  ep_display = "E".join("%02d" % e for e in metadata.episodes)
  log.info("Matched TV episode as %s (TMDB ID: %d) S%02d%s" % (metadata.showname, int(metadata.tmdbid), int(season), ep_display))
  return metadata


def _find_arr_instance(filepath, settings):
  """Return (instance, arr_type) for the first Sonarr/Radarr instance whose
  path prefix matches *filepath*, or (None, None) if none matches."""
  dirpath = os.path.dirname(filepath)
  for instance in settings.sonarr_instances:
    ipath = instance.get("path", "")
    if ipath and dirpath.startswith(ipath) and instance.get("apikey"):
      return instance, "sonarr"
  for instance in settings.radarr_instances:
    ipath = instance.get("path", "")
    if ipath and dirpath.startswith(ipath) and instance.get("apikey"):
      return instance, "radarr"
  return None, None


def triggerRescan(filepath, settings):
  """Trigger a rescan on the matching Sonarr/Radarr instance based on file path.

  When the matched instance has ``force-rename = True``, waits for the import
  command to complete, then calls Sonarr/Radarr's RenameFiles command.

  Returns the new file path if arr renamed the file, otherwise None.
  """
  try:
    import requests  # noqa: F401  # type: ignore[import-untyped]
  except ImportError:
    log.warning("Python module 'requests' not installed, skipping media manager rescan.")
    return None

  instance, arr_type = _find_arr_instance(filepath, settings)
  if not instance:
    log.debug("No matching Sonarr/Radarr instance found for path %s, skipping rescan." % os.path.dirname(filepath))
    return None

  if not instance.get("rescan", True):
    return None

  protocol = "https://" if instance.get("ssl") else "http://"
  base_url = protocol + instance["host"] + ":" + str(instance["port"]) + instance["webroot"]
  headers = {"X-Api-Key": instance["apikey"], "User-Agent": "SMA-NG - manual"}
  dirpath = os.path.dirname(filepath)

  if arr_type == "sonarr":
    payload = {"name": "DownloadedEpisodesScan", "path": dirpath}
  else:
    payload = {"name": "DownloadedMoviesScan", "path": dirpath}

  try:
    from resources.mediamanager import api_command, rename_via_arr, wait_for_command

    log.info("Requesting %s [%s] to rescan '%s'." % (arr_type.title(), instance["section"], dirpath))
    cmd = api_command(base_url, headers, payload, log)
    command_id = cmd.get("id")

    if instance.get("rename") and command_id:
      # Wait for the import to complete before triggering rename
      completed = wait_for_command(base_url, headers, command_id, log)
      if not completed:
        log.warning("%s [%s] import command did not complete; skipping arr rename." % (arr_type.title(), instance["section"]))
        return None
      new_path = rename_via_arr(base_url, headers, arr_type, filepath, log)
      if new_path:
        log.info("%s renamed file to: %s" % (arr_type.title(), new_path))
      return new_path
  except Exception:
    log.exception("Failed to trigger rescan on %s [%s]." % (arr_type.title(), instance["section"]))

  return None


def checkAlreadyProcessed(inputfile, processedList):
  """Check whether a file has already been recorded in the processed archive.

  Args:
      inputfile: Absolute path to the input file.
      processedList: List of previously processed paths, or ``None`` to skip
          the check.

  Returns:
      ``True`` if the file is in the list, ``False`` otherwise.
  """
  if processedList is None:
    return False

  return inputfile in processedList


def addtoProcessedArchive(files, processedList, processedArchive):
  """Append processed file paths to the in-memory list and persist to the JSON archive.

  Args:
      files: List of output file paths to record.
      processedList: In-memory list of previously processed paths. Modified
          in place.
      processedArchive: Path to the JSON archive file. No-op when ``None``.
  """
  if processedList is None or processedArchive is None:
    return

  processedList.extend(files)
  with open(processedArchive, "w", encoding="utf8") as pa:
    json.dump(list(set(processedList)), pa, indent=4)
  log.debug("Adding %s to processed archive %s" % (files, processedArchive))


def processFile(
  inputfile,
  mp,
  info=None,
  relativePath=None,
  silent=False,
  tag=True,
  tagOnly=False,
  optionsOnly=False,
  tmdbid=None,
  tvdbid=None,
  imdbid=None,
  season=None,
  episode=None,
  original=None,
  processedList=None,
  processedArchive=None,
  type_hint=None,
):
  """Process a single media file: convert, tag, copy/move, and post-process.

  Skips files already in ``processedList``. When ``tagOnly`` is ``True``,
  only rewrites tags on the existing file. When ``optionsOnly`` is ``True``,
  prints the FFmpeg option preview without converting.

  Args:
      inputfile: Absolute path to the source media file.
      mp: Initialised :class:`~resources.mediaprocessor.MediaProcessor`.
      info: Pre-fetched FFprobe info; probed if not provided.
      relativePath: Relative directory path used with copy-to/move-to to
          preserve directory structure.
      silent: Skip all interactive prompts; use guessit only.
      tag: Fetch and embed TMDB metadata when ``True``.
      tagOnly: Re-tag existing file without conversion.
      optionsOnly: Display FFmpeg options without converting.
      tmdbid: Optional TMDB ID hint.
      tvdbid: Optional TVDB ID hint.
      imdbid: Optional IMDB ID hint.
      season: Season number (TV).
      episode: Episode number (TV).
      original: Original release filename for guessit/metadata.
      processedList: In-memory list of already-processed paths.
      processedArchive: Path to the JSON processed-archive file.
      type_hint: ``"tv"`` or ``"movie"`` to bias guessit.

  Returns:
      ``True`` on success, ``False`` on conversion error, or ``None`` if the
      file was skipped (already processed or invalid source).
  """
  if checkAlreadyProcessed(inputfile, processedList):
    log.debug("%s is already processed and will be skipped based on archive %s." % (inputfile, processedArchive))
    return

  # Process
  info = info or mp.isValidSource(inputfile)
  if not info:
    log.debug("Invalid file %s." % inputfile)
    return

  language = mp.settings.taglanguage or None
  tagdata = getInfo(
    inputfile,
    mp.settings,
    silent=silent,
    tag=tag or tagOnly,
    tmdbid=tmdbid,
    tvdbid=tvdbid,
    imdbid=imdbid,
    season=season,
    episode=episode,
    language=language,
    original=original,
    type_hint=type_hint,
  )

  if optionsOnly:
    displayOptions(inputfile, mp.settings, tagdata)
    return

  if not tagdata:
    log.info("Processing file %s" % inputfile)
  elif tagdata.mediatype == MediaType.Movie:
    log.info("Processing %s" % (tagdata.title))
  elif tagdata.mediatype == MediaType.TV:
    ep_nums = ["%02d" % e for e in tagdata.episodes]
    ep_display = "E" + ep_nums[0] if len(ep_nums) == 1 else "E%s-E%s" % (ep_nums[0], ep_nums[-1])
    log.info("Processing %s S%02d%s - %s" % (tagdata.showname, int(tagdata.season), ep_display, tagdata.title))

  if tagOnly:
    if tagdata:
      try:
        tagdata.writeTags(
          inputfile, inputfile, mp.converter, mp.settings.artwork, mp.settings.thumbnail, cues_to_front=(os.path.splitext(inputfile)[1].lower() in [".mkv"] and mp.settings.relocate_moov)
        )
        if mp.settings.relocate_moov:
          mp.QTFS(inputfile)
      except KeyboardInterrupt:
        raise
      except Exception:
        log.exception("There was an error tagging the file")
    return

  output = mp.process(inputfile, True, info=info, original=original, tagdata=tagdata)
  if output:
    if not language:
      language = mp.getDefaultAudioLanguage(output["options"]) or None
      if language and tagdata:
        tagdata = Metadata(
          tagdata.mediatype,
          tmdbid=tagdata.tmdbid,
          imdbid=tagdata.imdbid,
          tvdbid=tagdata.tvdbid,
          season=tagdata.season,
          episode=tagdata.episodes or tagdata.episode,
          original=original,
          language=language,
          logger=log,
        )
    log.debug("Tag language setting is %s, using language %s for tagging." % (mp.settings.taglanguage or None, language))
    tagfailed = False
    if tagdata:
      try:
        tagdata.writeTags(output["output"], inputfile, mp.converter, mp.settings.artwork, mp.settings.thumbnail, width=output["x"], height=output["y"], cues_to_front=output["cues_to_front"])
      except KeyboardInterrupt:
        raise
      except Exception:
        log.exception("There was an error tagging the file")
        tagfailed = True
    if mp.settings.relocate_moov and not tagfailed:
      mp.QTFS(output["output"])

    # File renaming — skip when arr will rename via force-rename
    _arr_instance, _ = _find_arr_instance(output["output"], mp.settings)
    _arr_will_rename = bool(_arr_instance and _arr_instance.get("rename") and _arr_instance.get("rescan", True))
    if mp.settings.naming_enabled and tagdata and not _arr_will_rename:
      try:
        import guessit as _guessit

        from resources.naming import generate_name, rename_file

        guess_data = _guessit.guessit(original or os.path.basename(inputfile))
        new_name = generate_name(output["output"], info, tagdata, mp.settings, guess_data=guess_data, log=log)
        if new_name:
          output["output"] = rename_file(output["output"], new_name, log=log)
      except Exception:
        log.exception("Error during file rename")

    # When output_dir is set without moveto, restoreFromOutput will atomically
    # overwrite the input path with the output. Recycle the original now, while
    # it still exists, so it is preserved before the path is replaced.
    _overwrite_input = mp.settings.output_dir and not mp.settings.moveto and os.path.commonpath([mp.settings.output_dir, output["output"]]) == mp.settings.output_dir
    if _overwrite_input and output.get("delete"):
      mp._recycle_to_bin(output["input"])

    # Reverse Output
    output["output"] = mp.restoreFromOutput(inputfile, output["output"])
    for i, sub in enumerate(output["external_subs"]):
      output["external_subs"][i] = mp.restoreFromOutput(inputfile, sub)

    output_files = mp.replicate(output["output"], relativePath=relativePath)
    print(json.dumps(output, indent=4))
    for sub in [x for x in output["external_subs"] if os.path.exists(x)]:
      output_files.extend(mp.replicate(sub, relativePath=relativePath))
    for file in output_files:
      mp.setPermissions(file)

    # Clean up the original input only after the output is safely placed.
    # For the no-moveto case the input path was atomically overwritten by
    # restoreFromOutput (recycle copy was already taken above); just clear
    # any staged subtitle temp files. For the moveto case the original is
    # still at its source path and needs a full recycle + unlink.
    if _overwrite_input:
      for subfile in list(mp.deletesubs):
        if mp.removeFile(subfile):
          log.debug("Subtitle %s deleted." % subfile)
        else:
          log.debug("Unable to delete subtitle %s." % subfile)
      mp.deletesubs = set()
      output["input_deleted"] = bool(output.get("delete"))
    else:
      output["input_deleted"] = mp._cleanup_input(output["input"], output.get("delete", False))

    # Plex .plexmatch file (after file is in final destination)
    if mp.settings.plexmatch_enabled and tagdata:
      try:
        from resources.metadata import update_plexmatch

        update_plexmatch(output["output"], tagdata, mp.settings, log=log)
      except Exception:
        log.exception("Error updating .plexmatch")

    if mp.settings.postprocess:
      if tagdata:
        mp.post(output_files, tagdata.mediatype, tmdbid=tagdata.tmdbid, season=tagdata.season, episode=tagdata.episodes or tagdata.episode)
      elif type_hint:
        mp.post(output_files, type_hint, tmdbid=tmdbid, season=season, episode=episode)
    addtoProcessedArchive(output_files + [output["input"]] if not output["input_deleted"] else output_files, processedList, processedArchive)

    # Trigger rescan on matching Sonarr/Radarr instance; may also rename
    arr_renamed_path = triggerRescan(output["output"], mp.settings)
    if arr_renamed_path:
      output["output"] = arr_renamed_path
    return True
  else:
    log.error("There was an error processing file %s, no output data received" % inputfile)
    return False


def walkDir(
  dir, settings, silent=False, preserveRelative=False, tmdbid=None, imdbid=None, tvdbid=None, tag=True, tagOnly=False, optionsOnly=False, processedList=None, processedArchive=None, type_hint=None
):
  """Recursively walk a directory and process every valid media file found.

  Args:
      dir: Root directory path to walk.
      settings: Parsed ``ReadSettings`` instance.
      silent: Skip interactive prompts; use guessit only.
      preserveRelative: Preserve subdirectory structure when copying/moving.
      tmdbid: Optional TMDB ID hint applied to all files.
      imdbid: Optional IMDB ID hint applied to all files.
      tvdbid: Optional TVDB ID hint applied to all files.
      tag: Fetch and embed TMDB metadata.
      tagOnly: Re-tag existing files without conversion.
      optionsOnly: Display FFmpeg options without converting.
      processedList: In-memory list of already-processed paths.
      processedArchive: Path to the JSON processed-archive file.
      type_hint: ``"tv"`` or ``"movie"`` to bias guessit.

  Returns:
      ``True`` if all files processed successfully, ``False`` if any failed.
  """
  files = []
  error = []
  failed = False
  mp = MediaProcessor(settings, logger=log)
  for r, _, f in os.walk(dir):
    for file in f:
      files.append(os.path.join(r, file))
  for filepath in files:
    info = mp.isValidSource(filepath)
    if info:
      log.info("Processing file %s" % (filepath))
      relative = os.path.split(os.path.relpath(filepath, dir))[0] if preserveRelative else None
      if optionsOnly:
        displayOptions(filepath, settings)
        continue
      try:
        result = processFile(
          filepath,
          mp,
          info=info,
          relativePath=relative,
          silent=silent,
          tag=tag,
          tagOnly=tagOnly,
          optionsOnly=optionsOnly,
          tmdbid=tmdbid,
          tvdbid=tvdbid,
          imdbid=imdbid,
          processedList=processedList,
          processedArchive=processedArchive,
          type_hint=type_hint,
        )
        if result is False:
          failed = True
      except SkipFileException:
        log.debug("Skipping file %s." % filepath)
      except KeyboardInterrupt:
        break
      except Exception:
        log.exception("Error processing file %s." % filepath)
        error.append(filepath)
        failed = True
  if error:
    log.error("Script failed to process the following files:")
    for e in error:
      log.error(e)
  return not failed


def displayOptions(path, settings, tagdata=None):
  """Log the generated FFmpeg conversion options for a file without converting it.

  Args:
      path: Path to the input media file.
      settings: Parsed ``ReadSettings`` instance.
      tagdata: Optional pre-fetched ``Metadata`` instance.
  """
  mp = MediaProcessor(settings)
  log.info(mp.jsonDump(path, tagdata=tagdata))


def showCodecs():
  """Print a formatted list of all supported SMA-NG codecs with their FFmpeg encoder names."""
  data = {"video": video_codec_list, "audio": audio_codec_list, "subtitle": subtitle_codec_list, "attachment": attachment_codec_list}
  print("List of supported codecs within SMA-NG")
  print("Format:")
  print("  [SMA-NG Codec]: [FFMPEG Encoder]")
  for key in data:
    print("=============")
    print(" " + key)
    print("=============")
    for codec in data[key]:
      print("%s: %s" % (codec.codec_name, codec.ffmpeg_codec_name))


def apply_cli_overrides(args, settings):
  """Apply CLI argument overrides to a ReadSettings instance.

  Args:
      args: Parsed argument dict from ``vars(parser.parse_args())``.
      settings: A :class:`ReadSettings` instance to mutate in place.

  Returns:
      ``type_hint`` string (``"tv"``, ``"movie"``, or ``None``).
  """
  if args["nomove"]:
    settings.output_dir = None
    settings.moveto = None
    log.info("No-move enabled")
  elif args["moveto"]:
    settings.moveto = args["moveto"]
    log.info("Overriden move-to to " + args["moveto"])
  if args["nocopy"]:
    settings.copyto = None
    log.info("No-copy enabled")
  if args["nodelete"]:
    settings.delete = False
    log.info("No-delete enabled")
  if args["processsameextensions"]:
    settings.process_same_extensions = True
    log.info("Reprocessing of same extensions enabled")
  if args["forceconvert"]:
    settings.process_same_extensions = True
    settings.force_convert = True
    log.info("Force conversion of files enabled. As a result conversion of mp4 files is also enabled")
  if args["tagonly"]:
    log.info("Tag only enabled")
  elif args["notag"]:
    settings.tagfile = False
    log.info("No-tagging enabled")
  if args["nopost"]:
    settings.postprocess = False
    log.info("No post processing enabled")
  if args["optionsonly"]:
    logging.getLogger("resources.mediaprocessor").setLevel(logging.CRITICAL)
    log.info("Options only mode enabled")
  if args["minsize"]:
    try:
      settings.minimum_size = int(args["minsize"])
      log.info("Minimum size set to %d mb" % (int(args["minsize"])))
    except TypeError:
      log.error("Invalid minsize")

  type_hint = None
  if args.get("tv"):
    type_hint = "tv"
    log.info("Forcing media type detection to: TV")
  elif args.get("movie"):
    type_hint = "movie"
    log.info("Forcing media type detection to: Movie")

  return type_hint


def main():
  """Parse CLI arguments and drive the manual conversion/tagging workflow.

  Handles single files and directories. Applies any settings overrides from
  CLI flags before delegating to :func:`processFile` or :func:`walkDir`.
  Exits with code 1 if processing fails.
  """
  parser = argparse.ArgumentParser(description="SMA-NG manual conversion and tagging script")
  parser.add_argument("-i", "--input", help="The source that will be converted. May be a file or a directory")
  parser.add_argument("-c", "--config", help="Specify an alternate configuration file location")
  parser.add_argument(
    "-a", "--auto", action="store_true", help="Enable auto mode, the script will not prompt you for any further input, good for batch files. It will guess the metadata using guessit"
  )
  parser.add_argument("-s", "--season", help="Specifiy the season number")
  parser.add_argument("-e", "--episode", action="append", help="Specify the episode number (repeat for multi-episode, e.g. -e 1 -e 2)")
  parser.add_argument("-tvdb", "--tvdbid", help="Specify the TVDB ID for media")
  parser.add_argument("-imdb", "--imdbid", help="Specify the IMDB ID for media")
  parser.add_argument("-tmdb", "--tmdbid", help="Specify the TMDB ID for media")
  parser.add_argument("-nc", "--nocopy", action="store_true", help="Overrides and disables the custom copying of file options that come from output_dir and move-to")
  parser.add_argument("-nd", "--nodelete", action="store_true", help="Overrides and disables deleting of original files")
  parser.add_argument("-np", "--nopost", action="store_true", help="Overrides and disables the execution of additional post processing scripts")
  parser.add_argument("-pr", "--preserverelative", action="store_true", help="Preserves relative directories when processing multiple files using the copy-to or move-to functionality")
  parser.add_argument("-pse", "--processsameextensions", action="store_true", help="Overrides process-same-extensions setting in autoProcess.ini enabling the reprocessing of files")
  parser.add_argument(
    "-fc", "--forceconvert", action="store_true", help="Overrides force-convert setting in autoProcess.ini and also enables process-same-extenions if true forcing the conversion of files"
  )
  parser.add_argument("-oo", "--optionsonly", action="store_true", help="Display generated conversion options only, do not perform conversion")
  parser.add_argument("-cl", "--codeclist", action="store_true", help="Print a list of supported codecs and their paired FFMPEG encoders")
  parser.add_argument("-o", "--original", help="Specify the original source/release filename")
  parser.add_argument("-ms", "--minsize", help="Specify the minimum file size")
  parser.add_argument("-pa", "--processedarchive", help="Specify a processed list/archive so already processed files are skipped", nargs="?", const="archive.json")

  move_group = parser.add_mutually_exclusive_group()
  move_group.add_argument("-nm", "--nomove", action="store_true", help="Overrides and disables the custom moving of file options that come from output_dir and move-to")
  move_group.add_argument("-m", "--moveto", help="Override move-to value setting in autoProcess.ini changing the final destination of the file")

  tag_group = parser.add_mutually_exclusive_group()
  tag_group.add_argument("-nt", "--notag", action="store_true", help="Overrides and disables tagging when using the automated option")
  tag_group.add_argument("-to", "--tagonly", action="store_true", help="Only tag without conversion")

  mediatype_group = parser.add_mutually_exclusive_group()
  mediatype_group.add_argument("--tv", action="store_true", help="Force guessit to treat input as a TV episode")
  mediatype_group.add_argument("--movie", action="store_true", help="Force guessit to treat input as a movie")

  args = vars(parser.parse_args())

  # Setup the silent mode
  silent = args["auto"]

  log.debug("Python %s-bit %s." % (struct.calcsize("P") * 8, sys.version))
  log.debug("Guessit version: %s." % guessit.__version__)

  if args["codeclist"]:
    showCodecs()
    return

  # Settings overrides
  if args["config"] and os.path.exists(args["config"]):
    settings = ReadSettings(args["config"], logger=log)
  elif args["config"] and os.path.exists(os.path.join(os.path.dirname(sys.argv[0]), args["config"])):
    settings = ReadSettings(os.path.join(os.path.dirname(sys.argv[0]), args["config"]), logger=log)
  else:
    settings = ReadSettings(logger=log)

  processedArchive = None
  processedList = None
  if args["processedarchive"] and os.path.exists(args["processedarchive"]):
    processedArchive = args["processedarchive"]
    log.info("Processed archived specified at %s" % (processedArchive))
  elif args["processedarchive"] and os.path.exists(os.path.join(os.path.dirname(sys.argv[0]), args["processedarchive"])):
    processedArchive = os.path.join(os.path.dirname(sys.argv[0]), args["processedarchive"])
    log.info("Processed archived specified at %s" % (processedArchive))
  elif args["processedarchive"]:
    processedArchive = os.path.normpath(args["processedarchive"])
    with open(processedArchive, "w", encoding="utf8") as pa:
      json.dump([], pa)
    log.info("Processed archived specified at %s but file does not exist, creating" % (processedArchive))
  if processedArchive:
    pa = open(processedArchive, encoding="utf8")
    processedList = json.load(pa)
    log.info("Loaded archive list containing %d files" % (len(processedList)))

  type_hint = apply_cli_overrides(args, settings)

  # Establish the path we will be working with
  if args["input"]:
    path = str(args["input"])
    try:
      path = glob.glob(path)[0]
    except Exception:
      pass
  else:
    path = getValue("Enter path to file")

  if os.path.isdir(path):
    success = walkDir(
      path,
      settings,
      silent=silent,
      tmdbid=args.get("tmdbid"),
      tvdbid=args.get("tvdbid"),
      imdbid=args.get("imdbid"),
      preserveRelative=args["preserverelative"],
      tag=settings.tagfile,
      tagOnly=args.get("tagonly", False),
      optionsOnly=args["optionsonly"],
      processedList=processedList,
      processedArchive=processedArchive,
      type_hint=type_hint,
    )
    if not success:
      sys.exit(1)
  elif os.path.isfile(path):
    mp = MediaProcessor(settings, logger=log)
    info = mp.isValidSource(path)
    if info:
      try:
        result = processFile(
          path,
          mp,
          info=info,
          silent=silent,
          tag=settings.tagfile,
          tagOnly=args.get("tagonly", False),
          optionsOnly=args.get("optionsonly", False),
          tmdbid=args.get("tmdbid"),
          tvdbid=args.get("tvdbid"),
          imdbid=args.get("imdbid"),
          season=args.get("season"),
          episode=args.get("episode"),
          original=args.get("original"),
          processedList=processedList,
          processedArchive=processedArchive,
          type_hint=type_hint,
        )
        if result is False:
          sys.exit(1)
      except SkipFileException:
        log.debug("Skipping file %s" % path)

    else:
      log.info("File %s is not in a valid format" % (path))
  else:
    log.info("File %s does not exist" % (path))


if __name__ == "__main__":
  main()
