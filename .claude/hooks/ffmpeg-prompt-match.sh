#!/bin/sh
# Exit 0 when the UserPromptSubmit payload on stdin is about FFmpeg work in
# this repo (encoding, probing, stream mapping, hwaccel, or the files that
# build FFmpeg commands); exit 1 otherwise. Shared by the Claude Code and
# Codex skill-reminder hooks so both load the same FFmpeg skills.

# Matched anywhere: specific enough not to occur inside ordinary words.
SUBSTRINGS='ffmpeg|ffprobe|codec|hwaccel|videotoolbox|transcod|remux|stream[ -]?copy|-c copy|-map |bitstream|filter_complex|pix_fmt|dolby|hdr|truehd|mov_text|attached_pic|cover art|matroska|faststart|converter/|avcodecs|formats\.py|mediaprocessor|media_repair|repair\.py|library_audit|manual\.py'
# Matched as whole words only: short tokens that would otherwise hit "necessary", "standard", ...
WORDS='qsv|vaapi|nvenc|nvdec|vulkan|amf|bsf|dovi|sar|dar|aspect|x264|x265|h264|h265|hevc|av1|vp9|aac|eac3|ac3|dts|opus|subtitles?|mkv|mp4|m4v'

payload="$(cat)"
printf '%s' "$payload" | grep -Eiq "$SUBSTRINGS" && exit 0
printf '%s' "$payload" | grep -Eiqw "$WORDS"
