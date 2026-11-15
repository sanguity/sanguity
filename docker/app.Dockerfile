# The backend image holds Python and the pinned, hash-checked dependencies (requirements.txt, generated from
# requirements.in); compose.yaml mounts server/ read-only, so `git pull` updates the code. Rebuild
# (docker compose up -d --build) when requirements.txt changes.
FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt /app/requirements.txt
# Prebuilt packages exist for x86-64 and 64-bit ARM. Where one is missing (32-bit ARM, e.g. a Raspberry Pi on a
# 32-bit OS), cffi and argon2 are compiled: the compiler is installed only for that and removed again.
RUN pip install --no-cache-dir --disable-pip-version-check --require-hashes -r /app/requirements.txt \
 || (apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
     && pip install --no-cache-dir --disable-pip-version-check --require-hashes -r /app/requirements.txt \
     && apk del .build-deps)
COPY server/ /app/server/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "server"]
