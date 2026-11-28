# The backend image holds Python and the pinned, hash-checked dependencies (requirements.txt, generated from
# requirements.in); compose.yaml mounts server/ read-only, so `git pull` updates the code. Rebuild
# (docker compose up -d --build) when requirements.txt changes.
#
# Stages: "app" is what compose.yaml builds from the checkout (the default: the last stage below is the same).
# "release" is the published image, ghcr.io/sanguity/sanguity (.github/workflows/release.yml): the same plus all
# the code, the web pages and nginx, so an install needs no checkout (compose.release.yaml, deploy/).
# ---- Tesseract, for text recognition of scanned documents (#1129, server/ocr.py). Alpine's package brings its training
# tools and what they need (pango, cairo, glib, ICU: about 35 MB); the recogniser is only the program, libtesseract and
# leptonica, taken from here into the app's image (stage "app", one layer, its other libraries Alpine's own)
FROM python:3.12-alpine AS tesseract
RUN apk add --no-cache tesseract-ocr \
 && mkdir -p /out/usr/bin /out/usr/lib \
 && cp -a /usr/bin/tesseract /out/usr/bin/ \
 && cp -a /usr/lib/libtesseract.so.5* /usr/lib/libleptonica.so.6* /out/usr/lib/

FROM python:3.12-alpine AS app
WORKDIR /app
# Its English is tessdata_fast's (4 MB; Alpine's tesseract-ocr-data-eng is 23 MB), at a fixed commit, checked by its
# SHA-256. Other languages are an admin's to add, in the data folder. CI checks this layer's size (about 16 MB; at most
# 40 MB) and recognises a line of text with it (python -m server.ocr check).
ARG TESSDATA_FAST=87416418657359cb625c412a48b6e1d6d41c29bd
ARG TESSDATA_ENG_SHA256=7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2
RUN --mount=type=bind,from=tesseract,source=/out,target=/tmp/tesseract \
    cp -a /tmp/tesseract/. / \
 && apk add --no-cache giflib libgomp libjpeg-turbo libpng libstdc++ libwebp libwebpmux tiff \
 && mkdir -p /usr/share/tessdata \
 && wget -q -O /usr/share/tessdata/eng.traineddata "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/${TESSDATA_FAST}/eng.traineddata" \
 && echo "${TESSDATA_ENG_SHA256}  /usr/share/tessdata/eng.traineddata" | sha256sum -c - \
 && tesseract --list-langs
COPY requirements.txt /app/requirements.txt
# Prebuilt packages exist for x86-64 and 64-bit ARM. Where one is missing (32-bit ARM, e.g. a Raspberry Pi on a
# 32-bit OS), cffi, argon2 and cryptography (encryption at rest and passkeys; it needs Rust, so this takes a while) are
# compiled: the compilers are installed only for that and removed again.
RUN pip install --no-cache-dir --disable-pip-version-check --require-hashes -r /app/requirements.txt \
 || (apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev openssl-dev cargo \
     && pip install --no-cache-dir --disable-pip-version-check --require-hashes -r /app/requirements.txt \
     && apk del .build-deps)
COPY server/ /app/server/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "server"]

# ---- the published image: one image for every service of compose.release.yaml, or all of them in one container
FROM app AS release
ARG VERSION=dev
# nginx serves the pages (the "web" service, or inside the all-in-one container); tzdata gives the watch receiver
# its time zones without the host's /usr/share/zoneinfo
RUN apk add --no-cache nginx tzdata \
 && rm -rf /etc/nginx/http.d /var/lib/nginx/html
COPY build.py lab-aliases.json condition-presets.json lab-test-pages.json sources.example.json corrections.example.json settings.example.json /app/
COPY demo/generate.py /app/demo/generate.py
COPY docker/setup.py docker/allinone.py /app/docker/
COPY watch/receiver.py /app/receiver.py
COPY www/ /usr/share/nginx/html/
COPY nginx.conf /etc/sanguity/nginx.d/default.conf
COPY docker/nginx-main.conf /etc/nginx/nginx.conf
# Container paths of a released install (the same in every service and in the all-in-one container)
ENV SANGUITY_VERSION=${VERSION} \
    SANGUITY_DATA=/data \
    SANGUITY_BACKUPS=/backups \
    SANGUITY_WATCH=/watch \
    SANGUITY_WATCH_FILE=/watch/public/daily.json     SANGUITY_WATCH_KEYS=/watch/keys/receiver-secrets.json \
    SANGUITY_WATCH_STORE=/watch \
    SANGUITY_RECORDS=/srv/records \
    SANGUITY_CONFIG=/config \
    SANGUITY_AUTH=/run/secrets/sanguity \
    SANGUITY_KEY_FILE=/run/secrets/sanguity/data_key
LABEL org.opencontainers.image.title="Sanguity" \
      org.opencontainers.image.description="Your medical records, insurance claims and wearable data on one private timeline, on your own server" \
      org.opencontainers.image.source="https://github.com/sanguity/sanguity" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.version="${VERSION}"
EXPOSE 8080 8081
# With no command: everything in one container (setup, then the app, nginx and the watch receiver), for installs
# that run a single container (Unraid). compose.release.yaml gives each service its own command instead.
CMD ["python3", "-I", "/app/docker/allinone.py"]

# ---- default target: what `docker build` and compose.yaml build without naming a stage
FROM app
