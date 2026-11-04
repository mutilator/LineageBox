# The speech-to-text service for LineageBox's video transcripts, for Linux on the processor: whisper.cpp built from
# source, with the small Python service LineageBox talks to in front of it. compose.whisper.yaml runs it next to
# LineageBox; the models are downloaded into /data when it first starts (about 600 MB with the default one).
# Built from the project's root (docker build -f whisper_service/Dockerfile .), for LICENSE: Dockerfile.dockerignore
# next to this file says what the builder is sent.
FROM python:3.12-slim AS build
ARG WHISPER_CPP=v1.9.5
# ON: tuned to the processor it is built on, which is where compose runs it. OFF for an image to run elsewhere.
ARG NATIVE=ON
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$WHISPER_CPP" https://github.com/ggml-org/whisper.cpp /src \
 && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE="$NATIVE" \
          -DWHISPER_BUILD_TESTS=OFF \
 && cmake --build /build -j "$(nproc)" --target whisper-server whisper-vad-speech-segments \
 && mkdir /out \
 && cp /build/bin/whisper-server /build/bin/whisper-vad-speech-segments /out/ \
 && echo "whisper.cpp $WHISPER_CPP (CPU)" > /out/VERSION
# The licences of what is compiled into the two programs: whisper.cpp with ggml; cpp-httplib and nlohmann/json (the
# server); miniaudio and stb_vorbis (reading the sound); the YaRN code in ggml. miniaudio and stb_vorbis keep their
# terms at the end of their own files. cpp-httplib's and json's headers only name the MIT licence, so its text is
# fetched for the version whisper.cpp bundles, as is YaRN's (no tags there, so at a fixed commit).
RUN mkdir /out/licenses \
 && cp /src/LICENSE /out/licenses/whisper.cpp.txt \
 && sed -n '/^This software is available as a choice of the following licenses/,/^\*\//p' /src/examples/miniaudio.h \
    | sed '$d' > /out/licenses/miniaudio.txt \
 && sed -n '/^This software is available under 2 licenses/,/^\*\//p' /src/examples/stb_vorbis.c \
    | sed '$d' > /out/licenses/stb_vorbis.txt \
 && httplib=$(sed -n 's/^#define CPPHTTPLIB_VERSION "\([0-9.]*\)"/\1/p' /src/examples/server/httplib.h) \
 && json=$(sed -n 's/^#define NLOHMANN_JSON_VERSION_\(MAJOR\|MINOR\|PATCH\) \([0-9]*\).*/\2/p' /src/examples/json.hpp \
           | paste -sd.) \
 && fetch() { python -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"; } \
 && fetch "https://raw.githubusercontent.com/yhirose/cpp-httplib/v$httplib/LICENSE" /out/licenses/cpp-httplib.txt \
 && fetch "https://raw.githubusercontent.com/nlohmann/json/v$json/LICENSE.MIT" /out/licenses/nlohmann-json.txt \
 && fetch https://raw.githubusercontent.com/jquesnelle/yarn/995db5b575e75230b3384d658f8b944c9662f775/LICENSE \
          /out/licenses/yarn.txt \
 && for f in /out/licenses/*; do grep -qi "permission\|public domain" "$f" || { echo "no licence in $f"; exit 1; }; done

FROM python:3.12-slim
# packages.txt: every Debian package in the image and its version, for their sources (snapshot.debian.org) and
# /usr/share/doc/<package>/copyright; python-packages.txt the same for Python's
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir --disable-pip-version-check fastapi==0.162.0 uvicorn==0.54.0 httpx==0.28.1 \
 && mkdir -p /opt/whisper \
 && dpkg-query -W > /opt/whisper/packages.txt \
 && pip freeze --all --disable-pip-version-check > /opt/whisper/python-packages.txt
COPY --from=build /out/whisper-server /out/whisper-vad-speech-segments /out/VERSION /opt/whisper/bin/
COPY --from=build /out/licenses /opt/whisper/licenses
COPY whisper_service /app/whisper_service
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 WHISPER_HOME=/data WHISPER_BIN=/opt/whisper/bin
WORKDIR /app
EXPOSE 9300
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9300/health', timeout=8)"]
# org.opencontainers.image.source comes from docker/metadata-action in CI. The licence is the service's, LineageBox's
# (whisper.cpp itself is MIT; the notices are in /opt/whisper/licenses).
LABEL org.opencontainers.image.title="LineageBox Whisper" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="Speech to text for LineageBox's videos: whisper.cpp on the processor"
ENTRYPOINT ["python", "-m", "whisper_service", "--download"]
