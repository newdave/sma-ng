# shellcheck shell=bash
# Shared defaults for docker build/run tasks.
# FFmpeg version: docker/Dockerfile (ARG FFMPEG_VERSION) is the single source;
# set FFMPEG_VERSION only to build a different one locally.
FFMPEG_VERSION="${FFMPEG_VERSION:-}"
TAG="${TAG:-sma-ng:local}"
