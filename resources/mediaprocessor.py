"""
Core media processing pipeline for SMA-NG.

Provides the MediaProcessor class which orchestrates the full conversion
workflow: source validation, FFmpeg option generation, conversion, metadata
tagging, file placement, and post-processing notifications.
"""

import json
import logging
import os
import re
import shutil
import sys
import time

from autoprocess import plex
from converter import Converter, ConverterError, FFMpegConvertError
from converter.avcodecs import BaseCodec
from resources.analyzer import AnalyzerRecommendations, build_recommendations
from resources.extensions import bad_sub_extensions, subtitle_codec_extensions
from resources.lang import getAlpha3TCode
from resources.metadata import Metadata
from resources.openvino_analyzer import OpenVINOAnalyzerBackend, OpenVINOAnalyzerError
from resources.postprocess import PostProcessor
from resources.subtitles import SubtitleProcessor

try:
    import cleanit
except ImportError:
    cleanit = None
try:
    from ffsubsync import ffsubsync
except ImportError:
    ffsubsync = None
# Custom Functions
from resources.custom import *


class MediaProcessor:
    default_channel_bitrate = 128

    # Maps title keywords to disposition flags, used by titleDispositionCheck
    _TITLE_DISPO_MAP = {
        "comment": "comment",
        "hearing": "hearing_impaired",
        "sdh": "hearing_impaired",
        "visual": "visual_impaired",
        "forced": "forced",
    }

    def __init__(self, settings, logger=None):
        """
        Initialize a MediaProcessor with the given settings.

        Sets up the FFmpeg converter and prepares internal state. Does not
        begin processing until process() or fullprocess() is called.
        """
        self.log = logger or logging.getLogger(__name__)
        self.settings = settings
        self.converter = Converter(settings.ffmpeg, settings.ffprobe)
        self.deletesubs = set()
        self.subtitles = SubtitleProcessor(self)

    def fullprocess(
        self, inputfile, mediatype, reportProgress=False, original=None, info=None, tmdbid=None, tvdbid=None, imdbid=None, season=None, episode=None, language=None, tagdata=None, post=True
    ):
        """
        Run the complete processing pipeline for a single input file.

        Validates the source, fetches TMDB metadata, runs conversion, tags the
        output, relocates the moov atom, renames the file, copies/moves to
        destination directories, and triggers post-processing notifications.

        Returns a list of output file paths on success, or False on failure.
        """
        try:
            info = self.isValidSource(inputfile, tagdata=tagdata)
            if info:
                self.log.info("Processing %s." % inputfile)

                try:
                    tagdata = tagdata or Metadata(mediatype, tvdbid=tvdbid, tmdbid=tmdbid, imdbid=imdbid, season=season, episode=episode, original=original, language=language)
                    tmdbid = tagdata.tmdbid
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.log.exception("Unable to get metadata.")
                    tagdata = None

                output = self.process(inputfile, original=original, info=info, tagdata=tagdata, reportProgress=reportProgress)

                if output:
                    if not language:
                        language = self.settings.taglanguage or self.getDefaultAudioLanguage(output["options"]) or None
                    self.log.debug("Tag language setting is %s, using language %s for tagging." % (self.settings.taglanguage or None, language))
                    # Tag with metadata
                    tagfailed = False
                    if self.settings.tagfile and tagdata:
                        try:
                            self.log.info("Tagging %s with TMDB ID %s." % (inputfile, tagdata.tmdbid))
                            tagdata.writeTags(
                                output["output"], inputfile, self.converter, self.settings.artwork, self.settings.thumbnail, output["x"], output["y"], cues_to_front=output["cues_to_front"]
                            )
                        except KeyboardInterrupt:
                            raise
                        except Exception:
                            self.log.exception("Unable to tag file")
                            tagfailed = True

                    # QTFS
                    if self.settings.relocate_moov and not tagfailed:
                        self.QTFS(output["output"])

                    # File renaming
                    if self.settings.naming_enabled and tagdata:
                        try:
                            from resources.naming import generate_name, rename_file

                            new_name = generate_name(output["output"], info, tagdata, self.settings, log=self.log, lookup_path=inputfile)
                            if new_name:
                                output["output"] = rename_file(output["output"], new_name, log=self.log)
                        except Exception:
                            self.log.exception("Error during file rename")

                    # Reverse Ouput
                    output["output"] = self.restoreFromOutput(inputfile, output["output"])
                    for i, sub in enumerate(output["external_subs"]):
                        output["external_subs"][i] = self.restoreFromOutput(inputfile, sub)

                    # Copy to additional locations
                    output_files = self.replicate(output["output"])
                    for sub in [x for x in output["external_subs"] if os.path.exists(x)]:
                        output_files.extend(self.replicate(sub))

                    for file in output_files:
                        self.setPermissions(file)

                    # Plex .plexmatch file (after file is in final destination)
                    if self.settings.plexmatch_enabled and tagdata:
                        try:
                            from resources.metadata import update_plexmatch

                            update_plexmatch(output["output"], tagdata, self.settings, log=self.log)
                        except Exception:
                            self.log.exception("Error updating .plexmatch")

                    if post:
                        self.post(output_files, mediatype, tmdbid=tmdbid, tvdbid=tvdbid, imdbid=imdbid, season=season, episode=episode)

                    return output_files
            else:
                self.log.info("File %s is not valid" % inputfile)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Error processing")
        return False

    def post(self, output_files, mediatype, tvdbid=None, tmdbid=None, imdbid=None, season=None, episode=None):
        """
        Run post-processing steps after a successful conversion.

        Executes any configured post-process scripts and triggers a Plex
        library refresh if enabled in settings.
        """
        if self.settings.postprocess:
            # Run any post process scripts
            postprocessor = PostProcessor(output_files, self.log, wait=self.settings.waitpostprocess)
            postprocessor.setEnv(mediatype, tmdbid, season, episode)
            postprocessor.run_scripts()

        # Refresh Plex
        if self.settings.Plex.get("refresh", False):
            try:
                plex.refreshPlex(self.settings, output_files[0], self.log)
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Error refreshing Plex.")

    # Process a file from start to finish, with checking to make sure formats are compatible with selected settings
    def process(self, inputfile, reportProgress=False, original=None, info=None, progressOutput=None, tagdata=None):
        """
        Build FFmpeg options for the input file and run the conversion.

        Generates stream options via generateOptions(), invokes FFmpeg, and
        returns a dict containing input/output paths, stream options, external
        subtitle paths, and video dimensions. Returns None on failure.

        The original input file is NOT recycled or deleted here. The caller is
        responsible for calling _cleanup_input (or _recycle_to_bin for the
        no-moveto case) after the output has been safely placed at its final
        destination. The returned dict includes a ``"delete"`` flag indicating
        whether the caller should perform cleanup.
        """
        self.log.debug("Process started.")

        delete = self.settings.delete
        ripped_subs = []
        downloaded_subs = []

        info = info or self.isValidSource(inputfile, tagdata=tagdata)
        self.settings.output_dir = self.settings.output_dir if self.outputDirHasFreeSpace(inputfile) else None

        if not info:
            return None

        try:
            options, preopts, postopts, ripsubopts, downloaded_subs = self.generateOptions(inputfile, info=info, original=original, tagdata=tagdata)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Unable to generate options, unexpected exception occurred.")
            return None

        if options and tagdata and getattr(tagdata, "title", None):
            options["title"] = tagdata.title

        if self.canBypassConvert(inputfile, info, options):
            outputfile = inputfile
            self.log.info("Bypassing conversion and setting outputfile to inputfile.")
        else:
            if not options:
                self.log.error("Error converting, inputfile %s had a valid extension but returned no data. Either the file does not exist, was unreadable, or was an incorrect format." % inputfile)
                return None
            outputfile, inputfile, ripped_subs = self._run_ffmpeg(inputfile, options, preopts, postopts, ripsubopts, downloaded_subs, reportProgress, progressOutput)
            if outputfile is None:
                return None

        self.log.debug("%s created from %s successfully." % (outputfile, inputfile))

        if outputfile == inputfile:
            if self.settings.output_dir:
                try:
                    outputfile = os.path.join(self.settings.output_dir, os.path.split(inputfile)[1])
                    self.log.debug("Outputfile set to %s." % outputfile)
                    shutil.copy(inputfile, outputfile)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.log.exception("Error moving file to output directory.")
                    delete = False
            else:
                delete = False

        dim = self.getDimensions(outputfile)
        input_extension = self.parseFile(inputfile)[2]
        output_extension = self.parseFile(outputfile)[2]

        return {
            "input": inputfile,
            "input_extension": input_extension,
            "input_deleted": False,
            "delete": delete,
            "output": outputfile,
            "output_extension": output_extension,
            "options": options,
            "preopts": preopts,
            "postopts": postopts,
            "external_subs": downloaded_subs + ripped_subs,
            "x": dim["x"],
            "y": dim["y"],
            "cues_to_front": self.settings.output_format in ["mkv"] and self.settings.relocate_moov,
        }

    def _run_ffmpeg(self, inputfile, options, preopts, postopts, ripsubopts, downloaded_subs, reportProgress, progressOutput):
        """Log options, rip external subs, run FFmpeg conversion.

        Returns (outputfile, inputfile, ripped_subs) on success, or (None, inputfile, []) on failure.
        Note: inputfile may change after convert() if FFmpeg renames it.
        """
        try:
            input_dir, filename, input_extension = self.parseFile(inputfile)
            finaloutputfile, _ = self.getOutputFile(input_dir, filename, input_extension)
            if finaloutputfile:
                options["filename"] = os.path.basename(finaloutputfile)
            self.log.info("Output Data: %s" % json.dumps(options, sort_keys=False))
            self.log.debug("Preopts: %s" % json.dumps(preopts, sort_keys=False))
            self.log.debug("Postopts: %s" % json.dumps(postopts, sort_keys=False))
            if not self.settings.embedsubs:
                self.log.debug("Subtitle Extracts: %s" % json.dumps(ripsubopts, sort_keys=False))
            if self.settings.downloadsubs:
                self.log.debug("Downloaded Subtitles: %s" % json.dumps(downloaded_subs, sort_keys=False))
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Unable to log options.")

        ripped_subs = self.subtitles.ripSubs(inputfile, ripsubopts)
        for rs in ripped_subs:
            self.cleanExternalSub(rs)

        try:
            outputfile, inputfile = self.convert(options, preopts, postopts, reportProgress, progressOutput)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Unexpected exception encountered during conversion")
            return None, inputfile, []

        if not outputfile:
            self.log.debug("Error converting, no outputfile generated for inputfile %s." % inputfile)
            return None, inputfile, ripped_subs

        return outputfile, inputfile, ripped_subs

    def _recycle_to_bin(self, inputfile):
        """Copy inputfile to the recycle bin without deleting it.

        No-op when recycle_bin is not configured or the file does not exist.
        Call this before any operation that will overwrite the input path so
        the original bytes are preserved before the path is replaced.
        """
        if not self.settings.recycle_bin or not os.path.isfile(inputfile):
            return
        try:
            os.makedirs(self.settings.recycle_bin, exist_ok=True)
            recycle_dst = os.path.join(self.settings.recycle_bin, os.path.basename(inputfile))
            if os.path.exists(recycle_dst):
                base, ext = os.path.splitext(os.path.basename(inputfile))
                i = 2
                while os.path.exists(recycle_dst):
                    recycle_dst = os.path.join(self.settings.recycle_bin, "%s.%d%s" % (base, i, ext))
                    i += 1
            self._atomic_copy(inputfile, recycle_dst)
            self.log.info("Original file recycled to %s." % recycle_dst)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Failed to copy original to recycle bin %s." % self.settings.recycle_bin)

    def _cleanup_input(self, inputfile, delete):
        """Copy inputfile to the recycle bin then unlink it and any staged subtitle files.

        Returns True if the input file was deleted.
        Call this only after the output file has been successfully placed at its
        final destination so the original is never removed before the output is safe.
        """
        if delete:
            self._recycle_to_bin(inputfile)

        deleted = False
        if delete:
            self.log.debug("Attempting to remove %s." % inputfile)
            if self.removeFile(inputfile):
                self.log.debug("%s deleted." % inputfile)
                deleted = True
            else:
                self.log.error("Couldn't delete %s." % inputfile)

            for subfile in self.deletesubs:
                self.log.debug("Attempting to remove subtitle %s." % subfile)
                if self.removeFile(subfile):
                    self.log.debug("Subtitle %s deleted." % subfile)
                else:
                    self.log.debug("Unable to delete subtitle %s." % subfile)
            self.deletesubs = set()

        return deleted

    # Wipe disposition data based on settings
    def cleanDispositions(self, info):
        """Remove dispositions listed in sanitize-disposition settings from all streams."""
        for stream in info.streams:
            for dispo in self.settings.sanitize_disposition:
                self.log.debug("Setting %s to False for stream %d [sanitize-disposition]." % (dispo, stream.index))
                stream.disposition[dispo] = False

    # Get title for video stream based on disposition
    def videoStreamTitle(self, stream, options, hdr=False, tagdata=None):
        """
        Return a human-readable title for a video stream.

        Derives a label such as "4K HDR", "FHD", "HD", or "SD" from the
        stream's resolution. Calls the custom streamTitle hook first if
        defined, or preserves the original title if keep-titles is enabled.
        """
        width = options.get("width", 0)
        height = options.get("height", 0)
        if not width and not height:
            width = stream.video_width or 0
            height = stream.video_height or 0

        if streamTitle:
            try:
                customTitle = streamTitle(self, stream, options, hdr=hdr, tagdata=tagdata)
                if customTitle is not None:
                    return customTitle
            except Exception:
                self.log.exception("Custom streamTitle exception")

        if self.settings.keep_titles and stream.metadata.get("title"):
            return stream.metadata.get("title")

        output = "Video"

        if width >= 7600 or height >= 4300:
            output = "8K"
        elif width >= 3800 or height >= 2100:
            output = "4K"
        elif width >= 1900 or height >= 1060:
            output = "FHD"
        elif width >= 1260 or height >= 700:
            output = "HD"
        else:
            output = "SD"

        if hdr:
            output += " HDR"
        return output.strip() if output else None

    # Get title for audio stream based on disposition
    def audioStreamTitle(self, stream, options, tagdata=None):
        """
        Return a human-readable title for an audio stream.

        Derives a label such as "Stereo", "5.1 Channel Atmos", or "Mono" from
        channel count and disposition. Calls the custom streamTitle hook first
        if defined, or preserves the original title if keep-titles is enabled.
        """
        if streamTitle:
            try:
                customTitle = streamTitle(self, stream, options, tagdata=tagdata)
                if customTitle is not None:
                    return customTitle
            except Exception:
                self.log.exception("Custom streamTitle exception")

        if self.settings.keep_titles and stream.metadata.get("title"):
            return stream.metadata.get("title")

        channels = options.get("channels", 0)
        output = "Audio"
        if channels == 1:
            output = "Mono"
        elif channels == 2:
            output = "Stereo"
        elif channels > 2:
            output = "%d.1 Channel" % (channels - 1)

        if options.get("codec") == "copy":
            if self.isAudioStreamAtmos(stream):
                output += " Atmos"

        disposition = stream.disposition
        for dispo in BaseCodec.DISPO_STRINGS:
            if disposition.get(dispo):
                output += " (%s)" % BaseCodec.DISPO_STRINGS[dispo]
        return output.strip() if output else None

    # Get title for subtitle stream based on disposition
    def subtitleStreamTitle(self, stream, options, imageBased=False, path=None, tagdata=None):
        """
        Return a human-readable title for a subtitle stream.

        Builds a label from the stream's disposition flags (e.g. "Forced",
        "SDH", "Hearing Impaired") or falls back to "Full". Calls the custom
        streamTitle hook first if defined, or preserves the original title if
        keep-titles is enabled.
        """
        if streamTitle:
            try:
                customTitle = streamTitle(self, stream, options, imageBased=imageBased, path=path, tagdata=None)
                if customTitle is not None:
                    return customTitle
            except Exception:
                self.log.exception("Custom streamTitle exception")

        if self.settings.keep_titles and stream.metadata.get("title"):
            return stream.metadata.get("title")

        output = ""
        disposition = stream.disposition
        for dispo in BaseCodec.DISPO_STRINGS:
            if disposition.get(dispo):
                output += "%s " % BaseCodec.DISPO_STRINGS[dispo]
        if not output.strip():
            output = "Full"
        return output.strip()

    # Determine if a file can be read by FFPROBE
    def isValidSource(self, inputfile, tagdata=None):
        """
        Check whether the input file is a valid media source FFprobe can read.

        Rejects files with blacklisted extensions, files below the minimum size
        threshold, files with no video or audio streams, and files that fail the
        custom validation hook. Returns a MediaInfo object on success, or None
        on failure.
        """
        try:
            extension = self.parseFile(inputfile)[2]
            if extension in self.settings.ignored_extensions:
                self.log.debug("Invalid source, extension is blacklisted [ignored-extensions].")
                return None
            if self.settings.minimum_size > 0 and os.path.getsize(inputfile) < (self.settings.minimum_size * 1000000):
                self.log.debug("Invalid source, below minimum size threshold [minimum-size].")
                return None
            info = self.converter.probe(inputfile)
            if not info:
                self.log.debug("Invalid source, no data returned.")
                return None
            if not info.video:
                self.log.debug("Invalid source, no video stream detected.")
                return None
            if not info.audio or len(info.audio) < 1:
                self.log.debug("Invalid source, no audio stream detected.")
                return None
            if validation:
                try:
                    if not validation(self, info, inputfile, tagdata):
                        self.log.debug("Failed custom validation check, file is not valid.")
                        return None
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.log.exception("Custom validation check error.")
            return info
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("isValidSource unexpectedly threw an exception, returning None.")
            return None

    # Determine if a sub is an Atmos track
    def isAudioStreamAtmos(self, stream):
        """Return True if the audio stream profile indicates Dolby Atmos."""
        return stream.profile and "atmos" in stream.profile.lower()

    # Determine if a file can be read by FFPROBE and is a subtitle only
    def isValidSubtitleSource(self, inputfile):
        """
        Check whether the input file is a valid standalone subtitle source.

        Rejects files with bad subtitle extensions or ignored extensions, files
        that contain video or audio, and files with no subtitle streams. Returns
        a MediaInfo object on success, or None on failure.
        """
        _, _, extension = self.parseFile(inputfile)
        if extension in bad_sub_extensions or extension in self.settings.ignored_extensions:
            return None
        try:
            info = self.converter.probe(inputfile)
            if info:
                if len(info.subtitle) < 1 or info.video or len(info.audio) > 0:
                    return None
            return info
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("isValidSubtitleSource unexpectedly threw an exception, returning None.")
            return None

    # Parse filename of external subtitle file and set appropriate disposition and language information
    def processExternalSub(self, valid_external_sub, inputfile):
        """Delegate to SubtitleProcessor.processExternalSub."""
        return self.subtitles.processExternalSub(valid_external_sub, inputfile)

    # Default audio language based on encoder options
    def getDefaultAudioLanguage(self, options):
        """
        Return the language code of the default audio stream from the options dict.

        Accepts either a dict (output of generateOptions) or a list of
        MediaStreamInfo objects. Returns None if no default stream is found.
        """
        if isinstance(options, dict):
            for a in options.get("audio", []):
                if "+default" in a.get("disposition", "").lower():
                    self.log.debug("Default audio language is %s." % a.get("language"))
                    return a.get("language")
        else:
            for a in options.audio:
                if a.disposition.get("default"):
                    self.log.debug("Default audio language is %s." % a.metadata.get("language"))
                    return a.metadata.get("language")
        return None

    # Get values for width and height to be passed to the tagging classes for proper HD tags
    def getDimensions(self, inputfile):
        """
        Return the pixel width and height of the first video stream in the file.

        Returns a dict with keys 'x' (width) and 'y' (height), defaulting to 0
        if the file cannot be probed.
        """
        info = self.converter.probe(inputfile)

        if info and info.video:
            self.log.debug("Height: %s" % info.video.video_height)
            self.log.debug("Width: %s" % info.video.video_width)

            return {"y": info.video.video_height, "x": info.video.video_width}

        return {"y": 0, "x": 0}

    # Estimate the video bitrate
    def estimateVideoBitrate(self, info, baserate=64000, tolerance=0.95):
        """
        Estimate the video bitrate in kbps by subtracting audio from total bitrate.

        Falls back to the detected video stream bitrate if arithmetic fails.
        Returns None if no bitrate data is available.
        """
        # attempt to return the detected video bitrate, if applicable
        min_video_bitrate = (info.video.bitrate / 1000) if info.video and info.video.bitrate else None

        try:
            total_bitrate = info.format.bitrate
            audio_bitrate = 0
            min_audio_bitrate = 0
            for a in info.audio:
                audio_bitrate += a.bitrate if a.bitrate else (baserate * (a.audio_channels or 2))

            self.log.debug("Total bitrate is %s." % info.format.bitrate)
            self.log.debug("Total audio bitrate is %s." % audio_bitrate)
            audio_bitrate += min_audio_bitrate
            calculated_bitrate = (total_bitrate - audio_bitrate) / 1000
            self.log.debug("Estimated video bitrate is %s." % (calculated_bitrate * 1000))
            return min_video_bitrate if min_video_bitrate and min_video_bitrate < (calculated_bitrate * tolerance) else (calculated_bitrate * tolerance)
        except Exception:
            if info.format.bitrate:
                return min_video_bitrate if min_video_bitrate and min_video_bitrate < (info.format.bitrate / 1000) else (info.format.bitrate / 1000)
        return min_video_bitrate

    def _match_bitrate_profile(self, source_kbps, hd=False):
        """Return the best-matching crf-profile for *source_kbps*, or ``None``.

        When *hd* is ``True`` and ``crf-profiles-hd`` is configured, the HD
        profiles are used; otherwise falls back to ``crf-profiles``.

        Profiles are sorted by ``source_kbps`` ascending.  The matched profile
        is the one with the highest ``source_kbps`` threshold that is still
        less than or equal to the source bitrate — i.e. the last profile whose
        floor the source meets or exceeds.  Returns ``None`` when no profiles
        are configured or the source bitrate is unknown/zero.
        """
        profiles = (self.settings.vbitrate_profiles_hd or self.settings.vbitrate_profiles) if hd else self.settings.vbitrate_profiles
        if not profiles or not source_kbps:
            return None
        match = None
        for p in profiles:
            if source_kbps >= p["source_kbps"]:
                match = p
        return match

    # Generate a JSON formatter dataset with the input and output information and ffmpeg command for a theoretical conversion
    def jsonDump(self, inputfile, original=None, tagdata=None):
        """
        Return a JSON string describing what a conversion would produce without running it.

        Includes input stream data, generated FFmpeg options, and the full
        FFmpeg command line. Used by the -oo (output-options) CLI flag.
        """
        dump = {}
        dump["input"], info = self.generateSourceDict(inputfile, tagdata)
        analyzer_recommendations = self._get_analyzer_recommendations(inputfile, info) if info else AnalyzerRecommendations()
        dump["analyzer"] = self._serialize_analyzer_recommendations(analyzer_recommendations)
        dump["output"], dump["preopts"], dump["postopts"], dump["ripsubopts"], dump["downloadedsubs"] = self.generateOptions(
            inputfile, info=info, original=original, tagdata=tagdata, analyzer_recommendations=analyzer_recommendations
        )
        if self.canBypassConvert(inputfile, info, dump["output"]):
            dump["output"] = dump["input"]
            dump["output"]["bypassConvert"] = True
            dump["preopts"] = None
            dump["postopts"] = None
            dump["ripsubopts"] = None
            dump["downloadedsubs"] = None
        else:
            parsed = self.converter.parse_options(dump["output"])
            input_dir, filename, input_extension = self.parseFile(inputfile)
            outputfile, _ = self.getOutputFile(input_dir, filename, input_extension)
            cmds = self.converter.ffmpeg.generateCommands(outputfile, parsed, dump["preopts"], dump["postopts"])
            dump["ffmpeg_commands"] = []
            dump["ffmpeg_commands"].append(" ".join('"%s"' % item if " " in item and '"' not in item else item for item in cmds))
            for suboptions in dump["ripsubopts"]:
                subparsed = self.converter.parse_options(suboptions)
                extension = self.getSubExtensionFromCodec(suboptions["format"])
                suboutputfile = self.getSubOutputFileFromOptions(inputfile, suboptions, extension)
                subcmds = self.converter.ffmpeg.generateCommands(suboutputfile, subparsed)
                dump["ffmpeg_commands"].append(" ".join(str(item) for item in subcmds))
            for sub in dump["downloadedsubs"]:
                self.log.debug("Cleaning up downloaded sub %s which was only used to simulate options." % (sub))
                self.removeFile(sub)

        return json.dumps(dump, sort_keys=False, indent=4).replace("\\\\", "\\").replace('\\"', '"')

    # Generate a dict of data about a source file
    def generateSourceDict(self, inputfile, tagdata=None):
        """
        Build a serializable dict describing the source file's streams and format.

        Calls isValidSource() and returns a tuple of (source_dict, MediaInfo).
        Used by jsonDump() to populate the input section of the options preview.
        """
        output = {}
        _, _, input_extension = self.parseFile(inputfile)
        output["extension"] = input_extension
        probe = self.isValidSource(inputfile, tagdata)
        self.titleDispositionCheck(probe)
        if probe:
            output.update(probe.json)
        else:
            output["error"] = "Invalid input, unable to read"
        return output, probe

    # Pass over audio and subtitle streams to ensure the language properties are safe, return any adjustments made to SWL/AWL
    def safeLanguage(self, info, tagdata=None):
        """
        Normalize stream language codes and expand allowed language lists as needed.

        Standardizes undefined language tags, optionally appends the original
        content language from TMDB metadata, and relaxes audio/subtitle language
        whitelists if no streams match. Returns the (possibly modified) awl and
        swl lists.
        """
        awl = self.settings.awl
        original_language = None
        if self.settings.audio_original_language and tagdata:
            try:
                original_language = tagdata.original_language
                if awl and original_language not in awl:
                    self.log.debug("Appending %s to allowed audio languages [include-original-language]." % (original_language))
                    awl.append(original_language)
                    self.settings.adl = self.settings.adl or original_language
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Exception while trying to determine original language [include-original-language].")

        swl = self.settings.swl
        if self.settings.subtitle_original_language and tagdata:
            try:
                original_language = tagdata.original_language
                if swl and original_language not in swl:
                    self.log.debug("Appending %s to allowed subtitle languages [include-original-language]." % (original_language))
                    swl.append(original_language)
                    self.settings.sdl = self.settings.sdl or original_language
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Exception while trying to determine original language [include-original-language].")

        # Loop through audio streams and clean up language metadata by standardizing undefined languages and applying the ADL setting
        for a in info.audio:
            a.metadata["language"] = getAlpha3TCode(a.metadata.get("language"), self.settings.adl)

        if len(awl) > 0 and not any(a.metadata.get("language") in awl and self.validDisposition(a, self.settings.ignored_audio_dispositions) for a in info.audio):
            self.log.debug("No valid audio tracks found, relaxing audio language restrictions.")
            awl = []

        # Prep subtitle streams by cleaning up languages and setting SDL
        for s in info.subtitle:
            s.metadata["language"] = getAlpha3TCode(s.metadata.get("language"), self.settings.sdl)

        if len(swl) > 0 and not any(s.metadata.get("language") in swl and self.validDisposition(s, self.settings.ignored_subtitle_dispositions) for s in info.subtitle):
            self.log.debug("No valid subtitle tracks found, relaxing subtitle language restrictions.")
            swl = []

        return awl, swl

    # Check and see if clues about the disposition are in the title
    def titleDispositionCheck(self, info):
        """
        Set disposition flags on streams whose title metadata contains known keywords.

        Maps title keywords like 'comment', 'hearing', 'sdh', and 'forced' to
        their corresponding FFmpeg disposition flags on each stream.
        """
        for stream in info.streams:
            title = stream.metadata.get("title", "").lower()
            for k, flag in self._TITLE_DISPO_MAP.items():
                if k in title:
                    stream.disposition[flag] = True
                    self.log.debug("Found %s in stream title, setting %s disposition to True." % (k, flag))

    # Get source audio tracks that meet criteria for being the same based on codec combination, language, and dispostion
    def mapStreamCombinations(self, audiostreams):
        """
        Identify groups of audio streams that are likely duplicates.

        Checks stream-codec-combinations settings to find sequences of streams
        with the same codec pattern, language, and dispositions. Returns a list
        of index groups, where each group is a list of stream indexes that are
        considered copies.
        """
        combinations = []
        for combo in self.settings.stream_codec_combinations:
            indexes = self.sublistIndexes([x.codec for x in audiostreams], combo)
            self.log.debug("Found indexes %s where codec parameters matched combination %s" % (indexes, combo))
            for index in indexes:
                stream_sublist = audiostreams[index : index + len(combo)]
                language_sublist = [x.metadata["language"] for x in stream_sublist]
                dispo_sublist = [dict(x.disposition) for x in stream_sublist]
                for x in dispo_sublist:
                    x["default"] = False
                same_language = all(x == language_sublist[0] for x in language_sublist)
                same_dispo = all(x == dispo_sublist[0] for x in dispo_sublist)
                if same_language and same_dispo:
                    combinations.append([x.index for x in stream_sublist])
        self.log.debug("The following stream indexes have been identified as being copies: %s [stream-codec-combinations]." % combinations)
        return combinations

    # Iterate through generated options and remove potential duplicate streams based on mapped combinations
    def purgeDuplicateStreams(self, combinations, options, info, acodecs, uacodecs):
        """
        Remove duplicate audio streams from the options list.

        For each stream combination identified by mapStreamCombinations(),
        keeps the best stream (preferring copy, then highest bitrate, then
        default) and removes the rest. Returns True if any streams were purged.
        """
        purge = []
        for combo in combinations:
            filtered_options = [x for x in options if x["map"] in combo]
            channels = sorted(list(set([x["channels"] for x in filtered_options])), reverse=True)
            for c in channels:
                same_channel_options = [x for x in filtered_options if x["channels"] == c]
                if len(same_channel_options) > 1:
                    allowed_codecs = uacodecs if c <= 2 and uacodecs else acodecs
                    if any(x for x in same_channel_options if x["codec"] == "copy" and self.getSourceStream(x["map"], info).codec in allowed_codecs):
                        # Remuxable stream found but other audio streams of same channel quantity present
                        self.duplicateStreamSort(same_channel_options, info)
                        purge.extend(same_channel_options[1:])
                    else:
                        codecs = [self.getSourceStream(x["map"], info).codec if x["codec"] == "copy" else x["codec"] for x in same_channel_options]
                        for codec in set(codecs):
                            same_codec_options = [
                                x
                                for x in same_channel_options
                                if Converter.codec_name_to_ffprobe_codec_name(x["codec"]) == codec or (x["codec"] == "copy" and self.getSourceStream(x["map"], info).codec == codec)
                            ]
                            if len(same_codec_options) > 1:
                                # No remuxable streams but 2 streams of the output codec are being created
                                self.duplicateStreamSort(same_codec_options, info)
                                purge.extend(same_codec_options[1:])
        self.log.debug("Purging the following streams: %s" % json.dumps(purge))
        self.log.info("Found %d streams that can be removed from the output file since they will be duplicates [stream-codec-combinations]." % len(purge))
        for p in purge:
            try:
                options.remove(p)
            except Exception:
                self.log.debug("Unable to purge stream, may already have been removed.")
        return len(purge) > 0

    # Sorter used by purgeDuplicateStreams
    def duplicateStreamSort(self, options, info):
        """Sort stream options in-place: copy first, then by default, then by bitrate descending."""
        options.sort(key=lambda x: x["bitrate"], reverse=True)
        options.sort(key=lambda x: self.getSourceStream(x["map"], info).disposition["default"], reverse=True)
        options.sort(key=lambda x: x["codec"] == "copy", reverse=True)

    # Get indexes for sublists
    def sublistIndexes(self, x, y):
        """Return all starting indexes at which sublist y appears within list x."""
        indexes = []
        occ = [i for i, a in enumerate(x) if a == y[0]]
        for b in occ:
            if x[b : b + len(y)] == y:
                indexes.append(b)
        return indexes

    # Ensure ffprobe variant of codec is present
    def ffprobeSafeCodecs(self, codecs):
        """
        Ensure the FFprobe codec name is included alongside the FFmpeg encoder name.

        Some codecs have different names in ffprobe vs ffmpeg (e.g. 'h264' vs
        'libx264'). This adds the ffprobe name to the list so remux detection
        works correctly. Returns the (possibly extended) codec list.
        """
        if codecs:
            ffpcodec = Converter.codec_name_to_ffprobe_codec_name(codecs[0])
            if ffpcodec and ffpcodec not in codecs:
                self.log.debug("Codec pool is missing the FFPROBE value of the primary conversion codec %s which will prevent remuxing, adding %s to the list." % (codecs[0], ffpcodec))
                codecs.append(ffpcodec)
        return codecs

    def _get_analyzer_recommendations(self, inputfile, info):
        """Return bounded per-job analyzer recommendations for the current input."""
        analyzer_config = getattr(self.settings, "analyzer", None) or {}
        if not analyzer_config.get("enabled"):
            return AnalyzerRecommendations()

        backend = (analyzer_config.get("backend") or "").strip().lower()
        try:
            if backend == "openvino":
                observations = OpenVINOAnalyzerBackend(analyzer_config).analyze(inputfile=inputfile, info=info)
            else:
                self.log.warning("Analyzer backend '%s' is not supported, skipping analyzer recommendations." % backend)
                return AnalyzerRecommendations()
        except OpenVINOAnalyzerError as exc:
            self.log.warning("Analyzer backend unavailable, continuing without analyzer recommendations: %s" % exc)
            return AnalyzerRecommendations()
        except Exception:
            self.log.exception("Analyzer backend failed unexpectedly, continuing without analyzer recommendations.")
            return AnalyzerRecommendations()

        return build_recommendations(observations, analyzer_config)

    @staticmethod
    def _codec_family(codec):
        """Return a normalized codec family name for analyzer-driven reordering."""
        if not codec:
            return codec

        normalized = codec.lower()
        aliases = {
            "hevc": "h265",
            "x265": "h265",
            "x264": "h264",
        }
        for prefix in ["h265", "hevc", "x265", "h264", "x264", "av1", "vp9"]:
            if normalized.startswith(prefix):
                return aliases.get(prefix, prefix)
        return aliases.get(normalized, normalized)

    @classmethod
    def _reorder_codec_pool(cls, codec_pool, preferred_order):
        """Return codec_pool reordered by preferred_order while preserving mapped encoder priority within a family."""
        if not preferred_order:
            return codec_pool

        reordered = []
        seen = set()
        preferred_families = [cls._codec_family(codec) for codec in preferred_order]

        for family in preferred_families:
            for codec in codec_pool:
                if cls._codec_family(codec) == family and codec not in seen:
                    reordered.append(codec)
                    seen.add(codec)

        for codec in codec_pool:
            if codec not in seen:
                reordered.append(codec)
                seen.add(codec)
        return reordered or codec_pool

    @staticmethod
    def _merge_video_filters(current_filter, additional_filters):
        """Merge new video filters into an existing comma-separated filter string."""
        filters = [f for f in (current_filter or "").split(",") if f]
        for new_filter in additional_filters or []:
            if new_filter and new_filter not in filters:
                filters.append(new_filter)
        return ",".join(filters) if filters else None

    @staticmethod
    def _serialize_analyzer_recommendations(recommendations):
        """Convert analyzer recommendations into a compact serializable dict."""
        payload = {
            "codec_order": recommendations.codec_order,
            "bitrate_ratio_multiplier": recommendations.bitrate_ratio_multiplier,
            "max_bitrate_ceiling": recommendations.max_bitrate_ceiling,
            "preset": recommendations.preset,
            "filters": recommendations.filters,
            "force_reencode": recommendations.force_reencode,
            "reasons": recommendations.reasons,
        }
        return {key: value for key, value in payload.items() if value not in (None, [], False, "")}

    def _log_analyzer_recommendations(self, recommendations):
        """Emit a structured log line when analyzer recommendations are present."""
        payload = self._serialize_analyzer_recommendations(recommendations)
        if payload:
            self.log.info("Analyzer recommendations: %s" % json.dumps(payload, sort_keys=True))

    # Generate a dict of options to be passed to FFMPEG based on selected settings and the source file parameters and streams
    def generateOptions(self, inputfile, info=None, original=None, tagdata=None, analyzer_recommendations=None):
        """
        Build the full FFmpeg options dict for converting the input file.

        Analyses all video, audio, and subtitle streams against configured
        settings (codecs, languages, bitrates, dispositions, HDR, hardware
        acceleration) and returns a 5-tuple of:
        (options, preopts, postopts, ripsubopts, downloaded_subs)

        Returns (None, None, None, None, None) if the file cannot be probed.
        """
        sources = [inputfile]
        ripsubopts = []

        codecs = self.converter.ffmpeg.codecs
        pix_fmts = self.converter.ffmpeg.pix_fmts

        info = info or self.converter.probe(inputfile)

        if not info:
            self.log.error("FFPROBE returned no value for inputfile %s (exists: %s), either the file does not exist or is not a format FFPROBE can read." % (inputfile, os.path.exists(inputfile)))
            return None, None, None, None, None

        self.titleDispositionCheck(info)
        self.cleanDispositions(info)

        awl, swl = self.safeLanguage(info, tagdata)
        analyzer_recommendations = analyzer_recommendations or self._get_analyzer_recommendations(inputfile, info)
        self._log_analyzer_recommendations(analyzer_recommendations)

        try:
            self.log.info("Input Data: %s" % json.dumps(info.json, sort_keys=False))
        except Exception:
            self.log.exception("Unable to print input file data")

        ###############################################################
        # Video stream
        ###############################################################
        video_settings, vcodecs, vcodec, hdrOutput = self._select_video_codec(inputfile, info, codecs, pix_fmts, tagdata, analyzer_recommendations=analyzer_recommendations)

        ###############################################################
        # Audio streams
        ###############################################################
        self.log.debug("Reading audio streams.")
        audio_settings = self._build_audio_settings(inputfile, info, awl, tagdata)

        ###############################################################
        # Subtitle streams
        ###############################################################
        self.log.debug("Reading subtitle streams.")
        subtitle_settings, downloaded_subs = self._build_subtitle_settings(inputfile, info, swl, original, tagdata, sources, ripsubopts, video_settings, vcodecs)

        ###############################################################
        # Attachments
        ###############################################################
        attachments = []
        for f in info.attachment:
            if f.codec in self.settings.attachmentcodec and "mimetype" in f.metadata and "filename" in f.metadata:
                attachment = {"map": f.index, "codec": "copy", "filename": f.metadata["filename"], "mimetype": f.metadata["mimetype"]}
                attachments.append(attachment)

        ###############################################################
        # Chapters / external metadata
        ###############################################################
        metadata_map = []
        metadata_file = self.scanForExternalMetadata(inputfile)
        if metadata_file:
            self.log.info("Adding metadata file %s to sources and mapping metadata." % (metadata_file))
            sources.append(metadata_file)
            metadata_map = ["-map_chapters", str(sources.index(metadata_file)), "-map_metadata", str(sources.index(metadata_file))]
            if self.settings.strip_metadata:
                self.log.debug("Setting strip-metadata to False since metadata will be coming from external metadata file [strip-metadata].")
                self.settings.strip_metadata = False

        ###############################################################
        # Assemble options and build FFmpeg flags
        ###############################################################
        options = {"source": sources, "format": self.settings.output_format, "video": video_settings, "audio": audio_settings, "subtitle": subtitle_settings, "attachment": attachments}

        # Annotate action (copy vs transcode) for display in the log viewer
        if video_settings and video_settings.get("codec"):
            video_settings["action"] = "copy" if video_settings["codec"] == "copy" else "transcode"
        for a in audio_settings:
            if a.get("codec"):
                a["action"] = "copy" if a["codec"] == "copy" else "transcode"
        for s in subtitle_settings:
            if s.get("codec"):
                s["action"] = "copy" if s["codec"] == "copy" else "transcode"

        if self.settings.subencoding:
            options["sub-encoding"] = self.settings.subencoding

        preopts, postopts = self._build_preopts_postopts(vcodec, vcodecs, info, codecs, pix_fmts, options, metadata_map)

        self._warn_unsupported_encoders(codecs, [video_settings] + audio_settings + subtitle_settings + attachments)

        return options, preopts, postopts, ripsubopts, downloaded_subs

    def _build_audio_settings(self, inputfile, info, awl, tagdata):
        """Process all audio streams and return the audio_settings list."""
        audio_settings = []
        blocked_audio_languages = []
        blocked_audio_dispositions = []
        acombinations = self.mapStreamCombinations(info.audio)
        allowua = self.settings.ua_enabled and any(self.settings.ua)

        ua_codecs = self.ffprobeSafeCodecs(self.settings.ua)
        self.log.debug("Pool universal audio codecs is %s." % (ua_codecs))

        acodecs = self.ffprobeSafeCodecs(self.settings.acodec)
        self.log.debug("Pool of audio codecs is %s." % (acodecs))

        for a in info.audio:
            self.log.info("Audio detected for stream %s - %s %s %d channel." % (a.index, a.codec, a.metadata["language"], a.audio_channels))
            allowua = self._process_audio_stream(a, inputfile, info, awl, allowua, blocked_audio_languages, blocked_audio_dispositions, audio_settings, tagdata, acodecs=acodecs, ua_codecs=ua_codecs)

        self.purgeDuplicateStreams(acombinations, audio_settings, info, acodecs, ua_codecs)

        try:
            self.log.debug("Triggering audio track sort [audio.sorting-sorting].")
            audio_settings = self.sortStreams(audio_settings, self.settings.audio_sorting, awl, self.settings.audio_sorting_codecs or acodecs, info, acombinations, tagdata)
        except Exception:
            self.log.exception("Error sorting output stream options [audio.sorting-default-sorting].")

        try:
            self.setDefaultAudioStream(self.sortStreams(audio_settings, self.settings.audio_sorting_default, awl, self.settings.audio_sorting_codecs or acodecs, info, acombinations, tagdata))
        except Exception:
            self.log.exception("Unable to set the default audio stream.")

        return audio_settings

    def _build_subtitle_settings(self, inputfile, info, swl, original, tagdata, sources, ripsubopts, video_settings, vcodecs):
        """Process all subtitle streams and return (subtitle_settings, downloaded_subs).

        Mutates sources (external metadata), ripsubopts (rip opts), and video_settings (burn filter).
        """
        subtitle_settings = []
        blocked_subtitle_languages = []
        blocked_subtitle_dispositions = []
        valid_external_subs = []

        scodecs = self.ffprobeSafeCodecs(self.settings.scodec)
        self.log.debug("Pool of subtitle text based codecs is %s." % (scodecs))

        scodecs_image = self.ffprobeSafeCodecs(self.settings.scodec_image)
        self.log.debug("Pool of subtitle image based codecs is %s." % (scodecs_image))

        if not self.settings.ignore_embedded_subs:
            for s in info.subtitle:
                self.log.info("Subtitle detected for stream %s - %s %s." % (s.index, s.codec, s.metadata["language"]))
                self._process_subtitle_stream(
                    s,
                    inputfile,
                    info,
                    swl,
                    blocked_subtitle_languages,
                    blocked_subtitle_dispositions,
                    subtitle_settings,
                    valid_external_subs,
                    ripsubopts,
                    tagdata,
                    scodecs=scodecs,
                    scodecs_image=scodecs_image,
                )

        downloaded_subs = []
        try:
            downloaded_subs = self.subtitles.downloadSubtitles(inputfile, info.subtitle, swl, original, tagdata)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Unable to download subitltes [download-subs].")

        if not self.settings.embedonlyinternalsubs:
            valid_external_subs = self.subtitles.scanForExternalSubs(inputfile, swl, valid_external_subs)

        for external_sub in valid_external_subs:
            self._process_external_sub(external_sub, inputfile, swl, blocked_subtitle_languages, blocked_subtitle_dispositions, subtitle_settings, sources, tagdata)

        try:
            self.setDefaultSubtitleStream(subtitle_settings)
        except Exception:
            self.log.exception("Unable to set the default subtitle stream.")

        try:
            burnfilter = self.subtitles.burnSubtitleFilter(inputfile, info, swl, valid_external_subs, tagdata)
        except Exception:
            burnfilter = None
            self.log.exception("Encountered an error while trying to determine which subtitle stream for subtitle burn [burn-subtitle].")
        if burnfilter:
            self.log.debug("Found valid subtitle stream to burn into video, video cannot be copied [burn-subtitles].")
            video_settings["codec"] = vcodecs[0]
            video_settings["filter"] = video_settings["filter"] + "," + burnfilter if video_settings.get("filter") else burnfilter
            self.log.debug("Setting video filter to burn subtitle filter %s." % video_settings["filter"])
            video_settings["debug"] += ".burn-subtitles"

        try:
            subtitle_settings = self.sortStreams(subtitle_settings, self.settings.sub_sorting, swl, self.settings.sub_sorting_codecs or (scodecs + scodecs_image), info, tagdata=tagdata)
        except Exception:
            self.log.exception("Error sorting output stream options [subtitle.sorting-sorting].")

        return subtitle_settings, downloaded_subs

    def _build_preopts_postopts(self, vcodec, vcodecs, info, codecs, pix_fmts, options, metadata_map):
        """Build FFmpeg preopts and postopts lists, including hardware acceleration setup."""
        preopts = []
        postopts = ["-threads", str(self.settings.threads), "-metadata:g", "encoding_tool=SMA-NG"] + metadata_map

        if options.get("format") in ["mp4"] and any(a for a in options["audio"] if self.getCodecFromOptions(a, info) == "truehd"):
            self.log.debug("Adding experimental flag for mp4 with trueHD as a trueHD stream is being copied.")
            postopts.extend(["-strict", "experimental"])

        if self.isDolbyVision(info.video.framedata):
            postopts.extend(["-strict", "unofficial"])

        if vcodec != "copy":
            try:
                opts, device = self.setAcceleration(info.video.codec, info.video.pix_fmt, codecs, pix_fmts)
                preopts.extend(opts)
                for k in self.settings.hwdevices:
                    if k in vcodec:
                        match = self.settings.hwdevices[k]
                        self.log.debug("Found a matching device %s for encoder %s [hwdevices]." % (match, vcodec))
                        if not device:
                            self.log.debug("No device was set by the decoder, setting device to %s for encoder %s [hwdevices]." % (match, vcodec))
                            preopts.extend(self._init_hw_device_opts(k, "sma", match))
                            if k != "qsv":
                                options["video"]["device"] = "sma"
                        elif device == match:
                            self.log.debug("Device was already set by the decoder, using same device %s for encoder %s [hwdevices]." % (device, vcodec))
                            if k != "qsv":
                                options["video"]["device"] = "sma"
                        else:
                            self.log.debug("Device was already set by the decoder but does not match encoder, using secondary device %s for encoder %s [hwdevices]." % (match, vcodec))
                            preopts.extend(self._init_hw_device_opts(k, "sma2", match))
                            if k != "qsv":
                                options["video"]["device"] = "sma2"
                                options["video"]["decode_device"] = "sma"
                        break
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Error when trying to determine hardware acceleration support.")

        preopts.extend(self.settings.preopts)
        postopts.extend(self.settings.postopts)

        if info.video.codec in ["x265", "h265", "hevc"] and vcodec == "copy":
            postopts.extend(["-tag:v", "hvc1"])
            self.log.info("Tagging copied video stream as hvc1")

        return preopts, postopts

    def _select_video_codec(self, inputfile, info, codecs, pix_fmts, tagdata, analyzer_recommendations=None):
        """
        Analyse the video stream and determine encoding parameters.

        Returns a 4-tuple: (video_settings dict, vcodecs list, vcodec str, hdrOutput bool).
        """
        self.log.debug("Reading video stream.")
        self.log.debug("Video codec detected: %s." % info.video.codec)
        self.log.debug("Pix Fmt: %s." % info.video.pix_fmt)
        self.log.debug("Profile: %s." % info.video.profile)

        vdebug = "video"
        hdrInput = self.isHDRInput(info.video)
        if hdrInput:
            vdebug = vdebug + ".hdrInput"

        vcodecs = self.settings.hdr.get("codec", []) if hdrInput and len(self.settings.hdr.get("codec", [])) > 0 else self.settings.vcodec
        vcodecs = self.ffprobeSafeCodecs(vcodecs)
        analyzer_recommendations = analyzer_recommendations or AnalyzerRecommendations()
        vcodecs = self._reorder_codec_pool(vcodecs, analyzer_recommendations.codec_order)
        self.log.debug("Pool of video codecs is %s." % (vcodecs))
        vcodec = "copy" if info.video.codec in vcodecs else vcodecs[0]

        # Custom
        try:
            if blockVideoCopy and blockVideoCopy(self, info.video, inputfile):
                self.log.info("Custom video stream copy check is preventing copying the stream.")
                vdebug = vdebug + ".custom"
                vcodec = vcodecs[0]
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Custom video stream copy check error.")

        vbitrate_estimate = self.estimateVideoBitrate(info)
        vbitrate_ratio = self.settings.vbitrateratio.get(info.video.codec, self.settings.vbitrateratio.get("*", 1.0))
        vbitrate = vbitrate_estimate * vbitrate_ratio
        if analyzer_recommendations.bitrate_ratio_multiplier:
            vbitrate = vbitrate * analyzer_recommendations.bitrate_ratio_multiplier
        analyzer_max_bitrate = analyzer_recommendations.max_bitrate_ceiling
        effective_vmaxbitrate = self.settings.vmaxbitrate
        if analyzer_max_bitrate:
            effective_vmaxbitrate = min(effective_vmaxbitrate, analyzer_max_bitrate) if effective_vmaxbitrate else analyzer_max_bitrate
        self.log.debug("Using video bitrate ratio of %f, which results in %f changing to %f." % (vbitrate_ratio, vbitrate_estimate, vbitrate))
        if effective_vmaxbitrate and vbitrate > effective_vmaxbitrate:
            self.log.debug("Overriding video bitrate. Codec cannot be copied because video bitrate is too high [video-max-bitrate].")
            vdebug = vdebug + ".max-bitrate"
            vcodec = vcodecs[0]
            vbitrate = effective_vmaxbitrate

        vwidth = None
        if self.settings.vwidth and self.settings.vwidth < info.video.video_width:
            self.log.debug("Video width is over the max width, it will be downsampled. Video stream can no longer be copied [video-max-width].")
            vdebug = vdebug + ".max-width"
            vcodec = vcodecs[0]
            vwidth = self.settings.vwidth

        vlevel = self.settings.video_level
        if self.settings.video_level and info.video.video_level and (info.video.video_level > self.settings.video_level):
            self.log.debug("Video level %0.1f. Codec cannot be copied because video level is too high [video-max-level]." % (info.video.video_level))
            vdebug = vdebug + ".max-level"
            vcodec = vcodecs[0]

        vprofile = None
        if hdrInput and len(self.settings.hdr.get("profile")) > 0:
            if info.video.profile in self.settings.hdr.get("profile"):
                vprofile = info.video.profile
            else:
                vprofile = self.settings.hdr.get("profile")[0]
                self.log.debug("Overriding video profile. Codec cannot be copied because profile is not approved [hdr-profile].")
                vdebug = vdebug + ".hdr-profile-fmt"
                vcodec = vcodecs[0]
        else:
            if len(self.settings.vprofile) > 0:
                if info.video.profile in self.settings.vprofile:
                    vprofile = info.video.profile
                else:
                    vprofile = self.settings.vprofile[0] if len(self.settings.vprofile) > 0 else None
                    self.log.debug("Video profile is not supported. Video stream can no longer be copied [video-profile].")
                    vdebug = vdebug + ".profile"
                    vcodec = vcodecs[0]

        vfieldorder = info.video.field_order

        vmaxrate = None
        vbufsize = None
        is_hd = info.video.video_height is not None and info.video.video_height > 1080
        profile_match = self._match_bitrate_profile(vbitrate_estimate, hd=is_hd)
        if profile_match:
            vbitrate = profile_match["target"]
            vmaxrate = "%dk" % profile_match["maxrate"]
            vbufsize = "%dk" % (profile_match["maxrate"] * 2)
            vcodec = vcodecs[0]
            self.log.debug("Bitrate profile matched at source %dkbps: target=%dkbps maxrate=%s bufsize=%s [crf-profiles]." % (vbitrate_estimate, vbitrate, vmaxrate, vbufsize))
        elif effective_vmaxbitrate > 0:
            vmaxrate = "%dk" % effective_vmaxbitrate
            vbufsize = "%dk" % (effective_vmaxbitrate * 2)
            self.log.debug("Setting VBV maxrate=%s bufsize=%s from max-bitrate [video-max-bitrate]." % (vmaxrate, vbufsize))

        if analyzer_max_bitrate:
            if vbitrate and vbitrate > analyzer_max_bitrate:
                vbitrate = analyzer_max_bitrate
            if vmaxrate:
                vmaxrate = "%dk" % min(int(vmaxrate[:-1]), analyzer_max_bitrate)
                vbufsize = "%dk" % (int(vmaxrate[:-1]) * 2)
            elif analyzer_max_bitrate > 0:
                vmaxrate = "%dk" % analyzer_max_bitrate
                vbufsize = "%dk" % (analyzer_max_bitrate * 2)

        vfilter = self.settings.hdr.get("filter") or None if hdrInput else self.settings.vfilter or None
        if hdrInput and self.settings.hdr.get("filter") and self.settings.hdr.get("forcefilter"):
            self.log.debug("Video HDR force filter is enabled. Video stream can no longer be copied [hdr-force-filter].")
            vdebug = vdebug + ".hdr-force-filter"
            vcodec = vcodecs[0]
        elif not hdrInput and vfilter and self.settings.vforcefilter:
            self.log.debug("Video force filter is enabled. Video stream can no longer be copied [video-force-filter].")
            vfilter = self.settings.vfilter
            vcodec = vcodecs[0]
            vdebug = vdebug + ".force-filter"

        vpreset = self.settings.hdr.get("preset") or None if hdrInput else self.settings.preset or None

        vparams = self.settings.codec_params or None
        if hdrInput and self.settings.hdr.get("codec_params"):
            vparams = self.settings.hdr.get("codec_params")

        vlook_ahead_depth = self.settings.hdr.get("look_ahead_depth", 0) if hdrInput else self.settings.look_ahead_depth
        vb_frames = self.settings.hdr.get("b_frames", -1) if hdrInput else self.settings.b_frames
        vref_frames = self.settings.hdr.get("ref_frames", -1) if hdrInput else self.settings.ref_frames

        vpix_fmt = None
        if hdrInput and len(self.settings.hdr.get("pix_fmt")) > 0:
            if info.video.pix_fmt in self.settings.hdr.get("pix_fmt"):
                vpix_fmt = info.video.pix_fmt if self.settings.keep_source_pix_fmt else self.settings.hdr.get("pix_fmt")[0]
            else:
                vpix_fmt = self.settings.hdr.get("pix_fmt")[0]
                self.log.debug("Overriding video pix_fmt. Codec cannot be copied because pix_fmt is not approved [hdr-pix-fmt].")
                vdebug = vdebug + ".hdr-pix_fmt"
                vcodec = vcodecs[0]
        elif not hdrInput and len(self.settings.pix_fmt):
            if info.video.pix_fmt in self.settings.pix_fmt:
                vpix_fmt = info.video.pix_fmt if self.settings.keep_source_pix_fmt else self.settings.pix_fmt[0]
            else:
                vpix_fmt = self.settings.pix_fmt[0]
                self.log.debug("Overriding video pix_fmt. Codec cannot be copied because pix_fmt is not approved [pix-fmt].")
                vdebug = vdebug + ".pix_fmt"
                vcodec = vcodecs[0]

        # Bit depth pix-fmt safety check
        source_bit_depth = pix_fmts.get(info.video.pix_fmt, 0)
        output_bit_depth = pix_fmts.get(vpix_fmt, 0)
        bit_depth = output_bit_depth or source_bit_depth
        self.log.debug("Source bit-depth %d, output %d, using depth %d." % (source_bit_depth, output_bit_depth, bit_depth))

        if vcodec != "copy":
            vencoder = Converter.encoder(vcodec)
            if vencoder and not vencoder.supportsBitDepth(bit_depth):
                self.log.debug("Selected video encoder %s does not support bit depth %d." % (vcodec, bit_depth))
                vpix_fmt = None
                viable_formats = sorted([x for x in pix_fmts if pix_fmts[x] <= vencoder.max_depth], key=lambda x: pix_fmts[x], reverse=True)
                match = re.search(r"yuv[a-z]?[0-9]{3}", info.video.pix_fmt)
                if match:
                    vpix_fmt = next((x for x in viable_formats if match.group(0) in x), None)
                if vpix_fmt:
                    self.log.info("Pix-fmt adjusted to %s in order to maintain compatible bit-depth <=%d." % (vpix_fmt, vencoder.max_depth))
                else:
                    self.log.debug("No viable pix-fmt option found for bit-depth %d, leave as %s." % (vencoder.max_depth, vpix_fmt))

        if analyzer_recommendations.force_reencode and vcodec == "copy":
            vcodec = vcodecs[0]
            vdebug = vdebug + ".analyzer-force-reencode"

        if analyzer_recommendations.filters:
            vfilter = self._merge_video_filters(vfilter, analyzer_recommendations.filters)
            if vcodec == "copy":
                vcodec = vcodecs[0]
            vdebug = vdebug + ".analyzer-filter"

        if analyzer_recommendations.preset and vcodec != "copy":
            vpreset = analyzer_recommendations.preset
            vdebug = vdebug + ".analyzer-preset"

        vframedata = self.normalizeFramedata(info.video.framedata, hdrInput) if self.settings.dynamic_params else None
        if vpix_fmt and vframedata and "pix_fmt" in vframedata and vframedata["pix_fmt"] != vpix_fmt:
            self.log.debug("Pix_fmt is changing, will not preserve framedata")
            vframedata = None
            # range_filter = "scale=in_range=full:out_range=limited"
            # vfilter = vfilter + "," + range_filter if vfilter else range_filter

        hdrOutput = self.isHDROutput(vpix_fmt, bit_depth)

        vbsf = None
        if self.settings.removebvs and self.hasBitstreamVideoSubs(info.video.framedata):
            self.log.debug("Found side data type with closed captioning [remove-bitstream-subs]")
            vbsf = "filter_units=remove_types=6"
        if hdrInput and not hdrOutput:
            vbsf = vbsf + "|39" if vbsf else "filter_units=remove_types=39"

        self.log.debug("Video codec: %s." % vcodec)
        self.log.debug("Video bitrate: %s." % vbitrate)
        self.log.debug("Video maxrate: %s." % vmaxrate)
        self.log.debug("Video bufsize: %s." % vbufsize)
        self.log.debug("Video level: %s." % vlevel)
        self.log.debug("Video profile: %s." % vprofile)
        self.log.debug("Video preset: %s." % vpreset)
        self.log.debug("Video pix_fmt: %s." % vpix_fmt)
        self.log.debug("Video field order: %s." % vfieldorder)
        self.log.debug("Video width: %s." % vwidth)
        self.log.debug("Video debug %s." % vdebug)
        self.log.debug("Video framedata: %s." % vframedata)
        self.log.debug("Video filter: %s." % vfilter)
        self.log.debug("Video bit depth: %d." % bit_depth)
        self.log.debug("Video bsf: %s." % vbsf)
        self.log.debug("Video codec parameters %s." % vparams)
        self.log.info("Creating %s video stream from source stream %d." % (vcodec, info.video.index))

        video_settings = {
            "codec": vcodec,
            "map": info.video.index,
            "bitrate": vbitrate,
            "maxrate": vmaxrate,
            "bufsize": vbufsize,
            "level": vlevel,
            "profile": vprofile,
            "preset": vpreset,
            "pix_fmt": vpix_fmt,
            "field_order": vfieldorder,
            "width": vwidth,
            "filter": vfilter,
            "params": vparams,
            "framedata": vframedata,
            "bsf": vbsf,
            "debug": vdebug,
            "look_ahead_depth": vlook_ahead_depth,
            "b_frames": vb_frames,
            "ref_frames": vref_frames,
        }
        video_settings["title"] = self.videoStreamTitle(info.video, video_settings, hdr=hdrOutput, tagdata=tagdata)

        return video_settings, vcodecs, vcodec, hdrOutput

    def _process_audio_stream(self, a, inputfile, info, awl, allowua, blocked_audio_languages, blocked_audio_dispositions, audio_settings, tagdata, acodecs=None, ua_codecs=None):
        """
        Evaluate a single audio stream and append settings entries to audio_settings.

        Handles language/disposition filtering, universal-audio downmix creation,
        codec selection, bitrate calculations, and copy-original logic.

        acodecs/ua_codecs: ffprobe-filtered codec pools from generateOptions(); when None
        the method falls back to self.settings.acodec / self.settings.ua.

        Returns the (possibly updated) allowua flag.
        """
        if acodecs is None:
            acodecs = self.settings.acodec
        if ua_codecs is None:
            ua_codecs = self.settings.ua
        # Custom skip
        try:
            if skipStream and skipStream(self, a, info, inputfile, tagdata):
                self.log.info("Audio stream %s will be skipped, custom skipStream function returned True." % (a.index))
                return allowua
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Custom audio stream skip check error for stream %s." % (a.index))

        if self.settings.force_audio_defaults and a.disposition.get("default"):
            self.log.debug("Audio stream %s is flagged as default, forcing inclusion [Audio.force-default]." % (a.index))
        else:
            if not self.validLanguage(a.metadata["language"], awl, blocked_audio_languages):
                return allowua
            if not self.validDisposition(a, self.settings.ignored_audio_dispositions, self.settings.unique_audio_dispositions, a.metadata["language"], blocked_audio_dispositions):
                return allowua

        try:
            ua = allowua and not (skipUA and skipUA(self, a, info, inputfile, tagdata))
        except KeyboardInterrupt:
            raise
        except Exception:
            ua = allowua
            self.log.exception("Custom skipUA method threw an exception.")

        # Create friendly audio stream if the default audio stream has too many channels
        uadata = None
        if ua and a.audio_channels > 2:
            if self.settings.ua_bitrate == 0:
                self.log.warning("Universal audio channel bitrate must be greater than 0, defaulting to %d [universal-audio-channel-bitrate]." % self.default_channel_bitrate)
                self.settings.ua_bitrate = self.default_channel_bitrate
            ua_bitrate = (self.default_channel_bitrate * 2) if (self.settings.ua_bitrate * 2) > (self.default_channel_bitrate * 2) else (self.settings.ua_bitrate * 2)
            ua_disposition = a.dispostr
            ua_filter = self.settings.ua_filter or None
            ua_profile = self.settings.ua_profile or None

            # Custom channel based filters
            ua_afilterchannel = self.settings.afilterchannels.get(a.audio_channels, {}).get(2)
            if ua_afilterchannel:
                ua_filter = "%s,%s" % (ua_filter, ua_afilterchannel) if ua_filter else ua_afilterchannel
                self.log.debug("Found an audio filter for converting from %d channels to %d channels. Applying filter %s to UA." % ((a.audio_channels, 2, ua_afilterchannel)))

            self.log.debug("Audio codec: %s." % ua_codecs[0])
            self.log.debug("Channels: 2.")
            self.log.debug("Filter: %s." % ua_filter)
            self.log.debug("Bitrate: %s." % ua_bitrate)
            self.log.debug("VBR: %s." % self.settings.ua_vbr)
            self.log.debug("Profile: %s." % ua_profile)
            self.log.debug("Language: %s." % a.metadata["language"])
            self.log.debug("Disposition: %s." % ua_disposition)

            uadata = {
                "map": a.index,
                "codec": ua_codecs[0],
                "channels": 2,
                "bitrate": ua_bitrate,
                "quality": self.settings.ua_vbr,
                "profile": ua_profile,
                "samplerate": self.settings.audio_samplerates[0] if len(self.settings.audio_samplerates) > 0 else None,
                "sampleformat": self.settings.audio_sampleformat,
                "filter": ua_filter,
                "language": a.metadata["language"],
                "disposition": ua_disposition,
                "debug": "universal-audio",
            }
            uadata["title"] = self.audioStreamTitle(a, uadata, tagdata=tagdata)

        adebug = "audio"
        # If the universal audio option is enabled and the source audio channel is only stereo, the additional universal stream will be skipped and a single channel will be made regardless of codec preference to avoid multiple stereo channels
        afilter = None
        asample = None
        avbr = None
        adisposition = a.dispostr
        aprofile = None
        if ua and a.audio_channels <= 2:
            self.log.debug("Overriding default channel settings because universal audio is enabled but the source is stereo [universal-audio].")
            acodec = "copy" if a.codec in ua_codecs else ua_codecs[0]
            audio_channels = a.audio_channels
            abitrate = (
                (a.audio_channels * self.default_channel_bitrate)
                if (a.audio_channels * self.settings.ua_bitrate) > (a.audio_channels * self.default_channel_bitrate)
                else (a.audio_channels * self.settings.ua_bitrate)
            )
            avbr = self.settings.ua_vbr
            aprofile = self.settings.ua_profile or None
            adebug = "universal-audio"

            # Custom
            try:
                if blockAudioCopy and blockAudioCopy(self, a, inputfile):
                    self.log.info("Custom audio stream copy check is preventing copying the stream.")
                    adebug = adebug + ".custom"
                    acodec = ua_codecs[0]
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Custom audio stream copy check error.")

            # UA Filters
            afilter = self.settings.ua_filter or None
            if afilter and self.settings.ua_forcefilter:
                self.log.debug("Unable to copy codec because an universal audio filter is set [universal-audio-force-filter].")
                acodec = ua_codecs[0]
                adebug = adebug + ".force-filter"

            # Sample rates
            if len(self.settings.audio_samplerates) > 0 and a.audio_samplerate not in self.settings.audio_samplerates:
                self.log.debug("Unable to copy codec because audio sample rate %d is not approved [audio-sample-rates]." % (a.audio_samplerate))
                asample = self.settings.audio_samplerates[0]
                acodec = ua_codecs[0]
                adebug = adebug + ".audio-sample-rates"
        else:
            # If desired codec is the same as the source codec, copy to avoid quality loss
            acodec = "copy" if a.codec in acodecs else acodecs[0]
            avbr = self.settings.avbr
            aprofile = self.settings.aprofile or None
            # Audio channel adjustments
            if self.settings.maxchannels and a.audio_channels > self.settings.maxchannels:
                self.log.debug("Audio source exceeds maximum channels, can not be copied. Settings channels to %d [audio-max-channels]." % self.settings.maxchannels)
                adebug = adebug + ".max-channels"
                audio_channels = self.settings.maxchannels
                acodec = acodecs[0]
                abitrate = self.settings.maxchannels * self.settings.abitrate
            else:
                audio_channels = a.audio_channels
                abitrate = a.audio_channels * self.settings.abitrate

            # Custom
            try:
                if blockAudioCopy and blockAudioCopy(self, a, inputfile):
                    self.log.debug("Custom audio stream copy check is preventing copying the stream.")
                    adebug = adebug + ".custom"
                    acodec = acodecs[0]
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Custom audio stream copy check error.")

            # Filters
            afilter = self.settings.afilter or None
            if afilter and self.settings.aforcefilter:
                self.log.debug("Unable to copy codec because an audio filter is set [audio-force-filter].")
                acodec = acodecs[0]
                adebug = adebug + ".audio-force-filter"

            # Custom channel based filters
            afilterchannel = self.settings.afilterchannels.get(a.audio_channels, {}).get(audio_channels)
            if afilterchannel:
                afilter = "%s,%s" % (afilter, afilterchannel) if afilter else afilterchannel
                self.log.debug("Found an audio filter for converting from %d channels to %d channels. Applying filter %s." % (a.audio_channels, audio_channels, afilterchannel))

            # Sample rates
            if len(self.settings.audio_samplerates) > 0 and a.audio_samplerate not in self.settings.audio_samplerates:
                self.log.info("Unable to copy codec because audio sample rate %d is not approved [audio-sample-rates]." % (a.audio_samplerate))
                asample = self.settings.audio_samplerates[0]
                acodec = acodecs[0]
                adebug = adebug + ".audio-sample-rates"

        # Bitrate calculations/overrides
        if self.settings.abitrate == 0:
            self.log.debug("Attempting to set bitrate based on source stream bitrate.")
            try:
                abitrate = (((a.bitrate / 1000) if a.bitrate else 0) / a.audio_channels) * audio_channels
            except Exception:
                self.log.warning("Unable to determine audio bitrate from source stream %s, defaulting to %d per channel." % (a.index, self.default_channel_bitrate))
                abitrate = audio_channels * self.default_channel_bitrate
        if self.settings.amaxbitrate and abitrate > self.settings.amaxbitrate:
            self.log.debug("Calculated bitrate of %d exceeds maximum bitrate %d, setting to max value [audio-max-bitrate]." % (abitrate, self.settings.amaxbitrate))
            abitrate = self.settings.amaxbitrate
            acodec = acodecs[0]

        # Force copy if Atmos
        if self.settings.audio_atmos_force_copy and self.isAudioStreamAtmos(a):
            self.log.debug("Source audio stream contains Atmos data, forcing codec copy to preserve [audio-atmos-force-copy].")
            acodec = "copy"

        self.log.debug("Audio codec: %s." % acodec)
        self.log.debug("Channels: %s." % audio_channels)
        self.log.debug("Bitrate: %s." % abitrate)
        self.log.debug("VBR: %s." % avbr)
        self.log.debug("Audio Profile: %s." % aprofile)
        self.log.debug("Language: %s." % a.metadata["language"])
        self.log.debug("Filter: %s." % afilter)
        self.log.debug("Disposition: %s." % adisposition)
        self.log.debug("Debug: %s." % adebug)

        # If the ua_first_only option is enabled, disable the ua option after the first audio stream is processed
        if ua and self.settings.ua_first_only:
            self.log.debug("Not creating any additional universal audio streams [universal-audio-first-stream-only].")
            allowua = False

        absf = "aac_adtstoasc" if acodec == "copy" and a.codec == "aac" and self.settings.aac_adtstoasc else None

        self.log.info("Creating %s audio stream from source stream %d." % (acodec, a.index))
        audio_setting = {
            "map": a.index,
            "codec": acodec,
            "channels": audio_channels,
            "bitrate": abitrate,
            "profile": aprofile,
            "quality": avbr,
            "filter": afilter,
            "samplerate": asample,
            "sampleformat": self.settings.audio_sampleformat,
            "language": a.metadata["language"],
            "disposition": adisposition,
            "bsf": absf,
            "debug": adebug,
        }
        audio_setting["title"] = self.audioStreamTitle(a, audio_setting, tagdata=tagdata)
        audio_settings.append(audio_setting)

        # Add the universal audio stream
        if uadata:
            self.log.info("Creating %s audio stream from source audio stream %d [universal-audio]." % (uadata.get("codec"), a.index))
            audio_settings.append(uadata)

        # Copy the original stream
        if self.settings.audio_copyoriginal and acodec != "copy":
            self.log.info("Copying audio stream from source stream %d format %s [audio-copy-original]." % (a.index, a.codec))
            audio_setting = {
                "map": a.index,
                "codec": "copy",
                "bitrate": (a.bitrate / 1000) if a.bitrate else None,
                "channels": a.audio_channels,
                "language": a.metadata["language"],
                "disposition": adisposition,
                "debug": "audio-copy-original",
            }
            audio_setting["title"] = self.audioStreamTitle(a, audio_setting, tagdata=tagdata)
            audio_settings.append(audio_setting)

        # Remove the language if we only want the first stream from a given language
        if self.settings.audio_first_language_stream and a.metadata["language"] != BaseCodec.UNDEFINED:
            blocked_audio_languages.append(a.metadata["language"])
            self.log.debug("Blocking further %s audio streams to prevent multiple streams of the same language [audio-first-stream-of-language]." % a.metadata["language"])

        return allowua

    def _select_subtitle_codec(self, source_codec, image_based, embed, scodecs=None, scodecs_image=None):
        """Return the output codec string for a subtitle stream, or None if it should be skipped.

        Args:
            source_codec: The source stream's codec name (e.g. "mov_text", "hdmv_pgs_subtitle").
            image_based: True if the stream contains image-based subtitles.
            embed: True if we are selecting for embedding (use embed* settings),
                   False if we are selecting for ripping to an external file.
            scodecs: Filtered text-subtitle codec pool (defaults to self.settings.scodec).
            scodecs_image: Filtered image-subtitle codec pool (defaults to self.settings.scodec_image).
        """
        if image_based:
            pool = scodecs_image if scodecs_image is not None else self.settings.scodec_image
            enabled = self.settings.embedimgsubs if embed else not self.settings.embedimgsubs
        else:
            pool = scodecs if scodecs is not None else self.settings.scodec
            enabled = self.settings.embedsubs if embed else not self.settings.embedsubs
        if not (enabled and pool):
            return None
        return "copy" if source_codec in pool else pool[0]

    def _subtitle_passes_filter(self, stream, swl, blocked_subtitle_languages, blocked_subtitle_dispositions):
        """Return True if a subtitle stream passes language and disposition filters.

        Works with both embedded stream objects (MediaStreamInfo) and external
        subtitle stream objects — caller passes the stream object directly (not
        the outer MediaInfo wrapper).
        """
        if self.settings.force_subtitle_defaults and stream.disposition.get("default"):
            return True
        lang_blocklist = [] if stream.disposition.get("forced") else blocked_subtitle_languages
        if not self.validLanguage(stream.metadata["language"], swl, lang_blocklist):
            return False
        if not self.validDisposition(stream, self.settings.ignored_subtitle_dispositions, self.settings.unique_subtitle_dispositions, stream.metadata["language"], blocked_subtitle_dispositions):
            return False
        return True

    def _process_subtitle_stream(
        self, s, inputfile, info, swl, blocked_subtitle_languages, blocked_subtitle_dispositions, subtitle_settings, valid_external_subs, ripsubopts, tagdata, scodecs=None, scodecs_image=None
    ):
        """
        Evaluate a single embedded subtitle stream and update subtitle_settings, valid_external_subs, or ripsubopts.

        Handles language/disposition filtering, image vs text detection, cleanit/ffsubsync
        ripping, codec selection, and rip-for-extraction fallback.
        """
        # Custom skip
        try:
            if skipStream and skipStream(self, s, info, inputfile, tagdata):
                self.log.info("Subtitle stream %s will be skipped, custom skipStream function returned True." % (s.index))
                return
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Custom subtitle stream skip check error for stream %s." % (s.index))

        if not self._subtitle_passes_filter(s, swl, blocked_subtitle_languages, blocked_subtitle_dispositions):
            return

        try:
            image_based = self.isImageBasedSubtitle(inputfile, s.index)
            self.log.info("Stream %s is %s-based subtitle for codec %s." % (s.index, "image" if image_based else "text", s.codec))
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.error("Unknown error occurred while trying to determine if subtitle is text or image based. Probably corrupt, skipping.")
            return

        embed_codec = self._select_subtitle_codec(s.codec, image_based, embed=True, scodecs=scodecs, scodecs_image=scodecs_image)

        if embed_codec:
            if not image_based and ((self.settings.cleanit and cleanit) or (self.settings.ffsubsync and ffsubsync)):
                try:
                    rip_codec = "copy" if s.codec in ["srt"] else "srt"
                    rips = self.subtitles.ripSubs(inputfile, [self.generateRipSubOpts(inputfile, s, rip_codec)], include_all=True)
                    if rips:
                        new_sub_path = rips[0]
                        new_sub = self.isValidSubtitleSource(new_sub_path)
                        new_sub = self.subtitles.processExternalSub(new_sub, inputfile)
                        if new_sub:
                            self.log.info("Subtitle %s extracted for cleaning/syncing [subtitles.cleanit, subtitles.ffsubsync]." % (new_sub_path))
                            self.cleanExternalSub(new_sub.path)
                            self.subtitles.syncExternalSub(new_sub.path, inputfile)
                            valid_external_subs.append(new_sub)
                        return
                except Exception:
                    self.log.exception("Subtitle rip and cleaning failed.")
            self.log.info("Creating %s subtitle stream from source stream %d." % (embed_codec, s.index))
            subtitle_setting = {"map": s.index, "codec": embed_codec, "language": s.metadata["language"], "disposition": s.dispostr, "debug": "subtitle.embed-subs"}
            subtitle_setting["is_forced"] = s.disposition.get("forced", False)
            subtitle_setting["title"] = self.subtitleStreamTitle(s, subtitle_setting, image_based, tagdata=tagdata)
            subtitle_settings.append(subtitle_setting)
            if self.settings.sub_first_language_stream and not s.disposition["forced"]:
                blocked_subtitle_languages.append(s.metadata["language"])
        else:
            rip_codec = self._select_subtitle_codec(s.codec, image_based, embed=False, scodecs=scodecs, scodecs_image=scodecs_image)
            if rip_codec:
                ripsubopts.append(self.generateRipSubOpts(inputfile, s, rip_codec))
                if self.settings.sub_first_language_stream and not s.disposition["forced"]:
                    blocked_subtitle_languages.append(s.metadata["language"])

    def _process_external_sub(self, external_sub, inputfile, swl, blocked_subtitle_languages, blocked_subtitle_dispositions, subtitle_settings, sources, tagdata):
        """
        Evaluate a single external subtitle file and append a settings entry to subtitle_settings if appropriate.

        Handles image vs text detection, language/disposition filtering, source list management,
        and scheduling the external subtitle file for deletion after embedding.
        """
        try:
            image_based = self.isImageBasedSubtitle(external_sub.path, 0)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.error("Unknown error occurred while trying to determine if subtitle is text or image based. Probably corrupt, skipping.")
            return
        self.cleanDispositions(external_sub)
        stream = external_sub.subtitle[0]
        scodec = self._select_subtitle_codec(stream.codec if hasattr(stream, "codec") else "", image_based, embed=True)

        if not scodec:
            self.log.info("Skipping external subtitle file %s, no appropriate codecs found or embed disabled." % (os.path.basename(external_sub.path)))
            return

        if not self._subtitle_passes_filter(stream, swl, blocked_subtitle_languages, blocked_subtitle_dispositions):
            return

        if external_sub.path not in sources:
            sources.append(external_sub.path)

        sdisposition = stream.dispostr
        self.log.info("Creating %s subtitle stream by importing %s-based subtitle %s [embed-subs]." % (scodec, "Image" if image_based else "Text", os.path.basename(external_sub.path)))
        subtitle_setting = {
            "source": sources.index(external_sub.path),
            "map": 0,
            "codec": scodec,
            "disposition": sdisposition,
            "language": stream.metadata["language"],
            "is_forced": getattr(stream, "forced", False),
            "debug": "subtitle.embed-subs",
        }
        subtitle_setting["title"] = self.subtitleStreamTitle(stream, subtitle_setting, image_based, path=external_sub.path, tagdata=tagdata)
        subtitle_settings.append(subtitle_setting)
        self.log.debug("Path: %s." % external_sub.path)
        self.log.debug("Codec: %s." % scodec)
        self.log.debug("Language: %s." % stream.metadata["language"])
        self.log.debug("Disposition: %s." % sdisposition)

        self.deletesubs.add(external_sub.path)

        if self.settings.sub_first_language_stream and not stream.disposition["forced"]:
            blocked_subtitle_languages.append(stream.metadata["language"])

    def _warn_unsupported_encoders(self, codecs, stream_options):
        """Emit warnings for any chosen codec not supported by the current FFmpeg build."""
        encoders = [item for sublist in [codecs[x]["encoders"] for x in codecs] for item in sublist]
        for o in stream_options:
            if "codec" not in o or o["codec"] == "copy":
                continue
            ffcodec = self.converter.codec_name_to_ffmpeg_codec_name(o["codec"])
            if not ffcodec:
                self.log.warning("===========WARNING===========")
                self.log.warning(
                    "The encoder you have chosen %s is not defined and is not supported by SMA-NG, conversion will likely fail. Please check that this is defined in ./converter/avcodecs.py and if not open a Github feature request to add support."
                    % (o["codec"])
                )
                self.log.warning("===========WARNING===========")
            elif ffcodec not in encoders:
                self.log.warning("===========WARNING===========")
                self.log.warning(
                    "The encoder you have chosen %s (%s) is not listed as supported in your FFMPEG build, conversion will likely fail, please use a build of FFMPEG that supports %s or choose a different encoder."
                    % (o["codec"], ffcodec, ffcodec)
                )
                ffpcodec = Converter.codec_name_to_ffprobe_codec_name(o["codec"])
                if ffpcodec and ffpcodec in codecs and codecs[ffpcodec]["encoders"]:
                    self.log.warning("Other encoders your current FFMPEG build does support for codec %s:" % (ffpcodec))
                    self.log.warning(codecs[ffpcodec]["encoders"])
                self.log.warning("===========WARNING===========")

    # Determine if a stream has a valid language for the main option generator
    def validLanguage(self, language, whitelist, blocked=[]):
        """Return True if language passes the whitelist and is not in the blocked list."""
        return (len(whitelist) < 1 or language in whitelist) and language not in blocked

    # Complex valid disposition checker supporting unique dispositions, language combinations etc for the main option generator
    def validDisposition(self, stream, ignored, unique=False, language="", existing=None, append=True):
        if existing is None:
            existing = []
        """
        Check whether a stream's dispositions allow it to be included in the output.

        Rejects the stream if any of its active dispositions are in the ignored list.
        When unique=True, also rejects the stream if a stream with the same language
        and disposition profile has already been seen (tracked via the existing list).
        """
        truedispositions = [x for x in stream.disposition if stream.disposition[x]]
        for dispo in truedispositions:
            if BaseCodec.DISPO_ALTS.get(dispo, dispo) in ignored:
                self.log.debug("Ignoring stream because disposition %s is on the ignore list." % (dispo))
                return False
        if unique:
            search = "%s.%s" % (language, stream.dispostr)
            if search in existing:
                self.log.debug("Invalid disposition, stream fitting this disposition profile already exists, ignoring.")
                return False
            if append:
                self.log.debug("Valid disposition, adding %s to the ignored list." % (search))
                existing.append(search)
            return True
        return True

    # Help method to convert dispo string back into a dict
    def dispoStringToDict(self, dispostr):
        """Parse a disposition string like '+default-forced' into a dict of {flag: bool}."""
        dispo = {}
        if dispostr:
            d = re.findall("([+-][a-zA-Z_]*)", dispostr)
            for x in d:
                dispo[x[1:]] = x.startswith("+")
        return dispo

    # Simple disposition filter
    def checkDisposition(self, allowed, source):
        """Return True if all disposition flags in allowed are set in the source dict."""
        for a in allowed:
            if not source.get(a):
                return False
        return True

    @staticmethod
    def _init_hw_device_opts(hwaccel, name, device):
        """Return pre-input device arguments for *hwaccel* pointing at *device*.

        QSV uses the dedicated ``-qsv_device`` flag which accepts a DRI render
        node directly (no VAAPI intermediary needed):

            -qsv_device /dev/dri/renderD128

        All other hwaccel types use ``-init_hw_device``:

            -init_hw_device <hwaccel>=<name>:<device>
        """
        if hwaccel == "qsv":
            return ["-qsv_device", device]
        return ["-init_hw_device", "%s=%s:%s" % (hwaccel, name, device)]

    # Hardware acceleration options now with bit depth safety checks
    def setAcceleration(self, video_codec, pix_fmt, codecs=None, pix_fmts=None):
        """
        Build FFmpeg pre-options for hardware-accelerated decoding.

        Selects the first configured hwaccel platform available in the current
        FFmpeg build, optionally with a matching hardware decoder. Verifies bit
        depth support before enabling a decoder. Returns a list of FFmpeg
        argument strings to prepend before the input file.
        """
        opts = []
        pix_fmts = pix_fmts or self.converter.ffmpeg.pix_fmts
        bit_depth = pix_fmts.get(pix_fmt, 0)
        device = None
        # Look up which codecs and which decoders/encoders are available in this build of ffmpeg
        codecs = codecs or self.converter.ffmpeg.codecs

        # Lookup which hardware acceleration platforms are available in this build of ffmpeg
        hwaccels = self.converter.ffmpeg.hwaccels

        self.log.debug("Selected hwaccel options:")
        self.log.debug(self.settings.hwaccels)
        self.log.debug("Selected hwaccel decoder pairs:")
        self.log.debug(self.settings.hwaccel_decoders)
        self.log.debug("FFMPEG hwaccels:")
        self.log.debug(hwaccels)
        self.log.debug("Input format %s bit depth %d." % (pix_fmt, bit_depth))

        # Find the first of the specified hardware acceleration platform that is available in this build of ffmpeg.  The order of specified hardware acceleration platforms determines priority.
        for hwaccel in self.settings.hwaccels:
            if hwaccel in hwaccels:
                device = self.settings.hwdevices.get(hwaccel)
                if device:
                    self.log.debug("Setting hwaccel device to %s." % device)
                    opts.extend(self._init_hw_device_opts(hwaccel, "sma", device))
                    if hwaccel != "qsv":
                        opts.extend(["-hwaccel_device", "sma"])

                if hwaccel == "qsv":
                    opts.extend(["-extra_hw_frames", "20"])

                self.log.debug("%s hwaccel is supported by this ffmpeg build and will be used [hwaccels]." % hwaccel)
                opts.extend(["-hwaccel", hwaccel])
                if self.settings.hwoutputfmt.get(hwaccel):
                    opts.extend(["-hwaccel_output_format", self.settings.hwoutputfmt[hwaccel]])

                # If there's a decoder for this acceleration platform, also use it
                decoder = self.converter.ffmpeg.hwaccel_decoder(video_codec, self.settings.hwoutputfmt.get(hwaccel, hwaccel))
                self.log.debug("Decoder: %s." % decoder)
                if decoder in codecs[video_codec]["decoders"] and decoder in self.settings.hwaccel_decoders:
                    if Converter.decoder(decoder).supportsBitDepth(bit_depth):
                        self.log.debug("%s decoder is also supported by this ffmpeg build and will also be used [hwaccel-decoders]." % decoder)
                        opts.extend(["-vcodec", decoder])
                        self.log.debug("Decoder formats:")
                        self.log.debug(self.converter.ffmpeg.decoder_formats(decoder))
                    else:
                        self.log.debug("Decoder %s is supported but cannot support bit depth %d of format %s." % (decoder, bit_depth, pix_fmt))
                break
        if "-vcodec" not in opts:
            # No matching decoder found for hwaccel, see if there's still a valid decoder that may not match
            for decoder in self.settings.hwaccel_decoders:
                if decoder in codecs[video_codec]["decoders"] and decoder in self.settings.hwaccel_decoders and decoder.startswith(video_codec):
                    if Converter.decoder(decoder).supportsBitDepth(bit_depth):
                        self.log.debug("%s decoder is supported by this ffmpeg build and will also be used [hwaccel-decoders]." % decoder)
                        opts.extend(["-vcodec", decoder])
                        self.log.debug("Decoder formats:")
                        self.log.debug(self.converter.ffmpeg.decoder_formats(decoder))
                        break
                    else:
                        self.log.debug("Decoder %s is supported but cannot support bit depth %d of format %s." % (decoder, bit_depth, pix_fmt))
        return opts, device

    # Using sorting and filtering to determine which audio track should be flagged as default, only one track will be selected
    def setDefaultAudioStream(self, audio_streams):
        """
        Ensure exactly one audio stream has the default disposition.

        Prefers streams in the configured default audio language (adl). If none
        are marked default in that language, promotes the first preferred-language
        stream. Clears default flags from other languages and from extra streams
        in the preferred language.
        """
        if audio_streams:
            self.log.debug("Sorting audio streams for default audio stream designation.")
            preferred_language_audio_streams = [x for x in audio_streams if x.get("language") == self.settings.adl] if self.settings.adl else audio_streams
            default_stream = audio_streams[0]
            default_streams = [x for x in audio_streams if "+default" in (x.get("disposition") or "")]
            default_preferred_language_streams = [x for x in default_streams if x.get("language") == self.settings.adl] if self.settings.adl else default_streams
            default_streams_not_in_preferred_language = [x for x in default_streams if x not in default_preferred_language_streams]

            self.log.debug(
                "%d total audio streams with %d set to default disposition. %d defaults in your preferred language (%s), %d in other languages."
                % (len(audio_streams), len(default_streams), len(default_preferred_language_streams), self.settings.adl, len(default_streams_not_in_preferred_language))
            )
            if len(preferred_language_audio_streams) < 1:
                self.log.debug("No audio streams in your preferred language, using other languages to determine default stream.")

            if len(default_preferred_language_streams) < 1:
                try:
                    potential_streams = preferred_language_audio_streams if len(preferred_language_audio_streams) > 0 else default_streams
                    default_stream = potential_streams[0] if len(potential_streams) > 0 else audio_streams[0]
                except Exception:
                    self.log.exception("Error setting default stream in preferred language.")
            elif len(default_preferred_language_streams) > 1:
                default_stream = default_preferred_language_streams[0]
                try:
                    for remove in default_preferred_language_streams[1:]:
                        if remove.get("disposition"):
                            remove["disposition"] = remove.get("disposition").replace("+default", "-default")
                    self.log.debug("%d streams in preferred language cleared of default disposition flag from preferred language." % (len(default_preferred_language_streams) - 1))
                except Exception:
                    self.log.exception("Error in removing default disposition flag from extra audio streams, multiple streams may be set as default.")
            else:
                self.log.debug("Default audio stream already inherited from source material, will not override to audio-language-default.")
                default_stream = default_preferred_language_streams[0]

            default_streams_not_in_preferred_language = [x for x in default_streams_not_in_preferred_language if x != default_stream]
            if len(default_streams_not_in_preferred_language) > 0:
                self.log.debug("Cleaning up default disposition settings from not preferred languages. %d streams will have default flag removed." % (len(default_streams_not_in_preferred_language)))
                for remove in default_streams_not_in_preferred_language:
                    if remove.get("disposition"):
                        remove["disposition"] = remove.get("disposition").replace("+default", "-default")
            if default_stream.get("disposition"):
                default_stream["disposition"] = default_stream.get("disposition").replace("-default", "+default")
                if "+default" not in default_stream.get("disposition"):
                    default_stream["disposition"] += "+defaultDOESTHISEVENWORK"
            else:
                default_stream["disposition"] = "+default"

            self.log.info(
                "Default audio stream set to %s %s %s channel stream [audio-default-sorting: %s]."
                % (default_stream["language"], default_stream["codec"], default_stream["channels"], self.settings.audio_sorting_default)
            )
        else:
            self.log.debug("Audio output is empty, unable to set default audio streams.")

    # Ensure that at least one subtitle stream is default based on language data
    def setDefaultSubtitleStream(self, subtitle_settings):
        """
        Set the default disposition on the first subtitle stream in the preferred language.

        Only acts when force_subtitle_defaults is enabled and no default subtitle stream
        already exists in the output options.
        """
        if len(subtitle_settings) > 0 and self.settings.sdl:
            if len([x for x in subtitle_settings if "+default" in (x.get("disposition") or "")]) < 1 and self.settings.force_subtitle_defaults:
                matches = [x for x in subtitle_settings if x.get("language") == self.settings.sdl]
                if not matches:
                    self.log.debug("No subtitle stream found in default language %s, will not set a default subtitle stream.", self.settings.sdl)
                    return
                default_stream = matches[0]

                if default_stream.get("disposition"):
                    default_stream["disposition"] = default_stream.get("disposition").replace("-default", "+default")
                    if "+default" not in default_stream.get("disposition"):
                        default_stream["disposition"] += "+default"
                else:
                    default_stream["disposition"] = "+default"

            else:
                self.log.debug("Default subtitle stream already inherited from source material, will not override to subtitle-language-default.")
        else:
            self.log.debug("Subtitle output is empty or no default subtitle language is set, will not pass over subtitle output to set a default stream.")

    # Returns the sorted source index from a map value, adjusted for stream-codec-combinations
    def getSourceIndexFromMap(self, m, info, combinations):
        """Return the source stream list index for map value m, resolving combination groups."""
        m = self.minResolvedMap(m, combinations)
        source = next((s for s in info.streams if s.index == m), None)
        if source:
            return info.streams.index(source)
        return 999

    # Returns a modified map value based on stream-codec-combinations used for sorting
    def minResolvedMap(self, m, combinations):
        """Return the lowest stream index in the combination group containing m, or m itself."""
        combination = next((c for c in combinations if m in c), None)
        if combination:
            return min(combination)
        return m

    # Sort streams
    def sortStreams(self, streams, keys, languages, codecs, info, combinations=None, tagdata=None):
        """
        Sort a list of streams or option dicts by the given key sequence.

        Keys are applied in order and may include codec, channels, language,
        original-language, bitrate, map, ua, original, and disposition prefixes
        (e.g. 'd.default'). Append '.a' or '.d' to a key to force ascending or
        descending order. Returns the sorted list without modifying the original.
        """
        if combinations is None:
            combinations = []
        DISPO_PREFIX = "d."
        ASCENDING_SUFFIX = ".a"
        DESCENDING_SUFFIX = ".d"

        output = streams[:]
        self.log.debug("Sorting streams with keys %s." % (keys))
        original_language = tagdata.original_language if tagdata else None

        SORT_DICT = {
            "codec": lambda x: codecs.index(self.getCodecFromOptions(x, info)) if (self.getCodecFromOptions(x, info)) in codecs else 999,
            "channels": lambda x: x.get("channels", 999),
            "language": lambda x: languages.index(x.get("language")) if x.get("language") in languages else 999,
            "original-language": lambda x: x.get("language") == original_language,
            "bitrate": lambda x: x.get("bitrate", 999),
            "map": lambda x: self.getSourceIndexFromMap(x["map"], info, combinations),
            "ua": lambda x: "universal-audio" in x.get("debug", ""),
            "original": lambda x: "audio-copy-original" in x.get("debug", ""),
        }

        SORT_MEDIASTREAMINFO = {
            "codec": lambda x: codecs.index(x.codec) if x.codec in codecs else 999,
            "channels": lambda x: x.audio_channels,
            "language": lambda x: languages.index(x.metadata.get("language")) if x.metadata.get("language") in languages else 999,
            "original-language": lambda x: x.metadata.get("language") == original_language,
            "bitrate": lambda x: x.bitrate,
        }

        if len(streams) > 1:
            for k in keys:
                sort = output[:]
                reverse = False
                if k.endswith(ASCENDING_SUFFIX):
                    reverse = False
                    k = k[: -len(ASCENDING_SUFFIX)]
                elif k.endswith(DESCENDING_SUFFIX):
                    reverse = True
                    k = k[: -len(DESCENDING_SUFFIX)]

                if isinstance(streams[0], dict):
                    if k.startswith(DISPO_PREFIX):
                        disposition = k[len(DISPO_PREFIX) :]
                        if disposition:
                            sort.sort(key=lambda x: "+%s" % (disposition) in x.get("disposition", ""), reverse=reverse)
                    elif k in SORT_DICT:
                        sort.sort(key=SORT_DICT[k], reverse=reverse)
                    else:
                        self.log.debug("Skipping sort key %s." % (k))
                        continue
                else:
                    if k.startswith(DISPO_PREFIX):
                        disposition = k[len(DISPO_PREFIX) :]
                        disposition = BaseCodec.DISPO_ALTS.get(disposition, disposition)
                        if disposition and disposition in BaseCodec.DISPOSITIONS:
                            sort.sort(key=lambda x: x.disposition.get(disposition), reverse=reverse)
                    elif k in SORT_MEDIASTREAMINFO:
                        sort.sort(key=SORT_MEDIASTREAMINFO[k], reverse=reverse)
                    else:
                        self.log.debug("Skipping sort key %s." % (k))
                        continue
                self.log.debug("Sorted %s with %s:" % ("descending" if reverse else "ascending", k))
                self.log.debug(["%d->%d" % (output.index(x), sort.index(x)) for x in output])
                output = sort

        self.log.debug("Final sorting:")
        self.log.debug(["%d->%d" % (streams.index(x), output.index(x)) for x in streams])
        return output

    # Process external subtitle file with CleanIt library
    def cleanExternalSub(self, path):
        """Clean an external subtitle file using the CleanIt library, if enabled."""
        if self.settings.cleanit and cleanit:
            self.log.debug("Cleaning subtitle with path %s [subtitles.cleanit]." % (path))
            sub = cleanit.Subtitle(path)
            cfg = cleanit.Config.from_path(self.settings.cleanit_config) if self.settings.cleanit_config else cleanit.Config()
            rules = cfg.select_rules(tags=self.settings.cleanit_tags)
            if sub.clean(rules):
                sub.save()

    # Scan for external chapters file
    def scanForExternalMetadata(self, inputfile, suffix="metadata.txt"):
        """
        Scan the input file's directory for a metadata sidecar file.

        Looks for a file in the same directory whose name starts with the input
        filename and ends with the given suffix (default 'metadata.txt'). Returns
        the full path to the sidecar file if found, or None if none exists.
        """
        input_dir, filename, _ = self.parseFile(inputfile)
        for dirName, _, fileList in os.walk(input_dir):
            for fname in fileList:
                if fname.startswith(filename) and fname.endswith(suffix):
                    self.log.debug("Found valid external metadata file %s." % (fname))
                    return os.path.join(dirName, fname)
        return None

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

    # Undo output dir
    def restoreFromOutput(self, inputfile, outputfile):
        """
        Move outputfile back from output_dir to the original input directory.

        When output_dir is set and move-to is not configured, and the outputfile
        resides inside output_dir, this method moves the file back alongside the
        original input. Returns the new output path, or the original outputfile
        path if the move fails or is not applicable.
        """
        if self.settings.output_dir and not self.settings.moveto and os.path.commonpath([self.settings.output_dir, outputfile]) == self.settings.output_dir:
            input_dir, _, _ = self.parseFile(inputfile)
            outputfilename = os.path.split(outputfile)[1]
            newoutputfile = os.path.join(input_dir, outputfilename)
            self.log.info("Output file is in output_dir %s, moving back to original directory %s." % (self.settings.output_dir, input_dir))
            self.log.debug("New outputfile %s." % (newoutputfile))
            try:
                self._atomic_move(outputfile, newoutputfile)
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("First attempt to move the file has failed.")
                try:
                    self._atomic_move(outputfile, newoutputfile)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.log.exception("Unable to move %s to %s" % (outputfile, newoutputfile))
                    return outputfile
            return newoutputfile
        return outputfile

    # Reverse map option back to source stream
    def getSourceStream(self, index, info):
        """Return the stream at position index from info.streams."""
        return info.streams[index]

    # Safely get codec from options
    def getCodecFromOptions(self, x, info):
        """
        Resolve the effective codec name for an options dict entry.

        If the options entry specifies 'copy', returns the source stream's codec
        name from info; otherwise returns the codec value directly from options.
        """
        return self.getSourceStream(x["map"], info).codec if x.get("codec") == "copy" else x.get("codec")

    # Get subtitle extension based on codec
    def getSubExtensionFromCodec(self, codec):
        """
        Return the file extension for a given subtitle codec name.

        Looks up the codec in the subtitle_codec_extensions mapping. Falls back
        to using the codec name itself as the extension if no mapping is found.
        """
        try:
            return subtitle_codec_extensions[codec]
        except Exception:
            self.log.info("Wasn't able to determine subtitle file extension, defaulting to codec %s." % codec)
            return codec

    # Get subtitle file name based on options
    def getSubOutputFileFromOptions(self, inputfile, options, extension, include_all=False):
        """
        Build the subtitle output file path from an options dict.

        Extracts language and disposition from the options dict and delegates to
        getSubOutputFile() to construct the final path.
        """
        language = options["language"]
        return self.getSubOutputFile(inputfile, language, options["disposition"], extension, include_all)

    # Get subtitle file name based on language, disposition, and extension
    def getSubOutputFile(self, inputfile, language, disposition, extension, include_all):
        """
        Build the subtitle output file path from individual components.

        Constructs a filename of the form '<name>.<lang>[.<dispo>].<ext>' in the
        output_dir (or input directory). When include_all is True, all known
        disposition flags are considered for the filename suffix; otherwise only
        the dispositions listed in filename_dispositions settings are used.
        Appends a numeric counter if the file already exists.
        """
        disposition = self.dispoStringToDict(disposition)
        dispo = ""
        potentials = BaseCodec.DISPOSITIONS if include_all else self.settings.filename_dispositions
        for k in disposition:
            if disposition[k] and k in potentials:
                dispo += "." + k
        input_dir, filename, input_extension = self.parseFile(inputfile)
        output_dir = self.settings.output_dir or input_dir
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except Exception:
                self.log.exception("Unable to make output directory %s." % (output_dir))
        outputfile = os.path.join(output_dir, filename + "." + language + dispo + "." + extension)

        i = 2
        while os.path.isfile(outputfile):
            self.log.debug("%s exists, appending %s to filename." % (outputfile, i))
            outputfile = os.path.join(output_dir, filename + "." + language + dispo + "." + str(i) + "." + extension)
            i += 1
        return outputfile

    # Generate options to rip a subtitle from a container file
    def generateRipSubOpts(self, inputfile, s, scodec):
        """
        Build an FFmpeg options dict to extract a subtitle track to an external file.

        Returns an options dict suitable for passing to ripSubs(), describing the
        source file, subtitle stream index, codec, language, disposition, and
        output format.
        """
        ripsub = [{"map": s.index, "codec": scodec, "language": s.metadata["language"], "debug": "subtitle"}]
        options = {"source": [inputfile], "subtitle": ripsub, "format": s.codec if scodec == "copy" else scodec, "disposition": s.dispostr, "language": s.metadata["language"], "index": s.index}
        return options

    # Get output file name
    def getOutputFile(self, input_dir, filename, input_extension, temp_extension=None, ignore_output_dir=False, number=0):
        """
        Build the output file path for a conversion.

        Uses output_dir from settings unless ignore_output_dir is True, in which
        case input_dir is used. Applies temp_extension if given, otherwise the
        configured output extension. Appends a numeric suffix (e.g. '.2') when
        number > 0. Creates the output directory if it does not exist. Returns a
        tuple of (outputfile_path, output_dir).
        """
        if ignore_output_dir:
            output_dir = input_dir
        else:
            output_dir = self.settings.output_dir or input_dir
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except Exception:
                self.log.exception("Unable to create output directory %s." % output_dir)
                return None, output_dir

        output_extension = temp_extension or self.settings.output_extension

        self.log.debug("Input directory: %s." % input_dir)
        self.log.debug("File name: %s." % filename)
        self.log.debug("Input extension: %s." % input_extension)
        self.log.debug("Output directory: %s." % output_dir)
        self.log.debug("Output extension: %s." % output_dir)

        counter = (".%d" % number) if number > 0 else ""

        outputfile = os.path.join(output_dir, filename + counter + "." + output_extension)

        self.log.debug("Output file: %s." % outputfile)
        return outputfile, output_dir

    # Framedata normalization
    def parseAndNormalize(self, inputstring, denominator, splitter="/"):
        """
        Parse a fraction string and normalize its numerator to a target denominator.

        Splits inputstring on splitter (default '/') to get numerator and
        denominator, then scales the numerator so the denominator equals the
        target denominator. Returns the normalized integer numerator.
        """
        n, d = [float(x) for x in inputstring.split(splitter)]
        if d == denominator:
            return n
        return int(round((n / d) * denominator))

    # Ensure framedata has minimum parameters
    def hasValidFrameData(self, framedata):
        """
        Check whether FFprobe framedata contains the required HDR side data.

        Returns True only if both 'Mastering display metadata' and 'Content light
        level metadata' entries are present in the side_data_list. Returns False
        on any error or missing data.
        """
        try:
            if "side_data_list" in framedata:
                types = [x["side_data_type"] for x in framedata["side_data_list"] if "side_data_type" in x]
                if "Mastering display metadata" in types and "Content light level metadata" in types:
                    return True
            return False
        except Exception:
            return False

    def hasBitstreamVideoSubs(self, framedata):
        """
        Check whether framedata contains closed caption (bitstream subtitle) side data.

        Returns True if any entry in the side_data_list has a side_data_type that
        contains 'closed captions'. Returns False otherwise.
        """
        if "side_data_list" in framedata:
            for side_data in framedata["side_data_list"]:
                if "closed captions" in side_data.get("side_data_type", "").lower():
                    return True
        return False

    # Framedata normalization
    def normalizeFramedata(self, framedata, hdr):
        """
        Normalize HDR mastering display values in FFprobe framedata.

        Sets 'hdr' and 'repeat-headers' flags when hdr is True, then normalizes
        all chromaticity and luminance fraction values in the 'Mastering display
        metadata' side data entry to standard denominators (50000 for
        chromaticity, 10000 for luminance). Returns the modified framedata dict,
        or the original dict unchanged if an exception occurs.
        """
        try:
            if hdr:
                framedata["hdr"] = True
                framedata["repeat-headers"] = True
            if "side_data_list" in framedata:
                for side_data in framedata["side_data_list"]:
                    if side_data.get("side_data_type") == "Mastering display metadata":
                        side_data["red_x"] = self.parseAndNormalize(side_data.get("red_x"), 50000)
                        side_data["red_y"] = self.parseAndNormalize(side_data.get("red_y"), 50000)
                        side_data["green_x"] = self.parseAndNormalize(side_data.get("green_x"), 50000)
                        side_data["green_y"] = self.parseAndNormalize(side_data.get("green_y"), 50000)
                        side_data["blue_x"] = self.parseAndNormalize(side_data.get("blue_x"), 50000)
                        side_data["blue_y"] = self.parseAndNormalize(side_data.get("blue_y"), 50000)
                        side_data["white_point_x"] = self.parseAndNormalize(side_data.get("white_point_x"), 50000)
                        side_data["white_point_y"] = self.parseAndNormalize(side_data.get("white_point_y"), 50000)
                        side_data["min_luminance"] = self.parseAndNormalize(side_data.get("min_luminance"), 10000)
                        side_data["max_luminance"] = self.parseAndNormalize(side_data.get("max_luminance"), 10000)
                        break
            return framedata
        except Exception:
            return framedata

    def isDolbyVision(self, framedata):
        """
        Check whether framedata contains Dolby Vision metadata side data.

        Returns True if a side_data_list entry with side_data_type 'dolby vision
        metadata' (case-insensitive) is present. Returns False otherwise.
        """
        try:
            if "side_data_list" in framedata:
                for side_data in framedata["side_data_list"]:
                    if side_data.get("side_data_type", "").lower() == "dolby vision metadata":
                        return True
        except Exception:
            return False
        return False

    # Check if video stream meets criteria to be considered HDR
    def isHDRInput(self, videostream):
        """
        Check whether the video stream's colour properties match the configured HDR settings.

        Compares the stream's color space, transfer, and primaries against the
        hdr settings. Returns False immediately if no HDR screening parameters
        are defined. Returns True if all defined parameters match.
        """
        if len(self.settings.hdr["space"]) < 1 and len(self.settings.hdr["transfer"]) < 1 and len(self.settings.hdr["primaries"]) < 1:
            self.log.debug("No HDR screening parameters defined, returning false [hdr].")
            return False

        params = ["space", "transfer", "primaries"]
        for param in params:
            if param in videostream.color and len(self.settings.hdr.get(param)) > 0 and videostream.color.get(param) not in self.settings.hdr.get(param):
                self.log.debug("Stream is not HDR, color parameter %s does not match %s [hdr-%s]." % (videostream.color.get(param), self.settings.hdr.get(param), self.settings.hdr.get(param)))
                return False

        self.log.info("HDR video stream detected for %d." % videostream.index)
        return True

    def isHDR(self, videostream):
        """Alias for isHDRInput for backwards compatibility."""
        return self.isHDRInput(videostream)

    # Check if output pix_fmt is HDR
    def isHDROutput(self, pix_fmt, bit_depth):
        """
        Determine whether a pixel format and bit depth combination qualifies as HDR output.

        When pix_fmt is provided, returns True only if it is in the known HDR
        pixel format list and bit_depth is at least 10. When pix_fmt is None,
        returns True if bit_depth is at least 10.
        """
        if pix_fmt:
            hdr_pix_fmts = ["yuv420p10le", "yuv422p10le", "yuv444p10le", "yuv420p12le", "yuv422p12le", "yuv444p12le", "p010le"]
            return pix_fmt in hdr_pix_fmts and bit_depth >= 10
        else:
            return bit_depth >= 10

    # Run test conversion of subtitle to see if its image based, does not appear to be any other way to tell dynamically
    def isImageBasedSubtitle(self, inputfile, map):
        """
        Test whether a subtitle track is image-based by attempting a short SRT conversion.

        Runs FFmpeg for up to 1 second attempting to convert the track to SRT.
        Returns True (image-based) if FFmpeg raises an FFMpegConvertError, or
        False if the conversion succeeds (text-based).
        """
        ripsub = [{"map": map, "codec": "srt"}]
        options = {"source": [inputfile], "format": "srt", "subtitle": ripsub}
        postopts = ["-t", "00:00:01"]
        try:
            conv = self.converter.convert(None, options, timeout=30, postopts=postopts)
            _, cmds = next(conv)
            self.log.debug("isImageBasedSubtitle FFmpeg command:")
            self.log.debug(self.printableFFMPEGCommand(cmds))
            for _, debug in conv:
                if debug:
                    self.log.debug(debug)
        except FFMpegConvertError:
            return True
        return False

    # Check if video file meets criteria to just bypass conversion
    def canBypassConvert(self, inputfile, info, options=None):
        """
        Check whether the input file can skip FFmpeg conversion entirely.

        Returns True in three cases: the input and output extensions match and
        process-same-extensions is disabled; the file was already processed by
        SMA-NG and force-convert is off; or bypass-if-copying-all is enabled and
        all streams would be copied without reducing stream counts. Returns False
        otherwise.
        """
        # Process same extensions
        if self.settings.output_extension == self.parseFile(inputfile)[2]:
            if not self.settings.force_convert and not self.settings.process_same_extensions:
                self.log.info("Input and output extensions are the same so passing back the original file [process-same-extensions: %s]." % self.settings.process_same_extensions)
                return True
            elif info.format.metadata.get("encoder", "").startswith("sma") and not self.settings.force_convert:
                self.log.info(
                    "Input and output extensions match and the file appears to have already been processed by SMA-NG, enable force-convert to override [force-convert: %s]."
                    % self.settings.force_convert
                )
                return True
            elif (
                self.settings.bypass_copy_all
                and options
                and len([x for x in [options["video"]] + [x for x in options["audio"]] + [x for x in options["subtitle"]] if x["codec"] != "copy"]) == 0
                and len(options["audio"]) == len(info.audio)
                and len(options["subtitle"]) == len(info.subtitle)
                and not self.settings.force_convert
            ):
                self.log.info(
                    "Input and output extensions match, the file appears to copying all streams, and is not reducing the number of streams, enable force-convert to override [bypass-if-copying-all] [force-convert: %s]."
                    % self.settings.force_convert
                )
                return True
        self.log.debug("canBypassConvert returned False.")
        return False

    # Generate copy/paste friendly FFMPEG command
    def printableFFMPEGCommand(self, cmds):
        """Format an FFmpeg command list as a human-readable string, quoting items that contain spaces or pipes."""
        return " ".join('"%s"' % item if (" " in item or "|" in item) and '"' not in item else item for item in cmds)

    # Encode a new file based on selected options, built in naming conflict resolution
    def convert(self, options, preopts, postopts, reportProgress=False, progressOutput=None):
        """
        Run the FFmpeg conversion and return the output and input file paths.

        Resolves naming conflicts between input and output (renaming the input to
        a .original suffix or numbering the output), runs FFmpeg with optional
        progress reporting via displayProgressBar or a custom progressOutput
        callback, applies permissions to the output, and renames any temp
        extension to the final extension. Returns a tuple of (outputfile,
        inputfile), or (None, inputfile) on failure.
        """
        self.log.info("Starting conversion.")
        inputfile = options["source"][0]
        input_dir, filename, input_extension = self.parseFile(inputfile)
        originalinputfile = inputfile
        outputfile, output_dir = self.getOutputFile(input_dir, filename, input_extension, self.settings.temp_extension)
        finaloutputfile, _ = self.getOutputFile(input_dir, filename, input_extension)

        if outputfile is None or finaloutputfile is None:
            self.log.error("Unable to create output directory, aborting conversion.")
            return None, inputfile

        self.log.debug("Final output file: %s." % finaloutputfile)

        if len(options["audio"]) == 0:
            self.log.error("Conversion has no audio streams, aborting")
            return None, inputfile

        # Check if input file and the final output file are the same and preferentially rename files (input first, then output if that fails)
        if os.path.abspath(inputfile) == os.path.abspath(finaloutputfile):
            self.log.debug("Inputfile and final outputfile are the same, trying to rename inputfile first.")
            try:
                og = inputfile + ".original"
                i = 2
                while os.path.isfile(og):
                    og = "%s.%d.original" % (inputfile, i)
                    i += 1
                os.rename(inputfile, og)
                if self.settings.burn_subtitles:
                    try:
                        if self.raw(os.path.abspath(inputfile)) in (options["video"].get("filter") or ""):
                            self.log.debug("Renaming inputfile in burnsubtitles filter if its present [burn-subtitles].")
                            options["video"]["filter"] = options["video"]["filter"].replace(self.raw(os.path.abspath(inputfile)), self.raw(os.path.abspath(og)))
                    except Exception:
                        self.log.exception("Error trying to rename filter [burn-subtitles].")
                inputfile = og
                options["source"][0] = og
                self.log.debug("Renamed original file to %s." % inputfile)

            except Exception:
                i = 2
                while os.path.isfile(finaloutputfile):
                    outputfile, output_dir = self.getOutputFile(input_dir, filename, input_extension, self.settings.temp_extension, number=i)
                    finaloutputfile, _ = self.getOutputFile(input_dir, filename, input_extension, number=i)
                    i += 1
                self.log.debug("Unable to rename inputfile. Alternatively renaming output file to %s." % outputfile)

        # Delete output file if it already exists and deleting enabled
        if os.path.exists(outputfile) and self.settings.delete:
            self.removeFile(outputfile)

        # Final sweep to make sure outputfile does not exist, renaming as the final solution
        i = 2
        while os.path.isfile(outputfile):
            outputfile, output_dir = self.getOutputFile(input_dir, filename, input_extension, self.settings.temp_extension, number=i)
            finaloutputfile, _ = self.getOutputFile(input_dir, filename, input_extension, number=i)
            i += 1

        try:
            conv = self.converter.convert(outputfile, options, timeout=None, preopts=preopts, postopts=postopts, strip_metadata=self.settings.strip_metadata)
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Error converting file.")
            return None, inputfile

        _, cmds = next(conv)
        self.log.info("FFmpeg command:")
        self.log.info("======================")
        self.log.info(self.printableFFMPEGCommand(cmds))
        self.log.info("======================")

        try:
            timecode = 0
            debug = ""
            for timecode, debug in conv:
                if reportProgress:
                    if progressOutput:
                        progressOutput(timecode, debug)
                    else:
                        self.displayProgressBar(timecode, debug)
            if reportProgress:
                if progressOutput:
                    progressOutput(100, debug)
                else:
                    self.displayProgressBar(100, newline=True)

            self.log.info("%s created." % outputfile)
            self.setPermissions(outputfile)

        except FFMpegConvertError as e:
            self.log.exception("Error converting file, FFMPEG error.")
            self.log.error(e.cmd)
            self.log.error(e.output)
            if os.path.isfile(outputfile):
                self.removeFile(outputfile)
                self.log.error("%s deleted." % outputfile)
            outputfile = None
            try:
                os.rename(inputfile, originalinputfile)
                return None, originalinputfile
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Error restoring original inputfile after exception.")
                return None, inputfile
        except KeyboardInterrupt:
            raise
        except Exception:
            self.log.exception("Unexpected exception during conversion.")
            try:
                os.rename(inputfile, originalinputfile)
                return None, originalinputfile
            except Exception:
                self.log.exception("Error restoring original inputfile after FFMPEG error.")
                return None, inputfile

        # Check if the finaloutputfile differs from the outputfile. This can happen during above renaming or from temporary extension option
        if outputfile != finaloutputfile:
            self.log.debug("Outputfile and finaloutputfile are different attempting to rename to final extension [temp_extension].")
            try:
                os.rename(outputfile, finaloutputfile)
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Unable to rename output file to its final destination file extension [temp_extension].")
                finaloutputfile = outputfile

        return finaloutputfile, inputfile

    # Generate progress bar
    def displayProgressBar(self, complete, debug="", width=20, newline=False):
        """
        Print a text progress bar to stdout showing conversion progress.

        Renders a bar of the given width, the completion percentage, and
        optionally the current FFmpeg debug line when detailedprogress is
        enabled. Writes a newline at the end when newline is True. Falls back
        to printing the completion value if an exception occurs.

        Silently skips output when stdout is not connected to a TTY (e.g.
        when invoked as a daemon subprocess) to prevent progress-bar escape
        sequences from polluting log files. In non-TTY mode, emits the raw
        FFmpeg progress/debug line instead so daemon log consumers can still
        detect and forward periodic transcode progress updates.
        """
        if not sys.stdout.isatty():
            if debug:
                print(debug.strip(), flush=True)
            return
        try:
            divider = 100 / width

            if complete > 100:
                complete = 100

            sys.stdout.write("\r")
            sys.stdout.write("[{0}] {1}% ".format("#" * int(round(complete / divider)) + " " * int(round(width - (complete / divider))), complete))
            if debug and self.settings.detailedprogress:
                if complete == 100:
                    sys.stdout.write("%s" % debug.strip())
                else:
                    sys.stdout.write(" %s" % debug.strip())
            if newline:
                sys.stdout.write("\n")
            sys.stdout.flush()
        except KeyboardInterrupt:
            raise
        except Exception:
            print(complete)

    # Break apart a file path into the directory, filename, and extension
    def parseFile(self, path):
        """
        Split a file path into its directory, base filename, and lowercase extension.

        Returns a tuple of (directory, filename_without_extension, extension)
        where the extension has no leading dot and is lowercased.
        """
        path = os.path.abspath(path)
        input_dir, filename = os.path.split(path)
        filename, input_extension = os.path.splitext(filename)
        input_extension = input_extension[1:]
        return input_dir, filename, input_extension.lower()

    # Process a file with QTFastStart, removing the original file
    def QTFS(self, inputfile):
        """
        Run qtfaststart to relocate the moov atom to the start of the file.

        Writes output to a temporary file then replaces the original. Skips
        processing for MKV output or when relocate_moov is disabled. Does
        nothing and returns the inputfile path unchanged if the file does not
        exist or qtfaststart raises a FastStartException.
        """
        TEMP_EXT = ".QTFS"
        # Relocate MOOV atom to the very beginning. Can double the time it takes to convert a file but makes streaming faster
        if os.path.isfile(inputfile) and self.settings.relocate_moov and self.settings.output_format not in ["mkv"]:
            from qtfaststart import exceptions, processor

            self.log.info("Relocating MOOV atom to start of file.")

            outputfile = inputfile + TEMP_EXT

            # Clear out the temp file if it exists
            if os.path.exists(outputfile):
                self.removeFile(outputfile, 0, 0)

            try:
                processor.process(inputfile, outputfile)
                self.setPermissions(outputfile)

                # Cleanup
                if self.removeFile(inputfile, replacement=outputfile):
                    return inputfile
                else:
                    self.log.error("Error cleaning up QTFS temp files.")
                    return False
            except exceptions.FastStartException:
                self.log.debug("QT FastStart did not run - perhaps moov atom was at the start already or file is in the wrong format.")
                return inputfile
        return inputfile

    # Makes additional copies of the input file in each directory specified in the copy_to option
    def replicate(self, inputfile, relativePath=None):
        """
        Copy the output file to copy-to directories and/or move it to the move-to directory.

        When copy-to is configured, atomically copies inputfile to each
        destination and appends those paths to the returned list. When move-to is
        configured, atomically moves inputfile to the destination and updates the
        first entry in the returned list. Returns a list of all resulting file
        paths, with the primary path at index 0.
        """
        files = [inputfile]

        if self.settings.copyto:
            self.log.debug("Copyto option is enabled.")
            for d in self.settings.copyto:
                if relativePath:
                    d = os.path.join(d, relativePath)
                if not os.path.exists(d):
                    os.makedirs(d)
                dst_path = os.path.join(d, os.path.split(inputfile)[1])
                try:
                    self._atomic_copy(inputfile, dst_path)
                    self.log.info("%s copied to %s." % (inputfile, d))
                    files.append(dst_path)
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.log.exception("First attempt to copy the file has failed.")
                    try:
                        self._atomic_copy(inputfile, dst_path)
                        self.log.info("%s copied to %s." % (inputfile, d))
                        files.append(dst_path)
                    except KeyboardInterrupt:
                        raise
                    except Exception:
                        self.log.exception("Unable to create additional copy of file in %s." % (d))

        if self.settings.moveto:
            self.log.debug("Moveto option is enabled.")
            moveto = os.path.join(self.settings.moveto, relativePath) if relativePath else self.settings.moveto
            if not os.path.exists(moveto):
                os.makedirs(moveto)
            moveto_path = os.path.join(moveto, os.path.basename(inputfile))
            try:
                self._atomic_move(inputfile, moveto_path)
                self.log.info("%s moved to %s." % (inputfile, moveto))
                files[0] = moveto_path
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("First attempt to move the file has failed.")
                try:
                    self._atomic_move(inputfile, moveto_path)
                    self.log.info("%s moved to %s." % (inputfile, moveto))
                    files[0] = moveto_path
                except KeyboardInterrupt:
                    raise
                except Exception:
                    self.log.exception("Unable to move %s to %s" % (inputfile, moveto))
        for filename in files:
            self.log.debug("Final output file: %s." % filename)
        return files

    def outputDirHasFreeSpace(self, inputfile):
        """
        Check whether output_dir has enough free space to hold the converted file.

        Compares the available space on output_dir against the input file size
        multiplied by output_dir_ratio. Returns True when the check passes, when
        output_dir or output_dir_ratio is not configured, or when the check
        cannot be performed.
        """
        if self.settings.output_dir and self.settings.output_dir_ratio:
            try:
                needed = os.path.getsize(inputfile) * self.settings.output_dir_ratio
                usage = shutil.disk_usage(self.settings.output_dir)
                enough = usage.free > needed
                if not enough:
                    self.log.info("Output-directory does not have enough free space (%s needed) [output-directory-space-ratio]." % needed)
                return enough
            except Exception:
                self.log.exception("Unable to check free space on output directory %s [output-directory-space-ratio]." % self.settings.output_dir)
        return True

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

        Attempts os.rename first (instant on the same filesystem). Falls back to
        _atomic_copy followed by os.remove when a cross-device OSError is raised.
        """
        try:
            os.rename(src, dst)
        except OSError:
            self._atomic_copy(src, dst)
            os.remove(src)

    # Robust file removal function, with options to retry in the event the file is in use, and replace a deleted file
    def removeFile(self, filename, retries=2, delay=10, replacement=None):
        """
        Remove a file with retry logic, optionally replacing it with another file.

        Attempts to delete filename up to retries + 1 times, sleeping delay
        seconds between attempts. When replacement is provided, renames the
        replacement to filename after deletion. Returns True if the file no
        longer exists after the attempts, False if it still exists.
        """
        for _ in range(retries + 1):
            try:
                # Make sure file isn't read-only
                os.chmod(filename, int("0777", 8))
            except Exception:
                self.log.debug("Unable to set file permissions before deletion. This is not always required.")
            try:
                if os.path.exists(filename):
                    os.remove(filename)
                # Replaces the newly deleted file with another by renaming (replacing an original with a newly created file)
                if replacement:
                    os.rename(replacement, filename)
                    filename = replacement
                break
            except KeyboardInterrupt:
                raise
            except Exception:
                self.log.exception("Unable to remove or replace file %s." % filename)
                if delay > 0:
                    self.log.debug("Delaying for %s seconds before retrying." % delay)
                    time.sleep(delay)
        return False if os.path.isfile(filename) else True

    # Formatter needed for burn subtitle filter syntax
    def raw(self, text):
        """
        Escape special characters in a path string for use in FFmpeg subtitle filter syntax.

        Replaces backslashes with double-backslashes and colons with escaped
        colons so the path can be safely embedded in a subtitles= filter value.
        """
        escape_dict = {"\\": r"\\", ":": "\\:"}
        output = ""
        for char in text:
            try:
                output += escape_dict[char]
            except KeyError:
                output += char
        return output
