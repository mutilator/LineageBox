# LineageBox's image: the web UI, the worker and the 720p copies service all run from it (linux/amd64 and linux/arm64).
FROM python:3.12-slim AS build
COPY docker/requirements.txt /tmp/requirements.txt
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt
# insightface installs desktop OpenCV, which needs X11 libraries; the headless build is the same code without them
RUN /venv/bin/pip uninstall -y opencv-python \
 && /venv/bin/pip install --no-cache-dir --disable-pip-version-check --no-deps opencv-python-headless==5.0.0.93

FROM python:3.12-slim
# ffmpeg for videos: probing, frames, sound for transcripts and 720p copies (Debian's build, with x264);
# tzdata so TZ can name a zone (TZ=Europe/Berlin); ExifTool to write rotations into photo files (writeback.py);
# DejaVu Sans (which ffmpeg brings in anyway) for the words on tours saved as a video, accents and all (slideshow.py).
# packages.txt lists every Debian package in the image with its version (THIRD_PARTY_NOTICES.md says why)
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg tzdata libimage-exiftool-perl \
    fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /app \
 && dpkg-query -W > /app/packages.txt
COPY --from=build /venv /venv
COPY lineagebox /app/lineagebox
COPY docker/config.toml /app/docker/config.toml
# LineageBox's licence, and what else the image includes and under which licences
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
# compiled now, so containers running as any user (PUID/PGID) never need to write next to the code;
# python-packages.txt: the exact Python packages, whose licence files are in /venv/lib/python3.12/site-packages
RUN /venv/bin/python -m compileall -q /app/lineagebox \
 && /venv/bin/pip freeze --all --disable-pip-version-check > /app/python-packages.txt
ENV PATH=/venv/bin:$PATH PYTHONPATH=/app PYTHONUNBUFFERED=1 LINEAGEBOX_CONFIG=/app/docker/config.toml
# where this image's releases are published (owner/name on GitHub), for Status → Version and updates; a fork's CI
# sets its own
ARG LINEAGEBOX_REPOSITORY="mutilator/LineageBox"
ENV LINEAGEBOX_UPDATES_REPOSITORY=$LINEAGEBOX_REPOSITORY
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=20s --start-period=120s --retries=3 CMD ["python", "-m", "lineagebox.health"]
# org.opencontainers.image.source comes from docker/metadata-action in CI
LABEL org.opencontainers.image.title="LineageBox" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="A private family archive: the family tree, and the photos and videos its people are in"
ENTRYPOINT ["python", "-m", "lineagebox"]
