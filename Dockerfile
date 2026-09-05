# Ready-to-run image: ffmpeg for generating signatures, mpeg7dupes for
# comparing them. Build it yourself rather than pulling a prebuilt one; the
# Makefile compiles with -march=native, so the binary is tuned for whatever
# machine builds the image and may crash with SIGILL elsewhere.
#
#   docker build -t mpeg7dupes .
#   docker run --rm -v "$PWD:/data" mpeg7dupes
#
# See the README for the full workflow.

# ---------- build ----------
FROM debian:bookworm-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates \
        libavcodec-dev libavfilter-dev \
    && rm -rf /var/lib/apt/lists/*

# slog is needed only to build. libslog.a is static, so it ends up inside the
# binary and the runtime stage never sees it. Pin SLOG_REF to reproduce an
# exact build; the default tracks upstream.
ARG SLOG_REF=master
RUN git clone --quiet https://github.com/kala13x/slog /tmp/slog \
    && cd /tmp/slog \
    && git checkout --quiet "${SLOG_REF}" \
    && make \
    && make install \
    && rm -rf /tmp/slog

WORKDIR /src
COPY . .
RUN make release -l"$(nproc)"

# ---------- runtime ----------
FROM debian:bookworm-slim

# ffmpeg is doing double duty here: it generates the signatures in the first
# place, and it brings in the shared libavcodec/libavfilter that the binary
# links against.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/bin/mpeg7Dupes.elf /usr/local/bin/mpeg7dupes

# Mount your signatures here.
WORKDIR /data

CMD ["mpeg7dupes", "--help"]
