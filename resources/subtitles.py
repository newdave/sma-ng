"""Subtitle scanning, downloading, burning, and syncing."""

import os

from converter import ConverterError, FFMpegConvertError
from converter.avcodecs import BaseCodec
from resources.lang import getAlpha3TCode
from resources.metadata import MediaType

try:
  import cleanit
except ImportError:
  cleanit = None
try:
  from ffsubsync import ffsubsync
except ImportError:
  ffsubsync = None
try:
  import subliminal
  from babelfish import Language
  from guessit import guessit
except ImportError:
  subliminal = None
  guessit = None
  Language = None


class SubtitleProcessor:
  """Handles all subtitle operations for media conversion."""

  def __init__(self, media_processor):
    self.mp = media_processor
    self.settings = media_processor.settings
    self.converter = media_processor.converter
    self.log = media_processor.log

  def processExternalSub(self, valid_external_sub, inputfile):
    """
    Parse the filename of an external subtitle and infer its language and dispositions.

    Extracts language codes and disposition flags (e.g. forced, SDH) from
    the filename suffix, updates the subtitle stream metadata, and returns
    the updated MediaInfo object.
    """
    if not valid_external_sub:
      return valid_external_sub
    _, filename, _ = self.mp.parseFile(inputfile)
    _, subname, _ = self.mp.parseFile(valid_external_sub.path)
    subname = subname[len(filename + os.path.extsep) :]
    lang = BaseCodec.UNDEFINED
    for suf in subname.lower().split(os.path.extsep):
      self.log.debug("Processing subtitle file suffix %s." % (suf))
      l = getAlpha3TCode(suf)
      if lang == BaseCodec.UNDEFINED and l != BaseCodec.UNDEFINED:
        lang = l
        self.log.debug("Found language match %s." % (lang))
        continue
      dsuf = BaseCodec.DISPO_ALTS.get(suf, suf)
      if dsuf in BaseCodec.DISPOSITIONS:
        valid_external_sub.subtitle[0].disposition[dsuf] = True
        self.log.debug("Found disposition match %s." % (suf))
    if self.settings.sdl and lang == BaseCodec.UNDEFINED:
      lang = self.settings.sdl
    valid_external_sub.subtitle[0].metadata["language"] = lang
    return valid_external_sub

  def scanForExternalSubs(self, inputfile, swl, valid_external_subs=None):
    """
    Scan the input file's directory for external subtitle files.

    Identifies subtitle files whose names start with the input filename,
    validates them with isValidSubtitleSource(), parses language and
    disposition from the filename, and filters by the subtitle whitelist.
    Returns a list of MediaInfo objects sorted by language preference.
    """
    valid_external_subs = valid_external_subs or []
    input_dir, filename, _ = self.mp.parseFile(inputfile)
    for dirName, _, fileList in os.walk(input_dir):
      for fname in fileList:
        if any(os.path.join(dirName, fname) == x.path for x in valid_external_subs):
          self.log.debug("Already loaded %s, skipping." % (fname))
          continue
        if fname.startswith(filename):
          valid_external_sub = self.mp.isValidSubtitleSource(os.path.join(dirName, fname))
          if valid_external_sub:
            self.log.debug("Potential subtitle candidate identified %s." % (fname))
            valid_external_sub = self.processExternalSub(valid_external_sub, inputfile)
            lang = valid_external_sub.subtitle[0].metadata["language"]
            default = valid_external_sub.subtitle[0].disposition["default"]
            if self.mp.validLanguage(lang, swl) or (self.settings.force_subtitle_defaults and default):
              self.log.debug("Valid external %s subtitle file detected %s." % (lang, fname))
              valid_external_subs.append(valid_external_sub)
            else:
              self.log.debug("Ignoring %s external subtitle stream due to language %s." % (fname, lang))
      break
    self.log.info("Scanned for external subtitles and found %d results in your approved languages." % (len(valid_external_subs)))
    valid_external_subs.sort(key=lambda x: x.path, reverse=True)
    valid_external_subs.sort(key=lambda x: swl.index(x.subtitle[0].metadata["language"]) if x.subtitle[0].metadata["language"] in swl else 999)
    return valid_external_subs

  def burnSubtitleFilter(self, inputfile, info, swl, valid_external_subs=None, tagdata=None):
    """
    Generate the FFmpeg -vf filter string for burning subtitles into the video.

    Searches embedded subtitle streams first, then external subtitle files if
    embed-subs is enabled. Applies burn-dispositions and burn-sorting settings
    to select the best candidate. Returns a filter string like
    "subtitles='path':si=0", or None if no valid subtitle is found.
    """
    if self.settings.burn_subtitles:
      subtitle_streams = info.subtitle
      filtered_subtitle_streams = [x for x in subtitle_streams if self.mp.validLanguage(x.metadata.get("language"), swl) or (self.settings.force_subtitle_defaults and x.disposition.get("default"))]
      filtered_subtitle_streams = sorted(filtered_subtitle_streams, key=lambda x: swl.index(x.metadata.get("language")) if x.metadata.get("language") in swl else 999)
      sub_candidates = []
      if len(filtered_subtitle_streams) > 0 and not (self.settings.cleanit and cleanit):
        first_index = sorted([x.index for x in subtitle_streams])[0]

        sub_candidates = [x for x in filtered_subtitle_streams if self.mp.checkDisposition(self.settings.burn_dispositions, x.disposition)]
        for x in sub_candidates[:]:
          try:
            if self.mp.isImageBasedSubtitle(inputfile, x.index):
              sub_candidates.remove(x)
          except Exception:
            self.log.error("Unknown error occurred while trying to determine if subtitle is text or image based. Probably corrupt, skipping.")
            sub_candidates.remove(x)

        if len(sub_candidates) > 0:
          self.log.debug("Found %d potential sources from the included subs for burning [burn-subtitle]." % len(sub_candidates))
          sub_candidates = self.mp.sortStreams(
            sub_candidates, self.settings.burn_sorting, swl, self.settings.sub_sorting_codecs or (self.settings.scodec + self.settings.scodec_image), info, tagdata=tagdata
          )
          burn_sub = sub_candidates[0]
          relative_index = burn_sub.index - first_index
          self.log.info("Burning subtitle %d %s into video stream [burn-subtitles]." % (burn_sub.index, burn_sub.metadata["language"]))
          self.log.debug("Video codec cannot be copied because valid burn subtitle was found [burn-subtitle: %s]." % (self.settings.burn_subtitles))
          return "subtitles='%s':si=%d" % (self.mp.raw(os.path.abspath(inputfile)), relative_index)

      if self.settings.embedsubs:
        self.log.debug("No valid embedded subtitles for burning, search for external subtitles [embed-subs, burn-subtitle].")
        valid_external_subs = valid_external_subs if valid_external_subs else self.scanForExternalSubs(inputfile, swl)

        sub_candidates = [x for x in valid_external_subs if self.mp.checkDisposition(self.settings.burn_dispositions, x.subtitle[0].disposition)]
        for x in sub_candidates[:]:
          try:
            if self.mp.isImageBasedSubtitle(x.path, 0):
              sub_candidates.remove(x)
          except Exception:
            self.log.error("Unknown error occurred while trying to determine if subtitle is text or image based. Probably corrupt, skipping.")
            sub_candidates.remove(x)

        if len(sub_candidates) > 0:
          sub_candidates = self.mp.sortStreams(sub_candidates, self.settings.burn_sorting, swl, self.settings.sub_sorting_codecs or (self.settings.scodec + self.settings.scodec_image), info)
          burn_sub = sub_candidates[0]
          self.log.info("Burning external subtitle %s %s into video stream [burn-subtitles, embed-subs]." % (os.path.basename(burn_sub.path), burn_sub.subtitle[0].metadata["language"]))
          return "subtitles='%s'" % (self.mp.raw(os.path.abspath(burn_sub.path)))
      self.log.info("No valid subtitle stream candidates found to be burned into video stream [burn-subtitles].")
    return None

  def syncExternalSub(self, path, inputfile):
    """Synchronize an external subtitle file to the audio using ffsubsync, if enabled."""
    if self.settings.ffsubsync and ffsubsync:
      self.log.debug("Syncing subtitle with path %s [subtitles.ffsubsync]." % (path))
      syncedsub = path + ".sync.srt"
      try:
        unparsed_args = [inputfile, "-i", path, "-o", syncedsub, "--ffmpegpath", os.path.dirname(self.settings.ffmpeg)]
        parser = ffsubsync.make_parser()
        self.args = parser.parse_args(args=unparsed_args)
        if os.path.isfile(syncedsub):
          os.remove(syncedsub)
        result = ffsubsync.run(self.args)
        self.log.debug(result)
        if os.path.exists(syncedsub):
          os.remove(path)
          os.rename(syncedsub, path)
      except Exception:
        self.log.exception("Exception syncing subtitle %s." % (path))

  @staticmethod
  def custom_scan_video(path, tagdata=None):
    """
    Create a subliminal Video object enriched with metadata from tagdata.

    Overrides guessit-derived fields (title, season, episode) with data from
    the Metadata object so subliminal searches for the correct episode or
    movie. Returns a subliminal Video instance.
    """
    if not os.path.exists(path):
      raise ValueError("Path does not exist")

    if not path.lower().endswith(subliminal.VIDEO_EXTENSIONS):
      raise ValueError("%r is not a valid video extension" % os.path.splitext(path)[1])

    options = None
    if tagdata and tagdata.mediatype == MediaType.TV:
      options = {"type": "episode"}
    elif tagdata and tagdata.mediatype == MediaType.Movie:
      options = {"type": "movie"}

    guess = guessit(path, options)

    if tagdata and tagdata.mediatype == MediaType.TV:
      guess["episode"] = getattr(tagdata, "episodes", None) or tagdata.episode
      guess["title"] = tagdata.title
      guess["season"] = tagdata.season
    elif tagdata and tagdata.mediatype == MediaType.Movie:
      guess["title"] = tagdata.title

    video = subliminal.Video.fromguess(path, guess)
    video.size = os.path.getsize(path)
    return video

  def downloadSubtitles(self, inputfile, existing_subtitle_streams, swl, original=None, tagdata=None):
    """
    Download subtitles for inputfile using the subliminal library.

    Builds a set of target languages from the subtitle whitelist and the
    default subtitle language, optionally downloads forced subtitles and/or
    the best available subtitles, saves them next to the input file, and
    returns a list of saved subtitle file paths. Returns an empty list if
    subliminal is not available or downloads are disabled.
    """
    if (self.settings.downloadsubs or self.settings.downloadforcedsubs) and subliminal and guessit and Language:
      languages = set()
      for alpha3 in swl:
        try:
          languages.add(Language(alpha3))
        except:
          self.log.exception("Unable to add language for download with subliminal.")
      if self.settings.sdl:
        try:
          languages.add(Language(self.settings.sdl))
        except:
          self.log.exception("Unable to add language for download with subliminal.")

      if len(languages) < 1:
        self.log.error("No valid subtitle download languages detected, subtitles will not be downloaded.")
        return []

      self.log.info("Attempting to download subtitles.")

      try:
        subliminal.region.configure("dogpile.cache.memory")
      except:
        pass

      try:
        video = SubtitleProcessor.custom_scan_video(os.path.abspath(inputfile), tagdata)

        if self.settings.ignore_embedded_subs:
          video.subtitles = set()
        else:
          video.subtitles = set([Language(x.metadata["language"]) for x in existing_subtitle_streams])

        if tagdata:
          self.log.debug("Refining subliminal search using included metadata")
          tagdate = tagdata.date
          try:
            tagdate = tagdata.date[:4]
          except:
            pass
          video.year = tagdate or video.year
          video.imdb_id = tagdata.imdbid or video.imdb_id
          if tagdata.mediatype == MediaType.Movie and isinstance(video, subliminal.Movie):
            subliminal.refine(video, title=tagdata.title, year=tagdate, imdb_id=tagdata.imdbid)
            video.title = tagdata.title or video.title
          elif tagdata.mediatype == MediaType.TV and isinstance(video, subliminal.Episode):
            subliminal.refine(video, series=tagdata.showname, year=tagdate, series_imdb_id=tagdata.imdbid, series_tvdb_id=tagdata.tvdbid, title=tagdata.title)
            video.series_tvdb_id = tagdata.tvdbid or video.series_tvdb_id
            video.series_imdb_id = tagdata.imdbid or video.series_imdb_id
            video.season = tagdata.season or video.season
            video.episodes = getattr(tagdata, "episodes", None) or [tagdata.episode] or video.episodes
            video.series = tagdata.showname or video.series
            video.title = tagdata.title or video.title

        if original:
          try:
            self.log.debug("Found original filename, adding data from %s." % original)
            og = guessit(original)
            self.log.debug("Source %s, release group %s, resolution %s, streaming service %s." % (og.get("source"), og.get("release_group"), og.get("screen_size"), og.get("streaming_service")))
            video.source = og.get("source") or video.source
            video.release_group = og.get("release_group") or video.release_group
            video.resolution = og.get("screen_size") or video.resolution
            video.streaming_service = og.get("streaming_service") or video.streaming_service
          except KeyboardInterrupt:
            raise
          except:
            self.log.exception("Error importing original file data for subliminal, will attempt to proceed.")

        paths = []
        if self.settings.downloadforcedsubs:
          forced_subtitles = [
            s for s in subliminal.list_subtitles([video], languages, providers=self.settings.subproviders, provider_configs=self.settings.subproviders_auth)[video] if ".forced" in s.info.lower()
          ]
          self.log.debug("Found %d potential forced subtitles." % (len(forced_subtitles)))
          subliminal.download_subtitles(forced_subtitles, providers=self.settings.subproviders, provider_configs=self.settings.subproviders_auth)
          saves = subliminal.save_subtitles(video, forced_subtitles)
          paths.extend([(subliminal.subtitle.get_subtitle_path(video.name, x.language), x) for x in saves])
          for path, sub in paths:
            if ".forced" in sub.info and ".forced" not in path:
              base, ext = os.path.splitext(path)
              os.rename(path, "%s.forced%s" % (base, ext))
        if self.settings.downloadsubs:
          subtitles = subliminal.download_best_subtitles(
            [video], languages, hearing_impaired=self.settings.hearing_impaired, providers=self.settings.subproviders, provider_configs=self.settings.subproviders_auth
          )
          saves = subliminal.save_subtitles(video, subtitles[video])
          paths.extend([(subliminal.subtitle.get_subtitle_path(video.name, x.language), x) for x in saves])
        for path, sub in paths:
          self.log.info("Downloaded new subtitle %s from source %s." % (path, sub.info))
          self.mp.setPermissions(path)
        return [p for p, _ in paths]
      except KeyboardInterrupt:
        raise
      except:
        self.log.exception("Unable to download subtitles.")
    return []

  def ripSubs(self, inputfile, ripsubopts, include_all=False):
    """
    Extract subtitle tracks to external files using FFmpeg.

    Iterates over ripsubopts (each produced by generateRipSubOpts()), runs
    FFmpeg for each track, cleans up on failure, and returns a list of paths
    to the successfully created subtitle files.
    """
    rips = []
    ripsubopts = ripsubopts if isinstance(ripsubopts, list) else [ripsubopts]
    for options in ripsubopts:
      extension = self.mp.getSubExtensionFromCodec(options["format"])
      outputfile = self.mp.getSubOutputFileFromOptions(inputfile, options, extension, include_all)

      try:
        self.log.info("Ripping %s subtitle from source stream %s into external file." % (options["language"], options["index"]))
        conv = self.converter.convert(outputfile, options, timeout=None)
        _, cmds = next(conv)
        self.log.debug("Subtitle extraction FFmpeg command:")
        self.log.debug(self.mp.printableFFMPEGCommand(cmds))
        for _, debug in conv:
          self.log.debug(debug)
        self.log.info("%s created." % outputfile)
        rips.append(outputfile)
      except (FFMpegConvertError, ConverterError):
        self.log.error("Unable to create external %s subtitle file for stream %s, may be an incompatible format." % (extension, options["index"]))
        self.mp.removeFile(outputfile)
        continue
      except KeyboardInterrupt:
        raise
      except:
        self.log.exception("Unable to create external subtitle file for stream %s." % (options["index"]))
      self.mp.setPermissions(outputfile)
    return rips
