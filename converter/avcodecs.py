#!/usr/bin/env python3
"""Codec class definitions mapping SMA-NG codec names to FFmpeg encoder/decoder identifiers."""


class BaseCodec(object):
    """
    Base audio/video/subtitle codec class.
    """

    DISPOSITIONS = [
        "default",
        "dub",
        "original",
        "comment",
        "lyrics",
        "karaoke",
        "forced",
        "hearing_impaired",
        "visual_impaired",
        # 'clean_effects',
        # 'attached_pic',
        "captions",
        # 'descriptions',
        # 'dependent',
        # 'metadata',
    ]

    DISPO_ALTS = {"commentary": "comment", "force": "forced", "sdh": "hearing_impaired", "hi": "hearing_impaired", "cc": "hearing_impaired", "lyric": "lyrics", "dubbed": "dub"}

    DISPO_STRINGS = {
        "comment": "Commentary",
        "hearing_impaired": "Hearing Impaired",
        "visual_impaired": "Visual Impaired",
        "dub": "Dub",
        "forced": "Forced",
        "lyrics": "Lyrics",
        "Karaoke": "Karaoke",
        "original": "Original",
        "captions": "Captions",
    }

    UNDEFINED = "und"

    encoder_options = {}
    codec_name = None
    ffmpeg_codec_name = None
    ffprobe_codec_name = None
    max_depth = 9999

    def supportsBitDepth(self, depth):
        """Return True if the codec supports the given bit depth."""
        return depth <= self.max_depth

    def parse_options(self, opt):
        """Validate that opt['codec'] matches this codec's codec_name.

        Raises ValueError if the codec name is missing or does not match.
        Subclasses call super().parse_options(opt) at the top of their own
        parse_options implementations to perform this check before continuing.
        """
        if "codec" not in opt or opt["codec"] != self.codec_name:
            raise ValueError("invalid codec name")
        return None

    def _codec_specific_parse_options(self, safe):
        """Apply codec-specific validation and transformation to the safe options dict.

        Called by parse_options after generic option sanitisation. Subclasses
        override this to validate or mutate 'safe' (e.g. clamping quality
        ranges, removing conflicting keys). Returns the (possibly modified) dict.
        """
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        """Return additional FFmpeg command-line tokens for this codec.

        Called at the end of parse_options after all common options have been
        emitted. Subclasses override this to append codec-specific flags (e.g.
        quality parameters, hardware device selectors). Returns a list of
        strings that will be appended to the option list.
        """
        return []

    def safe_disposition(self, dispo):
        """Return a disposition string that explicitly negates all unset flags.

        Takes an existing disposition string (e.g. '+default+forced') and
        appends '-<flag>' for every known disposition flag not already present,
        so that FFmpeg clears inherited flags rather than leaving them ambiguous.
        """
        dispo = dispo or ""
        for d in self.DISPOSITIONS:
            if d not in dispo:
                dispo += "-" + d
        return dispo

    def safe_framedata(self, opts):
        """Return a safe frame-data string (currently unused stub, returns empty string)."""
        safe = ""
        return safe

    def safe_options(self, opts):
        """Filter and typecast raw options against this codec's encoder_options schema.

        Iterates over opts and retains only keys declared in encoder_options,
        casting each value to the declared type. Keys with a None value or
        a value that cannot be cast are silently dropped. Returns the filtered
        and cast dict.
        """
        safe = {}

        # Only copy options that are expected and of correct type
        # (and do typecasting on them)
        for k, v in opts.items():
            if k in self.encoder_options and v is not None:
                typ = self.encoder_options[k]
                try:
                    safe[k] = typ(v)
                except ValueError:
                    pass
        return safe

    @staticmethod
    def _sanitize_stream_metadata(safe):
        """Validate and remove malformed language/title/disposition keys from *safe* in place.

        - ``language``: removed when longer than 3 characters.
        - ``title``: removed when empty.
        - ``disposition``: removed when blank.

        Returns the modified ``safe`` dict.
        """
        if "language" in safe and len(safe["language"]) > 3:
            del safe["language"]
        if "title" in safe and len(safe["title"]) < 1:
            del safe["title"]
        if "disposition" in safe and len(safe["disposition"].strip()) < 1:
            del safe["disposition"]
        return safe

    @staticmethod
    def _build_stream_metadata(safe, stream_type, stream):
        """Emit ``-metadata:s:<stream_type>:<stream>`` flags for title/handler/language.

        Args:
            safe: Validated options dict (after ``_sanitize_stream_metadata``).
            stream_type: Single-letter stream specifier (``"a"``, ``"s"``, ``"v"``).
            stream: Stream index (int or str).

        Returns:
            List of FFmpeg argument tokens.
        """
        prefix = "-metadata:s:%s:%s" % (stream_type, stream)
        optlist = []
        if "title" in safe:
            optlist += [prefix, "title=" + str(safe["title"]), prefix, "handler_name=" + str(safe["title"])]
        else:
            optlist += [prefix, "title=", prefix, "handler_name="]
        lang = str(safe["language"]) if "language" in safe else BaseCodec.UNDEFINED
        optlist += [prefix, "language=" + lang]
        return optlist


class BaseDecoder(object):
    """
    Base decoder class.
    """

    decoder_name = None
    max_depth = 9999

    def supportsBitDepth(self, depth):
        return depth <= self.max_depth


class AudioCodec(BaseCodec):
    """
    Base audio codec class handles general audio options. Possible
    parameters are:
      * codec (string) - audio codec name
      * channels (integer) - number of audio channels
      * bitrate (integer) - stream bitrate
      * samplerate (integer) - sample rate (frequency)
      * language (string) - language of audio stream (3 char code)
      * map (int) - stream index
      * disposition (string) - disposition string (+default+forced)

    Supported audio codecs are: null (no audio), copy (copy from
    original), vorbis, aac, mp3, mp2
    """

    encoder_options = {
        "codec": str,
        "language": str,
        "title": str,
        "channels": int,
        "bitrate": int,
        "samplerate": int,
        "sample_fmt": str,
        "source": int,
        "path": str,
        "filter": str,
        "map": int,
        "disposition": str,
        "profile": str,
        "bsf": str,
    }

    max_channels = None  # subclasses set an int to cap channel count

    def _codec_specific_parse_options(self, safe, stream=0):
        if self.max_channels and "channels" in safe and safe["channels"] > self.max_channels:
            safe["channels"] = self.max_channels
        return safe

    def parse_options(self, opt, stream=0):
        """Translate an audio option dict into a list of FFmpeg command-line arguments.

        Validates opt via the base-class check, sanitises channels (1-12),
        bitrate (8-1536 kbps), samplerate (1000-50000 Hz), and language (max
        3 chars), then emits -c:a, -ac, -b:a, -ar, -filter, -profile, -metadata,
        -disposition, and -map tokens for the given stream index. Delegates
        codec-specific flags to _codec_specific_produce_ffmpeg_list.
        """
        super(AudioCodec, self).parse_options(opt)
        safe = self.safe_options(opt)
        stream = str(stream)

        if "channels" in safe:
            c = safe["channels"]
            if c < 1 or c > 12:
                del safe["channels"]

        br = None
        if "bitrate" in safe:
            br = safe["bitrate"]
            if br < 8:
                br = 8
            if br > 1536:
                br = 1536

        if "samplerate" in safe:
            f = safe["samplerate"]
            if f < 1000 or f > 50000:
                del safe["samplerate"]

        if "language" in safe:
            l = safe["language"]
            if len(l) > 3:
                del safe["language"]

        if "source" in safe:
            s = safe["source"]
        else:
            s = 0

        if "filter" in safe:
            x = safe["filter"]
            if len(x) < 1:
                del safe["filter"]

        if "disposition" in safe:
            if len(safe["disposition"].strip()) < 1:
                del safe["disposition"]

        if "title" in safe:
            if len(safe["title"]) < 1:
                del safe["title"]

        safe = self._codec_specific_parse_options(safe)
        optlist = []
        optlist.extend(["-c:a:" + stream, self.ffmpeg_codec_name])
        if "path" in safe:
            optlist.extend(["-i", str(safe["path"])])
        if "map" in safe:
            optlist.extend(["-map", str(s) + ":" + str(safe["map"])])
        if "channels" in safe:
            optlist.extend(["-ac:a:" + stream, str(safe["channels"])])
        if "bitrate" in safe:
            optlist.extend(["-b:a:" + stream, str(br) + "k"])
            optlist.extend(["-metadata:s:a:" + stream, "BPS=" + str(br * 1000)])
            optlist.extend(["-metadata:s:a:" + stream, "BPS-eng=" + str(br * 1000)])
        if "samplerate" in safe:
            optlist.extend(["-ar:a:" + stream, str(safe["samplerate"])])
        if "sample_fmt" in safe:
            optlist.extend(["-sample_fmt:a:" + stream, str(safe["sample_fmt"])])
        if "filter" in safe:
            optlist.extend(["-filter:a:" + stream, str(safe["filter"])])
        if "profile" in safe:
            optlist.extend(["-profile:a:" + stream, str(safe["profile"])])
        if "title" in safe:
            optlist.extend(["-metadata:s:a:" + stream, "title=" + str(safe["title"])])
            optlist.extend(["-metadata:s:a:" + stream, "handler_name=" + str(safe["title"])])
        else:
            optlist.extend(["-metadata:s:a:" + stream, "title="])
            optlist.extend(["-metadata:s:a:" + stream, "handler_name="])
        if "bsf" in safe:
            optlist.extend(["-bsf:a", safe["bsf"]])
        if "language" in safe:
            lang = str(safe["language"])
        else:
            lang = BaseCodec.UNDEFINED  # Never leave blank if not specified, always set to und for undefined
        optlist.extend(["-metadata:s:a:" + stream, "language=" + lang])
        optlist.extend(["-disposition:a:" + stream, self.safe_disposition(safe.get("disposition"))])

        optlist.extend(self._codec_specific_produce_ffmpeg_list(safe, stream))
        return optlist


class SubtitleCodec(BaseCodec):
    """
    Base subtitle codec class handles general subtitle options. Possible
    parameters are:
      * codec (string) - subtitle codec name (mov_text, subrib, ssa only supported currently)
      * language (string) - language of subtitle stream (3 char code)
      * disposition (string) - disposition as string (+default+forced)

    Supported subtitle codecs are: null (no subtitle), mov_text
    """

    encoder_options = {
        "codec": str,
        "language": str,
        "title": str,
        "map": int,
        "source": int,
        "path": str,
        "disposition": str,
    }

    def parse_options(self, opt, stream=0):
        """Translate a subtitle option dict into a list of FFmpeg command-line arguments.

        Validates opt via the base-class check, sanitises language (max 3 chars)
        and disposition, then emits -c:s, -map, -metadata (title, handler_name,
        language), and -disposition tokens for the given stream index.
        Delegates codec-specific flags to _codec_specific_produce_ffmpeg_list.
        """
        super(SubtitleCodec, self).parse_options(opt)
        stream = str(stream)
        safe = self.safe_options(opt)

        if "language" in safe:
            l = safe["language"]
            if len(l) > 3:
                del safe["language"]

        if "source" in safe:
            s = safe["source"]
        else:
            s = 0

        if "disposition" in safe:
            if len(safe["disposition"].strip()) < 1:
                del safe["disposition"]

        if "title" in safe:
            if len(safe["title"]) < 1:
                del safe["title"]

        safe = self._codec_specific_parse_options(safe)

        optlist = []
        optlist.extend(["-c:s:" + stream, self.ffmpeg_codec_name])
        stream = str(stream)
        if "map" in safe:
            optlist.extend(["-map", str(s) + ":" + str(safe["map"])])
        if "path" in safe:
            optlist.extend(["-i", str(safe["path"])])
        if "title" in safe:
            optlist.extend(["-metadata:s:s:" + stream, "title=" + str(safe["title"])])
            optlist.extend(["-metadata:s:s:" + stream, "handler_name=" + str(safe["title"])])
        else:
            optlist.extend(["-metadata:s:s:" + stream, "title="])
            optlist.extend(["-metadata:s:s:" + stream, "handler_name="])
        if "language" in safe:
            lang = str(safe["language"])
        else:
            lang = BaseCodec.UNDEFINED  # Never leave blank if not specified, always set to und for undefined
        optlist.extend(["-metadata:s:s:" + stream, "language=" + lang])
        optlist.extend(["-disposition:s:" + stream, self.safe_disposition(safe.get("disposition"))])

        optlist.extend(self._codec_specific_produce_ffmpeg_list(safe, stream))
        return optlist


class VideoCodec(BaseCodec):
    """
    Base video codec class handles general video options. Possible
    parameters are:
      * codec (string) - video codec name
      * bitrate (string) - stream bitrate
      * fps (integer) - frames per second
      * width (integer) - video width
      * height (integer) - video height
      * mode (string) - aspect preserval mode; one of:
            * stretch (default) - don't preserve aspect
            * crop - crop extra w/h
            * pad - pad with black bars
      * src_width (int) - source width
      * src_height (int) - source height

    Aspect preserval mode is only used if both source
    and both destination sizes are specified. If source
    dimensions are not specified, aspect settings are ignored.

    If source dimensions are specified, and only one
    of the destination dimensions is specified, the other one
    is calculated to preserve the aspect ratio.

    Supported video codecs are: null (no video), copy (copy directly
    from the source), Theora, H.264/AVC, DivX, VP8, H.263, Flv,
    MPEG-1, MPEG-2.
    """

    encoder_options = {
        "codec": str,
        "title": str,
        "bitrate": int,
        "maxrate": str,
        "bufsize": str,
        "fps": float,
        "width": int,
        "height": int,
        "mode": str,
        "src_width": int,
        "src_height": int,
        "filter": str,
        "pix_fmt": str,
        "field_order": str,
        "map": int,
        "bsf": str,
    }

    def _aspect_corrections(self, sw, sh, w, h, mode):
        # If we don't have source info, we don't try to calculate
        # aspect corrections
        if not sw or not sh:
            return w, h, None

        # Original aspect ratio
        aspect = (1.0 * sw) / (1.0 * sh)

        # If we have only one dimension, we can easily calculate
        # the other to match the source aspect ratio
        if not w and not h:
            return w, h, None
        elif w and not h:
            h = int((1.0 * w) / aspect)
            return w, h, None
        elif h and not w:
            w = int(aspect * h)
            return w, h, None

        # If source and target dimensions are actually the same aspect
        # ratio, we've got nothing to do
        if int(aspect * h) == w:
            return w, h, None

        if mode == "stretch":
            return w, h, None

        target_aspect = (1.0 * w) / (1.0 * h)

        if mode == "crop":
            # source is taller, need to crop top/bottom
            if target_aspect > aspect:  # target is taller
                h0 = int(w / aspect)
                assert h0 > h, (sw, sh, w, h)
                dh = (h0 - h) / 2
                return w, h0, "crop=%d:%d:0:%d" % (w, h, dh)
            else:  # source is wider, need to crop left/right
                w0 = int(h * aspect)
                assert w0 > w, (sw, sh, w, h)
                dw = (w0 - w) / 2
                return w0, h, "crop=%d:%d:%d:0" % (w, h, dw)

        if mode == "pad":
            # target is taller, need to pad top/bottom
            if target_aspect < aspect:
                h1 = int(w / aspect)
                assert h1 < h, (sw, sh, w, h)
                dh = (h - h1) / 2
                return w, h1, "pad=%d:%d:0:%d" % (w, h, dh)  # FIXED
            else:  # target is wider, need to pad left/right
                w1 = int(h * aspect)
                assert w1 < w, (sw, sh, w, h)
                dw = (w - w1) / 2
                return w1, h, "pad=%d:%d:%d:0" % (w, h, dw)  # FIXED

        assert False, mode

    def parse_options(self, opt, stream=0):
        """Translate a video option dict into a list of FFmpeg command-line arguments.

        Validates opt via the base-class check, sanitises fps, bitrate, CRF,
        field_order, and dimensions, then computes aspect-ratio corrections
        (stretch/crop/pad) via _aspect_corrections. Emits -vcodec, -map, -r:v,
        -pix_fmt, -field_order, -crf or -vb, -maxrate, -bufsize, -vf, -s,
        -aspect, -bsf:v, and -metadata tokens. Multiple -vf arguments are
        consolidated into a single comma-separated filter chain.
        """
        super(VideoCodec, self).parse_options(opt)

        safe = self.safe_options(opt)

        if "fps" in safe:
            f = safe["fps"]
            if f < 1:
                del safe["fps"]

        if "bitrate" in safe:
            bitrate = safe["bitrate"]
            if bitrate < 1:
                del safe["bitrate"]

        if "field_order" in safe:
            if safe["field_order"] not in ["progressive", "tt", "bb", "tb", "bt"]:
                del safe["field_order"]

        w = None
        h = None

        if "width" in safe:
            w = safe["width"]
            if w < 16:
                del safe["width"]
                w = None

        if "height" in safe:
            h = safe["height"]
            if h < 16:
                del safe["height"]
                h = None

        sw = None
        sh = None

        if "src_width" in safe and "src_height" in safe:
            sw = safe["src_width"]
            sh = safe["src_height"]
            if not sw or not sh:
                sw = None
                sh = None

        mode = "stretch"
        if "mode" in safe:
            if safe["mode"] in ["stretch", "crop", "pad"]:
                mode = safe["mode"]

        ow, oh = w, h  # FIXED
        w, h, filters = self._aspect_corrections(sw, sh, w, h, mode)

        safe["width"] = w
        safe["height"] = h
        safe["aspect_filters"] = filters

        if w and h:
            safe["aspect"] = "%d:%d" % (w, h)

        if "title" in safe:
            if len(safe["title"]) < 1:
                del safe["title"]

        safe = self._codec_specific_parse_options(safe)

        w = safe.get("width", None)
        h = safe.get("height", None)
        filters = safe.get("aspect_filters", None)

        optlist = ["-vcodec", self.ffmpeg_codec_name]
        if "map" in safe:
            optlist.extend(["-map", "0:" + str(safe["map"])])
        if "fps" in safe:
            optlist.extend(["-r:v", str(safe["fps"])])
        if "pix_fmt" in safe:
            optlist.extend(["-pix_fmt", str(safe["pix_fmt"])])
        if "field_order" in safe:
            optlist.extend(["-field_order", str(safe["field_order"])])
        if "bitrate" in safe:
            optlist.extend(["-vb", str(safe["bitrate"]) + "k"])
        if "maxrate" in safe:
            optlist.extend(["-maxrate:v", str(safe["maxrate"])])
        if "bufsize" in safe:
            optlist.extend(["-bufsize", str(safe["bufsize"])])
        if "bitrate" in safe:
            optlist.extend(["-metadata:s:v", "BPS=" + str(safe["bitrate"] * 1000)])
            optlist.extend(["-metadata:s:v", "BPS-eng=" + str(safe["bitrate"] * 1000)])
        # Collect -vf parts to emit as a single comma-joined filter chain
        vf_parts = []
        if "filter" in safe:
            vf_parts.append(str(safe["filter"]))
        if filters:
            vf_parts.append(filters)
        if w and h:
            optlist.extend(["-s", "%dx%d" % (w, h)])
            if ow and oh:
                optlist.extend(["-aspect", "%d:%d" % (ow, oh)])
        if "bsf" in safe:
            optlist.extend(["-bsf:v", safe["bsf"]])
        if "title" in safe:
            optlist.extend(["-metadata:s:v", "title=" + str(safe["title"])])
            optlist.extend(["-metadata:s:v", "handler_name=" + str(safe["title"])])
        else:
            optlist.extend(["-metadata:s:v", "title="])
            optlist.extend(["-metadata:s:v", "handler_name="])

        # Extract any -vf tokens from codec-specific list so they join the filter chain
        specific = self._codec_specific_produce_ffmpeg_list(safe, stream)
        i = 0
        while i < len(specific):
            if specific[i] == "-vf" and i + 1 < len(specific):
                vf_parts.append(specific[i + 1])
                i += 2
            else:
                optlist.append(specific[i])
                i += 1

        if vf_parts:
            optlist.extend(["-vf", ",".join(vf_parts)])

        return optlist


class AudioNullCodec(BaseCodec):
    """
    Null audio codec (no audio).
    """

    codec_name = None

    def parse_options(self, opt, stream=0):
        return ["-an"]


class VideoNullCodec(BaseCodec):
    """
    Null video codec (no video).
    """

    codec_name = None

    def parse_options(self, opt):
        return ["-vn"]


class SubtitleNullCodec(BaseCodec):
    """
    Null subtitle codec (no subtitle)
    """

    codec_name = None

    def parse_options(self, opt, stream=0):
        return ["-sn"]


class AudioCopyCodec(BaseCodec):
    """
    Copy audio stream directly from the source.
    """

    codec_name = "copy"
    encoder_options = {"map": int, "source": str, "bsf": str, "disposition": str, "language": str, "title": str}

    def parse_options(self, opt, stream=0):
        safe = self.safe_options(opt)
        self._sanitize_stream_metadata(safe)

        stream = str(stream)
        s = safe.get("source", 0)
        optlist = ["-c:a:" + stream, "copy"]
        if "map" in safe:
            optlist += ["-map", str(s) + ":" + str(safe["map"])]
        if "bsf" in safe:
            optlist += ["-bsf:a:" + stream, str(safe["bsf"])]
        optlist += self._build_stream_metadata(safe, "a", stream)
        optlist += ["-disposition:a:" + stream, self.safe_disposition(safe.get("disposition"))]
        return optlist


class VideoCopyCodec(BaseCodec):
    """
    Copy video stream directly from the source.
    """

    codec_name = "copy"
    encoder_options = {"map": int, "source": str, "fps": float, "bsf": str, "title": str}

    def parse_options(self, opt, stream=0):
        safe = self.safe_options(opt)
        if "fps" in safe and safe["fps"] < 1:
            del safe["fps"]
        if "title" in safe and len(safe["title"]) < 1:
            del safe["title"]

        s = safe.get("source", 0)
        optlist = ["-vcodec", "copy"]
        if "map" in safe:
            optlist += ["-map", str(s) + ":" + str(safe["map"])]
        if "fps" in safe:
            optlist += ["-r:v", str(safe["fps"])]
        if "bsf" in safe:
            optlist += ["-bsf:v", safe["bsf"]]
        optlist += self._build_stream_metadata(safe, "v", "")
        return optlist


class SubtitleCopyCodec(BaseCodec):
    """
    Copy subtitle stream directly from the source.
    """

    codec_name = "copy"
    encoder_options = {"map": int, "source": str, "disposition": str, "language": str, "title": str}

    def parse_options(self, opt, stream=0):
        safe = self.safe_options(opt)
        self._sanitize_stream_metadata(safe)

        stream = str(stream)
        s = safe.get("source", 0)
        optlist = ["-c:s:" + stream, "copy"]
        if "map" in safe:
            optlist += ["-map", str(s) + ":" + str(safe["map"])]
        optlist += self._build_stream_metadata(safe, "s", stream)
        optlist += ["-disposition:s:" + stream, self.safe_disposition(safe.get("disposition"))]
        return optlist


class AttachmentCopyCodec(BaseCodec):
    """
    Copy attachment stream directly from the source.
    """

    codec_name = "copy"
    encoder_options = {"map": int, "source": str, "filename": str, "mimetype": str}

    def parse_options(self, opt, stream=0):
        safe = self.safe_options(opt)

        stream = str(stream)
        s = safe.get("source", 0)
        optlist = ["-c:t:" + stream, "copy"]
        if "filename" in safe:
            optlist += ["-metadata:s:t:" + stream, "filename=" + str(safe["filename"])]
        if "mimetype" in safe:
            optlist += ["-metadata:s:t:" + stream, "mimetype=" + str(safe["mimetype"])]
        if "map" in safe:
            optlist += ["-map", str(s) + ":" + str(safe["map"])]
        return optlist


# Audio Codecs
class VorbisCodec(AudioCodec):
    """
    Vorbis audio codec.
    """

    codec_name = "vorbis"
    ffmpeg_codec_name = "libvorbis"
    ffprobe_codec_name = "vorbis"
    encoder_options = AudioCodec.encoder_options.copy()
    encoder_options.update(
        {
            "quality": int,  # audio quality. Range is 0-10 (10 highest quality)
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        if "quality" in safe:
            if safe["quality"] < 1 or safe["quality"] > 10:
                del safe["quality"]
            elif "bitrate" in safe:
                del safe["bitrate"]
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        stream = str(stream)

        if "quality" in safe:
            optlist.extend(["-q:a:" + stream, str(safe["quality"])])
        return optlist


class AacCodec(AudioCodec):
    """
    AAC audio codec.
    """

    codec_name = "aac"
    ffmpeg_codec_name = "aac"
    ffprobe_codec_name = "aac"
    max_channels = 6


class FdkAacCodec(AudioCodec):
    """
    AAC audio codec.
    """

    codec_name = "libfdk_aac"
    ffmpeg_codec_name = "libfdk_aac"
    ffprobe_codec_name = "aac"
    encoder_options = AudioCodec.encoder_options.copy()
    encoder_options.update(
        {
            "quality": int,  # audio quality. Range is 0-5 (5 highest quality)
        }
    )

    max_channels = 6

    def _codec_specific_parse_options(self, safe, stream=0):
        safe = super()._codec_specific_parse_options(safe, stream)
        if "profile" in safe:
            p = safe["profile"]
            if "channels" in safe:
                c = safe["channels"]
                if c > 2 and p in ["aac_he_v2"]:
                    safe["channels"] = 2  # Max 2
            if "quality" in safe:
                q = safe["quality"]
                if q > 3 and p in ["aac_he", "aac_he_v2"]:
                    safe["quality"] = 3  # Max 3
        if "quality" in safe:
            q = safe["quality"]
            if q < 1 or q > 5:
                del safe["quality"]
            elif "bitrate" in safe:
                del safe["bitrate"]
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        stream = str(stream)

        if "quality" in safe:
            optlist.extend(["-vbr:a:" + stream, str(safe["quality"])])
        return optlist


class FAacCodec(AudioCodec):
    """
    AAC audio codec.
    """

    codec_name = "libfaac"
    ffmpeg_codec_name = "libfaac"
    ffprobe_codec_name = "aac"
    max_channels = 6


class Ac3Codec(AudioCodec):
    """
    AC3 audio codec.
    """

    codec_name = "ac3"
    ffmpeg_codec_name = "ac3"
    ffprobe_codec_name = "ac3"
    max_channels = 6


class EAc3Codec(AudioCodec):
    """
    Dolby Digital Plus/EAC3 audio codec.
    """

    codec_name = "eac3"
    ffmpeg_codec_name = "eac3"
    ffprobe_codec_name = "eac3"

    def _codec_specific_parse_options(self, safe, stream=0):
        if "channels" in safe:
            c = safe["channels"]
            if c > 8:
                safe["channels"] = 6
        if "bitrate" in safe:
            br = safe["bitrate"]
            if br > 640:
                safe["bitrate"] = 640
        return safe


class TrueHDCodec(AudioCodec):
    """
    TrueHD audio codec.
    """

    codec_name = "truehd"
    ffmpeg_codec_name = "truehd"
    ffprobe_codec_name = "truehd"
    truehd_experimental_enable = ["-strict", "experimental"]

    def _codec_specific_parse_options(self, safe, stream=0):
        if "channels" in safe:
            c = safe["channels"]
            if c > 8:
                safe["channels"] = 8
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        return self.truehd_experimental_enable


class FlacCodec(AudioCodec):
    """
    FLAC audio codec.
    """

    codec_name = "flac"
    ffmpeg_codec_name = "flac"
    ffprobe_codec_name = "flac"


class DtsCodec(AudioCodec):
    """
    DTS audio codec.
    """

    codec_name = "dts"
    ffmpeg_codec_name = "dts"
    ffprobe_codec_name = "dts"
    dts_experimental_enable = ["-strict", "experimental"]

    def _codec_specific_parse_options(self, safe, stream=0):
        if "channels" in safe:
            c = safe["channels"]
            if c > 6:
                safe["channels"] = 6
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        return self.dts_experimental_enable


class Mp3Codec(AudioCodec):
    """
    MP3 (MPEG layer 3) audio codec.
    """

    codec_name = "mp3"
    ffmpeg_codec_name = "libmp3lame"
    ffprobe_codec_name = "mp3"
    encoder_options = AudioCodec.encoder_options.copy()
    encoder_options.update(
        {
            "quality": int,  # audio quality. Range is 9-0 (0 highest quality)
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        if "quality" in safe:
            q = safe["quality"]
            if q < 0 or q > 9:
                del safe["quality"]
            elif "bitrate" in safe:
                del safe["bitrate"]
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        stream = str(stream)

        if "quality" in safe:
            optlist.extend(["-q:a:" + stream, str(safe["quality"])])
        return optlist


class Mp2Codec(AudioCodec):
    """
    MP2 (MPEG layer 2) audio codec.
    """

    codec_name = "mp2"
    ffmpeg_codec_name = "mp2"
    ffprobe_codec_name = "mp2"


class OpusCodec(AudioCodec):
    """
    Opus audio codec
    """

    codec_name = "opus"
    ffmpeg_codec_name = "libopus"
    ffprobe_codec_name = "opus"


class PCMS24LECodec(AudioCodec):
    """
    PCM_S24LE Audio Codec
    """

    codec_name = "pcm_s24le"
    ffmpeg_codec_name = "pcm_s24le"
    ffprobe_codec_name = "pcm_s24le"


class PCMS16LECodec(AudioCodec):
    """
    PCM_S16LE Audio Codec
    """

    codec_name = "pcm_s16le"
    ffmpeg_codec_name = "pcm_s16le"
    ffprobe_codec_name = "pcm_s16le"


# Video Codecs
class TheoraCodec(VideoCodec):
    """
    Theora video codec.
    """

    codec_name = "theora"
    ffmpeg_codec_name = "libtheora"
    ffprobe_codec_name = "theora"
    encoder_options = VideoCodec.encoder_options.copy()
    encoder_options.update(
        {
            "quality": int,  # audio quality. Range is 0-10 (10 highest quality)
        }
    )

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        if "quality" in safe:
            optlist.extend(["-qscale:v", safe["quality"]])
        return optlist


class H264Codec(VideoCodec):
    """
    H.264/AVC video codec.
    """

    codec_name = "h264"
    ffmpeg_codec_name = "libx264"
    ffprobe_codec_name = "h264"
    codec_params = "x264-params"
    levels = [1, 1.1, 1.2, 1.3, 2, 2.1, 2.2, 3, 3.1, 3.2, 4, 4.1, 4.2, 5, 5.1, 5.2, 6, 6.1, 6.2]
    encoder_options = VideoCodec.encoder_options.copy()
    encoder_options.update(
        {
            "preset": str,  # common presets are ultrafast, superfast, veryfast,
            # faster, fast, medium(default), slow, slower, veryslow
            "profile": str,  # default: not-set, for valid values see above link
            "level": float,  # default: not-set, values range from 3.0 to 4.2
            "tune": str,  # default: not-set, for valid values see above link
            "wscale": int,  # special handlers for the even number requirements of h264
            "hscale": int,  # special handlers for the even number requirements of h264
            "params": str,  # x264-params
        }
    )
    scale_filter = "scale"

    @staticmethod
    def codec_specific_level_conversion(ffprobe_level):
        return ffprobe_level / 10.0

    def _codec_specific_parse_options(self, safe, stream=0):
        if "width" in safe and safe["width"]:
            safe["width"] = 2 * round(safe["width"] / 2)
            safe["wscale"] = safe["width"]
            del safe["width"]
        if "height" in safe and safe["height"]:
            if safe["height"] % 2 == 0:
                safe["hscale"] = safe["height"]
            del safe["height"]
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        if "level" in safe:
            if safe["level"] not in self.levels:
                try:
                    safe["level"] = [x for x in self.levels if x < safe["level"]][-1]
                except:
                    del safe["level"]

        if "preset" in safe:
            optlist.extend(["-preset", safe["preset"]])
        if "profile" in safe:
            optlist.extend(["-profile:v", safe["profile"]])
        if "level" in safe:
            optlist.extend(["-level", "%0.1f" % safe["level"]])
        if "params" in safe and self.codec_params:
            optlist.extend(["-%s" % self.codec_params, safe["params"]])
        if "tune" in safe:
            optlist.extend(["-tune", safe["tune"]])
        if "wscale" in safe and "hscale" in safe:
            optlist.extend(["-vf", "%s=%s:%s" % (self.scale_filter, safe["wscale"], safe["hscale"])])
        elif "wscale" in safe:
            optlist.extend(["-vf", "%s=%s:trunc(ow/a/2)*2" % (self.scale_filter, safe["wscale"])])
        elif "hscale" in safe:
            optlist.extend(["-vf", "%s=trunc((oh*a)/2)*2:%s" % (self.scale_filter, safe["hscale"])])
        optlist.extend(["-tag:v", "avc1"])
        return optlist


class H264CodecAlt(H264Codec):
    """
    H.264/AVC video codec alternate.
    """

    codec_name = "x264"


class HWAccelVideoCodec:
    """Mixin providing shared parse/produce helpers for hardware-accelerated video codecs.

    Subclasses set class attributes to configure behavior:
        hw_prefix: str - key prefix for scale vars in safe dict (e.g., 'qsv', 'vaapi', 'nvenc')
        hw_quality_key: str - safe dict key for quality value ('qp' or 'gq')
        hw_quality_flag: str - FFmpeg flag ('-qp' or '-global_quality')
        hw_quality_range: tuple(int, int) - valid (min, max) range for quality value
    """

    hw_prefix = ""
    hw_quality_key = "qp"
    hw_quality_flag = "-qp"
    hw_quality_range = (0, 52)
    hw_quality_default = None
    hw_presets = None
    hw_profiles = None
    hw_extbrc = False

    def _hw_parse_preset(self, safe):
        """Validate preset against hw_presets whitelist, remove if unsupported."""
        if self.hw_presets is not None and "preset" in safe:
            if safe["preset"] not in self.hw_presets:
                del safe["preset"]

    def _hw_parse_profile(self, safe):
        """Validate profile against hw_profiles whitelist, remove if unsupported."""
        if self.hw_profiles is not None and "profile" in safe:
            if safe["profile"] not in self.hw_profiles:
                del safe["profile"]

    def _hw_parse_scale(self, safe):
        """Convert width/height to hw-prefixed scale variables."""
        p = self.hw_prefix
        if "width" in safe and safe["width"]:
            safe["width"] = 2 * round(safe["width"] / 2)
            safe[p + "_wscale"] = safe["width"]
            del safe["width"]
        if "height" in safe and safe["height"]:
            if safe["height"] % 2 == 0:
                safe[p + "_hscale"] = safe["height"]
            del safe["height"]

    def _hw_parse_quality(self, safe):
        """Apply hw_quality_default when no explicit quality or bitrate is present."""
        if self.hw_quality_key not in safe and "bitrate" not in safe and self.hw_quality_default is not None:
            safe[self.hw_quality_key] = self.hw_quality_default

    def _hw_parse_pix_fmt(self, safe):
        """Move pix_fmt to hw-prefixed key."""
        if "pix_fmt" in safe:
            safe[self.hw_prefix + "_pix_fmt"] = safe["pix_fmt"]
            del safe["pix_fmt"]

    def _hw_quality_opts(self, safe):
        """Produce quality flag + rate control options."""
        optlist = []
        if self.hw_quality_key in safe:
            optlist.extend([self.hw_quality_flag, str(safe[self.hw_quality_key])])
            if self.hw_quality_flag != "-global_quality":
                if "maxrate" in safe:
                    optlist.extend(["-maxrate:v", str(safe["maxrate"])])
                if "bufsize" in safe:
                    optlist.extend(["-bufsize", str(safe["bufsize"])])
        if self.hw_extbrc and "bitrate" in safe:
            optlist.extend(["-extbrc", "1"])
        return optlist

    def _hw_device_opts(self, safe, hwdownload_filter="hwdownload,format=nv12,hwupload"):
        """Produce device selection and hwdownload options."""
        optlist = []
        if "device" in safe:
            optlist.extend(["-filter_hw_device", safe["device"]])
            if "decode_device" in safe and safe["decode_device"] != safe["device"]:
                optlist.extend(["-vf", hwdownload_filter])
        elif "decode_device" in safe:
            optlist.extend(["-vf", hwdownload_filter])
        return optlist

    def _hw_scale_opts(self, safe, fmtstr=""):
        """Produce named-parameter scale filter options (w=X:h=Y style)."""
        p = self.hw_prefix
        wkey, hkey = p + "_wscale", p + "_hscale"
        if wkey in safe and hkey in safe:
            return ["-vf", "%s=w=%s:h=%s%s" % (self.scale_filter, safe[wkey], safe[hkey], fmtstr)]
        elif wkey in safe:
            return ["-vf", "%s=w=%s:h=trunc(ow/a/2)*2%s" % (self.scale_filter, safe[wkey], fmtstr)]
        elif hkey in safe:
            return ["-vf", "%s=w=trunc((oh*a)/2)*2:h=%s%s" % (self.scale_filter, safe[hkey], fmtstr)]
        return []


class NVEncH264Codec(HWAccelVideoCodec, H264Codec):
    """
    Nvidia H.264/AVC video codec.
    """

    codec_name = "h264_nvenc"
    ffmpeg_codec_name = "h264_nvenc"
    scale_filter = "scale_npp"
    max_depth = 8
    hw_prefix = "nvenc"
    hw_quality_default = 23
    codec_params = None
    encoder_options = H264Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = super(NVEncH264Codec, self)._codec_specific_produce_ffmpeg_list(safe, stream)
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        optlist.extend(self._hw_scale_opts(safe))
        return optlist


class NVEncH264CodecCuda(NVEncH264Codec):
    """
    Nvidia H.264/AVC video codec using scale_cuda filter.
    """

    codec_name = "h264_nvenc_cuda"
    scale_filter = "scale_cuda"


class VideotoolboxEncH264(H264Codec):
    """
    Videotoolbox H.264/AVC video codec.
    """

    codec_name = "h264_videotoolbox"
    ffmpeg_codec_name = "h264_videotoolbox"


class OMXH264Codec(H264Codec):
    """
    OMX H.264/AVC video codec.
    """

    codec_name = "h264_omx"
    ffmpeg_codec_name = "h264_omx"


class VAAPIVideoCodec(HWAccelVideoCodec):
    """VAAPI-specific mixin with device init fallback and hwupload filter chain."""

    hw_prefix = "vaapi"
    hw_quality_default = 23
    default_fmt = "nv12"

    def _hw_device_opts(self, safe, hwdownload_filter="hwdownload"):
        optlist = []
        if "device" in safe:
            optlist.extend(["-filter_hw_device", safe["device"]])
            if "decode_device" in safe and safe["decode_device"] != safe["device"]:
                optlist.extend(["-vf", hwdownload_filter])
        else:
            optlist.extend(["-init_hw_device", "vaapi=vaapi0:/dev/dri/renderD128", "-filter_hw_device", "vaapi0"])
            if "decode_device" in safe:
                optlist.extend(["-vf", hwdownload_filter])
        return optlist

    def _hw_vaapi_scale_opts(self, safe):
        """VAAPI-specific scale filter with format conversion and hwupload."""
        p = self.hw_prefix
        wkey, hkey = p + "_wscale", p + "_hscale"
        fmt = safe.get(p + "_pix_fmt", self.default_fmt)
        fmtstr = ":format=%s" % safe[p + "_pix_fmt"] if p + "_pix_fmt" in safe else ""

        if wkey in safe and hkey in safe:
            return ["-vf", "format=%s|vaapi,hwupload,%s=w=%s:h=%s%s" % (fmt, self.scale_filter, safe[wkey], safe[hkey], fmtstr)]
        elif wkey in safe:
            return ["-vf", "format=%s|vaapi,hwupload,%s=w=%s:h=trunc(ow/a/2)*2%s" % (fmt, self.scale_filter, safe[wkey], fmtstr)]
        elif hkey in safe:
            return ["-vf", "format=%s|vaapi,hwupload,%s=w=trunc((oh*a)/2)*2:h=%s%s" % (fmt, self.scale_filter, safe[hkey], fmtstr)]
        else:
            fmtstr = ",%s=%s" % (self.scale_filter, fmtstr[1:]) if fmtstr else ""
            return ["-vf", "format=%s|vaapi,hwupload%s" % (fmt, fmtstr)]


class H264VAAPICodec(VAAPIVideoCodec, H264Codec):
    """
    H.264/AVC VAAPI video codec.
    """

    codec_name = "h264vaapi"
    ffmpeg_codec_name = "h264_vaapi"
    scale_filter = "scale_vaapi"
    codec_params = None
    encoder_options = H264Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = super(H264VAAPICodec, self)._codec_specific_produce_ffmpeg_list(safe, stream)
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        optlist.extend(self._hw_vaapi_scale_opts(safe))
        return optlist


class H264QSVCodec(HWAccelVideoCodec, H264Codec):
    """
    H.264/AVC QSV video codec.
    """

    codec_name = "h264qsv"
    ffmpeg_codec_name = "h264_qsv"
    scale_filter = "scale_qsv"
    hw_prefix = "qsv"
    hw_quality_key = "gq"
    hw_quality_flag = "-global_quality"
    hw_quality_range = (1, 51)
    hw_quality_default = 25
    hw_presets = ()
    hw_profiles = ("baseline", "main", "high", "high10")
    hw_extbrc = True
    codec_params = None
    encoder_options = H264Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
            "look_ahead_depth": int,
            "b_frames": int,
            "ref_frames": int,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        safe = super(H264QSVCodec, self)._codec_specific_parse_options(safe, stream)
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        self._hw_parse_preset(safe)
        self._hw_parse_profile(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        if "level" in safe:
            if safe["level"] not in self.levels:
                try:
                    safe["level"] = [x for x in self.levels if x < safe["level"]][-1]
                except:
                    del safe["level"]

        if "level" in safe:
            optlist.extend(["-level", "%0.0f" % (safe["level"] * 10)])
            del safe["level"]

        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        fmtstr = ":format=%s" % safe["qsv_pix_fmt"] if "qsv_pix_fmt" in safe else ""
        scale = self._hw_scale_opts(safe, fmtstr)
        if scale:
            optlist.extend(scale)
        elif fmtstr:
            optlist.extend(["-vf", "%s=%s" % (self.scale_filter, fmtstr[1:])])
        optlist.extend(super(H264QSVCodec, self)._codec_specific_produce_ffmpeg_list(safe, stream))
        look_ahead_depth = safe.get("look_ahead_depth", 0)
        if look_ahead_depth and look_ahead_depth > 0:
            optlist.extend(["-look_ahead", "1", "-look_ahead_depth", str(look_ahead_depth)])
        else:
            optlist.extend(["-look_ahead", "0"])
        if "b_frames" in safe and safe["b_frames"] >= 0:
            optlist.extend(["-bf", str(safe["b_frames"])])
        if "ref_frames" in safe and safe["ref_frames"] >= 0:
            optlist.extend(["-refs", str(safe["ref_frames"])])
        return optlist


class H264V4l2m2mCodec(H264Codec):
    """
    H.264/AVC video codec.
    """

    codec_name = "h264_v4l2m2m"
    ffmpeg_codec_name = "h264_v4l2m2m"

    def _codec_specific_parse_options(self, safe, stream=0):
        safe["pix_fmt"] = "yuv420p"
        return safe


class H264V4l2m2mDecoder(BaseDecoder):
    """H.264 hardware decoder using the V4L2 M2M (Video4Linux2 memory-to-memory) interface."""

    decoder_name = "h264_v4l2m2m"
    max_depth = 8


class H265Codec(VideoCodec):
    """
    H.265/AVC video codec.
    """

    codec_name = "h265"
    ffmpeg_codec_name = "libx265"
    ffprobe_codec_name = "hevc"
    codec_params = "x265-params"
    levels = [1, 2, 2.1, 3, 3.1, 4, 4.1, 5, 5.1, 5.2, 6, 6.1, 6.2]  # 8.5 excluded
    encoder_options = VideoCodec.encoder_options.copy()
    encoder_options.update(
        {
            "preset": str,  # common presets are ultrafast, superfast, veryfast,
            # faster, fast, medium(default), slow, slower, veryslow
            "profile": str,  # default: not-set, for valid values see above link
            "level": float,  # default: not-set, values range from 3.0 to 4.2
            "tune": str,  # default: not-set, for valid values see above link
            "wscale": int,  # special handlers for the even number requirements of h265
            "hscale": int,  # special handlers for the even number requirements of h265
            "params": str,  # x265-params
            "framedata": dict,  # dynamic params for framedata
        }
    )
    scale_filter = "scale"

    @staticmethod
    def codec_specific_level_conversion(ffprobe_level):
        return ffprobe_level / 30.0

    def _codec_specific_parse_options(self, safe, stream=0):
        if "width" in safe and safe["width"]:
            safe["width"] = 2 * round(safe["width"] / 2)
            safe["wscale"] = safe["width"]
            del safe["width"]
        if "height" in safe and safe["height"]:
            if safe["height"] % 2 == 0:
                safe["hscale"] = safe["height"]
            del safe["height"]
        return safe

    def safe_framedata(self, opts):
        params = ""
        if "hdr" in opts and opts["hdr"]:
            params += "hdr-opt=1:"
        if "repeat-headers" in opts and opts["repeat-headers"]:
            params += "repeat-headers=1:"
        if "color_primaries" in opts:
            params += "colorprim=" + opts["color_primaries"] + ":"
        if "color_transfer" in opts:
            params += "transfer=" + opts["color_transfer"] + ":"
        if "color_space" in opts:
            params += "colormatrix=" + opts["color_space"] + ":"
        if "side_data_list" in opts:
            for side_data in opts["side_data_list"]:
                if side_data.get("side_data_type") == "Mastering display metadata":
                    red_x = side_data["red_x"]
                    red_y = side_data["red_y"]
                    green_x = side_data["green_x"]
                    green_y = side_data["green_y"]
                    blue_x = side_data["blue_x"]
                    blue_y = side_data["blue_y"]
                    wp_x = side_data["white_point_x"]
                    wp_y = side_data["white_point_y"]
                    min_l = side_data["min_luminance"]
                    max_l = side_data["max_luminance"]
                    params += "master-display=G(%d,%d)B(%d,%d)R(%d,%d)WP(%d,%d)L(%d,%d):" % (green_x, green_y, blue_x, blue_y, red_x, red_y, wp_x, wp_y, max_l, min_l)
                elif side_data.get("side_data_type") == "Content light level metadata":
                    max_content = side_data["max_content"]
                    max_average = side_data["max_average"]
                    params += "max-cll=%d,%d:" % (max_content, max_average)
        return params[:-1]

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []

        if "level" in safe:
            if safe["level"] not in self.levels:
                try:
                    safe["level"] = [x for x in self.levels if x < safe["level"]][-1]
                except:
                    del safe["level"]

        if "preset" in safe:
            optlist.extend(["-preset", safe["preset"]])
        if "profile" in safe:
            optlist.extend(["-profile:v", safe["profile"]])
        if "level" in safe:
            optlist.extend(["-level", "%0.1f" % safe["level"]])
        params = ""
        if "params" in safe:
            params = safe["params"]
        if "framedata" in safe:
            if params:
                params = params + ":"
            params = params + self.safe_framedata(safe["framedata"])
        if params and self.codec_params:
            optlist.extend(["-%s" % self.codec_params, params])
        if "tune" in safe:
            optlist.extend(["-tune", safe["tune"]])
        if "wscale" in safe and "hscale" in safe:
            optlist.extend(["-vf", "%s=%s:%s" % (self.scale_filter, safe["wscale"], safe["hscale"])])
        elif "wscale" in safe:
            optlist.extend(["-vf", "%s=%s:trunc(ow/a/2)*2" % (self.scale_filter, safe["wscale"])])
        elif "hscale" in safe:
            optlist.extend(["-vf", "%s=trunc((oh*a)/2)*2:%s" % (self.scale_filter, safe["hscale"])])
        optlist.extend(["-tag:v", "hvc1"])
        return optlist


class H265CodecAlt(H265Codec):
    """
    H.265/AVC video codec alternate.
    """

    codec_name = "hevc"


class H265QSVCodec(HWAccelVideoCodec, H265Codec):
    """
    HEVC QSV video codec.
    """

    codec_name = "h265qsv"
    ffmpeg_codec_name = "hevc_qsv"
    scale_filter = "scale_qsv"
    hw_prefix = "qsv"
    hw_quality_key = "gq"
    hw_quality_flag = "-global_quality"
    hw_quality_range = (1, 51)
    hw_quality_default = 25
    hw_presets = ()
    hw_profiles = ("main", "main10", "main444")
    hw_extbrc = True
    codec_params = None
    encoder_options = H265Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
            "look_ahead_depth": int,
            "b_frames": int,
            "ref_frames": int,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        safe = super(H265QSVCodec, self)._codec_specific_parse_options(safe, stream)
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        self._hw_parse_preset(safe)
        self._hw_parse_profile(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        if "level" in safe:
            if safe["level"] not in self.levels:
                try:
                    safe["level"] = [x for x in self.levels if x < safe["level"]][-1]
                except:
                    del safe["level"]

        if "level" in safe:
            optlist.extend(["-level", "%0.0f" % (safe["level"] * 10)])
            del safe["level"]

        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        fmtstr = ":format=%s" % safe["qsv_pix_fmt"] if "qsv_pix_fmt" in safe else ""
        scale = self._hw_scale_opts(safe, fmtstr)
        if scale:
            optlist.extend(scale)
        elif fmtstr:
            optlist.extend(["-vf", "%s=%s" % (self.scale_filter, fmtstr[1:])])
        optlist.extend(super(H265QSVCodec, self)._codec_specific_produce_ffmpeg_list(safe, stream))
        look_ahead_depth = safe.get("look_ahead_depth", 0)
        if look_ahead_depth and look_ahead_depth > 0:
            optlist.extend(["-look_ahead", "1", "-look_ahead_depth", str(look_ahead_depth)])
        else:
            optlist.extend(["-look_ahead", "0"])
        if "b_frames" in safe and safe["b_frames"] >= 0:
            optlist.extend(["-bf", str(safe["b_frames"])])
        if "ref_frames" in safe and safe["ref_frames"] >= 0:
            optlist.extend(["-refs", str(safe["ref_frames"])])
        return optlist


class H265QSVCodecAlt(H265QSVCodec):
    """
    HEVC video codec alternate.
    """

    codec_name = "hevc_qsv"


class H265QSVCodecPatched(H265QSVCodec):
    """
    HEVC QSV alternate designed to work with patched FFMPEG that supports HDR metadata.
    Patch
        https://patchwork.ffmpeg.org/project/ffmpeg/patch/20201202131826.10558-1-omondifredrick@gmail.com/
    Github issue
        https://github.com/mdhiggins/sickbeard_mp4_automator/issues/1366
    """

    codec_name = "hevcqsvpatched"
    codec_params = "qsv_params"

    def safe_framedata(self, opts):
        params = ""
        if "color_primaries" in opts:
            params += "colorprim=" + opts["color_primaries"] + ":"
        if "color_transfer" in opts:
            params += "transfer=" + opts["color_transfer"] + ":"
        if "color_space" in opts:
            params += "colormatrix=" + opts["color_space"] + ":"
        if "side_data_list" in opts:
            for side_data in opts["side_data_list"]:
                if side_data.get("side_data_type") == "Mastering display metadata":
                    red_x = side_data["red_x"]
                    red_y = side_data["red_y"]
                    green_x = side_data["green_x"]
                    green_y = side_data["green_y"]
                    blue_x = side_data["blue_x"]
                    blue_y = side_data["blue_y"]
                    wp_x = side_data["white_point_x"]
                    wp_y = side_data["white_point_y"]
                    min_l = side_data["min_luminance"]
                    min_l = 50 if min_l < 50 else min_l
                    max_l = side_data["max_luminance"]
                    max_l = 10000000 if max_l > 10000000 else max_l
                    params += "master-display=G(%d,%d)B(%d,%d)R(%d,%d)WP(%d,%d)L(%d,%d):" % (green_x, green_y, blue_x, blue_y, red_x, red_y, wp_x, wp_y, min_l, max_l)
                elif side_data.get("side_data_type") == "Content light level metadata":
                    max_content = side_data["max_content"]
                    max_average = side_data["max_average"]
                    if max_content == 0 and max_average == 0:
                        continue
                    max_content = 1000 if max_content > 1000 else max_content
                    max_average = 400 if max_average < 400 else max_average
                    max_content = max_average if max_content < max_average else max_content
                    params += "max-cll=%d,%d:" % (max_content, max_average)
        return params[:-1]


class H265VAAPICodec(VAAPIVideoCodec, H265Codec):
    """
    H.265/AVC VAAPI video codec.
    """

    codec_name = "h265vaapi"
    ffmpeg_codec_name = "hevc_vaapi"
    scale_filter = "scale_vaapi"
    codec_params = None
    encoder_options = H265Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = super(H265VAAPICodec, self)._codec_specific_produce_ffmpeg_list(safe, stream)
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        optlist.extend(self._hw_vaapi_scale_opts(safe))
        return optlist


class H265VAAPICodecAlt(H265VAAPICodec):
    """
    HEVC video codec alternate.
    """

    codec_name = "hevc_vaapi"


class H265V4l2m2mCodec(H265Codec):
    """
    HEVC video codec.
    """

    codec_name = "hevc_v4l2m2m"
    ffmpeg_codec_name = "hevc_v4l2m2m"


class H265V4l2m2mDecoder(BaseDecoder):
    """H.265/HEVC hardware decoder using the V4L2 M2M (Video4Linux2 memory-to-memory) interface."""

    decoder_name = "hevc_v4l2m2m"
    max_depth = 10


class NVEncH265Codec(HWAccelVideoCodec, H265Codec):
    """
    Nvidia H.265/AVC video codec.
    """

    codec_name = "h265_nvenc"
    ffmpeg_codec_name = "hevc_nvenc"
    scale_filter = "scale_npp"
    max_depth = 10
    hw_prefix = "nvenc"
    hw_quality_default = 23
    codec_params = None
    encoder_options = H265Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = super(NVEncH265Codec, self)._codec_specific_produce_ffmpeg_list(safe, stream)
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        fmtstr = ":format=%s" % safe["nvenc_pix_fmt"] if "nvenc_pix_fmt" in safe else ""
        scale = self._hw_scale_opts(safe, fmtstr)
        if scale:
            optlist.extend(scale)
        elif fmtstr:
            optlist.extend(["-vf", "%s=%s" % (self.scale_filter, fmtstr[1:])])
        return optlist


class NVEncH265CodecAlt(NVEncH265Codec):
    """
    Nvidia H.265/AVC video codec alternate.
    """

    codec_name = "hevc_nvenc"


class NVEncH265CodecCuda(NVEncH265Codec):
    """
    Nvidia H.265/AVC video codec using scale_cuda filter.
    """

    codec_name = "h265_nvenc_cuda"
    scale_filter = "scale_cuda"


class NVEncH265CodecCudaAlt(NVEncH265CodecCuda):
    """
    Nvidia H.265/AVC video codec using scale_cuda filter alternate.
    """

    codec_name = "hevc_nvenc_cuda"


class NVEncH265CodecPatched(NVEncH265Codec):
    """
    Nvidia H.265/AVC video codec alternate designed to work with patched FFMPEG that supports HDR metadata.
    https://github.com/klob/FFmpeg/releases/tag/release-hevc_nvenc_hdr_sei
    """

    codec_name = "hevc_nvenc_patched"

    color_transfer = {"smpte2084": 16, "smpte2086": 18, "bt709": 1}
    color_primaries = {"bt2020": 9, "bt709": 1}
    color_space = {"bt2020nc": 9, "bt709": 1}

    def _codec_specific_parse_options(self, safe, stream=0):
        safe = super(NVEncH265CodecPatched, self)._codec_specific_parse_options(safe, stream)
        if "framedata" in safe:
            if "bsf" in safe:
                safe["bsf"] = safe["bsf"] + "," + self.safe_framedata(safe["framedata"])
            else:
                safe["bsf"] = self.safe_framedata(safe["framedata"])
            del safe["framedata"]
        return safe

    def safe_framedata(self, opts):
        metadata = "hevc_metadata="
        if "color_primaries" in opts and self.color_primaries.get(opts["color_primaries"]):
            metadata += "colour_primaries=%d:" % (self.color_primaries.get(opts["color_primaries"]))
        if "color_transfer" in opts and self.color_transfer.get(opts["color_transfer"]):
            metadata += "transfer_characteristics=%d:" % (self.color_transfer.get(opts["color_transfer"]))
        if "color_space" in opts and self.color_space.get(opts["color_space"]):
            metadata += "matrix_coefficients=%d:" % (self.color_space.get(opts["color_space"]))
        if "side_data_list" in opts:
            for side_data in opts["side_data_list"]:
                if side_data.get("side_data_type") == "Mastering display metadata":
                    red_x = side_data["red_x"]
                    red_y = side_data["red_y"]
                    green_x = side_data["green_x"]
                    green_y = side_data["green_y"]
                    blue_x = side_data["blue_x"]
                    blue_y = side_data["blue_y"]
                    wp_x = side_data["white_point_x"]
                    wp_y = side_data["white_point_y"]
                    min_l = side_data["min_luminance"]
                    min_l = 50 if min_l < 50 else min_l
                    max_l = side_data["max_luminance"]
                    max_l = 10000000 if max_l > 10000000 else max_l
                    metadata += 'master_display="G(%d|%d)B(%d|%d)R(%d|%d)WP(%d|%d)L(%d|%d)":' % (green_x, green_y, blue_x, blue_y, red_x, red_y, wp_x, wp_y, max_l, min_l)
                elif side_data.get("side_data_type") == "Content light level metadata":
                    max_content = side_data["max_content"]
                    max_average = side_data["max_average"]
                    if max_content == 0 and max_average == 0:
                        continue
                    max_content = 1000 if max_content > 1000 else max_content
                    max_average = 400 if max_average < 400 else max_average
                    max_content = max_average if max_content < max_average else max_content
                    metadata += 'max_cll="%d|%d":' % (max_content, max_average)
        return metadata[:-1]


class H264CuvidDecoder(BaseDecoder):
    """H.264 hardware decoder using NVIDIA CUVID (CUDA Video Decode)."""

    decoder_name = "h264_cuvid"
    max_depth = 8


class H265CuvidDecoder(BaseDecoder):
    """H.265/HEVC hardware decoder using NVIDIA CUVID (CUDA Video Decode)."""

    decoder_name = "hevc_cuvid"
    max_depth = 10


class VideotoolboxEncH265(H265Codec):
    """
    Videotoolbox H.265/HEVC video codec.
    """

    codec_name = "h265_videotoolbox"
    ffmpeg_codec_name = "hevc_videotoolbox"


class DivxCodec(VideoCodec):
    """
    DivX video codec.
    """

    codec_name = "divx"
    ffmpeg_codec_name = "mpeg4"
    ffprobe_codec_name = "mpeg4"


class Vp8Codec(VideoCodec):
    """
    Google VP8 video codec.
    """

    codec_name = "vp8"
    ffmpeg_codec_name = "libvpx"
    ffprobe_codec_name = "vp8"


class Vp9Codec(VideoCodec):
    """
    Google VP9 video codec.
    """

    codec_name = "vp9"
    ffmpeg_codec_name = "libvpx-vp9"
    ffprobe_codec_name = "vp9"
    encoder_options = VideoCodec.encoder_options.copy()
    encoder_options.update(
        {
            "profile": str,  # default: not-set, for valid values see above link
            "framedata": dict,  # dynamic params for framedata
        }
    )
    color_transfer = {"smpte2084": 16, "smpte2086": 18, "bt709": 1}
    color_primaries = {"bt2020": 9, "bt709": 1}
    color_space = {"bt2020nc": 9, "bt709": 1}

    def _codec_specific_parse_options(self, safe, stream=0):
        framedata = safe["framedata"]
        if "color_primaries" in framedata and self.color_primaries.get(framedata["color_primaries"]):
            safe["color_primaries"] = self.color_primaries.get(framedata["color_primaries"])
        if "color_transfer" in framedata and self.color_transfer.get(framedata["color_transfer"]):
            safe["color_transfer"] = self.color_trc.get(framedata["color_transfer"])
        if "color_space" in framedata and self.color_space.get(framedata["color_space"]):
            safe["color_space"] = self.color_space.get(framedata["color_space"])
        if "color_range" in framedata and framedata["color_range"] in [0, 1, 2]:
            safe["color_range"] = framedata["color_range"]
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = super(Vp9Codec, self)._codec_specific_produce_ffmpeg_list(safe, stream)
        if "profile" in safe:
            optlist.extend(["-profile:v", safe["profile"]])
        if "color_primaries" in safe:
            optlist.extend(["-color_primaries", safe["color_primaries"]])
        if "color_transfer" in safe:
            optlist.extend(["-color_trc", safe["color_transfer"]])
        if "color_space" in safe:
            optlist.extend(["-colorspace", safe["color_space"]])
        if "color_range" in safe:
            optlist.extend(["-color_range", safe["color_range"]])
        return optlist


class AV1Codec(VideoCodec):
    """
    Libaom-AV1 Codec
    """

    codec_name = "av1"
    ffmpeg_codec_name = "libaom-av1"
    ffprobe_codec_name = "av1"
    encoder_options = VideoCodec.encoder_options.copy()
    encoder_options.update(
        {
            "preset": int,  # present range 0-13
            "framedata": dict,  # dynamic params for framedata
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        if "preset" in safe:
            p = safe["preset"]
            if p < 0 or p > 13:
                del safe["preset"]
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []

        if "preset" in safe:
            optlist.extend(["-preset", str(safe["preset"])])
        if "framedata" in safe:
            if "color_space" in safe["framedata"]:
                optlist.extend(["-colorspace", str(safe["framedata"]["color_space"])])
            if "color_transfer" in safe["framedata"]:
                optlist.extend(["-color_trc", str(safe["framedata"]["color_transfer"])])
            if "color_primaries" in safe["framedata"]:
                optlist.extend(["-color_primaries", str(safe["framedata"]["color_primaries"])])
        return optlist


class SVTAV1Codec(AV1Codec):
    """
    SVT-AV1 Codec
    """

    codec_name = "svtav1"
    ffmpeg_codec_name = "libsvtav1"


class RAV1ECodec(AV1Codec):
    """
    RAV1E Codec
    """

    codec_name = "rav1e"
    ffmpeg_codec_name = "librav1e"


class AV1QSVCodec(HWAccelVideoCodec, AV1Codec):
    """
    QSV AV1 Codec
    """

    codec_name = "av1qsv"
    ffmpeg_codec_name = "av1_qsv"
    scale_filter = "scale_qsv"
    hw_prefix = "qsv"
    hw_quality_key = "gq"
    hw_quality_flag = "-global_quality"
    hw_quality_range = (1, 51)
    hw_quality_default = 25
    hw_presets = ()
    hw_extbrc = True
    encoder_options = AV1Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
            "look_ahead_depth": int,
            "b_frames": int,
            "ref_frames": int,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        safe = super(AV1QSVCodec, self)._codec_specific_parse_options(safe, stream)
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        self._hw_parse_preset(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        if "preset" in safe:
            optlist.extend(["-preset", str(safe["preset"])])
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        fmtstr = ":format=%s" % safe["qsv_pix_fmt"] if "qsv_pix_fmt" in safe else ""
        scale = self._hw_scale_opts(safe, fmtstr)
        if scale:
            optlist.extend(scale)
        elif fmtstr:
            optlist.extend(["-vf", "%s=%s" % (self.scale_filter, fmtstr[1:])])
        look_ahead_depth = safe.get("look_ahead_depth", 0)
        if look_ahead_depth and look_ahead_depth > 0:
            optlist.extend(["-look_ahead", "1", "-look_ahead_depth", str(look_ahead_depth)])
        else:
            optlist.extend(["-look_ahead", "0"])
        if "b_frames" in safe and safe["b_frames"] >= 0:
            optlist.extend(["-bf", str(safe["b_frames"])])
        if "ref_frames" in safe and safe["ref_frames"] >= 0:
            optlist.extend(["-refs", str(safe["ref_frames"])])
        return optlist


class AV1VAAPICodec(VAAPIVideoCodec, AV1Codec):
    """
    AV1 VAAPI Codec
    """

    codec_name = "av1vaapi"
    ffmpeg_codec_name = "av1_vaapi"
    scale_filter = "scale_vaapi"
    hw_quality_range = (0, 255)
    encoder_options = AV1Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        safe = super(AV1VAAPICodec, self)._codec_specific_parse_options(safe, stream)
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        if "preset" in safe:
            optlist.extend(["-preset", str(safe["preset"])])
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        optlist.extend(self._hw_vaapi_scale_opts(safe))
        return optlist


class NVEncAV1Codec(AV1Codec):
    """
    NVEnc AV1 Codec
    """

    codec_name = "av1nvenc"
    ffmpeg_codec_name = "av1_nvenc"


class Vp9QSVCodec(HWAccelVideoCodec, Vp9Codec):
    """
    Google VP9 QSV video codec.
    """

    codec_name = "vp9qsv"
    ffmpeg_codec_name = "vp9_qsv"
    scale_filter = "scale_qsv"
    hw_prefix = "qsv"
    hw_quality_key = "gq"
    hw_quality_flag = "-global_quality"
    hw_quality_range = (1, 255)
    hw_quality_default = 25
    hw_presets = ()
    hw_extbrc = True
    encoder_options = Vp9Codec.encoder_options.copy()
    encoder_options.update(
        {
            "decode_device": str,
            "device": str,
            "look_ahead_depth": int,
            "b_frames": int,
            "ref_frames": int,
        }
    )

    def _codec_specific_parse_options(self, safe, stream=0):
        if "framedata" in safe:
            safe = super(Vp9QSVCodec, self)._codec_specific_parse_options(safe, stream)
        self._hw_parse_scale(safe)
        self._hw_parse_quality(safe)
        self._hw_parse_pix_fmt(safe)
        self._hw_parse_preset(safe)
        return safe

    def _codec_specific_produce_ffmpeg_list(self, safe, stream=0):
        optlist = []
        optlist.extend(self._hw_quality_opts(safe))
        optlist.extend(self._hw_device_opts(safe))
        fmtstr = ":format=%s" % safe["qsv_pix_fmt"] if "qsv_pix_fmt" in safe else ""
        scale = self._hw_scale_opts(safe, fmtstr)
        if scale:
            optlist.extend(scale)
        elif fmtstr:
            optlist.extend(["-vf", "%s=%s" % (self.scale_filter, fmtstr[1:])])
        look_ahead_depth = safe.get("look_ahead_depth", 0)
        if look_ahead_depth and look_ahead_depth > 0:
            optlist.extend(["-look_ahead", "1", "-look_ahead_depth", str(look_ahead_depth), "-extra_hw_frames", str(look_ahead_depth + 4)])
        else:
            optlist.extend(["-look_ahead", "0"])
        if "b_frames" in safe and safe["b_frames"] >= 0:
            optlist.extend(["-bf", str(safe["b_frames"])])
        if "ref_frames" in safe and safe["ref_frames"] >= 0:
            optlist.extend(["-refs", str(safe["ref_frames"])])
        return optlist


class Vp9QSVAltCodec(Vp9QSVCodec):
    """
    Google VP9 QSV video codec alt.
    """

    codec_name = "vp9_qsv"


class H263Codec(VideoCodec):
    """
    H.263 video codec.
    """

    codec_name = "h263"
    ffmpeg_codec_name = "h263"
    ffprobe_codec_name = "h263"


class FlvCodec(VideoCodec):
    """
    Flash Video codec.
    """

    codec_name = "flv"
    ffmpeg_codec_name = "flv"
    ffprobe_codec_name = "flv1"


class MpegCodec(VideoCodec):
    """
    Base MPEG video codec.
    """

    # Workaround for a bug in ffmpeg in which aspect ratio
    # is not correctly preserved, so we have to set it
    # again in vf; take care to put it *before* crop/pad, so
    # it uses the same adjusted dimensions as the codec itself
    # (pad/crop will adjust it further if neccessary)
    def _codec_specific_parse_options(self, safe, stream=0):
        w = safe["width"]
        h = safe["height"]

        if w and h:
            filters = safe["aspect_filters"]
            tmp = "aspect=%d:%d" % (w, h)

            if filters is None:
                safe["aspect_filters"] = tmp
            else:
                safe["aspect_filters"] = tmp + "," + filters

        return safe


class Mpeg1Codec(MpegCodec):
    """
    MPEG-1 video codec.
    """

    codec_name = "mpeg1"
    ffmpeg_codec_name = "mpeg1video"
    ffprobe_codec_name = "mpeg1video"


class Mpeg2Codec(MpegCodec):
    """
    MPEG-2 video codec.
    """

    codec_name = "mpeg2"
    ffmpeg_codec_name = "mpeg2video"
    ffprobe_codec_name = "mpeg2video"


# Subtitle Codecs
class MOVTextCodec(SubtitleCodec):
    """
    mov_text subtitle codec.
    """

    codec_name = "mov_text"
    ffmpeg_codec_name = "mov_text"
    ffprobe_codec_name = "mov_text"


class SrtCodec(SubtitleCodec):
    """
    SRT subtitle codec.
    """

    codec_name = "srt"
    ffmpeg_codec_name = "srt"
    ffprobe_codec_name = "srt"


class WebVTTCodec(SubtitleCodec):
    """
    SRT subtitle codec.
    """

    codec_name = "webvtt"
    ffmpeg_codec_name = "webvtt"
    ffprobe_codec_name = "webvtt"


class PGSCodec(SubtitleCodec):
    """
    PGS subtitle codec.
    """

    codec_name = "pgs"
    ffmpeg_codec_name = "copy"  # Does not have an encoder
    ffprobe_codec_name = "hdmv_pgs_subtitle"


class PGSCodecAlt(PGSCodec):
    """
    PGS subtitle codec alternate.
    """

    codec_name = "hdmv_pgs_subtitle"


class SSACodec(SubtitleCodec):
    """
    SSA (SubStation Alpha) subtitle.
    """

    codec_name = "ass"
    ffmpeg_codec_name = "ass"
    ffprobe_codec_name = "ass"


class SSACodecAlt(SSACodec):
    """
    SSA (SubStation Alpha) subtitle.
    """

    codec_name = "ssa"


class SubRip(SubtitleCodec):
    """
    SubRip subtitle.
    """

    codec_name = "subrip"
    ffmpeg_codec_name = "subrip"
    ffprobe_codec_name = "subrip"


class DVBSub(SubtitleCodec):
    """
    DVB subtitles.
    """

    codec_name = "dvbsub"
    ffmpeg_codec_name = "dvbsub"
    ffprobe_codec_name = "dvb_subtitle"


class DVDSub(SubtitleCodec):
    """
    DVD subtitles.
    """

    codec_name = "dvdsub"
    ffmpeg_codec_name = "dvdsub"
    ffprobe_codec_name = "dvd_subtitle"


class DVDSubAlt(DVDSub):
    """
    DVD subtitles alternate.
    """

    codec_name = "dvd_subtitle"


audio_codec_list = [
    AudioNullCodec,
    AudioCopyCodec,
    VorbisCodec,
    AacCodec,
    Mp3Codec,
    Mp2Codec,
    FdkAacCodec,
    FAacCodec,
    EAc3Codec,
    Ac3Codec,
    DtsCodec,
    FlacCodec,
    OpusCodec,
    PCMS24LECodec,
    PCMS16LECodec,
    TrueHDCodec,
]

video_codec_list = [
    VideoNullCodec,
    VideoCopyCodec,
    TheoraCodec,
    H263Codec,
    H264Codec,
    H264CodecAlt,
    H264QSVCodec,
    H264VAAPICodec,
    OMXH264Codec,
    VideotoolboxEncH264,
    NVEncH264Codec,
    NVEncH264CodecCuda,
    H264V4l2m2mCodec,
    H265Codec,
    H265QSVCodecAlt,
    H265QSVCodec,
    H265CodecAlt,
    H265QSVCodecPatched,
    H265VAAPICodec,
    H265VAAPICodecAlt,
    VideotoolboxEncH265,
    NVEncH265Codec,
    NVEncH265CodecAlt,
    NVEncH265CodecPatched,
    H265V4l2m2mCodec,
    NVEncH265CodecCuda,
    NVEncH265CodecCudaAlt,
    DivxCodec,
    Vp8Codec,
    Vp9Codec,
    Vp9QSVCodec,
    Vp9QSVAltCodec,
    FlvCodec,
    Mpeg1Codec,
    Mpeg2Codec,
    AV1Codec,
    SVTAV1Codec,
    RAV1ECodec,
    AV1QSVCodec,
    AV1VAAPICodec,
    NVEncAV1Codec,
]

subtitle_codec_list = [SubtitleNullCodec, SubtitleCopyCodec, MOVTextCodec, SrtCodec, SSACodec, SSACodecAlt, SubRip, DVDSub, DVBSub, DVDSubAlt, WebVTTCodec, PGSCodec, PGSCodecAlt]

attachment_codec_list = [AttachmentCopyCodec]

decoder_list = [H264CuvidDecoder, H264V4l2m2mDecoder, H265CuvidDecoder, H265V4l2m2mDecoder]
