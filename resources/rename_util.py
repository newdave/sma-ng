"""
Standalone rename utility for SMA-NG.

Renames already-converted media files using the existing naming templates
without running any conversion. Optionally updates .plexmatch sidecars and
triggers a Plex library refresh after renaming.
"""

import logging
import os
import re

import guessit

os.environ["REGEX_DISABLED"] = "1"  # Fixes Toilal/rebulk#20

from autoprocess.plex import refreshPlex
from resources.mediaprocessor import MediaProcessor
from resources.metadata import MediaType, Metadata, update_plexmatch
from resources.naming import generate_name
from resources.naming import rename_file as _rename_file

_FALLBACK_MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".m4v",
    ".ts",
    ".m2ts",
    ".flv",
    ".webm",
}


class _TypeStub:
    """Minimal duck-type standing in for a Metadata object.

    Used when we know the media type (TV vs Movie) from guessit or a
    type_hint but have no TMDB IDs to resolve.  Provides just enough
    interface for generate_name() to select the correct template; all
    naming fields come from the guessit/mediainfo data already passed
    via NamingData.from_mediainfo / NamingData.from_tagdata.
    """

    def __init__(self, mediatype):
        self.mediatype = mediatype
        # TV fields that generate_name / NamingData.from_tagdata may read.
        self.showname = None
        self.showdata = None
        self.season = None
        self.episode = None
        self.episodes = None
        self.title = None
        self.tmdbid = None
        self.tvdbid = None
        self.imdbid = None
        self.date = None


class RenameProcessor:
    """
    Renames media files using SMA-NG naming templates without converting.

    Handles single files and directory trees. Optionally updates .plexmatch
    sidecars and triggers Plex refresh after renaming.
    """

    def __init__(self, settings, logger=None):
        self.settings = settings
        self.log = logger or logging.getLogger(__name__)
        self.mp = MediaProcessor(settings, logger=self.log)

    @staticmethod
    def _extract_ids_from_path(filepath):
        """Extract TMDB/TVDB IDs embedded in the path by Plex/Radarr/Sonarr.

        Paths like '.../Show Name {tvdb-289574}/...' or filenames containing
        '{tmdb-1640}' encode the database ID in the name.  Returns a dict with
        any found keys: 'tmdbid', 'tvdbid'.  Also returns a cleaned basename
        with those tags stripped so guessit is not confused by them.
        """
        ids = {}
        # Check both the filename and parent directory components
        for part in [os.path.basename(filepath), os.path.basename(os.path.dirname(filepath)), os.path.basename(os.path.dirname(os.path.dirname(filepath)))]:
            m = re.search(r"\{tmdb-(\d+)\}", part, re.IGNORECASE)
            if m and "tmdbid" not in ids:
                ids["tmdbid"] = int(m.group(1))
            m = re.search(r"\{tvdb-(\d+)\}", part, re.IGNORECASE)
            if m and "tvdbid" not in ids:
                ids["tvdbid"] = int(m.group(1))
            m = re.search(r"\{imdb-(tt\d+)\}", part, re.IGNORECASE)
            if m and "imdbid" not in ids:
                ids["imdbid"] = m.group(1)

        # Clean basename: strip all {xxx-NNN} tags so guessit isn't confused
        clean_basename = re.sub(r"\{[a-z]+-[^}]+\}", "", os.path.basename(filepath), flags=re.IGNORECASE).strip()
        return ids, clean_basename

    def _infer_mediatype(self, filepath):
        """Infer MediaType from directory path structure then filename.

        Checks (in order):
        1. Any ancestor directory named 'Season XX', 'S01' → TV.
        2. Any ancestor directory whose name (lowercased) contains 'movie'
           or 'film' → Movie.  Common library layouts use these keywords.
        3. guessit on the cleaned filename — type='episode' AND a plausible
           season number (≤ 100) AND episode number (≤ 100) → TV.
           A bare leading number (e.g. '57 Seconds') regularly fools guessit
           into treating the number as an episode count; discard that signal
           when there is no corroborating season number.
        4. A 'date' key in guessit (air-date show) → TV.
        5. Default → Movie.
        """
        parts = os.path.normpath(os.path.dirname(filepath)).split(os.sep)
        _season_re = re.compile(r"^(season\s*\d+|s\d{2})$", re.IGNORECASE)
        _movie_re = re.compile(r"\b(movie|movies|film|films)\b", re.IGNORECASE)

        for part in reversed(parts):
            if _season_re.match(part):
                return MediaType.TV
            if _movie_re.search(part):
                return MediaType.Movie

        _, clean_basename = self._extract_ids_from_path(filepath)
        guess = guessit.guessit(clean_basename)

        if "date" in guess:
            return MediaType.TV

        if guess.get("type") == "episode":
            season = guess.get("season")
            episode = guess.get("episode")
            # Only trust the episode classification when both season and episode
            # are present and within sane ranges.  A title like "57 Seconds"
            # produces episode=57, season=None — that's a false positive.
            if season is not None and episode is not None and season <= 100 and episode <= 100:
                return MediaType.TV

        return MediaType.Movie

    def _resolve_metadata(self, filepath, tmdbid=None, tvdbid=None, imdbid=None, season=None, episode=None, type_hint=None):
        """
        Get (info, tagdata) for a file.

        info is the MediaFormatInfo from mp.isValidSource(); tagdata is a
        Metadata instance when any ID or type_hint is given, otherwise None
        (generate_name falls back to guessit internally).

        Returns (info, tagdata) or (None, None) if the file is not valid media.
        """
        info = self.mp.isValidSource(filepath)
        if not info:
            self.log.warning("Not a valid media source, skipping: %s" % filepath)
            return None, None

        # Extract any TMDB/TVDB IDs embedded in the path (Plex/Radarr naming).
        # These take lower priority than explicitly passed IDs.
        path_ids, _ = self._extract_ids_from_path(filepath)
        if tmdbid is None:
            tmdbid = path_ids.get("tmdbid")
        if tvdbid is None:
            tvdbid = path_ids.get("tvdbid")
        if imdbid is None:
            imdbid = path_ids.get("imdbid")

        has_id = any(x is not None for x in (tmdbid, tvdbid, imdbid))

        # Always determine media type so generate_name picks the right template.
        # Priority: explicit flag > season arg > path structure > guessit.
        if type_hint == "movie":
            mediatype = MediaType.Movie
        elif type_hint == "tv" or season is not None:
            mediatype = MediaType.TV
        else:
            mediatype = self._infer_mediatype(filepath)

        if has_id:
            # For TV, Metadata() requires season and episode; without them we
            # cannot fetch meaningful data so fall back to a stub.
            if mediatype == MediaType.TV and (season is None or episode is None):
                self.log.debug("TV media type inferred but no season/episode provided for %s; using stub" % os.path.basename(filepath))
                tagdata = _TypeStub(mediatype)
            else:
                try:
                    tagdata = Metadata(
                        mediatype,
                        tmdbid=tmdbid,
                        tvdbid=tvdbid,
                        imdbid=imdbid,
                        season=season,
                        episode=episode,
                        language=getattr(self.settings, "tagging_language", None),
                        logger=self.log,
                    )
                except Exception:
                    self.log.exception("Failed to fetch metadata for %s" % filepath)
                    tagdata = _TypeStub(mediatype)
        elif mediatype == MediaType.TV:
            # No explicit IDs.  For TV, attempt a TMDB lookup so that air-date
            # episodes get a real title rather than an empty string.  This is
            # especially important for late-night shows (e.g. The Late Show)
            # where guessit provides the series title and a date but no S/E.
            tagdata = self._lookup_tmdb_tv(filepath, season, episode)
        else:
            tagdata = _TypeStub(mediatype)

        return info, tagdata

    def _lookup_tmdb_tv(self, filepath, season=None, episode=None):
        """Search TMDB by series title and return a Metadata object or _TypeStub.

        For air-date shows (filename contains YYYY-MM-DD, no S/E numbers) we:
          1. Search TMDB by the guessit series title to get the TMDB ID.
          2. Fetch the show's season list to identify which season contains
             the air date (avoids an extra show.info() call when season is known).
          3. Fetch that season's episode list and match by air date to get the
             real episode number — bypassing episode/0 entirely, which would
             result in an unnecessary 404.
          4. Construct Metadata() with the real season + episode number so the
             full episode title and credits are retrieved in one clean request.

        Falls back to _TypeStub(MediaType.TV) on any failure so rename still works.
        """
        import tmdbsimple as _tmdb

        from resources.extensions import tmdb_api_key

        basename = os.path.basename(filepath)
        _, clean_basename = self._extract_ids_from_path(filepath)
        guess = guessit.guessit(clean_basename)
        series_title = guess.get("title", "")
        air_date = None
        m = re.search(r"(\d{4}-\d{2}-\d{2})", basename)
        if m:
            air_date = m.group(1)

        if not series_title:
            self.log.debug("No series title from guessit for %s, using stub" % basename)
            return _TypeStub(MediaType.TV)

        try:
            _tmdb.API_KEY = tmdb_api_key
            _tmdb.REQUESTS_TIMEOUT = 30
            results = _tmdb.Search().tv(query=series_title).get("results", [])
        except Exception:
            self.log.debug("TMDB search failed for %r" % series_title)
            return _TypeStub(MediaType.TV)

        if not results:
            self.log.debug("No TMDB results for %r" % series_title)
            return _TypeStub(MediaType.TV)

        resolved_tmdbid = results[0]["id"]
        self.log.debug("TMDB search: %r -> tmdbid %s (%s)" % (series_title, resolved_tmdbid, results[0].get("name", "")))

        # Determine season number.
        resolved_season = season
        if resolved_season is None:
            resolved_season = guess.get("season")
        if resolved_season is None and air_date:
            resolved_season = self._find_season_for_date(_tmdb, resolved_tmdbid, air_date)
        if resolved_season is None:
            self.log.debug("Could not determine season for %s, using stub" % basename)
            return _TypeStub(MediaType.TV)

        # Determine episode number.  For air-date shows the episode number is
        # not in the filename, so we look it up from the season episode list
        # rather than passing episode=0 to Metadata() and triggering a 404.
        resolved_episode = episode
        if resolved_episode is None and air_date:
            resolved_episode = self._find_episode_for_date(_tmdb, resolved_tmdbid, resolved_season, air_date)
        if resolved_episode is None:
            self.log.debug("Could not resolve episode number for %s, using stub" % basename)
            return _TypeStub(MediaType.TV)

        language = getattr(self.settings, "tagging_language", None)
        try:
            tagdata = Metadata(
                MediaType.TV,
                tmdbid=resolved_tmdbid,
                season=resolved_season,
                episode=resolved_episode,
                original=filepath,
                language=language,
                logger=self.log,
            )
            self.log.info("Resolved %s -> S%02dE%02d - %s" % (basename, resolved_season, resolved_episode, tagdata.title or ""))
            return tagdata
        except Exception:
            self.log.debug("Metadata() failed for tmdbid %s S%sE%s, using stub" % (resolved_tmdbid, resolved_season, resolved_episode))
            return _TypeStub(MediaType.TV)

    @staticmethod
    def _find_season_for_date(_tmdb, tmdbid, air_date):
        """Return the season number whose air date range contains *air_date*.

        Walks the show's season list in chronological order, returning the last
        season whose premiere date is on or before the target date.
        """
        try:
            show = _tmdb.TV(tmdbid).info()
            seasons = [s for s in show.get("seasons", []) if s.get("season_number", 0) > 0]
            seasons.sort(key=lambda s: s.get("air_date") or "")
            best = None
            for s in seasons:
                s_date = s.get("air_date") or ""
                if s_date and s_date <= air_date:
                    best = s["season_number"]
            return best
        except Exception:
            return None

    @staticmethod
    def _find_episode_for_date(_tmdb, tmdbid, season, air_date):
        """Return the episode number in *season* whose air date matches *air_date*.

        Fetches the season episode list (one API call) and returns the episode
        number of the first matching entry, or None if not found.
        """
        try:
            season_data = _tmdb.TV_Seasons(tmdbid, season).info()
            for ep in season_data.get("episodes", []):
                if ep.get("air_date") == air_date:
                    return ep["episode_number"]
        except Exception:
            pass
        return None

    def rename_file(self, filepath, dry_run=False, tmdbid=None, tvdbid=None, imdbid=None, season=None, episode=None, type_hint=None, use_arr=False):
        """
        Rename a single file.

        When *use_arr* is True, delegates to Sonarr/Radarr's RenameFiles
        command instead of SMA's own template engine.  The matched instance is
        determined by the file path prefix, the same way the conversion
        pipeline does it.  Dry-run is not supported for arr delegation
        (Sonarr/Radarr do not have a preview mode) and is ignored.

        Returns a dict with keys:
            old     -- original path
            new     -- new path (same as old when unchanged or dry_run with no change)
            changed -- whether the name would change (or did change)
            dry_run -- whether this was a dry run
        """
        result = {
            "old": filepath,
            "new": filepath,
            "changed": False,
            "dry_run": dry_run,
        }

        if use_arr:
            # Skip FFprobe validation — Sonarr/Radarr already has a record of
            # the file and will reject it if it doesn't match.
            self.log.info("arr-rename: %s" % os.path.basename(filepath))
            return self._rename_file_via_arr(filepath, result)

        info, tagdata = self._resolve_metadata(
            filepath,
            tmdbid=tmdbid,
            tvdbid=tvdbid,
            imdbid=imdbid,
            season=season,
            episode=episode,
            type_hint=type_hint,
        )
        if info is None:
            return result

        guess_data = guessit.guessit(os.path.basename(filepath))

        new_name = generate_name(
            filepath,
            info,
            tagdata,
            self.settings,
            guess_data=guess_data,
            log=self.log,
        )

        if new_name is None:
            self.log.warning("Naming disabled or no name produced for: %s" % filepath)
            return result

        # Determine what the new path would be (same dir, new stem, same ext).
        directory = os.path.dirname(filepath)
        ext = os.path.splitext(filepath)[1]
        prospective_path = os.path.join(directory, new_name + ext)

        if prospective_path == filepath:
            self.log.debug("Filename unchanged: %s" % filepath)
            return result

        result["changed"] = True

        if dry_run:
            result["new"] = prospective_path
            self.log.info("[dry-run] Would rename: %s -> %s" % (os.path.basename(filepath), os.path.basename(prospective_path)))
            return result

        new_path = _rename_file(filepath, new_name, log=self.log)
        result["new"] = new_path
        # If rename_file returned the original path the rename failed.
        if new_path == filepath:
            result["changed"] = False

        return result

    def _rename_file_via_arr(self, filepath, result):
        """Delegate rename to the matching Sonarr/Radarr instance."""
        from resources.mediamanager import build_api, rename_via_arr

        dirpath = os.path.dirname(filepath)
        instance = None
        arr_type = None
        for inst in getattr(self.settings, "sonarr_instances", []):
            ipath = inst.get("path", "")
            if ipath and dirpath.startswith(ipath) and inst.get("apikey"):
                instance, arr_type = inst, "sonarr"
                break
        if instance is None:
            for inst in getattr(self.settings, "radarr_instances", []):
                ipath = inst.get("path", "")
                if ipath and dirpath.startswith(ipath) and inst.get("apikey"):
                    instance, arr_type = inst, "radarr"
                    break

        if instance is None:
            self.log.warning("--arr-rename: no matching Sonarr/Radarr instance for %s" % filepath)
            return result

        base_url, headers = build_api(instance, "SMA-NG rename")
        new_path = rename_via_arr(base_url, headers, arr_type, filepath, self.log)
        if new_path and new_path != filepath:
            result["new"] = new_path
            result["changed"] = True
        elif new_path is None:
            self.log.warning("--arr-rename: arr did not return a new path for %s" % filepath)
        return result

    def rename_directory(self, dirpath, dry_run=False, tmdbid=None, tvdbid=None, imdbid=None, season=None, episode=None, type_hint=None, use_arr=False):
        """
        Recursively rename all media files in a directory tree.

        ID overrides apply to every file in the tree, which is useful for a
        single-show or single-movie directory.

        Returns a list of dicts with the same shape as rename_file's return value.
        """
        raw_exts = getattr(self.settings, "input_extension", None)
        if raw_exts:
            media_extensions = set("." + ext.lstrip(".") for ext in raw_exts)
        else:
            media_extensions = _FALLBACK_MEDIA_EXTENSIONS

        results = []

        for root, dirs, files in os.walk(dirpath):
            # Skip hidden directories in-place so os.walk won't descend.
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for filename in files:
                if filename.startswith("."):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext not in media_extensions:
                    continue

                filepath = os.path.join(root, filename)
                result = self.rename_file(
                    filepath,
                    dry_run=dry_run,
                    tmdbid=tmdbid,
                    tvdbid=tvdbid,
                    imdbid=imdbid,
                    season=season,
                    episode=episode,
                    type_hint=type_hint,
                    use_arr=use_arr,
                )
                results.append(result)

        return results

    def update_plexmatch_for_results(self, results):
        """
        Write .plexmatch sidecars for each changed file in results.

        Silently skips when plexmatch_enabled is False on settings.
        """
        if not getattr(self.settings, "plexmatch_enabled", False):
            return

        for r in results:
            if not r.get("changed") or r.get("dry_run"):
                continue
            try:
                update_plexmatch(r["new"], None, self.settings, log=self.log)
            except Exception:
                self.log.exception("Error updating .plexmatch for %s" % r["new"])

    def refresh_plex_for_results(self, results):
        """
        Trigger a Plex refresh once per unique parent directory of changed files.

        Silently skips when no Plex host is configured.
        """
        if not self.settings.Plex.get("host"):
            return

        # Build a mapping of parent-dir -> one representative file path so that
        # refreshPlex (which calls os.path.dirname internally) gets a real file.
        dir_to_file = {}
        for r in results:
            if r.get("changed") and not r.get("dry_run"):
                dirpath = os.path.dirname(r["new"])
                dir_to_file.setdefault(dirpath, r["new"])

        for dirpath, filepath in dir_to_file.items():
            try:
                refreshPlex(self.settings, filepath, self.log)
            except Exception:
                self.log.exception("Plex refresh failed for directory: %s" % dirpath)
