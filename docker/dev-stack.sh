#!/usr/bin/env bash
# Run a separate test copy of Sanguity from any branch, beside a real install on the same machine.
#
#   bash dev-stack.sh --dir /mnt/pool/apps/sanguity-dev [--branch NAME] [options]
#
# Where it runs: with access to Docker (root, or the docker group) it's a compose project. On TrueNAS without
# that, it becomes a custom app ("Install via YAML") through the TrueNAS middleware, so an admin account can run
# it without sudo; the app then shows on the Apps page and starts with the server.
#
# Everything lives under --dir and nothing else is written:
#   src/       the checked-out code (replaced on every run; don't edit here)
#   data/      the test copy's database and profiles     watch/   its own watch receiver
#   backups/   its backups (server.manage backup)
#   auth/      its secrets                               config/  your sources.json, corrections.json and
#                                                                 settings.json (copied into src/ on each run)
# Record exports given with --records and the watch summary given with --watch-public are mounted read-only,
# so the real install's files are never changed.
#
# Options
#   --branch NAME          branch or tag to run (default: main)
#   --name NAME            compose project or TrueNAS app name (default: sanguity-dev)
#   --port N               web port; the watch receiver gets N+1 (default: 8740)
#   --records DIR          folder holding your record exports (read-only); without it the demo data is used
#   --watch-public DIR     a real install's watch summary folder (WATCH_DIR/public) to show, read-only
#   --watch-key FILE       that install's key file (auth/data_key): the summary is encrypted with it. Added after
#                          the test copy's own key in its key file, so it is used only to read the summary
#   --puid N --pgid N      user and group the containers run as; use the owner of --records (default: 568:568)
#   --tz ZONE              time zone (default: this machine's)
#   --public-origin URL    an HTTPS address in front of it (e.g. tailscale serve), for passkeys and the CSRF check
#   --truenas-app          deploy as a TrueNAS custom app even when Docker is reachable
#   --down                 stop the test copy (its data stays in --dir)
#   --logs                 follow its logs (compose project only; for an app use the Apps page)
#   --reset                stop it and delete its database, profiles and secrets (src/ and config/ stay)
#   --remove               TrueNAS app only: delete the app (--dir stays; delete the dataset yourself)
set -euo pipefail

REPO=https://github.com/sanguity/sanguity
DIR="" BRANCH=main NAME=sanguity-dev PORT=8740 RECORDS="" WATCH_PUBLIC="" WATCH_KEY="" PUID=568 PGID=568 ACTION=up MODE="" ORIGIN=""
TZONE=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo UTC)
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR=$2; shift ;;
    --branch) BRANCH=$2; shift ;;
    --name) NAME=$2; shift ;;
    --port) PORT=$2; shift ;;
    --records) RECORDS=$2; shift ;;
    --watch-public) WATCH_PUBLIC=$2; shift ;;
    --watch-key) WATCH_KEY=$2; shift ;;
    --puid) PUID=$2; shift ;;
    --pgid) PGID=$2; shift ;;
    --tz) TZONE=$2; shift ;;
    --public-origin) ORIGIN=$2; shift ;;
    --truenas-app) MODE=app ;;
    --down) ACTION=down ;;
    --logs) ACTION=logs ;;
    --reset) ACTION=reset ;;
    --remove) ACTION=remove ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
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
[ -z "$WATCH_KEY" ] || [ -r "$WATCH_KEY" ] || { echo "Can't read $WATCH_KEY (run as root, or as its owner)." >&2; exit 2; }
if [ -z "$MODE" ]; then
  if docker info >/dev/null 2>&1; then MODE=compose
  elif command -v midclt >/dev/null; then MODE=app
  else echo "Can't reach Docker (run as root or in the docker group) and this isn't TrueNAS." >&2; exit 2
  fi
fi

compose() { (cd "$DIR/src" && docker compose "$@"); }
app_exists() { midclt call app.query "[[\"name\",\"=\",\"$NAME\"]]" | grep -q "\"$NAME\""; }
stop() {
  if [ "$MODE" = app ]; then if app_exists; then midclt call -j app.stop "$NAME" >/dev/null; fi
  elif [ -d "$DIR/src" ]; then compose down; fi
}

case "$ACTION" in
  down) stop; echo "Stopped $NAME."; exit 0 ;;
  logs)
    [ "$MODE" = compose ] || { echo "Open Apps > $NAME in the TrueNAS UI and use its logs." >&2; exit 2; }
    compose logs -f; exit 0 ;;
  reset)
    stop
    rm -rf "$DIR/data" "$DIR/watch" "$DIR/auth"
    echo "Removed the test copy's database, profiles, watch data and secrets. Run again to start fresh."
    exit 0 ;;
  remove)
    [ "$MODE" = app ] || { echo "--remove is for the TrueNAS app; use --down for a compose project." >&2; exit 2; }
    if app_exists; then midclt call -j app.delete "$NAME" '{"remove_images": true}' >/dev/null; fi
    echo "Deleted the app $NAME. Its files are still in $DIR."
    exit 0 ;;
esac

mkdir -p "$DIR/data" "$DIR/backups" "$DIR/watch" "$DIR/auth" "$DIR/config" "$DIR/records"
if [ -n "$WATCH_KEY" ]; then   # read the real install's (encrypted) watch summary: its keys after this copy's own
  KEY="$DIR/auth/data_key"
  if [ ! -s "$KEY" ]; then
    { echo "# The test copy's encryption key (the first line encrypts). Lines after it come from --watch-key and"
      echo "# are only used to read the real install's watch summary."
      python3 -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'; } > "$KEY"
  fi
  grep -v '^#' "$WATCH_KEY" | grep . | while read -r line; do grep -qxF "$line" "$KEY" || echo "$line" >> "$KEY"; done
  chmod 600 "$KEY"
  chown "$PUID:$PGID" "$KEY" 2>/dev/null || echo "Note: couldn't hand $KEY to $PUID:$PGID; the app must be able to read it." >&2
fi

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
COMPOSE_PROJECT_NAME=$NAME
WEB_PORT=$PORT
RECEIVER_PORT=$((PORT + 1))
DATA_DIR=$DIR/data
BACKUP_DIR=$DIR/backups
WATCH_DIR=$DIR/watch
RECORDS_DIR=${RECORDS:-$DIR/records}
TZ=$TZONE
PUID=$PUID
PGID=$PGID
PUBLIC_ORIGINS=$ORIGIN
EOF
if [ -n "$WATCH_PUBLIC" ]; then   # show the real install's watch summary instead of this copy's own (read-only)
  cat > "$DIR/src/compose.override.yaml" <<EOF
services:
  app:
    volumes:
      - $WATCH_PUBLIC:/srv/watch:ro
EOF
fi

# ---- build and start
if [ "$MODE" = compose ]; then
  compose up -d --build --remove-orphans
else
  # The repo's compose.yaml with .env filled in and every path made absolute (a custom app has no folder of its
  # own), plus the override; the backend image is built from src/ on every deploy instead of being pulled.
  python3 -I - "$DIR/src" > "$DIR/app.json" <<'PY'
import json, os, re, sys
import yaml
src = sys.argv[1]
env = dict(l.strip().split("=", 1) for l in open(os.path.join(src, ".env")) if "=" in l and not l.startswith("#"))
text = re.sub(r"\$\{(\w+)(?::-([^}]*))?\}", lambda m: env.get(m.group(1)) or m.group(2) or "", open(os.path.join(src, "compose.yaml")).read())
c = yaml.safe_load(text)
c.pop("name", None)
here = lambda p: os.path.realpath(os.path.join(src, p)) if p.startswith(".") else p
for svc in c["services"].values():
    if isinstance(svc.get("environment"), dict):   # "" stays "", not null (which would mean: copy from the host)
        svc["environment"] = {k: "" if v is None else str(v) for k, v in svc["environment"].items()}
    vols = []
    for v in svc.get("volumes", []):
        parts = v.split(":")
        parts[0] = here(parts[0])
        vols.append(":".join(parts))
    svc["volumes"] = vols
    if "build" in svc:
        svc["build"]["context"] = here(svc["build"].get("context", "."))
        svc.pop("image", None)            # nothing to pull: built here
        svc["pull_policy"] = "build"      # rebuilt on every deploy, so a code update takes effect
override = os.path.join(src, "compose.override.yaml")
if os.path.exists(override):
    for name, svc in (yaml.safe_load(open(override)).get("services") or {}).items():
        for v in svc.get("volumes", []):
            target = v.split(":")[1]
            c["services"][name]["volumes"] = [x for x in c["services"][name]["volumes"] if x.split(":")[1] != target] + [v]
print(json.dumps(yaml.safe_dump(c, sort_keys=False)))
PY
  if app_exists; then
    midclt call -j app.update "$NAME" "{\"custom_compose_config_string\": $(cat "$DIR/app.json")}" >/dev/null
    midclt call -j app.redeploy "$NAME" >/dev/null   # rebuild even when only the code changed
  else
    midclt call -j app.create "{\"app_name\": \"$NAME\", \"custom_app\": true, \"custom_compose_config_string\": $(cat "$DIR/app.json")}" >/dev/null
  fi
fi

# ---- wait until it answers
printf 'Waiting for the test copy'
for _ in $(seq 1 90); do
  if curl -fs -o /dev/null "http://127.0.0.1:$PORT/api/healthz"; then break; fi
  printf .; sleep 2
done
echo
if ! curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/healthz"; then
  if [ "$MODE" = compose ]; then echo "It didn't come up; see: bash $0 --dir $DIR --logs" >&2
  else echo "It didn't come up; see Apps > $NAME in the TrueNAS UI." >&2; fi
  exit 1
fi
HOST=$( (hostname -I 2>/dev/null || true) | awk '{print $1}')
echo "Running $COMMIT"
echo "Open http://${HOST:-this-machine}:$PORT  (watch receiver on port $((PORT + 1)))"
if curl -fsS "http://127.0.0.1:$PORT/api/setup" | grep -q '"needed":true'; then
  CODE=$(cat "$DIR/data/setup-code.txt" 2>/dev/null || true)
  [ -n "$CODE" ] || CODE=$(compose logs app 2>/dev/null | grep -o 'Setup code: [A-Z0-9-]*' | tail -1 | cut -d' ' -f3 || true)
  [ -n "$CODE" ] || CODE="(in the log of the app container)"
  echo "First run: create the test copy's first account with setup code $CODE"
fi
