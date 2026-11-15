#!/usr/bin/env bash
# Run a separate test copy of Sanguity from any branch, beside a real install on the same machine.
#
#   sudo bash dev-stack.sh --dir /mnt/pool/apps/sanguity-dev [--branch NAME] [options]
#
# Everything lives under --dir and nothing else is written:
#   src/       the checked-out code (replaced on every run; don't edit here)
#   data/      the test copy's database and profiles     watch/   its own watch receiver
#   auth/      its secrets                               config/  your sources.json, corrections.json and
#                                                                 settings.json (copied into src/ on each run)
# It runs as its own compose project (sanguity-dev) on its own ports. Record exports given with --records and
# the watch summary given with --watch-public are mounted read-only, so the real install's files are never changed.
#
# Options
#   --branch NAME          branch or tag to run (default: main)
#   --port N               web port; the watch receiver gets N+1 (default: 8740)
#   --records DIR          folder holding your record exports (read-only); without it the demo data is used
#   --watch-public DIR     a real install's watch summary folder (WATCH_DIR/public) to show, read-only
#   --puid N --pgid N      user and group the containers run as; use the owner of --records (default: 568:568)
#   --tz ZONE              time zone (default: this machine's)
#   --down                 stop the test copy (its data stays in --dir)
#   --logs                 follow its logs
#   --reset                stop it and delete its database, profiles and secrets (src/ and config/ stay)
set -euo pipefail

REPO=https://github.com/sanguity/sanguity
DIR="" BRANCH=main PORT=8740 RECORDS="" WATCH_PUBLIC="" PUID=568 PGID=568 TZONE="" ACTION=up
TZONE=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR=$2; shift ;;
    --branch) BRANCH=$2; shift ;;
    --port) PORT=$2; shift ;;
    --records) RECORDS=$2; shift ;;
    --watch-public) WATCH_PUBLIC=$2; shift ;;
    --puid) PUID=$2; shift ;;
    --pgid) PGID=$2; shift ;;
    --tz) TZONE=$2; shift ;;
    --down) ACTION=down ;;
    --logs) ACTION=logs ;;
    --reset) ACTION=reset ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$DIR" ] || { echo "Give --dir: an empty folder (ideally its own dataset) for the test copy." >&2; exit 2; }
DIR=$(realpath -m "$DIR")
for p in "$RECORDS" "$WATCH_PUBLIC"; do
  [ -z "$p" ] || [ -d "$p" ] || { echo "No such folder: $p" >&2; exit 2; }
  case "$p" in "$DIR"/*) echo "Keep $p outside --dir." >&2; exit 2 ;; esac
done
compose() { (cd "$DIR/src" && docker compose "$@"); }

case "$ACTION" in
  down) compose down; exit 0 ;;
  logs) compose logs -f; exit 0 ;;
  reset)
    [ -d "$DIR/src" ] && compose down || true
    rm -rf "$DIR/data" "$DIR/watch" "$DIR/auth"
    echo "Removed the test copy's database, profiles, watch data and secrets. Run again to start fresh."
    exit 0 ;;
esac

mkdir -p "$DIR/data" "$DIR/watch" "$DIR/auth" "$DIR/config" "$DIR/records"

# ---- code: the branch's latest commit (git if this machine has it, otherwise GitHub's archive)
if command -v git >/dev/null; then
  if [ -d "$DIR/src/.git" ]; then
    git -C "$DIR/src" fetch --quiet --depth 1 origin "$BRANCH"
    git -C "$DIR/src" checkout --quiet --force -B "$BRANCH" FETCH_HEAD
    git -C "$DIR/src" clean --quiet -fdx -e auth   # drop files from older runs (config is copied in again below)
  else
    rm -rf "$DIR/src"
    git clone --quiet --depth 1 --branch "$BRANCH" "$REPO.git" "$DIR/src"
  fi
  COMMIT=$(git -C "$DIR/src" log -1 --format='%h %s')
else
  rm -rf "$DIR/src.new" && mkdir -p "$DIR/src.new"
  curl -fsSL "$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$DIR/src.new" --strip-components 1
  rm -rf "$DIR/src" && mv "$DIR/src.new" "$DIR/src"
  COMMIT="$BRANCH (downloaded $(date '+%F %R'))"
fi
rm -rf "$DIR/src/auth" && ln -s "$DIR/auth" "$DIR/src/auth"   # secrets survive code updates

# ---- your configuration for this copy (only what exists in config/)
for f in sources.json corrections.json settings.json; do
  if [ -f "$DIR/config/$f" ]; then cp "$DIR/config/$f" "$DIR/src/$f"; fi
done
if [ -n "$RECORDS" ] && [ ! -f "$DIR/config/sources.json" ]; then
  echo "--records is set but $DIR/config/sources.json doesn't exist: describe your exports there (see sources.example.json)." >&2
  echo "The folders in $RECORDS are:" >&2; ls -1 "$RECORDS" >&2; exit 2
fi

cat > "$DIR/src/.env" <<EOF
# Written by docker/dev-stack.sh; edit the script's options rather than this file.
COMPOSE_PROJECT_NAME=sanguity-dev
WEB_PORT=$PORT
RECEIVER_PORT=$((PORT + 1))
DATA_DIR=$DIR/data
WATCH_DIR=$DIR/watch
RECORDS_DIR=${RECORDS:-$DIR/records}
TZ=$TZONE
PUID=$PUID
PGID=$PGID
EOF
if [ -n "$WATCH_PUBLIC" ]; then   # show the real install's watch summary instead of this copy's own (read-only)
  cat > "$DIR/src/compose.override.yaml" <<EOF
services:
  app:
    volumes:
      - $WATCH_PUBLIC:/srv/watch:ro
EOF
fi

# ---- build and start, then wait until it answers
compose up -d --build --remove-orphans
printf 'Waiting for the test copy'
for _ in $(seq 1 90); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/healthz"; then break; fi
  printf .; sleep 2
done
echo
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/healthz" || { echo "It didn't come up; see: bash $0 --dir $DIR --logs" >&2; exit 1; }
HOST=$( (hostname -I 2>/dev/null || true) | awk '{print $1}')
echo "Running $COMMIT"
echo "Open http://${HOST:-this-machine}:$PORT  (watch receiver on port $((PORT + 1)))"
CODE=$(compose logs app 2>/dev/null | grep -o 'Setup code: [A-Z0-9-]*' | tail -1 | cut -d' ' -f3 || true)
if curl -fsS "http://127.0.0.1:$PORT/api/setup" | grep -q '"needed":true'; then
  echo "First run: create the test copy's first account with setup code $CODE"
fi
