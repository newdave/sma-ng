# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: FFmpeg 8.x builder
#
# Produces a mostly-static FFmpeg binary. Codec libraries (x264, x265, aac,
# opus, etc.) are linked statically. GPU acceleration APIs (VAAPI, QSV via
# oneVPL, NVENC via nv-codec-headers) are linked dynamically because they
# must dlopen GPU-vendor driver backends at runtime on the host machine.
#
# Note: libfdk-aac is intentionally excluded — it is non-free and not
# available in Ubuntu's main repository. FFmpeg's native AAC encoder is used
# instead (--enable-encoder=aac is on by default).
# ──────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS ffmpeg-builder

ARG FFMPEG_VERSION=8.1
# Injected automatically by Docker Buildx for multi-arch builds
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive

# Intel oneVPL (QSV) is only available on amd64 — add the Intel graphics PPA
# before installing any Intel iGPU-specific packages, and skip that path on arm64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common \
    && if [ "$TARGETARCH" = "amd64" ]; then \
    add-apt-repository ppa:kobuk-team/intel-graphics \
    && apt-get update; \
    fi \
    && apt-get install -y --no-install-recommends \
    # Build tools
    build-essential \
    cmake \
    git \
    nasm \
    yasm \
    pkg-config \
    wget \
    ca-certificates \
    # VAAPI / DRM (dynamic — GPU driver loaded at runtime)
    libva-dev \
    libdrm-dev \
    # Codec libraries (statically linked into the binary)
    libssl-dev \
    zlib1g-dev \
    libx264-dev \
    libx265-dev \
    libmp3lame-dev \
    libopus-dev \
    libvorbis-dev \
    libogg-dev \
    libvpx-dev \
    libass-dev \
    libfreetype6-dev \
    libfontconfig1-dev \
    libzimg-dev \
    libopenjp2-7-dev \
    libaom-dev \
    libdav1d-dev \
    libsvtav1enc-dev \
    # oneVPL / Intel QSV — amd64 only (not available in arm64 Ubuntu repos)
    && if [ "$TARGETARCH" = "amd64" ]; then \
    apt-get install -y --no-install-recommends libvpl-dev; \
    fi \
    && apt-get purge -y --auto-remove software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# ── nv-codec-headers (NVENC/NVDEC — headers only, no CUDA needed at build time)
RUN git clone --depth 1 https://github.com/FFmpeg/nv-codec-headers /tmp/nv-codec-headers \
    && cd /tmp/nv-codec-headers \
    && make install PREFIX=/usr \
    && rm -rf /tmp/nv-codec-headers

# ── FFmpeg 8.x ────────────────────────────────────────────────────────────────
RUN wget -q "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" -O /tmp/ffmpeg.tar.xz \
    && tar -xf /tmp/ffmpeg.tar.xz -C /tmp \
    && rm /tmp/ffmpeg.tar.xz \
    && mv /tmp/ffmpeg-${FFMPEG_VERSION} /tmp/ffmpeg

WORKDIR /tmp/ffmpeg

# Build FFmpeg; --enable-libvpl (Intel QSV) is amd64-only
RUN VPL_FLAG=$([ "$TARGETARCH" = "amd64" ] && echo "--enable-libvpl" || true); \
    ./configure \
    --prefix=/usr/local \
    --extra-cflags="-I/usr/local/include" \
    --extra-ldflags="-L/usr/local/lib" \
    --extra-libs="-lpthread -lm -lz -ldl" \
    --enable-static \
    --disable-shared \
    --enable-gpl \
    --enable-version3 \
    --disable-doc \
    --disable-htmlpages \
    --disable-manpages \
    --disable-podpages \
    --disable-txtpages \
    --disable-debug \
    --enable-optimizations \
    --enable-vaapi \
    --enable-libdrm \
    ${VPL_FLAG} \
    --enable-ffnvcodec \
    --enable-libx264 \
    --enable-libx265 \
    --enable-libmp3lame \
    --enable-libopus \
    --enable-libvorbis \
    --enable-libvpx \
    --enable-libass \
    --enable-libfreetype \
    --enable-libfontconfig \
    --enable-libzimg \
    --enable-libopenjpeg \
    --enable-libaom \
    --enable-libdav1d \
    --enable-libsvtav1 \
    --enable-openssl \
    --enable-protocol=https \
    && make -j$(nproc) \
    && make install \
    && strip /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Python dependency builder
# ──────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS python-builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY setup/requirements.txt ./requirements.txt
COPY pyproject.toml ./
COPY README.md ./

RUN python3 -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install --no-warn-script-location -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# Stage 3: Runtime image
# ──────────────────────────────────────────────────────────────────────────────
FROM ubuntu:24.04 AS runtime

# Injected automatically by Docker Buildx for multi-arch builds
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/venv \
    PATH="/venv/bin:/usr/local/bin:$PATH" \
    # SMA paths — override via environment or volume mounts
    SMA_CONFIG=/config/sma-ng.yml \
    SMA_FFMPEG=/usr/local/bin/ffmpeg \
    SMA_FFPROBE=/usr/local/bin/ffprobe \
    # Daemon runtime defaults — override any of these with -e / env_file
    SMA_DAEMON_HOST=0.0.0.0 \
    SMA_DAEMON_PORT=8585 \
    SMA_DAEMON_WORKERS=2 \
    SMA_DAEMON_CONFIG=/config/sma-ng.yml \
    SMA_DAEMON_LOGS_DIR=/logs

# Runtime shared libraries required by the FFmpeg binary.
# Each entry maps to a --enable-* flag used at build time.
#
# GPU / hardware acceleration (dynamic — GPU driver on host at runtime):
#   libva2        — VAAPI dispatcher          (--enable-vaapi)
#   libva-drm2    — VAAPI DRM backend         (--enable-vaapi)
#   mesa-va-drivers — Generic VAAPI userspace drivers (AMD/Intel fallback)
#   intel-media-va-driver-non-free / i965-va-driver-shaders — Intel VAAPI userspace drivers
#   vainfo       — VAAPI capability/debug utility
#   libdrm2       — DRM/KMS                   (--enable-libdrm)
#   libvpl2       — Intel oneVPL dispatcher    (--enable-libvpl)
#   libmfx1       — Intel Media SDK runtime    (legacy/libmfx compatibility)
#   libmfx-gen1*  — Intel oneVPL GPU runtime   (QSV implementation backend)
#   intel-opencl-icd — Intel OpenCL userspace runtime (compute dependency)
#   intel-gpu-tools — Intel GPU diagnostics    (intel_gpu_top, intel_gpu_time)
# NVENC: no runtime lib — provided by the NVIDIA host driver
#         (--enable-ffnvcodec, run with --gpus / --runtime=nvidia)
#
# Video codecs:
#   libx264-164   — H.264 encoder             (--enable-libx264)
#   libx265-199   — H.265/HEVC encoder        (--enable-libx265)
#   libvpx9       — VP8/VP9                   (--enable-libvpx)
#   libaom3       — AV1 encoder/decoder       (--enable-libaom)
#   libdav1d7     — AV1 decoder               (--enable-libdav1d)
#   libsvtav1enc1d1— AV1 encoder (SVT)         (--enable-libsvtav1)
#   libopenjp2-7  — JPEG2000                  (--enable-libopenjpeg)
#
# Audio codecs:
#   libmp3lame0   — MP3 encoder               (--enable-libmp3lame)
#   libopus0      — Opus encoder/decoder      (--enable-libopus)
#   libvorbis0a   — Vorbis decoder            (--enable-libvorbis)
#   libvorbisenc2 — Vorbis encoder            (--enable-libvorbis)
#
# Filters / rendering:
#   libass9       — ASS/SSA subtitle rendering (--enable-libass)
#   libfreetype6  — Font rendering             (--enable-libfreetype)
#   libfontconfig1— Font configuration         (--enable-libfontconfig)
#   libzimg2      — zscale filter              (--enable-libzimg)
#
# Network / TLS:
#   libssl3       — OpenSSL TLS               (--enable-openssl)
# Intel oneVPL and Intel VA userspace packages are only available/useful on
# amd64 here — add the Intel graphics PPA before installing any Intel iGPU
# runtime packages and skip that path on arm64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common \
    && if [ "$TARGETARCH" = "amd64" ]; then \
    add-apt-repository ppa:kobuk-team/intel-graphics \
    && apt-get update; \
    fi \
    && apt-get install -y --no-install-recommends \
    python3 \
    libva2 \
    libva-drm2 \
    mesa-va-drivers \
    vainfo \
    libdrm2 \
    libx264-164 \
    libx265-199 \
    libvpx9 \
    libaom3 \
    libdav1d7 \
    libsvtav1enc1d1 \
    libopenjp2-7 \
    libmp3lame0 \
    libopus0 \
    libvorbis0a \
    libvorbisenc2 \
    libass9 \
    libfreetype6 \
    libfontconfig1 \
    libzimg2 \
    libssl3 \
    ca-certificates \
    tini \
    # oneVPL / Intel QSV runtime, MFX compatibility libs, and Intel VA userspace drivers — amd64 only
    && if [ "$TARGETARCH" = "amd64" ]; then \
    if apt-cache show libmfx-gen1 >/dev/null 2>&1; then MFX_GEN_PKG=libmfx-gen1; else MFX_GEN_PKG=libmfx-gen1.2; fi; \
    apt-get install -y --no-install-recommends \
    libvpl2 \
    libmfx1 \
    "$MFX_GEN_PKG" \
    intel-media-va-driver-non-free \
    i965-va-driver-shaders \
    intel-opencl-icd \
    intel-gpu-tools; \
    fi \
    && apt-get purge -y --auto-remove software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# Copy FFmpeg binaries from builder
COPY --from=ffmpeg-builder /usr/local/bin/ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /usr/local/bin/ffprobe /usr/local/bin/ffprobe

# Copy Python venv from builder
COPY --from=python-builder /venv /venv

# Copy application code
# ubuntu:24.04 ships 'ubuntu' at UID/GID 1000 — use it as-is, but ensure it
# can access Intel render nodes in environments where the container relies on a
# fixed supplemental render group.
RUN if ! getent group render >/dev/null; then \
    groupadd -g 992 render; \
    fi \
    && usermod -aG render ubuntu

WORKDIR /app
COPY --chown=ubuntu:ubuntu . .

# Rewrite repo entry-script shebangs for the container venv location.
# Source checkouts use /opt/sma/venv/bin/python3 for bare-metal installs,
# while the Docker image builds and runs its virtualenv at /venv.
RUN find /app -maxdepth 1 -type f \( \
    -name 'daemon.py' -o \
    -name 'manual.py' -o \
    -name 'rename.py' \
    \) -exec sed -i '1s|^#!/opt/sma/venv/bin/python3$|#!/venv/bin/python3|' {} +

# Install entrypoint script (root-owned, world-executable)
COPY docker/docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Runtime directories (seeded at startup via entrypoint; override with volume mounts)
RUN mkdir -p /config /logs /mnt /downloads /transcodes \
    && chown -R ubuntu:ubuntu /config /logs /app

VOLUME ["/config", "/logs", "/mnt", "/downloads", "/transcodes"]

EXPOSE 8585

USER ubuntu

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8585/health')" || exit 1

# tini as PID 1 → entrypoint seeds /app/config → daemon starts
# All daemon options are driven by environment variables so they can be
# overridden at runtime with -e / --env-file without rebuilding the image.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "exec python3 daemon.py \
    --host      \"${SMA_DAEMON_HOST}\" \
    --port      \"${SMA_DAEMON_PORT}\" \
    --workers   \"${SMA_DAEMON_WORKERS}\" \
    --daemon-config \"${SMA_DAEMON_CONFIG}\" \
    --logs-dir  \"${SMA_DAEMON_LOGS_DIR}\""]
