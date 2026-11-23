#!/usr/bin/env bash
# UserPromptSubmit hook shared by Claude Code and Codex.
# Usage: skill-reminder.sh claude|codex
# claude prints plain text; codex prints hookSpecificOutput JSON and names the .agents/skills paths.
# shellcheck disable=SC2016  # backticks in the message text are literal
set -u

host="${1:-claude}"
hooks_dir="$(cd "$(dirname "$0")" && pwd)"

if "$hooks_dir/ffmpeg-prompt-match.sh"; then
  ffmpeg=1
else
  ffmpeg=0
fi

case "$host" in
claude)
  echo 'Before implementation, check <available_skills>. Activate relevant ones via Skill() tool. If none are relevant, proceed directly.'
  if [ "$ffmpeg" = 1 ]; then
    echo 'This request involves FFmpeg work. Before reasoning about or changing FFmpeg options, load both FFmpeg skills with the Skill tool: `ffmpeg-cli` and `ffmpeg-hardware-acceleration`. For edits to converter/*.py, resources/mediaprocessor.py or resources/media_repair.py, also run the `ffmpeg-args-auditor` subagent before finishing (CLAUDE.md Activation Routing).'
  fi
  ;;
codex)
  if [ "$ffmpeg" = 1 ]; then
    msg='Before implementation, check available skills/repo instructions and activate or follow the relevant ones. This request involves FFmpeg work: before reasoning about or changing FFmpeg options, read and follow both FFmpeg skills, .agents/skills/ffmpeg-cli/SKILL.md and .agents/skills/ffmpeg-hardware-acceleration/SKILL.md. For edits to converter/*.py, resources/mediaprocessor.py or resources/media_repair.py, also run the ffmpeg-args-auditor agent before finishing (AGENTS.md).'
  else
    msg='Before implementation, check available skills/repo instructions and activate or follow the relevant ones. If none apply, proceed directly.'
  fi
  printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}\n' "$msg"
  ;;
*)
  echo "skill-reminder.sh: unknown host '$host'" >&2
  ;;
esac

exit 0
