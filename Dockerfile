# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: FFmpeg 8.x builder
#
# Produces a mostly-static FFmpeg binary. Codec libraries (x264, x265, aac,
# opus, etc.) are linked statically. GPU acceleration APIs (VAAPI, QSV via
# oneVPL, NVENC via nv-codec-headers) are linked dynamically because they
# must dlopen GPU-vendor driver backends at runtime on the host machine.
# ──────────────────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS ffmpeg-builder

ARG FFMPEG_VERSION=8.0
ARG MAKEFLAGS="-j$(nproc)"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
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
    # oneVPL / Intel QSV (dynamic — MFX dispatcher loaded at runtime)
    libvpl-dev \
    # Codec libraries (static)
    libssl-dev \
    zlib1g-dev \
    libx264-dev \
    libx265-dev \
    libmp3lame-dev \
    libopus-dev \
    libvorbis-dev \
    libogg-dev \
    libvpx-dev \
    libfdk-aac-dev \
    libass-dev \
    libfreetype6-dev \
    libfontconfig1-dev \
    libzimg-dev \
    libopenjp2-7-dev \
    libaom-dev \
    libdav1d-dev \
    libsvtav1enc-dev \
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

RUN ./configure \
    --prefix=/usr/local \
    --extra-cflags="-I/usr/local/include" \
    --extra-ldflags="-L/usr/local/lib" \
    --pkg-config-flags="--static" \
    # Link all codec libraries statically
    --extra-libs="-lpthread -lm -lz -ldl" \
    # Disable shared FFmpeg libs — produce standalone binaries
    --enable-static \
    --disable-shared \
    # Enable GPL and non-free (required for x264, fdk-aac)
    --enable-gpl \
    --enable-nonfree \
    --enable-version3 \
    # Disable unnecessary components to keep the binary lean
    --disable-doc \
    --disable-htmlpages \
    --disable-manpages \
    --disable-podpages \
    --disable-txtpages \
    --disable-debug \
    --enable-optimizations \
    # Hardware acceleration (dynamic — GPU drivers on host)
    --enable-vaapi \
    --enable-libdrm \
    --enable-libvpl \
    --enable-ffnvcodec \
    --enable-nvenc \
    --enable-nvdec \
    # Codec support (statically linked)
    --enable-libx264 \
    --enable-libx265 \
    --enable-libmp3lame \
    --enable-libopus \
    --enable-libvorbis \
    --enable-libvpx \
    --enable-libfdk-aac \
    --enable-libass \
    --enable-libfreetype \
    --enable-libfontconfig \
    --enable-libzimg \
    --enable-libopenjpeg \
    --enable-libaom \
    --enable-libdav1d \
    --enable-libsvtav1 \
    # Network / TLS
    --enable-openssl \
    --enable-protocol=https \
    && make ${MAKEFLAGS} \
    && make install \
    && strip /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: Python dependency builder
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS python-builder

WORKDIR /build

COPY setup/requirements.txt ./
COPY pyproject.toml ./
COPY README.md ./docs/README.md

RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-warn-script-location -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# Stage 3: Runtime image
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

ARG UID=1000
ARG GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:$PATH" \
    # Default SMA paths — override via environment or volume mounts
    SMA_CONFIG=/config/autoProcess.ini

# GPU acceleration runtime libraries:
#   libva2        — VAAPI dispatcher
#   libva-drm2    — VAAPI DRM backend
#   libdrm2       — DRM/KMS
#   libvpl2       — Intel oneVPL dispatcher (QSV)
#   libass9       — ASS subtitle rendering
#   libfreetype6  — Font rendering
#   libfontconfig1— Font configuration
#   libopenjp2-7  — JPEG2000
#   libzimg2      — zscale filter
# NVENC requires no runtime libs — the NVIDIA driver on the host provides them
# when the container is run with --gpus / --runtime=nvidia
RUN apt-get update && apt-get install -y --no-install-recommends \
    libva2 \
    libva-drm2 \
    libdrm2 \
    libvpl2 \
    libass9 \
    libfreetype6 \
    libfontconfig1 \
    libopenjp2-7 \
    libzimg2 \
    libopus0 \
    libvorbis0a \
    libvorbisenc2 \
    libvpx7 \
    libmp3lame0 \
    # Misc runtime
    ca-certificates \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copy FFmpeg binaries from builder
COPY --from=ffmpeg-builder /usr/local/bin/ffmpeg  /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /usr/local/bin/ffprobe /usr/local/bin/ffprobe

# Copy Python packages from builder
COPY --from=python-builder /install /usr/local

# Create non-root user
RUN groupadd -g ${GID} sma && useradd -u ${UID} -g sma -s /bin/sh -m sma

# Copy application code
WORKDIR /app
COPY --chown=sma:sma . .

# Install entrypoint script (root-owned, world-executable)
COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Runtime directories (seeded at startup via entrypoint; override with volume mounts)
RUN mkdir -p /config /logs \
    && chown -R sma:sma /config /logs /app

VOLUME ["/config", "/logs"]

EXPOSE 8585

USER sma

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8585/health')" || exit 1

# tini as PID 1 → entrypoint seeds /config → daemon starts
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "daemon.py", \
     "--host", "0.0.0.0", \
     "--port", "8585", \
     "--workers", "2", \
     "--daemon-config", "/config/daemon.json", \
     "--db", "/config/daemon.db", \
     "--logs-dir", "/logs"]
