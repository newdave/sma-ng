#!/usr/bin/env bash
hooks_dir="$(cd "$(dirname "$0")/.." && pwd)"

if "$hooks_dir/ffmpeg-prompt-match.sh"; then
  cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Before implementation, check available skills/repo instructions and activate or follow the relevant ones. This request involves FFmpeg work: before reasoning about or changing FFmpeg options, read and follow both FFmpeg skills, .agents/skills/ffmpeg-cli/SKILL.md and .agents/skills/ffmpeg-hardware-acceleration/SKILL.md. For edits to converter/*.py, resources/mediaprocessor.py or resources/media_repair.py, also run the ffmpeg-args-auditor agent before finishing (AGENTS.md)."}}
JSON
else
  cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Before implementation, check available skills/repo instructions and activate or follow the relevant ones. If none apply, proceed directly."}}
JSON
fi

exit 0
