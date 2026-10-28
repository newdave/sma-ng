#!/bin/sh
# PostToolUse(Bash) hook shared by Claude Code (.claude/settings.json) and
# Codex (.codex/hooks.json): after a `git push` of main, publish the generated
# GitHub wiki when the pushed commits changed docs/ (incl. openapi.yaml) or
# .mise/tasks/ (the wiki's Mise-Tasks page). wiki:publish is a no-op when the
# wiki is already current, and refuses to publish uncommitted docs.
#
# SMA_HOOK_WIKI_DRY_RUN=1 passes --dry-run to wiki:publish (for testing).
set -u

command_line=$(jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
case "$command_line" in
  *"git push"*) ;;
  *) exit 0 ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root" || exit 0
[ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "main" ] || exit 0

# The push moves origin/main; its previous reflog entry is where it stood
# before the push, so old..new is exactly what was just pushed.
new=$(git rev-parse -q --verify origin/main 2>/dev/null) || exit 0
old=$(git rev-parse -q --verify 'origin/main@{1}' 2>/dev/null || true)
if [ -n "$old" ]; then
  [ "$old" != "$new" ] || exit 0
  [ -n "$(git diff --name-only "$old" "$new" -- docs .mise/tasks)" ] || exit 0
fi

set -- wiki:publish
[ "${SMA_HOOK_WIKI_DRY_RUN:-}" = "1" ] && set -- wiki:publish -- --dry-run
output=$(mise run "$@" 2>&1)
status=$?
summary=$(printf '%s\n' "$output" | grep -E "wiki (published|is already up to date)|dry run:|uncommitted changes|ERROR" | tail -1)
if [ "$status" -eq 0 ]; then
  message="Docs changed in the push; mise run wiki:publish: ${summary:-done}"
else
  message="Docs changed in the push but mise run wiki:publish failed (exit $status): ${summary:-see mise run wiki:publish}"
fi
jq -n --arg m "$message" '{systemMessage: $m, hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $m}}'
exit 0
