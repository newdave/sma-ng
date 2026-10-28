#!/usr/bin/env bash
hooks_dir="$(cd "$(dirname "$0")/.." && pwd)"

cat <<'TEXT'
Before implementation, check <available_skills>. Activate relevant ones via Skill() tool. If none are relevant, proceed directly.
TEXT

if "$hooks_dir/ffmpeg-prompt-match.sh"; then
  cat <<'TEXT'
This request involves FFmpeg work. Before reasoning about or changing FFmpeg options, load both FFmpeg skills with the Skill tool: `ffmpeg-cli` and `ffmpeg-hardware-acceleration`. For edits to converter/*.py, resources/mediaprocessor.py or resources/media_repair.py, also run the `ffmpeg-args-auditor` subagent before finishing (CLAUDE.md Activation Routing).
TEXT
fi

exit 0
