# The backend image holds Python and the pinned, hash-checked dependencies (requirements.txt, generated from
# requirements.in); compose.yaml mounts server/ read-only, so `git pull` updates the code. Rebuild
# (docker compose up -d --build) when requirements.txt changes.
#
# Stages: "app" is what compose.yaml builds from the checkout (the default: the last stage below is the same).
# "release" is the published image, ghcr.io/sanguity/sanguity (.github/workflows/release.yml): the same plus all
# the code, the web pages and nginx, so an install needs no checkout (compose.release.yaml, deploy/).
FROM python:3.12-alpine AS app
WORKDIR /app
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
COPY build.py lab-aliases.json sources.example.json corrections.example.json settings.example.json /app/
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
