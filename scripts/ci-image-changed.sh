#!/bin/sh
# Read changed file paths (one per line) on stdin; exit 0 when any of them
# changes what the Docker image ships or how it is built, 1 otherwise.
#
# Image-affecting: application source (*.py and the package directories,
# including their non-Python assets), setup/ (requirements, samples, host
# CLI), docker/, scripts/, the webhook script, pyproject.toml, .dockerignore
# and the image workflow itself. Tests, docs, planning files and agent
# tooling never change the image.
set -eu

grep -vE '^(tests|docs|\.claude|\.agents|\.codex|\.omnigent|\.plane|\.windsurf)/' |
  grep -qE '\.py$|^(converter|resources|autoprocess|triggers|scripts|setup|docker)/|^(sma-webhook\.sh|pyproject\.toml|\.dockerignore|\.github/workflows/docker\.yml)$'
